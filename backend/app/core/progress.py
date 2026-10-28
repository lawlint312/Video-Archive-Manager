"""Structured progress for long scans: which phase, how far, how much longer.

The duplicate scans used to report a single string — the name of the file being
processed right now. That says "not stuck", but it never says *when this ends*,
which over 16,000 files is the only question anyone actually has.

Deliberately **per phase, not one global percentage**. A scan walks folders,
hashes head+tail of same-sized files, runs ffprobe for durations, then decodes
frames for perceptual hashes. Those per-item costs differ by orders of
magnitude, so a single bar would crawl and then sprint — more misleading than
no bar at all. Phase k of n with its own bar tells the truth at every moment.

The remaining-time estimate is a rolling rate over a short window, not
total-elapsed / done: the early part of a phase is not representative (caches
cold, NAS spinning up), and a stale average would keep an obviously wrong
number on screen for minutes. It stays hidden until there is enough evidence
to say anything at all.
"""
from __future__ import annotations

import time
from collections import deque
from typing import Any, Callable, Optional

# Emit at most this often. The UI polls every 700 ms, so anything faster is
# thrown away — and every emit takes the jobs lock.
MIN_INTERVAL = 0.25
# Rate is measured over this window; long enough to ride out one slow file,
# short enough to react when a phase moves from local disk to the NAS.
ETA_WINDOW = 30.0
ETA_MIN_SAMPLES = 6
ETA_MIN_SECONDS = 4.0


class Reporter:
    """Collects phase/count/rate and pushes a dict to the job's progress field.

    `cb` is the job progress callback. When it carries a `.cancelled` flag
    (the one `jobs.start` supplies), `Reporter.cancelled` exposes it, so a scan
    only has to talk to one object.
    """

    def __init__(self, cb: Optional[Callable[[Any], None]] = None,
                 phases: int = 1):
        self._cb = cb
        self.phases = phases
        self.index = 0
        self.label = ""
        self.hint = ""
        self.total: Optional[int] = None
        self.approx = False
        self.done = 0
        self.name = ""
        self._last_emit = 0.0
        self._samples: deque = deque()      # (monotonic, done) within the phase

    # ── state ───────────────────────────────────────────────────────────────
    def phase(self, index: int, label: str, total: Optional[int] = None,
              hint: str = "", approx: bool = False):
        """Start a phase.

        `total=None` means the size is not knowable yet. `approx` means it is
        an expectation rather than a fact — the folder walk borrows last run's
        count, and the library will have grown since, so "16,500 / 16,204" is
        a real thing that would otherwise appear on screen. The bar can still
        use it; the counter must not pretend it is a limit.
        """
        self.finish()
        self.index = index
        self.label = label
        self.total = total
        self.approx = approx
        self.hint = hint
        self.done = 0
        self.name = ""
        self._samples.clear()
        self._samples.append((time.monotonic(), 0))
        self.emit(force=True)

    def step(self, name: str = "", n: int = 1):
        """One item done. Cheap: the throttle is the only work most calls do."""
        self.done += n
        if name:
            self.name = name
        self.emit()

    def note(self, name: str):
        """Update the current item without counting it (phase 1 has no total)."""
        self.name = name
        self.emit()

    def finish(self):
        """Push the phase's final count.

        Without this the throttle eats the last update and a phase visibly
        ends part-way — 812/1000 sitting there while the next phase starts
        reads as "it gave up", which is exactly the doubt this whole feature
        exists to remove.
        """
        if self.index:
            self.emit(force=True)

    # ── output ──────────────────────────────────────────────────────────────
    def emit(self, force: bool = False):
        if not self._cb:
            return
        now = time.monotonic()
        if not force and now - self._last_emit < MIN_INTERVAL:
            return
        self._last_emit = now
        self._samples.append((now, self.done))
        while len(self._samples) > 2 and now - self._samples[0][0] > ETA_WINDOW:
            self._samples.popleft()
        self._cb(self.snapshot())

    def eta(self) -> Optional[float]:
        """Seconds left in this phase, or None while that would be a guess."""
        if not self.total or self.done <= 0 or len(self._samples) < ETA_MIN_SAMPLES:
            return None
        t0, d0 = self._samples[0]
        t1, d1 = self._samples[-1]
        if t1 - t0 < ETA_MIN_SECONDS or d1 <= d0:
            return None
        remaining = self.total - d1
        if remaining <= 0:
            # Past an approximate total the truth is simply unknown — saying
            # "快好了" while the walk keeps finding files is worse than silence.
            return None if self.approx else 0.0
        return remaining * (t1 - t0) / (d1 - d0)

    def snapshot(self) -> dict:
        return {"phase": self.index, "phases": self.phases, "label": self.label,
                "done": self.done, "total": self.total, "approx": self.approx,
                "name": self.name, "hint": self.hint, "eta": self.eta()}

    # ── cancellation ────────────────────────────────────────────────────────
    @property
    def cancelled(self) -> bool:
        return bool(getattr(self._cb, "cancelled", False))
