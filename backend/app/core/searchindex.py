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
from typing import Callable, List, Optional

from ..config import DATA_DIR
from . import javdb, media, filetitle, videoflags, costar

log = logging.getLogger("vam")

TTL = 600.0
# How often a request may look at the inbox folders' mtimes (`_inbox_moved`).
INBOX_CHECK = 15.0
_FILE = DATA_DIR / "search_index.json"

_lock = threading.Lock()
_cache: Optional[dict] = None
# Told about the videos whose `.nfo` is new, changed or moved since the last
# walk: `on_nfo([(video, character), ...], roots)`, on a thread of its own.
# main.py reads them in (only filling what VAM does not know yet). Before
# this an NFO was only read when someone ran the import in 角色查詢, and a
# video downloaded with its NFO showed no title (2026-11-16).
on_nfo: Optional[Callable[[list, List[Path]], None]] = None
_refreshing = False
_checked_at = 0.0


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

    nfo_files: dict = {}
    with_nfo: dict = {}     # {video path: [its .nfo's mtime, character]}

    def add(path: Path, char: str, inbox_flag: int):
        m = nfo_files.get(str(path.with_suffix(".nfo")).lower())
        if m is not None:
            with_nfo[str(path)] = [m, char]
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
            for path, _size, _mtime in media.scan_videos_with_stat(
                    Path(p), nfos=nfo_files):
                add(path, c["name"], 0)
    box_dirs: dict = {}
    for p in inbox:
        for path, _size, _mtime in media.scan_videos_with_stat(
                p, dirs=box_dirs, nfos=nfo_files):
            add(path, "", 1)

    return {"dirs": list(dirs), "v": rows, "at": time.time(),
            "inbox_dirs": box_dirs, "nfo": with_nfo}


def nfo_changes(old: Optional[dict], new: dict) -> list:
    """[(video, character)] whose NFO the last walk had not seen as it is now:
    a new file, one rewritten since, or a video that moved (its path is new).
    An index from before NFOs were noted counts as having seen none, so the
    first walk after the upgrade reads every NFO in once."""
    before = (old or {}).get("nfo")
    out = []
    for p, (m, char) in new.get("nfo", {}).items():
        if before is None or (before.get(p) or [None])[0] != m:
            out.append((Path(p), char))
    return out


def _announce(old: Optional[dict], new: dict, roots: List[Path]):
    hook = on_nfo
    found = nfo_changes(old, new) if hook else []
    if not found:
        return

    def run():
        try:
            hook(found, roots)
        except Exception:
            log.exception("reading new NFO files failed")
    threading.Thread(target=run, daemon=True).start()


def _inbox_moved(cached: dict, inbox: List[Path]) -> bool:
    """Has anything arrived in, left or been renamed in an inbox folder since
    the index was built?

    The TTL alone missed downloads: a video that finished a minute after the
    index was built stayed unsearchable for up to ten minutes, although the
    inbox grid (which lists the folder live) already showed it. Archiving and
    deleting go through `invalidate()`, but nothing tells the server about a
    browser or a torrent client writing into Downloads. A folder's mtime moves
    whenever a file in it is created, renamed or deleted, so a stat per inbox
    folder answers "would a walk find something new" without the walk — a few
    hundred local stats, well under a millisecond each. A new subfolder moves
    its parent's mtime, so folders the last walk never saw are covered too.

    Only the inbox: archive folders change through VAM itself, and there are
    over a thousand of them, many on the NAS.
    """
    seen = cached.get("inbox_dirs")
    if seen is None:
        return True                 # built before this existed
    if any(str(p) not in seen for p in inbox):
        return True                 # an inbox folder was added in settings
    for d, mtime in seen.items():
        try:
            now = os.stat(d).st_mtime
        except OSError:
            now = None              # gone, or a drive that is not mounted
        if now != mtime:
            return True
    return False


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
            old, _cache = _cache, data
        _write_disk(data)
        _announce(old, data, roots)
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
    global _cache, _refreshing, _checked_at

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
        _announce(None, data, roots)
        return {**_public(data), "stale": False}

    # an index from before NFOs were noted is rebuilt at once, not at the
    # TTL: until then nothing is read in by itself
    stale = (time.time() - cached.get("at", 0)) > TTL or "nfo" not in cached
    if not stale and time.time() - _checked_at > INBOX_CHECK:
        _checked_at = time.time()
        if _inbox_moved(cached, inbox):
            stale = True
            with _lock:
                cached["at"] = 0.0      # stays stale until the rebuild lands
    if stale:
        with _lock:
            if not _refreshing:
                _refreshing = True
                threading.Thread(target=_refresh,
                                 args=(list(roots), list(inbox)),
                                 daemon=True).start()
    return {**_public(cached), "stale": stale}


def _public(data: dict) -> dict:
    """The index without the server's own bookkeeping: the inbox folder list
    is hundreds of paths the browser has no use for — nor the NFO list."""
    return {k: v for k, v in data.items() if k not in ("inbox_dirs", "nfo")}


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
