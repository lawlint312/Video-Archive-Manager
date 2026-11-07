"""What each finished shrink actually saved, so the estimate can learn.

The candidate list's "estimated saving" comes from a curve measured on eight
files (see `frontend/src/shrinkestimate.js`). The first real 4K film it met
saved 3.23 GB where it said 7.12 GB, and content — motion, grain — is not
something a bitrate can see. Each row here is one real result; the panel
fetches them and turns them into a correction factor.

Kept apart from `pairs`: a pending pair disappears the moment the user deletes
one side, which is exactly when the measurement is still worth having. And no
paths are stored — only the numbers the estimate needs — so nothing here has
to follow a file when it moves, and nothing here can leak where a file is.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

log = logging.getLogger("vam.shrinkstats")

KEEP = 500                  # newest results kept; plenty for a correction
_FIELDS = ("mbps", "width", "height", "duration", "crf", "codec", "max_edge",
           "encoder", "before", "after")
_lock = threading.Lock()


def rows(store: Path) -> list:
    try:
        data = json.loads(store.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _write(store: Path, data: list):
    try:
        store.parent.mkdir(parents=True, exist_ok=True)
        tmp = store.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(store)
    except Exception:
        log.exception("could not save the shrink results")


def _row(info: dict, crf: int, max_edge: int, codec: str, encoder: str,
         after: int, at: Optional[float] = None) -> dict:
    return {"mbps": info.get("mbps", 0), "width": info.get("width", 0),
            "height": info.get("height", 0),
            "duration": round(info.get("duration", 0), 1),
            "crf": crf, "codec": codec, "max_edge": max_edge,
            "encoder": encoder, "before": info.get("size", 0), "after": after,
            "at": at or time.time()}


def record(store: Path, info: dict, crf: int, max_edge: int, codec: str,
           encoder: str, after: int):
    """One finished, verified encode: `info` is the source's `shrink.probe`."""
    if not (info.get("size") and after):
        return
    with _lock:
        data = rows(store)
        data.append(_row(info, crf, max_edge, codec, encoder, after))
        _write(store, data[-KEEP:])


def backfill(store: Path, pending: Iterable[dict],
             probe: Callable[[Path], dict]) -> int:
    """Seed the store, once, from shrinks done before it existed.

    Those are still on the pending-pairs list as long as both files exist —
    the original's probe gives the bitrate, the produced file gives the size.
    Runs only when the store has never been written, so a result the user
    has since dealt with is not counted twice. Returns how many were added.
    """
    if store.exists():
        return 0
    found = []
    for p in pending:
        if p.get("kind") != "shrink":
            continue
        src, dst = Path(p.get("original", "")), Path(p.get("produced", ""))
        try:
            after = dst.stat().st_size
        except OSError:
            continue
        info = probe(src)
        if not info:
            continue
        found.append(_row(info, p.get("crf", 26), p.get("max_edge", 0),
                          p.get("codec", "h264"), p.get("encoder", ""),
                          after, p.get("at")))
    with _lock:
        if store.exists():          # someone finished an encode meanwhile
            return 0
        found.sort(key=lambda r: r["at"])
        _write(store, found[-KEEP:])
    return len(found)


def public(data: list) -> list:
    """Only the fields the estimate uses."""
    return [{k: r.get(k) for k in _FIELDS} for r in data]
