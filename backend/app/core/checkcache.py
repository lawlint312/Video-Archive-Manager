"""Remembered answers of the convert scan's checks (`data/convert_checks.sqlite3`).

The full decode check has always remembered its answer (`decodecheck.Cache`);
the other checks ran again on every scan, so scanning the same character a
second time paid for all of it again. Each check is remembered on its own —
a file may have been through some of them and not others — keyed on
(kind, path) and valid only while the file's size and mtime are unchanged:
a file downloaded again over the damaged one is checked again.

`version` is per kind and comes from the caller: when a check's rules
change, bumping it turns every old answer of that kind into a miss.

Losing the file only means the next scan checks again, so it is not backed up.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

MISS = object()          # no usable answer (None is an answer: "nothing found")

# Commit after this many writes rather than after each one: the format probe
# runs at about 10 ms a file on 8 threads, and a commit per file would be a
# noticeable share of that.
_BATCH = 50


class Store:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._pending = 0
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.execute("""CREATE TABLE IF NOT EXISTS results (
            kind TEXT, path TEXT, size INTEGER, mtime REAL, version INTEGER,
            value TEXT, at REAL, PRIMARY KEY (kind, path))""")
        self.conn.commit()

    def get(self, kind: str, version: int, path: Path, size: int, mtime: float):
        with self._lock:
            row = self.conn.execute(
                "SELECT size, mtime, version, value FROM results "
                "WHERE kind = ? AND path = ?", (kind, str(path))).fetchone()
        if (row and row[0] == size and abs(row[1] - mtime) < 1e-6
                and row[2] == version):
            return json.loads(row[3])
        return MISS

    def put(self, kind: str, version: int, path: Path, size: int, mtime: float,
            value) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO results "
                "(kind, path, size, mtime, version, value, at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (kind, str(path), size, mtime, version, json.dumps(value),
                 time.time()))
            self._pending += 1
            if self._pending >= _BATCH:
                self.conn.commit()
                self._pending = 0

    def close(self) -> None:
        with self._lock:
            self.conn.commit()
            self.conn.close()
