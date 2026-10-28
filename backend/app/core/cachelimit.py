"""Keep the derived-file cache under a size limit, oldest-used first.

Why a limit rather than tidying up orphans: the cache filenames are md5 of the
source path, so nothing here can tell which entries belong to files that no
longer exist. Deleting a video *through the app* cleans up after itself, but
deleting it in Explorer or on the NAS leaves entries that are unidentifiable
from this side. A cap needs no such knowledge — worst case it drops something
still wanted, and the next view regenerates it.

Recency comes from mtime, which the readers refresh with `touch()` on a cache
hit. Windows disables NTFS last-access updates by default, so atime cannot be
trusted for this.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

log = logging.getLogger("vam")

# Two independent budgets, because the two caches behave nothing alike and
# users reason about them separately. Thumbnails are tiny (~25 KB) but there is
# one per video and every page view needs them; contact sheets are ~220 KB each
# and only exist for videos that have been inspected. Sharing one budget meant
# a limit set with contact sheets in mind silently starved the thumbnails —
# measured: a 0.2 GB cap against 404 MB of thumbnails swept hundreds of files
# every five minutes, so nothing was ever cached.
#
# `notes/` is deliberately in neither: those are the user's own memos for inbox
# videos, not something we can regenerate.
# `photos/` joined them for the same reason rather than sharing `thumbs/`:
# there are 54,047 photos against 16,204 videos, and at 560px they need up to
# 2.9 GB against the video thumbnails' 400 MB. Under one budget the photos
# would evict the video thumbnails on every browse — the measured failure mode
# above, made structural.
# `shrink_samples/` is here because it was growing with nothing to stop it —
# 136 MB from three previews. Each one keeps a 10-second *stream copy* of the
# source, and the sources are picked for being over-encoded: median 32.8 MB,
# largest 62.1 MB, against 5.6 MB for the re-encoded half. So one click on 試看
# costs ~45 MB. Unlike the others these answer a single question and are dead
# once it is answered, hence the much smaller budget — it only has to cover
# trying a few settings in one sitting.
# `gfriends/` holds the headshots shown as avatar choices in 角色查詢 → 個資
# (core/gfriends.py), ~100 KB each, a dozen per character looked at. Only the
# chosen one matters afterwards and that one is copied into the folder, so
# this is scratch with a small fixed budget, like the samples.
KINDS = ("thumbs", "sheets", "photos", "shrink_samples", "gfriends")
DEFAULT_LIMIT_GB = {"thumbs": 2.0, "sheets": 2.0, "photos": 3.0,
                    "shrink_samples": 0.5, "gfriends": 0.3}

_MIN_GAP = 300.0            # seconds between sweeps; walking the tree is not free

_lock = threading.Lock()
_last_run: dict = {}
_running: set = set()


def touch(p: Path):
    """Mark a cache entry as just used, so eviction sees it as recent."""
    try:
        now = time.time()
        os.utime(p, (now, now))
    except OSError:
        pass


def usage(cache: Path) -> int:
    total = 0
    for dirpath, _dirs, names in os.walk(cache, onerror=lambda _e: None):
        for n in names:
            try:
                total += os.path.getsize(os.path.join(dirpath, n))
            except OSError:
                continue
    return total


def enforce(cache: Path, limit_bytes: int) -> dict:
    """Delete least-recently-used entries until the cache fits. Blocking."""
    if limit_bytes <= 0 or not cache.exists():
        return {"before": 0, "after": 0, "removed": 0}

    entries = []
    total = 0
    for dirpath, _dirs, names in os.walk(cache, onerror=lambda _e: None):
        for n in names:
            p = os.path.join(dirpath, n)
            try:
                st = os.stat(p)
            except OSError:
                continue
            entries.append((st.st_mtime, st.st_size, p))
            total += st.st_size

    before = total
    removed = 0
    if total > limit_bytes:
        entries.sort()                      # oldest first
        for mt, size, p in entries:
            if total <= limit_bytes:
                break
            try:
                # The list above is a snapshot, and deleting 300 files takes
                # long enough that a request can `touch()` one of them in the
                # meantime — it would then be deleted *while being served*,
                # and the browser gets a broken image. Re-check the mtime and
                # leave anything that has been used since the snapshot.
                if os.stat(p).st_mtime > mt:
                    continue
                os.remove(p)
            except OSError:
                continue
            total -= size
            removed += 1
        _prune_empty(cache)
        log.info("cache sweep: %.0f MB → %.0f MB, removed %d files",
                 before / 1048576, total / 1048576, removed)
    return {"before": before, "after": total, "removed": removed}


def _prune_empty(cache: Path):
    for dirpath, dirnames, names in os.walk(cache, topdown=False,
                                            onerror=lambda _e: None):
        if Path(dirpath) == cache:
            continue
        if not names and not dirnames:
            try:
                os.rmdir(dirpath)
            except OSError:
                pass


def kind_dir(cache: Path, kind: str) -> Path:
    return cache / kind


def maybe_enforce(cache: Path, kind: str, limit_bytes: int):
    """Sweep one kind in the background, at most once every few minutes.

    Called after work that adds to that cache. Throttled because a sweep walks
    every entry, and generating one contact sheet can add a dozen files. The
    throttle is per kind — browsing a folder must not postpone the contact
    sheet sweep, or vice versa.
    """
    with _lock:
        if kind in _running or time.time() - _last_run.get(kind, 0.0) < _MIN_GAP:
            return
        _running.add(kind)
        _last_run[kind] = time.time()

    def _run():
        try:
            enforce(kind_dir(cache, kind), limit_bytes)
        except Exception:
            log.exception("cache sweep failed for %s", kind)
        finally:
            with _lock:
                _running.discard(kind)

    threading.Thread(target=_run, daemon=True).start()
