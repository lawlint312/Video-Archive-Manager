"""Security alerts: the handful of events an admin should actually look at.

The login log already records everything, and that is the problem — nobody
reads two hundred rows. This is the short list, and it exists mostly for one
event the failure counts can never show: **a successful sign-in from an
address that account has never used**. A leaked password produces successes,
not failures.

Every alert is deduplicated within an hour on (kind, username, ip), because
the failure modes here are floods: a scanner trying forty account names would
otherwise bury the one row that matters.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

log = logging.getLogger("vam.alerts")

NEW_IP = "new_ip"
UNKNOWN_USER = "unknown_user"
LOCKED = "locked"
SPRAY = "spray"
TWOFA = "2fa"
FAV_FORCED = "fav_forced"

# How an alert reads in the UI. Kept here so the wording lives next to the
# rule that raises it.
LABELS = {
    NEW_IP: "從沒用過的位址登入成功",
    UNKNOWN_USER: "有人嘗試不存在的帳號",
    LOCKED: "連續失敗，已鎖定",
    SPRAY: "同一位址猜遍多個帳號",
    TWOFA: "兩步驟驗證變更",
    FAV_FORCED: "管理員刪除了別人的最愛",
}

DEDUPE_MINUTES = 60
KEEP_DAYS = 90


def _now() -> datetime:
    return datetime.now(timezone.utc)


def raise_alert(conn, kind: str, username: str = "", ip: str = "",
                detail: str = "", dedupe: bool = True) -> bool:
    """Record one alert unless the same thing was already recorded this hour.

    `dedupe=False` for events that are each their own fact — two different
    favourites deleted in the same hour are two things to know, not a flood.
    """
    if dedupe:
        since = (_now() - timedelta(minutes=DEDUPE_MINUTES)).isoformat()
        dup = conn.execute(
            "SELECT 1 FROM security_alerts WHERE kind = ? AND username = ? "
            "AND ip = ? AND at > ? LIMIT 1",
            (kind, username or "", ip or "", since)).fetchone()
        if dup:
            return False
    conn.execute(
        "INSERT INTO security_alerts (at, kind, username, ip, detail) "
        "VALUES (?,?,?,?,?)",
        (_now().isoformat(), kind, username or "", ip or "", detail or ""))
    conn.commit()
    log.warning("security alert: %s user=%r ip=%s %s", kind, username, ip, detail)
    return True


def seen_ip_before(conn, username: str, ip: str) -> bool:
    """Has this account ever signed in successfully from this address?

    Answered from `login_events`, which means existing accounts do not all
    look brand new on the day this feature ships — their history is already
    in there.
    """
    row = conn.execute(
        "SELECT 1 FROM login_events WHERE username = ? AND ip = ? AND ok = 1 "
        "LIMIT 1", (username, ip)).fetchone()
    return bool(row)


def recent(conn, limit: int = 50) -> List[dict]:
    rows = conn.execute(
        "SELECT id, at, kind, username, ip, detail FROM security_alerts "
        "ORDER BY id DESC LIMIT ?", (max(1, min(500, limit)),)).fetchall()
    return [{**dict(r), "label": LABELS.get(r["kind"], r["kind"])} for r in rows]


def unseen_count(conn, user_id: int) -> int:
    row = conn.execute("SELECT last_id FROM alert_seen WHERE user_id = ?",
                       (user_id,)).fetchone()
    last = row["last_id"] if row else 0
    return conn.execute("SELECT COUNT(*) FROM security_alerts WHERE id > ?",
                        (last,)).fetchone()[0]


def mark_seen(conn, user_id: int) -> int:
    top = conn.execute("SELECT COALESCE(MAX(id), 0) FROM security_alerts").fetchone()[0]
    conn.execute("INSERT INTO alert_seen (user_id, last_id) VALUES (?,?) "
                 "ON CONFLICT(user_id) DO UPDATE SET last_id = excluded.last_id",
                 (user_id, top))
    conn.commit()
    return top


def prune(conn, days: int = KEEP_DAYS) -> int:
    cutoff = (_now() - timedelta(days=days)).isoformat()
    cur = conn.execute("DELETE FROM security_alerts WHERE at < ?", (cutoff,))
    conn.commit()
    return cur.rowcount


def describe_agent(ua: Optional[str]) -> str:
    """"iPhone · Safari" — enough to recognise your own devices, no more."""
    ua = ua or ""
    device = ("iPhone" if "iPhone" in ua else
              "iPad" if "iPad" in ua else
              "Android" if "Android" in ua else
              "Mac" if "Macintosh" in ua else
              "Windows" if "Windows" in ua else
              "Linux" if "Linux" in ua else "")
    # Order matters: Edge and Chrome both say "Chrome", Chrome also says "Safari".
    browser = ("Edge" if "Edg/" in ua else
               "Chrome" if "Chrome/" in ua or "CriOS" in ua else
               "Firefox" if "Firefox/" in ua else
               "Safari" if "Safari/" in ua else "")
    return " · ".join(x for x in (device, browser) if x) or "未知裝置"
