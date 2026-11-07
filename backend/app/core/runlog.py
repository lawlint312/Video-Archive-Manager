"""A short, capped history of a job's runs, kept as a JSON list.

For jobs nobody watches — a scheduled face index finishes at 04:00 — the
dialog shows what the last runs did. Also where the per-video time that the
estimate uses comes from.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

log = logging.getLogger("vam.runlog")
_lock = threading.Lock()


def read(store: Path) -> list:
    try:
        data = json.loads(store.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def append(store: Path, row: dict, keep: int = 30):
    with _lock:
        data = read(store)
        data.append(row)
        try:
            store.parent.mkdir(parents=True, exist_ok=True)
            tmp = store.with_suffix(".tmp")
            tmp.write_text(json.dumps(data[-keep:], ensure_ascii=False),
                           encoding="utf-8")
            tmp.replace(store)
        except Exception:
            log.exception("could not save the run history")
