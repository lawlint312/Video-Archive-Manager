"""Run a long job at a set date and time: once, every day, or on weekdays.

Built for the face index (about 4 s a video, ~4,000 videos not yet indexed:
hours), but nothing here knows what the job is — `kind` names it and
`params` is handed back untouched — so a scan can use the same thing later.

The user's decisions (2026-11-07):

* **Once, or repeating** (daily / chosen weekdays), at a start time.
* **An optional end time.** The job is told to stop then and continue at the
  next occurrence; what is done stays done. An end at or before the start
  means the next morning (01:00–07:00 is fine, so is 23:00–06:00).
* **A missed start is made up only while still inside its window.** This is
  a desktop that gets switched off: switched on at 05:00 for a 01:00–07:00
  window, it runs until 07:00; switched on at 08:00, it waits for the next
  one. Without an end time the window lasts until the next occurrence (a
  one-off: for ever).

Times are the server's local wall clock. Every rule below is a pure function
of (schedule, now), so it is tested without waiting for a clock.
"""
from __future__ import annotations

import json
import re
import time
from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple

from .. import db

REPEATS = ("once", "daily", "weekly")
_HM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ── rules ────────────────────────────────────────────────────────────────────

def _hm(s: str) -> Tuple[int, int]:
    h, m = s.split(":")
    return int(h), int(m)


def runs_on(s: dict, d: date) -> bool:
    if s["repeat"] == "once":
        return s.get("date") == d.isoformat()
    if s["repeat"] == "weekly":
        return d.weekday() in (s.get("weekdays") or [])
    return True


def _start_on(s: dict, d: date) -> datetime:
    h, m = _hm(s["start"])
    return datetime(d.year, d.month, d.day, h, m)


def _explicit_end(s: dict, start: datetime) -> Optional[datetime]:
    if not s.get("end"):
        return None
    h, m = _hm(s["end"])
    end = start.replace(hour=h, minute=m)
    return end if end > start else end + timedelta(days=1)


def next_start(s: dict, after: datetime) -> Optional[datetime]:
    """The first occurrence starting strictly after `after`."""
    d = after.date()
    for k in range(0, 9):
        day = d + timedelta(days=k)
        if runs_on(s, day):
            st = _start_on(s, day)
            if st > after:
                return st
    return None


def window_of(s: dict, start: datetime) -> Tuple[datetime, Optional[datetime]]:
    """(start, end) of the occurrence starting at `start`; end None = open."""
    end = _explicit_end(s, start)
    if end is None and s["repeat"] != "once":
        end = next_start(s, start)
    return start, end


def slot_at(s: dict, now: datetime) -> Optional[Tuple[datetime, Optional[datetime]]]:
    """The occurrence whose window contains `now`, if any."""
    # the latest start at or before now; a week back covers weekly
    for k in range(0, 8):
        day = now.date() - timedelta(days=k)
        if runs_on(s, day):
            st = _start_on(s, day)
            if st <= now:
                st, end = window_of(s, st)
                return (st, end) if end is None or now < end else None
    return None


def state(s: dict, now: datetime) -> str:
    """off | due (window open, not started) | started | scheduled | done | missed"""
    if not s.get("enabled"):
        return "off"
    slot = slot_at(s, now)
    if slot and s.get("last_slot") == slot[0].timestamp():
        return "started"
    if slot:
        return "due"
    if s["repeat"] == "once":
        if s.get("last_slot"):
            return "done"
        return "scheduled" if next_start(s, now) else "missed"
    return "scheduled"


def validate(body: dict, now: datetime) -> Optional[str]:
    """The problem with a new schedule, in words, or None."""
    rep = body.get("repeat")
    if rep not in REPEATS:
        return "無效的重複方式"
    if not _HM.match(body.get("start") or ""):
        return "無效的開始時間"
    if body.get("end") and not _HM.match(body["end"]):
        return "無效的結束時間"
    if body.get("end") == body.get("start"):
        return "結束時間不能和開始時間相同"
    if rep == "once":
        if not _DATE.match(body.get("date") or ""):
            return "請指定日期"
        try:
            date.fromisoformat(body["date"])
        except ValueError:
            return "請指定日期"
        st, end = window_of(body, _start_on(body, date.fromisoformat(body["date"])))
        if end is not None and end <= now:
            return "這個時間已經過了"
    if rep == "weekly":
        wd = body.get("weekdays") or []
        if not wd or any(not isinstance(x, int) or not 0 <= x <= 6 for x in wd):
            return "請至少選一天"
    return None


# ── store (vam.db, table `schedules`) ────────────────────────────────────────

def _row(r) -> dict:
    d = dict(r)
    d["params"] = json.loads(d.get("params") or "{}")
    d["weekdays"] = json.loads(d.get("weekdays") or "[]")
    d["last_result"] = json.loads(d["last_result"]) if d.get("last_result") else None
    d["enabled"] = bool(d["enabled"])
    d["start"], d["end"] = d.pop("start_hm"), d.pop("end_hm")
    return d


def add(kind: str, user_id: int, body: dict, params: dict) -> int:
    conn = db.connect()
    try:
        cur = conn.execute(
            "INSERT INTO schedules (kind, user_id, params, repeat, date, weekdays,"
            " start_hm, end_hm, enabled, created_at) VALUES (?,?,?,?,?,?,?,?,1,?)",
            (kind, user_id, json.dumps(params, ensure_ascii=False), body["repeat"],
             body.get("date") if body["repeat"] == "once" else None,
             json.dumps(sorted(set(body.get("weekdays") or [])))
             if body["repeat"] == "weekly" else None,
             body["start"], body.get("end") or None, time.time()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get(sid: int) -> Optional[dict]:
    conn = db.connect()
    try:
        r = conn.execute("SELECT * FROM schedules WHERE id=?", (sid,)).fetchone()
        return _row(r) if r else None
    finally:
        conn.close()


def all_of(kind: Optional[str] = None, user_id: Optional[int] = None) -> List[dict]:
    q, args = "SELECT * FROM schedules WHERE 1=1", []
    if kind:
        q += " AND kind=?"
        args.append(kind)
    if user_id is not None:
        q += " AND user_id=?"
        args.append(user_id)
    conn = db.connect()
    try:
        return [_row(r) for r in conn.execute(q + " ORDER BY id", args)]
    finally:
        conn.close()


def _set(sid: int, **cols):
    conn = db.connect()
    try:
        conn.execute(f"UPDATE schedules SET {', '.join(k + '=?' for k in cols)}"
                     " WHERE id=?", (*cols.values(), sid))
        conn.commit()
    finally:
        conn.close()


def set_enabled(sid: int, on: bool):
    _set(sid, enabled=1 if on else 0)


def started(sid: int, slot_start: float, job_id: str):
    _set(sid, last_slot=slot_start, last_job=job_id)


def finished(sid: int, result: dict):
    _set(sid, last_result=json.dumps(result, ensure_ascii=False))


def delete(sid: int):
    conn = db.connect()
    try:
        conn.execute("DELETE FROM schedules WHERE id=?", (sid,))
        conn.commit()
    finally:
        conn.close()
