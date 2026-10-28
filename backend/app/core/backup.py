"""Scheduled copies of the databases that cannot be rebuilt.

Why this exists: `data/vam.db` holds every account, every 看過／最愛 mark, the
sidebar groups, the app settings and the per-folder 相似 thresholds. None of it
can be recovered from the media files — unlike the caches, which regenerate on
their own. The face and person databases can be rebuilt, but only by re-running
scans that take hours, so they are copied too.

Two rules shape the implementation:

* **Copy with SQLite's own backup API, not `shutil.copy`.** The server is
  running while this happens; a plain file copy can catch a database mid-write
  and produce a file that looks fine until the day you need it.
* **The destination must be a disk the service does not otherwise touch.** A
  backup on the same physical drive dies with that drive. Nothing here can
  enforce that, so `problem_with()` says so and the settings UI repeats it.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from ..config import DATA_DIR

log = logging.getLogger("vam.backup")

# (filename, required). vam.db is the one that cannot be rebuilt at all; the
# other two only cost hours of scanning, and may not exist on a fresh install.
SOURCES: Tuple[Tuple[str, bool], ...] = (
    ("vam.db", True),
    ("faces.db", False),
    ("person.sqlite3", False),
)

KEEP_DEFAULT = 14
KEEP_MIN, KEEP_MAX = 2, 90
INTERVAL_HOURS = 24

# vam-2026-09-16_0312/  — one folder per run, so a restore is "take the newest
# folder", not "work out which three files belong together".
_STAMP = "%Y-%m-%d_%H%M"
_DIR_RE = re.compile(r"^vam-\d{4}-\d{2}-\d{2}_\d{4}$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def problem_with(dest: str) -> Optional[str]:
    """None if this destination is usable, else a message for the settings UI."""
    if not (dest or "").strip():
        return "請先指定備份資料夾"
    p = Path(dest)
    if not p.is_absolute():
        return "請使用完整路徑，例如 D:\\VAM備份"
    try:
        p.mkdir(parents=True, exist_ok=True)
        probe = p / ".vam_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as e:
        return f"無法寫入這個資料夾：{e.strerror or e}"
    return None


def same_drive_as_data(dest: str) -> bool:
    """True when the backup would share a drive letter with the live data.

    Not an error — an external disk that is sometimes unplugged is worse than a
    second copy on the same machine — but the UI has to say it out loud.
    """
    try:
        a = os.path.splitdrive(str(Path(dest).resolve()))[0].upper()
        b = os.path.splitdrive(str(DATA_DIR.resolve()))[0].upper()
        return bool(a) and a == b
    except OSError:
        return False


def _copy_db(src: Path, dst: Path):
    """Consistent copy of a live SQLite file (plus a fallback for odd cases)."""
    try:
        source = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30)
        try:
            target = sqlite3.connect(str(dst))
            try:
                source.backup(target)
            finally:
                target.close()
        finally:
            source.close()
    except sqlite3.Error as e:
        # A file that is not SQLite at all, or is locked by something that does
        # not speak SQLite. A plain copy is worse but better than nothing, and
        # the log says which one this is.
        log.warning("backup: sqlite copy of %s failed (%s), falling back", src.name, e)
        shutil.copy2(src, dst)


def prune(dest: Path, keep: int) -> int:
    """Delete all but the newest `keep` run folders. Returns how many went."""
    runs = sorted((d for d in dest.iterdir() if d.is_dir() and _DIR_RE.match(d.name)),
                  key=lambda d: d.name)
    gone = 0
    for d in runs[:max(0, len(runs) - keep)]:
        try:
            shutil.rmtree(d)
            gone += 1
        except OSError as e:
            log.warning("backup: cannot remove old backup %s: %s", d, e)
    return gone


def run(dest: str, keep: int = KEEP_DEFAULT) -> dict:
    """Copy the databases into a new timestamped folder under `dest`.

    Raises RuntimeError with a message meant for the user; the caller records
    it so the settings page can show what went wrong and when.
    """
    problem = problem_with(dest)
    if problem:
        raise RuntimeError(problem)
    keep = max(KEEP_MIN, min(KEEP_MAX, int(keep)))
    root = Path(dest)
    folder = root / f"vam-{datetime.now().strftime(_STAMP)}"
    tmp = Path(str(folder) + ".part")
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)

    started = time.time()
    files: List[dict] = []
    try:
        for name, required in SOURCES:
            src = DATA_DIR / name
            if not src.exists():
                if required:
                    raise RuntimeError(f"找不到 {name}，備份中止")
                continue
            _copy_db(src, tmp / name)
            files.append({"name": name, "bytes": (tmp / name).stat().st_size})
        if not files:
            raise RuntimeError("沒有任何資料庫可以備份")
        # Rename last: a folder with the final name always holds a finished
        # backup, so "restore the newest folder" can never pick up a half-copy.
        os.replace(tmp, folder)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise

    removed = prune(root, keep)
    total = sum(f["bytes"] for f in files)
    log.info("backup: %s (%d files, %.1f MB, %.1fs, pruned %d)",
             folder, len(files), total / 1e6, time.time() - started, removed)
    return {"at": _now().isoformat(), "folder": str(folder), "files": files,
            "bytes": total, "seconds": round(time.time() - started, 1),
            "pruned": removed}


def due(last_at: Optional[str], hours: int = INTERVAL_HOURS) -> bool:
    """True when the last successful run is older than the interval (or never)."""
    if not last_at:
        return True
    try:
        last = datetime.fromisoformat(last_at)
    except ValueError:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (_now() - last).total_seconds() >= hours * 3600


def existing(dest: str) -> List[dict]:
    """The backups already sitting in `dest`, newest first."""
    out: List[dict] = []
    try:
        root = Path(dest)
        for d in sorted((x for x in root.iterdir()
                         if x.is_dir() and _DIR_RE.match(x.name)),
                        key=lambda x: x.name, reverse=True):
            size = 0
            for f in d.iterdir():
                if f.is_file():
                    size += f.stat().st_size
            out.append({"name": d.name, "bytes": size})
    except OSError:
        return out
    return out
