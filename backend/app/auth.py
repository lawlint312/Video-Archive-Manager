"""Authentication: argon2 passwords, server-side sessions, role checks."""
from __future__ import annotations

import json
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, HTTPException, Request, Response

from . import db
from .config import SESSION_COOKIE, SESSION_DAYS

_ph = PasswordHasher()

# Tests that are about something else switch this off; test_twofactor.py
# covers the requirement itself. Never touched outside tests.
TWOFA_ENFORCED = True

# What an account with must_change_pw may still reach: read itself, change the
# password, sign out. Nothing else.
_PW_ALLOWED = {"/api/auth/me", "/api/auth/password", "/api/auth/logout"}
# Same idea for "you have to set two-factor up first", plus the setup calls.
_2FA_ALLOWED = _PW_ALLOWED | {"/api/auth/2fa", "/api/auth/2fa/setup",
                              "/api/auth/2fa/enable"}


# ── passwords ────────────────────────────────────────────────────────────────

def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(hashed: str, pw: str) -> bool:
    try:
        return _ph.verify(hashed, pw)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


# ── users ────────────────────────────────────────────────────────────────────

def create_user(username: str, password: str, role: str = "viewer") -> int:
    conn = db.connect()
    try:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, role, created_at) "
            "VALUES (?,?,?,?)",
            (username, hash_password(password), role,
             datetime.now(timezone.utc).isoformat()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_user_by_name(username: str) -> Optional[dict]:
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM users WHERE username = ?",
                           (username,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def count_users() -> int:
    conn = db.connect()
    try:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    finally:
        conn.close()


def list_users() -> list:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT id, username, role, created_at, allowed_roots, allowed_ips, "
            "disabled, totp_secret IS NOT NULL AS twofa FROM users ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_user_by_id(uid: int) -> Optional[dict]:
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def count_admins() -> int:
    """Admins who can actually sign in — a disabled one cannot let anyone back in."""
    conn = db.connect()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM users WHERE role = 'admin' "
            "AND COALESCE(disabled, 0) = 0").fetchone()[0]
    finally:
        conn.close()


_UNSET = object()


def update_user(uid: int, role: Optional[str] = None,
                password: Optional[str] = None, allowed_roots=_UNSET,
                allowed_ips=_UNSET, disabled: Optional[bool] = None):
    sets, args = [], []
    if role is not None:
        sets.append("role = ?"); args.append(role)
    if disabled is not None:
        sets.append("disabled = ?"); args.append(1 if disabled else 0)
    if password is not None:
        sets.append("password_hash = ?"); args.append(hash_password(password))
    if allowed_roots is not _UNSET:
        # None = unrestricted; a list = only these roots
        sets.append("allowed_roots = ?")
        args.append(json.dumps(allowed_roots, ensure_ascii=False)
                    if allowed_roots else None)
    if allowed_ips is not _UNSET:
        # empty list = reachable from anywhere, same convention as the roots
        sets.append("allowed_ips = ?")
        args.append(json.dumps(allowed_ips, ensure_ascii=False)
                    if allowed_ips else None)
    if not sets:
        return
    conn = db.connect()
    try:
        conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?",
                     (*args, uid))
        conn.commit()
    finally:
        conn.close()
    # A changed password, role or allowlist must not leave old cookies
    # working — the new restriction has to bite immediately.
    destroy_user_sessions(uid)


def delete_user(uid: int):
    conn = db.connect()
    try:
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))
        conn.execute("DELETE FROM users WHERE id = ?", (uid,))
        conn.commit()
    finally:
        conn.close()


def perms_of(user: dict) -> list:
    """Every permission this account has — admin has all of them."""
    from . import roles
    if (user or {}).get("role") == roles.ADMIN:
        return list(roles.PERM_KEYS)
    return roles.perms_for((user or {}).get("role", ""))


def twofa_required(user: dict) -> bool:
    """Two-factor is compulsory for every account that can change anything.

    The user's rule: read-only groups may choose, every other group must. So
    it follows the permissions, not a list of role names — a new group with a
    single ticked box is covered the moment it is saved.
    """
    from . import roles
    if not TWOFA_ENFORCED:
        return False
    return (user or {}).get("role") == roles.ADMIN or bool(perms_of(user))


def needs_2fa_setup(user: dict) -> bool:
    """True when this account must have two-factor and has none yet."""
    return twofa_required(user) and not user.get("totp_secret")


def set_totp(uid: int, secret=_UNSET, pending=_UNSET, last=_UNSET,
             recovery=_UNSET):
    """Write the two-factor columns without touching sessions.

    `update_user` ends every session of the account, which is right for a
    password or a role change and wrong here: enabling two-factor should not
    sign you out of the very page you are enabling it from.
    """
    sets, args = [], []
    for col, val in (("totp_secret", secret), ("totp_pending", pending),
                     ("totp_last", last), ("recovery_codes", recovery)):
        if val is not _UNSET:
            sets.append(f"{col} = ?")
            args.append(val)
    if not sets:
        return
    conn = db.connect()
    try:
        conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?",
                     (*args, uid))
        conn.commit()
    finally:
        conn.close()


# The reason given for the first admin's password when the server made it up
# (no VAM_ADMIN_PASSWORD). It is strong, so the sign-in check would clear the
# flag at once; sign-in keeps it while this is the stored reason, and only
# changing the password clears it.
GENERATED_PW = "這是第一次啟動時自動產生的密碼，已經寫在伺服器的日誌檔裡，請換一個新密碼"


def set_must_change(uid: int, problem: Optional[str]):
    """Flag (or clear) "this account has to pick a new password first".

    Deliberately not part of `update_user`: that ends every session of the
    account, and this is set *during* a sign-in that is about to issue one.
    """
    conn = db.connect()
    try:
        conn.execute("UPDATE users SET must_change_pw = ?, pw_problem = ? WHERE id = ?",
                     (1 if problem else 0, problem or None, uid))
        conn.commit()
    finally:
        conn.close()


def destroy_user_sessions(uid: int):
    conn = db.connect()
    try:
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))
        conn.commit()
    finally:
        conn.close()


# ── sessions ─────────────────────────────────────────────────────────────────

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user_id: int, ip: str = "", user_agent: str = "") -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at, "
            "ip, user_agent, last_seen) VALUES (?,?,?,?,?,?,?)",
            (_hash_token(token), user_id, now.isoformat(),
             (now + timedelta(days=SESSION_DAYS)).isoformat(),
             ip, (user_agent or "")[:300], now.isoformat()))
        conn.commit()
    finally:
        conn.close()
    return token


def session_id(token: str) -> str:
    """A short handle the owner can use to revoke one session.

    The first 16 characters of the stored hash: it identifies the row without
    being the token, so showing it in a list gives nothing away.
    """
    return _hash_token(token)[:16]


def list_sessions(uid: int, current_token: str = "") -> list:
    here = session_id(current_token) if current_token else ""
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT token_hash, created_at, last_seen, ip, user_agent "
            "FROM sessions WHERE user_id = ? AND expires_at > ? "
            "ORDER BY COALESCE(last_seen, created_at) DESC",
            (uid, datetime.now(timezone.utc).isoformat())).fetchall()
        out = []
        for r in rows:
            sid = r["token_hash"][:16]
            out.append({"id": sid, "created_at": r["created_at"],
                        "last_seen": r["last_seen"] or r["created_at"],
                        "ip": r["ip"] or "", "agent": r["user_agent"] or "",
                        "current": sid == here})
        return out
    finally:
        conn.close()


def destroy_session_id(uid: int, sid: str) -> bool:
    """Revoke one of this account's sessions. Scoped to the owner on purpose."""
    conn = db.connect()
    try:
        cur = conn.execute(
            "DELETE FROM sessions WHERE user_id = ? AND substr(token_hash, 1, 16) = ?",
            (uid, sid))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def touch_session(token: str, ip: str = ""):
    """Record that this session is still in use, at most once a minute.

    Every request would otherwise mean a write, and a page of thumbnails is
    thirty requests.
    """
    sid = _hash_token(token)
    now = datetime.now(timezone.utc)
    conn = db.connect()
    try:
        row = conn.execute("SELECT last_seen FROM sessions WHERE token_hash = ?",
                           (sid,)).fetchone()
        if row and row["last_seen"]:
            try:
                last = datetime.fromisoformat(row["last_seen"])
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                if (now - last).total_seconds() < 60:
                    return
            except ValueError:
                pass
        conn.execute("UPDATE sessions SET last_seen = ?, ip = COALESCE(NULLIF(?, ''), ip) "
                     "WHERE token_hash = ?", (now.isoformat(), ip, sid))
        conn.commit()
    finally:
        conn.close()


def destroy_session(token: str):
    conn = db.connect()
    try:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?",
                     (_hash_token(token),))
        conn.commit()
    finally:
        conn.close()


def user_for_token(token: str) -> Optional[dict]:
    conn = db.connect()
    try:
        # created_at as well as expires_at: expires_at is fixed when the
        # session is issued, so shortening SESSION_DAYS would otherwise leave
        # every existing cookie on the old, longer lifetime.
        now = datetime.now(timezone.utc)
        # COALESCE: rows written before the column existed have NULL there.
        row = conn.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash = ? AND s.expires_at > ? AND s.created_at > ? "
            "AND COALESCE(u.disabled, 0) = 0",
            (_hash_token(token), now.isoformat(),
             (now - timedelta(days=SESSION_DAYS)).isoformat())).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ── FastAPI dependencies ─────────────────────────────────────────────────────

def current_user(request: Request) -> dict:
    """Require a valid session; return the user dict or 401."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="not authenticated")
    user = user_for_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="session invalid or expired")
    # Checked on every request, not only at login. A session is a bearer
    # token: once issued from an allowed address it would otherwise keep
    # working from anywhere the cookie is carried to — which, over plain HTTP,
    # includes anywhere it was copied from.
    from . import security, settings as app_settings
    if user.get("allowed_ips"):
        ip = security.client_ip(request,
                                app_settings.get().get("trust_proxy", False))
        if not security.ip_allowed(ip, user["allowed_ips"]):
            raise HTTPException(status_code=403,
                                detail="這個帳號不允許從目前的網路位址存取")
    # A weak password found at sign-in (see security.weak_existing) leaves the
    # session usable for exactly the three calls needed to fix it. Enforced
    # here rather than in the UI: hiding the buttons stops nobody.
    if user.get("must_change_pw") and request.url.path not in _PW_ALLOWED:
        raise HTTPException(status_code=403,
                            detail="請先修改密碼：" + (user.get("pw_problem") or ""))
    # Same shape as the weak-password gate: an admin or editor without
    # two-factor can reach the setup calls and nothing else.
    if needs_2fa_setup(user) and request.url.path not in _2FA_ALLOWED:
        raise HTTPException(status_code=403, detail="請先設定兩步驟驗證")
    return user


def require_editor(user: dict = Depends(current_user)) -> dict:
    """Any permission at all. Kept for the few calls every tool shares."""
    if not perms_of(user):
        raise HTTPException(status_code=403, detail="沒有編輯權限")
    return user


def require_perm(*keys: str):
    """Dependency: the account needs at least one of these permissions.

    Several keys is for the handful of endpoints two tools share (the folder
    picker, the job list); every feature endpoint names exactly one.
    """
    from . import roles
    label = "、".join(dict((k, l) for k, l, _, _ in roles.PERMS).get(k, k)
                     for k in keys)

    def dep(user: dict = Depends(current_user)) -> dict:
        have = set(perms_of(user))
        if not have.intersection(keys):
            raise HTTPException(status_code=403,
                                detail=f"沒有「{label}」的權限")
        return user
    return dep


def require_admin(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="admin permission required")
    return user


def public_user(**kwargs) -> dict:
    """Strip sensitive fields before returning a user to the client."""
    u = kwargs
    from . import roles
    perms = perms_of(u)
    return {"id": u["id"], "username": u["username"], "role": u["role"],
            "role_name": roles.name_of(u["role"]),
            "perms": perms,
            # "Has anything to switch into edit mode for" — the toggle and the
            # generic tool bar still key off this.
            "can_edit": bool(perms),
            "scoped": bool(u.get("allowed_roots")),
            "disabled": bool(u.get("disabled")),
            "must_change_pw": bool(u.get("must_change_pw")),
            "pw_problem": u.get("pw_problem") or "",
            "twofa": bool(u.get("totp_secret")),
            "twofa_required": twofa_required(u),
            "needs_2fa_setup": needs_2fa_setup(u)}
