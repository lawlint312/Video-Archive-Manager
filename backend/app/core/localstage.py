"""Convert on a local disk, then copy the finished file to where it belongs.

Measured on the 23-file VC-1 batch (2026-11-22, convert.md "轉檔先寫本機"):
XV-1080-1 took 6 minutes with its output on a local disk and 16 minutes
with it on the NAS, where every file of the batch took 16–23. The time went
to the MP4 muxer's `+faststart`: once the encode is done it moves the index
to the front by reading and rewriting the whole output — 3 GB through SMB at
5.5 MB/s, with the CPU and GPU idle. On a local disk the same pass is a few
seconds (Windows keeps the just-written file in its cache), and the one copy
to the NAS runs at the network's speed.

Why a disk and not memory: ffmpeg needs a seekable file for that pass, and
Windows has no RAM disk without a third-party driver. Its file cache already
gives the rewrite memory speed, and a full RAM copy of a long 4K output
(15–20 GB) could exhaust the machine.

The folder is a setting (`convert_staging_dir`, empty = write beside the
source as before). A file is staged only when the folder is on another volume
than the output — staging a local file on the same disk gains nothing — and
when the folder has room for twice the source plus `RESERVE`; otherwise that
file is written where it was before.

Relay (2026-11-22, the user's design): with only the output staged the GPU
still sat idle while each file was copied back. So a source on the network is
downloaded into the folder first (`Prefetcher`, up to `AHEAD` files before the
encoder needs them), encoded there without touching the NAS, and handed to a
background thread that verifies it and copies it back while the GPU starts the
next one. The user's rule for the NAS: one transfer at a time — a download and
a copy back to the same NAS never run together, because its disks would seek
between the two (`TURNS`; copies back go first, they free the folder and finish
a file). The staging disk itself may do several things at once.
"""
from __future__ import annotations

import contextlib
import logging
import ntpath
import os
import shutil
import threading
import uuid
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger("vam")

RESERVE = 10 * 2 ** 30          # left free on the staging disk, for the system
CHUNK = 16 * 2 ** 20
AHEAD = 3                       # sources downloaded before the encoder asks
FETCH_FACTOR = 3                # a download needs room for it + its output (outputs
                                # were up to 1.35× the source at crf 20)


class Stopped(Exception):
    """The copy was stopped; the partial copy is gone."""


def _volume(p) -> str:
    return ntpath.splitdrive(str(p).replace("/", "\\"))[0].lower()


def is_remote(p) -> bool:
    """A network path: UNC, or a drive letter mapped to a share."""
    s = str(p).replace("/", "\\")
    if s.startswith("\\\\"):
        return True
    drive = ntpath.splitdrive(s)[0]
    if os.name == "nt" and len(drive) == 2 and drive[1] == ":":
        try:
            import ctypes
            return ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") == 4  # DRIVE_REMOTE
        except Exception:
            return False
    return False


class _Turns:
    """One transfer at a time; a caller with `first=True` goes before the
    others waiting (copies back, and the encoder working on the NAS itself)."""

    def __init__(self):
        self._cv = threading.Condition()
        self._busy = False
        self._first_waiting = 0

    @contextlib.contextmanager
    def turn(self, first: bool = False):
        with self._cv:
            if first:
                self._first_waiting += 1
            try:
                while self._busy or (not first and self._first_waiting):
                    self._cv.wait()
            finally:
                if first:
                    self._first_waiting -= 1
            self._busy = True
        try:
            yield
        finally:
            with self._cv:
                self._busy = False
                self._cv.notify_all()


TURNS = _Turns()                # shared by every job: the NAS is one


def nas(*paths, first: bool = False):
    """Hold the NAS's turn when any of `paths` is on the network."""
    if any(p is not None and is_remote(p) for p in paths):
        return TURNS.turn(first)
    return contextlib.nullcontext()


def stamp(p: Path):
    """What says a file is still the one that was downloaded."""
    st = p.stat()
    return (st.st_size, st.st_mtime_ns)


def problem_with(folder: str) -> Optional[str]:
    """Why `folder` cannot be the staging folder, or None. For the settings."""
    f = (folder or "").strip()
    if not f:
        return None
    if f.startswith("\\\\") or f.startswith("//"):
        return "暫存資料夾要在這台電腦的磁碟上，不能是網路位置"
    p = Path(f)
    if not p.is_absolute():
        return "請使用完整路徑，例如 Z:\\VAM-temp"
    try:
        p.mkdir(parents=True, exist_ok=True)
        probe = p / f".vam-write-test-{uuid.uuid4().hex[:6]}"
        probe.write_bytes(b"ok")
        probe.unlink()
    except OSError as e:
        return f"無法寫入這個資料夾：{e.strerror or e}"
    return None


def pick(src: Path, dst: Path, folder: str) -> Optional[Path]:
    """A path in `folder` to encode `dst` into, or None to write `dst` directly."""
    f = (folder or "").strip()
    if not f:
        return None
    base = Path(f)
    if _volume(base) == _volume(dst):
        return None
    try:
        base.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(base).free
        need = 2 * src.stat().st_size + RESERVE
    except OSError as e:
        log.warning("轉檔暫存資料夾無法使用，直接寫到原處：%s（%s）", e, base)
        return None
    if free < need:
        log.info("轉檔暫存資料夾空間不足（剩 %.1f GB，需要 %.1f GB），%s 直接寫到原處",
                 free / 2 ** 30, need / 2 ** 30, src.name)
        return None
    return base / f"{uuid.uuid4().hex[:8]}-{dst.name}"


def fetch_target(src: Path, folder: str) -> Optional[Path]:
    """Where to download a network `src` before encoding it, or None to read
    it in place (not on the network, no folder, no room)."""
    f = (folder or "").strip()
    if not f or not is_remote(src) or is_remote(f):
        return None
    base = Path(f)
    try:
        base.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(base).free
        need = FETCH_FACTOR * src.stat().st_size + RESERVE
    except OSError as e:
        log.warning("轉檔暫存資料夾無法使用，直接讀原檔：%s（%s）", e, base)
        return None
    if free < need:
        log.info("轉檔暫存資料夾空間不足（剩 %.1f GB，需要 %.1f GB），%s 直接讀原檔",
                 free / 2 ** 30, need / 2 ** 30, src.name)
        return None
    return base / f"{uuid.uuid4().hex[:8]}-src-{src.name}"


def _copy(a: Path, b: Path, progress, cancelled, mismatch: str) -> None:
    """`a` → `b` (which must not exist), synced and size-checked.

    On any failure `b` is removed; a file already at `b` is never touched.
    """
    total = a.stat().st_size
    done = 0
    # opened before the cleanup below applies: a file already at `b` is not
    # this copy's to remove (FileExistsError goes straight to the caller)
    fo = open(b, "xb")
    try:
        with open(a, "rb") as fi, fo:
            while True:
                if cancelled and cancelled():
                    raise Stopped()
                chunk = fi.read(CHUNK)
                if not chunk:
                    break
                fo.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done / max(total, 1))
            fo.flush()
            os.fsync(fo.fileno())
        got = b.stat().st_size
        if got != total:
            raise RuntimeError(mismatch.format(got=got, total=total))
    except BaseException:
        try:
            b.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def fetch(src: Path, local: Path,
          progress: Optional[Callable[[float], None]] = None,
          cancelled: Optional[Callable[[], bool]] = None):
    """Download `src` to `local`; returns its `stamp` from before the copy.

    A source that changed while it was being read is refused (`local` removed).
    """
    before = stamp(src)
    _copy(src, local, progress, cancelled, "下載原檔後大小不符（{got} ≠ {total}）")
    if stamp(src) != before:
        local.unlink(missing_ok=True)
        raise RuntimeError("原檔在下載期間被修改過")
    return before


def bring_back(local: Path, dst: Path,
               progress: Optional[Callable[[float], None]] = None,
               cancelled: Optional[Callable[[], bool]] = None) -> None:
    """Copy `local` to `dst` (which must not exist), check it, remove `local`.

    Raises OSError / RuntimeError with `dst` removed and `local` kept — the
    caller decides what to do with it — or `Stopped` with `dst` removed.
    """
    _copy(local, dst, progress, cancelled, "複製到原資料夾後大小不符（{got} ≠ {total}）")
    local.unlink(missing_ok=True)


class Prefetcher:
    """Downloads the network sources of the coming items, in order, one at a
    time and within the NAS's turn, keeping at most `AHEAD` of them waiting.

    `take(key)` hands the encoder its file — `(local, stamp)`, or `(None,
    None)` to read the original in place (not planned, no room, the download
    failed). The encoder calls `done_with(local)` when it no longer needs it;
    `close()` stops the downloads and removes whatever was never taken.

    `on_start` runs first in the download thread (the job binds itself there,
    so `writing`/`wrote` reach its registry).
    """

    def __init__(self, plan, folder: str, cancelled: Callable[[], bool],
                 report: Callable[[Optional[dict]], None],
                 writing: Callable, wrote: Callable,
                 on_start: Optional[Callable[[], None]] = None):
        self._plan = list(plan)              # [(key, src)]
        self._folder = folder
        self._cancelled = cancelled
        self._report = report
        self._writing, self._wrote = writing, wrote
        self._on_start = on_start
        self._cv = threading.Condition()
        self._slots = {k: None for k, _s in self._plan}     # key → (local, stamp)
        self._waiting = 0
        self._closed = False
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        if self._plan:
            self._thread.start()
        return self

    def _stop(self) -> bool:
        return self._closed or self._cancelled()

    def _run(self):
        try:
            if self._on_start:
                self._on_start()
            self._loop()
        except Exception:
            log.exception("prefetch thread failed; the rest is read in place")
        finally:
            with self._cv:
                for k, v in self._slots.items():
                    if v is None:
                        self._slots[k] = (None, None)      # never coming
                self._cv.notify_all()

    def _loop(self):
        for key, src in self._plan:
            with self._cv:
                while self._waiting >= AHEAD and not self._stop():
                    self._cv.wait(0.5)
            if self._stop():
                break
            got = (None, None)
            local = fetch_target(src, self._folder)
            if local:
                self._writing(src, local)
                self._report({"stage": "下載原檔", "name": src.name, "pct": 0.0})
                try:
                    with TURNS.turn():
                        if self._stop():
                            raise Stopped()
                        st = fetch(src, local, cancelled=self._stop,
                                   progress=lambda f, n=src.name: self._report(
                                       {"stage": "下載原檔", "name": n, "pct": f}))
                    got = (local, st)
                except Stopped:
                    local.unlink(missing_ok=True)
                    self._wrote(local)
                    self._report(None)
                    break
                except Exception as e:
                    local.unlink(missing_ok=True)
                    self._wrote(local)
                    log.warning("下載原檔失敗，改為直接讀原檔：%s（%s）", e, src)
                self._report(None)
            with self._cv:
                self._slots[key] = got
                if got[0]:
                    self._waiting += 1
                self._cv.notify_all()

    def take(self, key):
        """The downloaded file for `key`, waiting for it if it is on its way."""
        if key not in self._slots:
            return None, None
        with self._cv:
            while self._slots[key] is None and not self._cancelled():
                self._cv.wait(0.5)
            got = self._slots[key] or (None, None)
            self._slots[key] = (None, None)              # taken
            if got[0]:
                self._waiting -= 1
                self._cv.notify_all()
            return got

    def done_with(self, local: Optional[Path]):
        if local:
            local.unlink(missing_ok=True)
            self._wrote(local)

    def close(self):
        with self._cv:
            self._closed = True
            self._cv.notify_all()
        if self._thread.is_alive():
            self._thread.join()
        for k, v in self._slots.items():
            if v and v[0]:
                self.done_with(v[0])
                self._slots[k] = (None, None)
