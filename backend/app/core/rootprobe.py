"""Which root folder the server is waiting on right now, for the loading screen.

On 2026-11-18 opening VAM sat on 「載入中…」 for over a minute: the NAS had
been idle for an hour and a half, and every request — even `/api/config`,
which only reads settings — first resolves the configured roots, so they all
waited on the same reconnect and then finished in the same second. From the
outside that looked like a hang.

Code that goes through the roots one by one (`settings._resolve_list`,
`media.list_characters`) registers a *round* here and says which folder it is
on; `snapshot()` reports the round stuck longest. In memory only: a few dict
writes per call, nothing that could itself wait on a disk.
"""
from __future__ import annotations

import itertools
import threading
import time
from contextlib import contextmanager
from typing import Optional

_lock = threading.Lock()
_rounds: dict = {}
_ids = itertools.count()


class _Round:
    __slots__ = ("kind", "total", "done", "current", "since")

    def __init__(self, kind: str, total: int):
        self.kind, self.total, self.done = kind, total, 0
        self.current: Optional[str] = None
        self.since = time.monotonic()

    def at(self, path) -> None:
        """Now working on `path` (the previous one, if any, is done)."""
        with _lock:
            if self.current is not None:
                self.done += 1
            self.current, self.since = str(path), time.monotonic()


@contextmanager
def track(kind: str, total: int):
    rid = next(_ids)
    r = _Round(kind, total)
    with _lock:
        _rounds[rid] = r
    try:
        yield r
    finally:
        with _lock:
            _rounds.pop(rid, None)


def snapshot() -> Optional[dict]:
    """The folder waited on longest, or None when nothing is in progress.
    `waited` is in seconds; a folder answers in milliseconds normally, so
    anything over a second is worth showing."""
    now = time.monotonic()
    with _lock:
        live = [r for r in _rounds.values() if r.current is not None]
        if not live:
            return None
        r = min(live, key=lambda x: x.since)
        return {"kind": r.kind, "done": r.done, "total": r.total,
                "current": r.current, "waited": round(now - r.since, 1)}
