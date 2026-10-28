"""How many files each root held last time, so phase 1 can show a bar too.

Walking the folders is the one phase whose size cannot be known before it
finishes — and on the NAS it is not the fast one. The previous run's count per
root is a good expectation, and it costs nothing to keep.

Stored **per root**, not as one library-wide total, because the scan dialog
lets you pick a subset: a total from "everything" would be nonsense when you
scan one folder. The expectation is only offered when every selected root has
a recorded count, so the number on screen is never a partial sum pretending to
be a whole one.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger(__name__)


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}          # missing or corrupt is normal: it is only a hint


def expected(path: Optional[Path], kind: str,
             roots: List[Path]) -> Optional[int]:
    """Total expected across `roots`, or None if any of them is unknown."""
    if not path or not roots:
        return None
    known = _load(path).get(kind) or {}
    total = 0
    for r in roots:
        n = known.get(str(r))
        if not isinstance(n, int):
            return None
        total += n
    return total


def save(path: Optional[Path], kind: str, counts: Dict[str, int]):
    if not path:
        return
    try:
        data = _load(path)
        data.setdefault(kind, {}).update({k: int(v) for k, v in counts.items()})
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        log.exception("could not record scan counts")   # never fail a scan
