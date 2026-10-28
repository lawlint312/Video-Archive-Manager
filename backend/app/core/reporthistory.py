"""History of duplicate-scan reports: one file per scan, newest kept.

The scan used to keep a single report that each new scan overwrote, so a
report clicked away from ("重新掃描", then the dialog closed) was only one
scan away from being gone — and the only way back to it at all was closing
and reopening the dialog. Reports are small (13 KB / 9 KB for the two on disk
when this was written), so keeping the last `KEEP` per kind costs nothing.

Ids are the scan time, `YYYYmmdd-HHMMSS`, with `-2`, `-3`… on a collision.
They arrive in URLs, so anything not matching `_ID` is refused before a path
is built from it.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import List, Optional

KEEP = 20
_ID = re.compile(r"^\d{8}-\d{6}(-\d+)?$")


def valid_id(rid: str) -> bool:
    return bool(_ID.match(rid or ""))


def _stamp(at: float) -> str:
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(at))


def save(folder: Path, data: dict) -> str:
    """Write `data` as a new entry and prune to `KEEP`. Returns its id.

    Written to `.part` and renamed, like the contact sheets: a file under its
    final name is always complete, so the list never offers a half-written
    report."""
    folder.mkdir(parents=True, exist_ok=True)
    base = _stamp(data.get("at") or time.time())
    rid, n = base, 1
    while (folder / f"{rid}.json").exists():
        n += 1
        rid = f"{base}-{n}"
    tmp = folder / f"{rid}.json.part"
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, folder / f"{rid}.json")
    prune(folder)
    return rid


def ids(folder: Path) -> List[str]:
    """Newest first."""
    if not folder.is_dir():
        return []
    out = [p.stem for p in folder.glob("*.json") if valid_id(p.stem)]
    return sorted(out, key=_sort_key, reverse=True)


def _sort_key(rid: str):
    parts = rid.split("-")
    return (parts[0], parts[1], int(parts[2]) if len(parts) > 2 else 1)


def prune(folder: Path, keep: int = KEEP) -> None:
    for rid in ids(folder)[keep:]:
        try:
            (folder / f"{rid}.json").unlink()
        except OSError:
            pass


def path_of(folder: Path, rid: str) -> Optional[Path]:
    if not valid_id(rid):
        return None
    p = folder / f"{rid}.json"
    return p if p.is_file() else None


def delete(folder: Path, rid: str) -> bool:
    p = path_of(folder, rid)
    if not p:
        return False
    p.unlink()
    return True


# ── what was left, as of the last time a report was opened ──────────────────
# The list used to count "還剩 N 組" afresh every time it was shown, which meant
# checking every file of all twenty reports on the NAS just to draw the list —
# 51 s once (2026-10-26). The user's call: show the list as it is, check the
# files of the one report you open. What that check found is kept here, per
# account (the count is of what that account may see), so the list can still
# say "上次開啟時還剩 N 組" without touching a file. Not an id, so `ids()`
# never lists it as a report.
_SEEN = "remaining.json"


def remember(folder: Path, rid: str, uid, remaining: int) -> None:
    if not valid_id(rid):
        return
    p = folder / _SEEN
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    data.setdefault(rid, {})[str(uid)] = {"n": int(remaining), "at": time.time()}
    keep = set(ids(folder))
    data = {k: v for k, v in data.items() if k in keep}     # pruned reports go too
    try:
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / (_SEEN + ".part")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass                     # a hint for the list, not worth failing over


def remembered(folder: Path, uid) -> dict:
    """{report id: {"n", "at"}} for this account."""
    try:
        data = json.loads((folder / _SEEN).read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {rid: v[str(uid)] for rid, v in data.items() if str(uid) in v}


def migrate(legacy: Path, folder: Path) -> Optional[str]:
    """Seed an empty history with the single report kept before history
    existed, so the first upgrade loses nothing. Idempotent: does nothing
    once the folder has any entry."""
    if ids(folder) or not legacy.is_file():
        return None
    try:
        data = json.loads(legacy.read_text(encoding="utf-8"))
    except Exception:
        return None
    return save(folder, data)
