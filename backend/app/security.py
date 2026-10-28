"""Login hardening: password rules, throttling, audit, per-account IP limits.

Written after measuring what the service actually withstood. argon2 takes
**41 ms** per verification here, so one thread can try ~24 passwords a second
and nothing was stopping it:

    8 chars, lowercase only      137 years to guess on average
    6 chars, mixed case + digits  37 years
    6 chars, lowercase only       74 days
    a 10-million common-password list   57 hours (hours, in parallel)

So the hash was doing the work and the *policy* was the weak part: a 6
character minimum, no attempt limit, and — worst of all — no record. A
guessing run against this service used to leave no trace whatsoever.

Note what this file cannot fix: the service is reachable over plain HTTP, so
passwords and session cookies cross the network in the clear. Throttling does
not help against an attacker who simply reads the password off the wire.
Transport encryption is a separate and more important job.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

log = logging.getLogger("vam.security")

# ── password policy ─────────────────────────────────────────────────────────
MIN_LEN = 8
_RULES = (
    (re.compile(r"[a-z]"), "小寫英文字母"),
    (re.compile(r"[A-Z]"), "大寫英文字母"),
    (re.compile(r"[0-9]"), "數字"),
)
POLICY_TEXT = "密碼至少 8 個字元，且必須包含大寫英文、小寫英文與數字"


# Passwords that pass the rules above and are still the first thing anybody
# tries: `Password1`, `Qwerty123`, `Abcd1234`. Kept as *bases* and matched
# after stripping digits and punctuation, because the rules force people to
# decorate a common word rather than choose a different one. A downloaded
# ten-million-line list would catch more, but this project ships no data files
# and these are the shapes that actually appear in credential-stuffing runs.
_COMMON_BASES = {
    "password", "passwd", "pass", "welcome", "letmein", "admin", "administrator",
    "root", "user", "guest", "login", "master", "secret", "changeme", "default",
    "qwerty", "qwertyui", "qwertyuiop", "asdf", "asdfgh", "asdfghjk", "zxcvbn",
    "zxcvbnm", "abc", "abcd", "abcde", "abcdef", "abcdefg", "abcdefgh",
    "iloveyou", "sunshine", "princess", "dragon", "monkey", "football",
    "baseball", "superman", "batman", "starwars", "pokemon", "trustno",
    "whatever", "shadow", "michael", "jordan", "jennifer", "hunter", "freedom",
    "computer", "internet", "samsung", "google", "facebook", "windows",
    "taiwan", "taipei", "test", "testing", "demo", "temp", "aaaa", "aaaaaaaa",
    "qazwsx", "qweasd", "qwe", "asd", "zaq", "wasd", "love", "hello", "happy",
    "money", "server", "system", "backup", "media", "video", "photo", "family",
}
_ONLY_LETTERS = re.compile(r"[^a-z]+")


def _bases(pw: str) -> set:
    """The words underneath the decoration, both ways round.

    Two candidates, because the two habits need opposite treatment:
    `Password1` -> drop the digits -> `password`; `P@ssw0rd` -> read the
    substitutions -> `password`. Doing only one of them misses half of them.
    """
    lowered = (pw or "").lower()
    leet = lowered
    for a, b in (("@", "a"), ("$", "s"), ("0", "o"), ("1", "i"),
                 ("3", "e"), ("4", "a"), ("5", "s"), ("7", "t"), ("!", "i")):
        leet = leet.replace(a, b)
    return {_ONLY_LETTERS.sub("", lowered), _ONLY_LETTERS.sub("", leet)}


def password_problem(pw: str, username: str = "") -> Optional[str]:
    """None if acceptable, else a message saying exactly what is wrong."""
    if len(pw or "") < MIN_LEN:
        return f"密碼至少 {MIN_LEN} 個字元"
    missing = [name for rx, name in _RULES if not rx.search(pw)]
    if missing:
        return "密碼還缺少：" + "、".join(missing)
    bases = {b for b in _bases(pw) if b}
    # Only when the password is *made of* that word. `長密碼Abc12345` reduces
    # to "abc", which is on the list, but the password is not "abc" with
    # decoration -- most of it is characters the list knows nothing about.
    ascii_only = all(ord(c) < 128 for c in pw)
    if ascii_only and bases & _COMMON_BASES:
        return "這是最常被猜的密碼之一，請換一個"
    names = {n for n in _bases(username) if n and len(n) >= 3}
    if any(n in b for n in names for b in bases):
        return "密碼裡不能包含帳號名稱"
    if len(set((pw or "").lower())) <= 3:
        return "密碼用的字元太少，請換一個"
    return None


def weak_existing(pw: str, username: str = "") -> Optional[str]:
    """Why an *already working* password should be changed, or None.

    Checked at sign-in, where the plain password is in hand for the only time.
    The rules arrived after these accounts did, so this is the only way to
    find out that one of them is `admin1234`.
    """
    return password_problem(pw, username)


# ── throttling ──────────────────────────────────────────────────────────────
# Counted per (username, ip). Per username alone would let anyone lock a real
# account out by guessing at it — a denial of service dressed as security.
# Per IP alone would let one attacker work through many usernames.
WINDOW_MIN = 15          # how far back failures are counted
FREE_TRIES = 5           # failures allowed before the delay starts
LOCK_STEPS = (60, 300, 900, 3600)      # seconds, after 5, 6, 7, 8+ failures

# The pair rule alone leaves two gaps, both of which a real attacker uses:
#
# * **Spraying**: one address tries one common password against every account.
#   Each (account, ip) pair stays under its own 5 free tries, so nothing ever
#   locks. Counted per IP across all usernames, that is obvious.
# * **Rotating addresses**: the same account guessed from many IPs. Taiwanese
#   residential proxies are cheap, and the geo rule on the router only keeps
#   out the rest of the world.
#
# The two rules are deliberately different in kind. The per-IP one is a real
# lock: an address making 20 failed attempts in a quarter of an hour is not
# somebody mistyping. The per-account one is only a slowdown, because the
# account is the half an attacker chooses — a hard lock there is exactly the
# denial of service the pair rule exists to avoid.
IP_FREE_TRIES = 20       # failures from one address, any username
IP_LOCK_SECONDS = 900
ACCOUNT_FREE_TRIES = 10  # failures against one account, any address
ACCOUNT_DELAY = 30       # seconds between attempts once that is passed

# Attempts rejected by the throttle itself are still audited, but they must not
# count as fresh failures. Found by driving the real endpoint: clicking "log
# in" three more times while locked out pushed the wait from 60 s to an hour,
# punishing the person most likely to be doing it — the owner who mistyped.
# An attacker gains nothing either way; the lock is already closed.
BLOCKED = "鎖定中"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def record_attempt(conn, username: str, ip: str, ok: bool,
                   reason: str = ""):
    conn.execute(
        "INSERT INTO login_events (at, username, ip, ok, reason) "
        "VALUES (?, ?, ?, ?, ?)",
        (_now().isoformat(), (username or "")[:64], ip, 1 if ok else 0, reason))
    conn.commit()
    if not ok:
        log.warning("login rejected: user=%r ip=%s (%s)", username, ip, reason)
    else:
        log.info("login ok: user=%r ip=%s", username, ip)


def recent_failures(conn, username: str, ip: str) -> int:
    """Failures for this pair since the last success, within the window.

    Since the last success matters: signing in correctly should clear the
    slate, or a day of typos would keep punishing someone who has proved they
    know the password.
    """
    since = (_now() - timedelta(minutes=WINDOW_MIN)).isoformat()
    rows = conn.execute(
        "SELECT ok FROM login_events WHERE username = ? AND ip = ? AND at > ? "
        "AND COALESCE(reason, '') NOT LIKE ? "
        # by id, not by `at`: several attempts can land in the same microsecond
        # (a script, or a test), and then ordering by the timestamp is
        # arbitrary — a success could sort behind the failures it should have
        # cleared. The row id is the only strictly increasing thing here.
        "ORDER BY id DESC", (username, ip, since, BLOCKED + "%")).fetchall()
    n = 0
    for r in rows:
        if r["ok"]:
            break
        n += 1
    return n


def lock_seconds(failures: int) -> int:
    """How long this pair must wait, given its recent failure count."""
    if failures < FREE_TRIES:
        return 0
    step = min(failures - FREE_TRIES, len(LOCK_STEPS) - 1)
    return LOCK_STEPS[step]


def _failures_since(conn, column: str, value: str) -> Tuple[int, Optional[str]]:
    """Failures for one address or one account in the window, and the last one.

    Unlike `recent_failures` this does not stop at a success: a successful
    sign-in to *one* account says nothing about the twelve other accounts the
    same address is working through.
    """
    since = (_now() - timedelta(minutes=WINDOW_MIN)).isoformat()
    row = conn.execute(
        f"SELECT COUNT(*) AS n, MAX(at) AS last FROM login_events "
        f"WHERE {column} = ? AND ok = 0 AND at > ? "
        f"AND COALESCE(reason, '') NOT LIKE ?",
        (value, since, BLOCKED + "%")).fetchone()
    return (row["n"] or 0), row["last"]


def _left(last_at: Optional[str], seconds: int) -> Optional[int]:
    if not last_at:
        return None
    try:
        last = datetime.fromisoformat(last_at)
    except ValueError:
        return None
    left = (last + timedelta(seconds=seconds) - _now()).total_seconds()
    return int(left) + 1 if left > 0 else None


def throttle_check(conn, username: str, ip: str) -> Optional[int]:
    """Seconds still to wait, or None to let the attempt through.

    Three rules, longest wait wins: the (account, ip) pair, the address on its
    own, and the account on its own. See `throttle_reason` for which is which.
    """
    wait, _ = throttle_reason(conn, username, ip)
    return wait


def throttle_reason(conn, username: str, ip: str) -> Tuple[Optional[int], str]:
    """(seconds to wait, which rule) — the reason goes into the audit trail."""
    best, why = None, ""

    fails = recent_failures(conn, username, ip)
    pair_wait = lock_seconds(fails)
    if pair_wait:
        row = conn.execute(
            "SELECT at FROM login_events WHERE username = ? AND ip = ? AND ok = 0 "
            "AND COALESCE(reason, '') NOT LIKE ? "
            "ORDER BY id DESC LIMIT 1", (username, ip, BLOCKED + "%")).fetchone()
        left = _left(row["at"] if row else None, pair_wait)
        if left:
            best, why = left, "帳號+位址"

    n_ip, last_ip = _failures_since(conn, "ip", ip)
    if n_ip >= IP_FREE_TRIES:
        left = _left(last_ip, IP_LOCK_SECONDS)
        if left and (best is None or left > best):
            best, why = left, "同一位址猜遍多個帳號"

    n_acc, last_acc = _failures_since(conn, "username", username)
    if n_acc >= ACCOUNT_FREE_TRIES:
        # A slowdown, not a lock: whoever owns this account must still be able
        # to sign in while someone else is guessing at it.
        left = _left(last_acc, ACCOUNT_DELAY)
        if left and (best is None or left > best):
            best, why = left, "同一帳號被大量嘗試"

    return best, why


# ── per-account IP allowlist ────────────────────────────────────────────────

def parse_networks(raw) -> List[ipaddress._BaseNetwork]:
    """Accepts a JSON string or a list; single addresses become /32 or /128."""
    if not raw:
        return []
    items = json.loads(raw) if isinstance(raw, str) else raw
    out = []
    for s in items or []:
        s = str(s).strip()
        if not s:
            continue
        out.append(ipaddress.ip_network(s, strict=False))
    return out


def ip_allowed(ip: str, raw_allowlist) -> bool:
    """Empty allowlist means anywhere — the same convention as allowed_roots."""
    nets = parse_networks(raw_allowlist)
    if not nets:
        return True
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False        # unparseable source: refuse rather than guess
    return any(addr in n for n in nets)


def validate_allowlist(items) -> Tuple[List[str], Optional[str]]:
    """Normalise a user-supplied list, or explain what is wrong with it."""
    out = []
    for s in items or []:
        s = str(s).strip()
        if not s:
            continue
        try:
            out.append(str(ipaddress.ip_network(s, strict=False)))
        except ValueError:
            return [], f"看不懂的 IP 或網段：{s}"
    return out, None


def client_ip(request, trust_proxy: bool = False) -> str:
    """The source address, honouring X-Forwarded-For only when configured.

    Trusting the header by default would be a hole rather than a feature:
    anyone can send `X-Forwarded-For: 127.0.0.1` and walk straight through an
    allowlist. It is only meaningful when a reverse proxy you control is in
    front and is overwriting the header.

    **And only when the connection actually comes from that proxy.** The first
    version honoured the header from any peer once `trust_proxy` was on, so a
    direct connection from the internet could still name its own source. The
    proxy runs on this machine (Caddy, for HTTPS), so the header is believed
    only from a loopback peer.
    """
    peer = getattr(getattr(request, "client", None), "host", "") or "unknown"
    if trust_proxy and _is_loopback(peer):
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return peer


def _addr(ip: str):
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return None
    mapped = getattr(a, "ipv4_mapped", None)   # ::ffff:192.168.1.5
    return mapped or a


def _is_loopback(ip: str) -> bool:
    a = _addr(ip)
    return bool(a and a.is_loopback)


# RFC 6598 carrier-grade NAT space. Not "private" to Python, but it is what
# Tailscale and some VPN servers hand out, and it is never routable from the
# internet.
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def is_local_source(ip: str) -> bool:
    """True for loopback, LAN, link-local and VPN address space.

    What `lan_only` lets through. WireGuard on the router hands its clients
    an address inside a private range, so a phone on the VPN passes and the
    same phone on mobile data — arriving as its carrier's public address —
    does not. Such a carrier address is exactly what the log showed on
    2026-09-15, when a port forward had put the plain-HTTP login page on the
    internet.

    An address that does not parse is let through. Every TCP peer uvicorn
    reports is an IP; the only non-IP peers are in-process transports such as
    the test client, which cannot be reached from a network at all.
    """
    a = _addr(ip)
    if a is None:
        return True
    if a.version == 4 and a in _CGNAT:
        return True
    return a.is_private or a.is_loopback or a.is_link_local
