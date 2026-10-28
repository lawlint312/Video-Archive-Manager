"""Cached index of every video filename, for the sidebar search.

Measured on the real library: 16,204 videos, 4.4 s to walk (warm — a cold
walk over the NAS and seven drives is slower), 1.02 MB of JSON, 0.20 MB after
gzip. A substring scan over all of it takes about a millisecond.

Those numbers are why the whole index goes to the browser instead of a
`?q=` endpoint: 200 KB once beats a round trip per keystroke, and searching
locally needs no debounce. Same caching shape as charstats — memory, mirrored
to disk so a restart is not a cold walk, refreshed in the background when
stale.

Payload is deduplicated by directory: 16,204 videos sit in 1,288 folders, and
storing an index into a folder list instead of a full path per row saves a
quarter of the compressed size. `dirs[d] + "/" + name` reconstructs the path
(verified against the real library — 0 mismatches).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import List, Optional

from ..config import DATA_DIR
from . import javdb, media, filetitle, videoflags, costar

log = logging.getLogger("vam")

TTL = 600.0
_FILE = DATA_DIR / "search_index.json"

_lock = threading.Lock()
_cache: Optional[dict] = None
_refreshing = False


def _compute(roots: List[Path], inbox: List[Path]) -> dict:
    dirs: dict = {}
    rows = []

    known = javdb.titles()
    # Hand-typed titles for everything without a catalogue number. Searching a
    # title has to find those too, or the feature only works for 5% of the
    # library and looks broken everywhere else.
    typed = filetitle.all_titles()
    # Co-stars marked by name only (no character folder): searching 横田夏美
    # should find RCTD-208 although nobody's folder is called that.
    try:
        named = costar.all_names()
    except Exception:
        # a missing table must not cost the whole search; names are extra
        log.exception("search index: co-star names unavailable")
        named = {}

    def add(path: Path, char: str, inbox_flag: int):
        d = str(path.parent)
        idx = dirs.get(d)
        if idx is None:
            idx = dirs[d] = len(dirs)
        # the film title rides along so searching a title finds the file —
        # most JAV filenames are a bare catalogue number and say nothing
        vid = media.detect_video_id(path.name)
        pk = videoflags.key(path)
        rows.append([path.name, char, idx, inbox_flag,
                     known.get(vid, "") if vid else typed.get(pk, ""),
                     " ".join(named.get(f"id:{vid.upper()}" if vid else f"path:{pk}", []))])

    for c in media.list_characters(roots):
        for p in c["paths"]:
            for path, _size, _mtime in media.scan_videos_with_stat(Path(p)):
                add(path, c["name"], 0)
    for p in inbox:
        for path, _size, _mtime in media.scan_videos_with_stat(p):
            add(path, "", 1)

    return {"dirs": list(dirs), "v": rows, "at": time.time()}


def _read_disk() -> Optional[dict]:
    try:
        d = json.loads(_FILE.read_text(encoding="utf-8"))
        if isinstance(d, dict) and "dirs" in d and "v" in d:
            return d
    except Exception:
        pass
    return None


def _write_disk(data: dict):
    try:
        _FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        log.exception("could not write the search index")


def _refresh(roots: List[Path], inbox: List[Path]):
    global _cache, _refreshing
    try:
        data = _compute(roots, inbox)
        with _lock:
            _cache = data
        _write_disk(data)
    except Exception:
        log.exception("search-index refresh failed")
    finally:
        with _lock:
            _refreshing = False


def _under(path: str, prefixes: List[str]) -> bool:
    p = os.path.normcase(path)
    return any(p.startswith(r) for r in prefixes)


def _prefixes(roots: List[Path]) -> List[str]:
    out = []
    for r in roots:
        s = os.path.normcase(str(r))
        out.append(s if s.endswith(os.sep) else s + os.sep)
    return out


def _narrow(data: dict, roots: List[Path], inbox: List[Path]) -> dict:
    """The shared index cut down to one user's roots.

    Every row of the shared index was found by walking one of the configured
    folders, so keeping the rows under this user's folders gives what walking
    only those would have — without the walk. A row keeps its place by the
    flag it was filed under: archive rows need an archive root, inbox rows an
    inbox folder, exactly as `_compute` would have tagged them.
    """
    arch, box = _prefixes(roots), _prefixes(inbox)
    remap: dict = {}
    dirs: list = []
    rows = []
    for row in data["v"]:
        d = data["dirs"][row[2]]
        if not _under(d + os.sep, box if row[3] else arch):
            continue
        idx = remap.get(row[2])
        if idx is None:
            idx = remap[row[2]] = len(dirs)
            dirs.append(d)
        rows.append([row[0], row[1], idx, *row[3:]])
    return {"dirs": dirs, "v": rows, "at": data.get("at", 0.0)}


def get(roots: List[Path], inbox: List[Path], scoped: bool = False) -> dict:
    """Never blocks once anything is cached; `stale` asks the caller to retry.

    `scoped=True` means these are one user's roots. The answer is the shared
    index filtered to them — never a cache of its own, and never written back,
    so a restricted account can neither poison the shared index with a partial
    one nor read someone else's. It used to walk that user's folders on every
    call (4 s over the NAS, uncached), and the sidebar asks on every remount.
    """
    global _cache, _refreshing

    if scoped:
        from .. import settings
        full = get(settings.archive_roots(), settings.inbox_paths())
        return {**_narrow(full, roots, inbox), "stale": full["stale"]}

    with _lock:
        if _cache is None:
            _cache = _read_disk()
        cached = _cache

    if cached is None:
        data = _compute(roots, inbox)
        with _lock:
            _cache = data
        _write_disk(data)
        return {**data, "stale": False}

    stale = (time.time() - cached.get("at", 0)) > TTL
    if stale:
        with _lock:
            if not _refreshing:
                _refreshing = True
                threading.Thread(target=_refresh,
                                 args=(list(roots), list(inbox)),
                                 daemon=True).start()
    return {**cached, "stale": stale}


def invalidate():
    """Mark the index old — in memory *and* on disk.

    Staling only the memory copy leaves a hole: after a restart the disk file
    is read back with its original timestamp, so a still-fresh-looking cache
    can serve rows written before the change. That bit when film titles were
    added — the fetch invalidated the index but a reload served the old rows,
    which had no title field at all.
    """
    with _lock:
        if _cache is not None:
            _cache["at"] = 0.0
    try:
        if _FILE.exists():
            data = json.loads(_FILE.read_text(encoding="utf-8"))
            data["at"] = 0.0
            _FILE.write_text(json.dumps(data, ensure_ascii=False),
                             encoding="utf-8")
    except Exception:
        log.exception("could not stale the on-disk search index")
