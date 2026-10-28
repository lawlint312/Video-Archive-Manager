"""Cached per-character video counts, sizes and newest-file times.

Walking every character folder takes 2–3 seconds on a NAS, and both the sidebar
and the home grid want exactly the same numbers. The result is therefore cached
in memory, mirrored to disk so a restart does not cost a cold walk, and
refreshed in a background thread once it goes stale.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import List, Optional

from ..config import DATA_DIR
from . import media

log = logging.getLogger("vam")

TTL = 600.0                        # seconds before a cached result is refreshed
_FILE = DATA_DIR / "counts_cache.json"

_lock = threading.Lock()
_cache: Optional[dict] = None      # {"counts": …, "stats": …, "at": epoch}
_refreshing = False


def _compute(roots: List[Path]) -> dict:
    counts, stats = {}, {}
    for c in media.list_characters(roots):
        agg = {"count": 0, "bytes": 0, "newest": 0.0}
        for p in c["paths"]:
            s = media.character_stats(Path(p))
            agg["count"] += s["count"]
            agg["bytes"] += s["bytes"]
            agg["newest"] = max(agg["newest"], s["newest"])
        counts[c["name"]] = agg["count"]
        stats[c["name"]] = agg
    return {"counts": counts, "stats": stats, "at": time.time()}


def _read_disk() -> Optional[dict]:
    try:
        d = json.loads(_FILE.read_text(encoding="utf-8"))
        if isinstance(d, dict) and "counts" in d and "stats" in d:
            return d
    except Exception:
        pass
    return None


def _write_disk(data: dict):
    try:
        _FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        log.exception("could not write the character-stats cache")


def _refresh(roots: List[Path]):
    global _cache, _refreshing
    try:
        data = _compute(roots)
        with _lock:
            _cache = data
        _write_disk(data)
    except Exception:
        log.exception("character-stats refresh failed")
    finally:
        with _lock:
            _refreshing = False


def get(roots: List[Path], scoped: bool = False) -> dict:
    """Never blocks once anything is cached — a stale result is returned right
    away and replaced by a background walk. `stale` tells the caller to ask
    again shortly.

    `scoped=True` means these roots are one user's subset, so the answer must
    not be written to (or read from) the cache shared by everybody else.
    """
    global _cache, _refreshing

    if scoped:
        return {**_compute(roots), "stale": False}

    with _lock:
        if _cache is None:
            _cache = _read_disk()
        cached = _cache

    if cached is None:                      # nothing at all yet: must compute
        data = _compute(roots)
        with _lock:
            _cache = data
        _write_disk(data)
        return {**data, "stale": False}

    stale = (time.time() - cached.get("at", 0)) > TTL
    if stale:
        with _lock:
            if not _refreshing:
                _refreshing = True
                threading.Thread(target=_refresh, args=(list(roots),),
                                 daemon=True).start()
    return {**cached, "stale": stale}


def invalidate():
    """Mark the cache old after something changed the files on disk.

    The next read still answers instantly from the old numbers and kicks off
    the recount in the background.
    """
    with _lock:
        if _cache is not None:
            _cache["at"] = 0.0
