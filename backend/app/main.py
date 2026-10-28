"""
Video Archive Manager — Web backend (FastAPI).
Phase 1: scan / thumbnail / stream.   Phase 2: auth + roles.
"""
from __future__ import annotations

import os
import re
import sys
import json
import stat
import time
import shutil
import secrets
import logging
import hashlib
import mimetypes
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import formatdate
from logging.handlers import RotatingFileHandler
from pathlib import Path
from collections import Counter
from typing import Dict, List, Optional

from fastapi import (FastAPI, HTTPException, Request, Response, Query,
                     Depends, UploadFile, File)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import (FileResponse, JSONResponse, HTMLResponse,
                               StreamingResponse)
from pydantic import BaseModel

from .config import (CACHE_DIR, DATA_DIR, INIT_ADMIN_USER, INIT_ADMIN_PASSWORD,
                     SESSION_COOKIE, SESSION_DAYS)
from .core import (media, dedup, imgdedup, faces, charstats, convert,
                   javdb, performers, searchindex, idolprofile,
                   contactsheet, cachelimit, clip, repair, shrink, pairs, reporthistory,
                   photothumb, videoflags, wikiimport, personscan, subtitles,
                   filetitle, costar, retime, iafd, photosimilar, backup, twofactor,
                   alerts, releasedate, decodecheck, nfo, dmm, avbase, catalog,
                   gfriends, dmmcover)
from . import db, auth, jobs, security, roles, settings as app_settings

is_allowed = app_settings.is_allowed

# Files produced beside an original (shrink, repair) that are waiting on a
# keep-or-discard decision. Declared here because it is used well above
# where the other report paths are defined.
_PAIRS_PATH = DATA_DIR / "pending_pairs.json"

log = logging.getLogger("vam")


# How many previous runs of the server to keep logs for. One file per run makes
# "what happened the time it went wrong" a single file to open, but nothing ever
# deletes them otherwise — and this server is started from the Startup folder,
# so it starts every time the machine is logged into.
LOG_KEEP_RUNS = 15


def _prune_logs(log_dir: Path, keep: int = LOG_KEEP_RUNS):
    """Delete all but the newest `keep` runs.

    Grouped by run rather than by file: a long run rotates into `…_LOG.txt.1`,
    and dropping half a run's history would be worse than dropping all of it.
    """
    runs = {}
    for f in list(log_dir.glob("*_LOG.txt*")) + list(log_dir.glob("*_CONSOLE.txt")):
        # "2026-09-05_0538_LOG.txt.2" -> "2026-09-05_0538"
        # "2026-09-05_0538_CONSOLE.txt" -> the same run, written by the vbs
        stamp = f.name.split("_LOG.txt")[0].split("_CONSOLE.txt")[0]
        runs.setdefault(stamp, []).append(f)
    for stamp in sorted(runs)[:-keep] if len(runs) > keep else []:
        for f in runs[stamp]:
            try:
                f.unlink()
            except OSError:
                pass          # in use, or gone already; the next run retries


def _setup_logging():
    """Console plus one file per run.

    The server is normally launched with its console window hidden, so console
    output goes nowhere you can read afterwards — which made "why did that scan
    return what it did?" unanswerable. The file survives however it was started.

    One file per run, named for the moment it started, rather than one file
    appended to forever: the question is almost always about a particular
    session. It still rotates within a run, because the server stays up for
    days at a time.
    """
    root = logging.getLogger()
    if any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        return                                  # uvicorn --reload runs this twice
    log_dir = DATA_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    _prune_logs(log_dir)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                            "%Y-%m-%d %H:%M:%S")
    stamp = time.strftime("%Y-%m-%d_%H%M")
    fh = RotatingFileHandler(log_dir / f"{stamp}_LOG.txt", maxBytes=2 << 20,
                             backupCount=2, encoding="utf-8")
    fh.setFormatter(fmt)
    fh.setLevel(logging.INFO)
    root.setLevel(logging.INFO)
    root.addHandler(fh)
    # Only when there is somewhere to write. Launched hidden through wscript
    # the process has no console, and on Windows `sys.stderr` is then None —
    # a StreamHandler built on it raises on the first log line, before
    # anything has had a chance to record why.
    if sys.stderr is not None:
        ch = logging.StreamHandler()
        ch.setFormatter(fmt)
        root.addHandler(ch)


# The built frontend, served by this process in normal use.
# backend/app/main.py -> backend/app -> backend -> repo root
DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"

_setup_logging()

# No /docs, /redoc or /openapi.json. FastAPI serves them by default and the
# app never uses them, but once the site was public they handed anyone the
# full list of endpoints and parameters without signing in.
app = FastAPI(title="Video Archive Manager Web", version="0.2.0",
              docs_url=None, redoc_url=None, openapi_url=None)

# Requests slower than this get a line in the log. Chosen so the log stays
# quiet in normal use — a cached thumbnail is ~30 ms, the character list ~170 ms
# — while anything a person would call "slow" is recorded.
SLOW_REQUEST_MS = 500
VERY_SLOW_MS = 3000


_refused_logged: Dict[str, float] = {}


@app.middleware("http")
async def lan_only_guard(request: Request, call_next):
    """Refuse requests from public internet addresses when `lan_only` is on.

    Runs before everything else, the login page included: an attacker who can
    reach the login form can guess passwords, and one who can reach nothing
    cannot. The per-account IP allowlist stays as a finer control on top.

    A refusal is logged at most once an hour per address, so a scanner cannot
    turn this into a way of filling the disk.
    """
    cfg = app_settings.get_cached()
    if cfg.get("lan_only", True):
        ip = security.client_ip(request, cfg.get("trust_proxy", False))
        if not security.is_local_source(ip):
            now = time.time()
            if now - _refused_logged.get(ip, 0) > 3600:
                _refused_logged[ip] = now
                log.warning("refused %s %s from %s: lan_only is on",
                            request.method, request.url.path, ip)
            return JSONResponse({"detail": "只接受內網或 VPN 連線"}, status_code=403)
    return await call_next(request)


@app.middleware("http")
async def log_slow_requests(request: Request, call_next):
    """Record how long the *server* took, so "it felt slow" becomes answerable.

    Twice now a performance complaint has been impossible to investigate after
    the fact, because nothing about ordinary API calls was written down: the
    slow duplicate scan, and a phone browsing the 2,000-item inbox. Knowing
    whether the server took 20 ms or 4 s separates "the backend is busy" from
    "the phone is struggling to render", and those have nothing in common.

    Timed up to the point the response starts. For a streaming reply that is
    the handler's own work, not how long the client took to read it — which is
    the number that says anything about this side.
    """
    t0 = time.perf_counter()
    response = await call_next(request)
    ms = (time.perf_counter() - t0) * 1000
    if ms >= SLOW_REQUEST_MS:
        ip = getattr(getattr(request, "client", None), "host", "?")
        log.log(logging.WARNING if ms >= VERY_SLOW_MS else logging.INFO,
                "slow %s %s%s -> %s in %.0f ms (from %s)",
                request.method, request.url.path,
                f"?{request.url.query}" if request.url.query else "",
                response.status_code, ms, ip)
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 30 days; "private" keeps proxies from storing someone's library thumbnails
_IMG_CACHE_HEADERS = {"Cache-Control": "private, max-age=2592000"}

# ── who is holding this file? ────────────────────────────────────────────────
# Deleting a video used to fail with WinError 32 forever, and the culprit was
# this process: streaming from a sync generator leaked the file handle every
# time a client disconnected mid-transfer (see /api/stream for the details).
# That is fixed, but the question "who has it open?" is worth keeping
# answerable — the next holder might be a thumbnailer, a probe, or something
# outside the process entirely, and guessing at it wasted a whole session once.
#
# Asks the OS rather than a registry of our own, so it stays true no matter
# which layer opened the file.
def _holders(p: Path) -> str:
    """Who is holding `p` open — the answer decides where to look next.

    Three outcomes, and telling them apart is the whole point:

      * this process        → a handle leak in our own code
      * one of our children → ffmpeg/ffprobe, i.e. the app is busy with the
                              file (contact sheet, clip, convert). Transient.
      * neither             → genuinely external: antivirus, the NAS indexer,
                              another player.

    The middle case used to be reported as the last one, which sent the user
    hunting through antivirus settings while the answer was "your own contact
    sheet is still being built" — measured: prefetching one 145-minute video
    keeps a child on the file for ~14 s spread over 67 s.
    """
    try:
        import psutil
    except ImportError:                     # optional; never break a delete
        return "未知（未安裝 psutil）"
    try:
        target = os.path.normcase(str(p.resolve()))
        me = psutil.Process()
        mine = [f for f in me.open_files()
                if os.path.normcase(f.path) == target]
        kids = []
        for c in me.children(recursive=True):
            try:
                if any(os.path.normcase(f.path) == target
                       for f in c.open_files()):
                    kids.append(c.name())
            except Exception:
                continue                    # it exited, or access denied
    except Exception as e:                  # psutil raises plenty on Windows
        return f"查詢失敗（{type(e).__name__}）"
    if mine:
        return f"本行程仍開著 {len(mine)} 個控制代碼"
    if kids:
        return f"本程式的子行程正在讀取（{'、'.join(sorted(set(kids)))}）"
    return "本行程沒有開啟此檔（握有者在外部）"


def _busy_reason(p: Path) -> Optional[str]:
    """What *our own* app is doing with `p`, in words a user can act on."""
    try:
        import psutil
        me = psutil.Process()
        target = os.path.normcase(str(p.resolve()))
        for c in me.children(recursive=True):
            try:
                if any(os.path.normcase(f.path) == target
                       for f in c.open_files()):
                    return ("這個檔案正在產生縮圖表或轉檔，"
                            "背景工作結束後就能刪除（通常幾秒）")
            except Exception:
                continue
    except Exception:
        pass
    return None


@app.on_event("startup")
def _startup():
    db.init_db()
    roles.seed()
    # thumbnails used to live directly in the cache root, mixed in with
    # sheets/ and notes/, which made per-kind limits impossible
    media.migrate_thumb_layout(CACHE_DIR)
    # thumbnails and sheets named before the file's size and mtime were part
    # of the name can never be served again; free that space in the
    # background (about 16,000 files the first time, nothing afterwards)
    threading.Thread(target=media.drop_legacy_cache, args=(CACHE_DIR,),
                     daemon=True).start()
    # GPAC is the second external tool this project needs, after ffmpeg, and
    # only one feature uses it. Say so at startup rather than letting the
    # frame-timing repair quietly not appear — "the button is missing" gives
    # nobody anything to go on (same reasoning as personscan.probe).
    threading.Thread(target=_backup_loop, daemon=True).start()
    # Set here, not at import: tests import this module and start jobs, and
    # must not write the real registry (or rerun the real interrupted jobs).
    jobs.REGISTRY, jobs.NOTICES = JOBS_RUNNING, JOBS_INTERRUPTED
    # a thread: a rerun scan may walk folders before it returns its job id
    threading.Thread(target=_recover_jobs, daemon=True).start()
    box = retime.mp4box()
    if box:
        log.info("MP4Box: %s（影格時序修復可用）", box)
    else:
        log.info("MP4Box 找不到 —— 影格時序修復停用。"
                 "安裝方式：winget install GPAC.GPAC")
    if auth.count_users() == 0:
        pw = INIT_ADMIN_PASSWORD or secrets.token_urlsafe(12)
        auth.create_user(INIT_ADMIN_USER, pw, role="admin")
        if INIT_ADMIN_PASSWORD:
            log.info("Created initial admin '%s' (password from env).",
                     INIT_ADMIN_USER)
        else:
            log.warning("=" * 56)
            log.warning(" Created initial admin account:")
            log.warning("   username: %s", INIT_ADMIN_USER)
            log.warning("   password: %s", pw)
            log.warning(" (set VAM_ADMIN_PASSWORD to choose your own)")
            log.warning("=" * 56)


# ── helpers ──────────────────────────────────────────────────────────────────

def _files_changed():
    """Something moved, converted or deleted files — drop the derived caches.

    One function rather than two calls at each site: there are six of them,
    and a new endpoint that remembers the counts but forgets the search index
    would leave the sidebar offering videos that are no longer there.
    """
    charstats.invalidate()
    searchindex.invalidate()


def _resumable(endpoint: str, body: BaseModel, user: dict) -> dict:
    """What `jobs.start(resume=…)` keeps so a restart can run this again:
    the endpoint (a key of `_RESUME`), its request and whose request it was."""
    return {"endpoint": endpoint, "body": body.dict(), "user": user.get("id")}


def _checked_path(path: str, user: dict) -> Path:
    p = Path(path)
    if not is_allowed(p, user):
        raise HTTPException(status_code=403, detail="path not allowed")
    if not p.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return p


def _is_https(request: Request) -> bool:
    """Whether the browser is talking HTTPS to us.

    Behind Caddy the connection this process sees is plain HTTP from
    127.0.0.1, so the scheme has to come from `X-Forwarded-Proto` — believed
    only from a loopback peer, for the same reason `security.client_ip` only
    believes `X-Forwarded-For` from one.
    """
    if request.url.scheme == "https":
        return True
    peer = getattr(getattr(request, "client", None), "host", "") or ""
    return (security._is_loopback(peer)
            and request.headers.get("x-forwarded-proto", "").lower() == "https")


def _set_session_cookie(resp: Response, token: str, request: Request):
    # `Secure` only over HTTPS: the LAN address on plain http://…:8301 is still
    # a supported way in, and a Secure cookie would silently log it out.
    resp.set_cookie(
        SESSION_COOKIE, token,
        max_age=SESSION_DAYS * 86400,
        httponly=True, samesite="lax", secure=_is_https(request), path="/")


# ── two-factor login tickets ────────────────────────────────────────────────
# In memory on purpose: a ticket is worth five minutes, and a restart is a
# perfectly good reason to type the password again. Nothing here grants access
# on its own — it only says "this password was correct a moment ago".
_TICKETS: Dict[str, dict] = {}
_TICKET_SECONDS = 300
_TICKET_TRIES = 5


def _new_ticket(uid: int, username: str, ip: str, weak) -> str:
    now = time.time()
    for k, v in list(_TICKETS.items()):
        if now - v["at"] > _TICKET_SECONDS:
            _TICKETS.pop(k, None)
    token = secrets.token_urlsafe(24)
    _TICKETS[token] = {"uid": uid, "username": username, "ip": ip,
                       "at": now, "tries": 0, "weak": weak}
    return token


def _ticket(token: str):
    t = _TICKETS.get(token or "")
    if not t:
        return None
    if time.time() - t["at"] > _TICKET_SECONDS:
        _TICKETS.pop(token, None)
        return None
    return t


# ── auth routes ──────────────────────────────────────────────────────────────

class LoginBody(BaseModel):
    username: str
    password: str


@app.post("/api/auth/login")
def login(body: LoginBody, request: Request, response: Response):
    """Sign in, with the attempt recorded either way.

    Order matters here. The throttle is checked *before* the password, so a
    locked-out attacker cannot keep burning 41 ms of argon2 per guess; and the
    IP allowlist is checked *after* the password, so it can never be used to
    probe which accounts have one.
    """
    ip = security.client_ip(request, app_settings.get().get("trust_proxy", False))
    conn = db.connect()
    try:
        wait, why = security.throttle_reason(conn, body.username, ip)
        if wait:
            security.record_attempt(conn, body.username, ip, False,
                                    f"{security.BLOCKED}（{why}，還有 {wait} 秒）")
            raise HTTPException(
                status_code=429,
                detail=f"嘗試次數過多，請在 {wait} 秒後再試")

        user = auth.get_user_by_name(body.username)
        if not user or not auth.verify_password(user["password_hash"],
                                                body.password):
            security.record_attempt(conn, body.username, ip, False, "帳號或密碼錯誤")
            _raise_login_alerts(conn, body.username, ip, exists=bool(user))
            # deliberately the same message whether or not the account exists
            raise HTTPException(status_code=401, detail="invalid credentials")

        if user.get("disabled"):
            # After the password on purpose: before it, this would answer
            # "does this account exist" to anyone who asked.
            security.record_attempt(conn, body.username, ip, False, "帳號已停用")
            raise HTTPException(status_code=403, detail="這個帳號已停用")

        if not security.ip_allowed(ip, user.get("allowed_ips")):
            security.record_attempt(conn, body.username, ip, False,
                                    "來源 IP 不在允許清單內")
            raise HTTPException(status_code=403,
                                detail="這個帳號不允許從目前的網路位址登入")

        # Before recording this success, because the question is whether the
        # address was known *until now*.
        first_time = not alerts.seen_ip_before(conn, body.username, ip)
        security.record_attempt(conn, body.username, ip, True)
        if first_time:
            alerts.raise_alert(conn, alerts.NEW_IP, body.username, ip,
                               "這個帳號第一次從這個位址登入成功")
    finally:
        conn.close()

    # The one moment the plain password exists: check it against the rules the
    # account may predate. Sign-in still succeeds — the session is then good
    # for changing the password and nothing else (see auth.current_user).
    weak = security.weak_existing(body.password, body.username)

    if user.get("totp_secret"):
        # No cookie yet. For this account the password alone is not a sign-in,
        # so what comes back is a ticket for the second step and nothing else.
        ticket = _new_ticket(user["id"], body.username, ip, weak)
        return {"need_2fa": True, "ticket": ticket, "username": body.username}

    auth.set_must_change(user["id"], weak)
    if weak:
        log.warning("weak password in use: user=%r (%s)", body.username, weak)
    user = auth.get_user_by_id(user["id"])
    token = auth.create_session(user["id"], ip,
                                request.headers.get("user-agent", ""))
    _set_session_cookie(response, token, request)
    return auth.public_user(**user)


class SecondFactorBody(BaseModel):
    ticket: str
    code: str


@app.post("/api/auth/login/2fa")
def login_second_factor(body: SecondFactorBody, request: Request,
                        response: Response):
    """Second step: the six-digit code, or one of the recovery codes.

    Wrong codes are recorded and throttled exactly like wrong passwords —
    otherwise this endpoint would be a way to brute-force six digits with the
    login form's protections switched off. A ticket also dies after a handful
    of tries, so a stolen one cannot be worked on at leisure.
    """
    ip = security.client_ip(request, app_settings.get().get("trust_proxy", False))
    t = _ticket(body.ticket)
    if not t:
        raise HTTPException(status_code=400, detail="驗證階段已逾時，請重新登入")
    name = t["username"]
    conn = db.connect()
    try:
        wait, why = security.throttle_reason(conn, name, ip)
        if wait:
            security.record_attempt(conn, name, ip, False,
                                    f"{security.BLOCKED}（{why}，還有 {wait} 秒）")
            raise HTTPException(status_code=429,
                                detail=f"嘗試次數過多，請在 {wait} 秒後再試")
        user = auth.get_user_by_id(t["uid"])
        if not user or user.get("disabled"):
            raise HTTPException(status_code=403, detail="這個帳號已停用")

        ok, step = twofactor.verify(user.get("totp_secret") or "", body.code,
                                    user.get("totp_last") or 0)
        used_recovery = False
        if ok:
            # Storing the step is what stops the same code being replayed
            # inside its 30 seconds — see twofactor.verify.
            auth.set_totp(user["id"], last=step)
        else:
            used_recovery, left = twofactor.use_recovery(
                user.get("recovery_codes"), body.code)
            if used_recovery:
                auth.set_totp(user["id"], recovery=left)

        if not (ok or used_recovery):
            t["tries"] += 1
            security.record_attempt(conn, name, ip, False, "兩步驟驗證碼錯誤")
            if t["tries"] >= _TICKET_TRIES:
                _TICKETS.pop(body.ticket, None)
                raise HTTPException(status_code=400,
                                    detail="驗證碼錯誤次數過多，請重新登入")
            raise HTTPException(status_code=400, detail="驗證碼不正確")

        security.record_attempt(conn, name, ip, True,
                                "備用碼" if used_recovery else "")
    finally:
        conn.close()

    _TICKETS.pop(body.ticket, None)
    auth.set_must_change(user["id"], t.get("weak"))
    user = auth.get_user_by_id(user["id"])
    token = auth.create_session(user["id"], ip,
                                request.headers.get("user-agent", ""))
    _set_session_cookie(response, token, request)
    out = auth.public_user(**user)
    if used_recovery:
        out["recovery_used"] = True
        out["recovery_left"] = twofactor.recovery_left(user.get("recovery_codes"))
    return out


# ── two-factor, the account's own ───────────────────────────────────────────

class TotpPasswordBody(BaseModel):
    password: str


class TotpCodeBody(BaseModel):
    code: str
    password: Optional[str] = None


def _check_own_password(user: dict, password: str):
    row = auth.get_user_by_name(user["username"])
    if not row or not auth.verify_password(row["password_hash"], password or ""):
        raise HTTPException(status_code=400, detail="密碼不正確")


@app.get("/api/auth/2fa")
def twofa_status(user: dict = Depends(auth.current_user)):
    full = auth.get_user_by_id(user["id"])
    return {"enabled": bool(full.get("totp_secret")),
            "required": auth.twofa_required(full),
            "recovery_left": twofactor.recovery_left(full.get("recovery_codes"))}


@app.post("/api/auth/2fa/setup")
def twofa_setup(body: TotpPasswordBody, user: dict = Depends(auth.current_user)):
    """Show a new secret. The password is required: a borrowed open session
    must not be enough to bolt an attacker's own authenticator onto the
    account."""
    _check_own_password(user, body.password)
    secret = twofactor.new_secret()
    auth.set_totp(user["id"], pending=secret)
    uri = twofactor.otpauth_uri(secret, user["username"])
    return {"secret": secret, "uri": uri, "svg": twofactor.qr_svg(uri)}


@app.post("/api/auth/2fa/enable")
def twofa_enable(body: TotpCodeBody, user: dict = Depends(auth.current_user)):
    """Prove the app is set up, then switch it on and hand out recovery codes."""
    full = auth.get_user_by_id(user["id"])
    pending = full.get("totp_pending")
    if not pending:
        raise HTTPException(status_code=400, detail="請先開始設定")
    ok, step = twofactor.verify(pending, body.code, 0)
    if not ok:
        raise HTTPException(status_code=400,
                            detail="驗證碼不正確，請確認手機時間是否準確")
    codes, stored = twofactor.new_recovery()
    auth.set_totp(user["id"], secret=pending, pending=None, last=step,
                  recovery=stored)
    log.info("2FA enabled: user=%r", user["username"])
    _twofa_alert(user["username"], "已啟用兩步驟驗證")
    return {"ok": True, "recovery_codes": codes}


@app.post("/api/auth/2fa/disable")
def twofa_disable(body: TotpCodeBody, user: dict = Depends(auth.current_user)):
    full = auth.get_user_by_id(user["id"])
    if auth.twofa_required(full):
        raise HTTPException(
            status_code=403,
            detail="這個帳號有編輯權限，必須保持兩步驟驗證；請先請管理員調整權限組")
    if not full.get("totp_secret"):
        raise HTTPException(status_code=400, detail="這個帳號沒有啟用兩步驟驗證")
    _check_own_password(user, body.password or "")
    ok, _ = twofactor.verify(full["totp_secret"], body.code,
                             full.get("totp_last") or 0)
    if not ok:
        raise HTTPException(status_code=400, detail="驗證碼不正確")
    auth.set_totp(user["id"], secret=None, pending=None, last=0, recovery=None)
    log.info("2FA disabled by its owner: user=%r", user["username"])
    _twofa_alert(user["username"], "自行關閉了兩步驟驗證")
    return {"ok": True}


@app.post("/api/auth/2fa/recovery")
def twofa_new_recovery(body: TotpPasswordBody,
                       user: dict = Depends(auth.current_user)):
    """Replace the recovery codes — the old ones stop working immediately."""
    full = auth.get_user_by_id(user["id"])
    if not full.get("totp_secret"):
        raise HTTPException(status_code=400, detail="這個帳號沒有啟用兩步驟驗證")
    _check_own_password(user, body.password)
    codes, stored = twofactor.new_recovery()
    auth.set_totp(user["id"], recovery=stored)
    return {"ok": True, "recovery_codes": codes}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        auth.destroy_session(token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@app.get("/api/auth/me")
def me(request: Request, user: dict = Depends(auth.current_user)):
    # Cheap enough here (once per page load) and it keeps the device list
    # honest; auth.touch_session writes at most once a minute anyway.
    token = request.cookies.get(SESSION_COOKIE, "")
    if token:
        ip = security.client_ip(request,
                                app_settings.get_cached().get("trust_proxy", False))
        auth.touch_session(token, ip)
    return auth.public_user(**user)


class OwnPasswordBody(BaseModel):
    current_password: str
    new_password: str


@app.post("/api/auth/password")
def change_own_password(body: OwnPasswordBody, request: Request,
                        response: Response,
                        user: dict = Depends(auth.current_user)):
    """Any signed-in account changes its own password.

    The current password is required even though the caller is signed in: a
    session left open on someone else's screen must not be enough to take the
    account over. A wrong one is recorded and throttled exactly like a failed
    login, under the same (username, ip) pair, so this cannot become a way to
    guess passwords past the login throttle. Success is not recorded as a
    login — it is not one, and it would clear the failure count.

    Changing the password ends every session of the account, this one
    included (auth.update_user does that), so the cookie is dropped too.
    """
    ip = security.client_ip(request, app_settings.get().get("trust_proxy", False))
    name = user["username"]
    conn = db.connect()
    try:
        wait = security.throttle_check(conn, name, ip)
        if wait:
            security.record_attempt(conn, name, ip, False,
                                    f"{security.BLOCKED}（修改密碼，還有 {wait} 秒）")
            raise HTTPException(status_code=429,
                                detail=f"嘗試次數過多，請在 {wait} 秒後再試")
        row = auth.get_user_by_name(name)
        if not row or not auth.verify_password(row["password_hash"],
                                               body.current_password):
            security.record_attempt(conn, name, ip, False, "修改密碼：目前密碼錯誤")
            raise HTTPException(status_code=400, detail="目前的密碼不正確")
    finally:
        conn.close()

    problem = security.password_problem(body.new_password, name)
    if problem:
        raise HTTPException(status_code=400, detail=problem)
    if body.new_password == body.current_password:
        raise HTTPException(status_code=400, detail="新密碼不能和目前的密碼相同")

    auth.update_user(row["id"], password=body.new_password)
    auth.set_must_change(row["id"], None)
    log.info("password changed by its owner: user=%r ip=%s", name, ip)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


# ── user management (admin only) ─────────────────────────────────────────────

class NewUserBody(BaseModel):
    username: str
    password: str
    role: str = "viewer"                    # viewer | editor | admin
    allowed_roots: Optional[list] = None    # None/[] = every configured root
    allowed_ips: Optional[list] = None      # None/[] = from anywhere


@app.get("/api/groups")
def get_groups(user: dict = Depends(auth.current_user)):
    """Sidebar grouping — readable by everyone, editable by editors."""
    s = app_settings.get()
    return {"groups": s.get("sidebar_groups", {}),
            "order": s.get("sidebar_group_order", []),
            "view": s.get("sidebar_group_view", {})}


class GroupsBody(BaseModel):
    groups: dict
    order: list
    # Absent means "leave the stored view settings alone", which is what the
    # sidebar's 設定分類 sends — it only ever moves a character between groups.
    view: Optional[dict] = None


# What a group looks like when nothing has been chosen for it. `未分類` is a
# section the frontend derives rather than a stored group, so it can never
# appear here and always renders with exactly these.
GROUP_VIEW_DEFAULT = {"videos": True, "profile": True,
                      "ratio": "16:9", "poster": False, "home": "circle",
                      # 個人資料 split into one album per subfolder. Off by
                      # default: photo-set folders (Social Media) want it,
                      # one-film-per-folder JAV characters do not.
                      "albums": False}
# 16:9 is the frame's own shape; 4:5 matches the photo grid on the profile tab;
# 9:16 crops nothing off a phone video. Anything else is refused rather than
# passed through to `aspect-ratio`, where a junk value silently collapses the
# card to zero height.
GROUP_RATIOS = ("16:9", "4:5", "9:16")
# Home-grid card shape. `circle` is the original round avatar tile and stays
# the default. The three rectangles also switch on the folder-cover fallback —
# that is one setting, not two, because a round tile is small and neutral
# enough that the person icon reads fine in it, while an empty rectangle does
# not (89% of characters have no 大頭照).
GROUP_HOME_SHAPES = ("circle",) + GROUP_RATIOS


def _clean_group_view(raw: dict, order: list) -> dict:
    """Keep only real groups, real keys, and *only what differs from default*.

    Storing just the deviations is the same trick `groups.js` uses for which
    sections are open: a settings file written before this feature existed has
    no key at all, and a group added later inherits the defaults instead of
    behaving oddly because it is missing from a saved list. No migration.
    """
    out = {}
    for g in order:
        v = raw.get(g)
        if not isinstance(v, dict):
            continue
        cur = {"videos": bool(v.get("videos", True)),
               "profile": bool(v.get("profile", True)),
               "ratio": v.get("ratio") if v.get("ratio") in GROUP_RATIOS else "16:9",
               "poster": bool(v.get("poster", False)),
               "home": (v.get("home") if v.get("home") in GROUP_HOME_SHAPES
                        else "circle"),
               "albums": bool(v.get("albums", False))}
        # Hiding both tabs leaves a character page with nothing on it and no
        # way back to the other tab. The dialog blocks it; this is the second
        # line, because the endpoint is reachable without the dialog.
        if not cur["videos"] and not cur["profile"]:
            cur["videos"] = True
        diff = {k: val for k, val in cur.items() if GROUP_VIEW_DEFAULT[k] != val}
        if diff:
            out[g] = diff
    return out


@app.post("/api/groups")
def save_groups(body: GroupsBody, user: dict = Depends(auth.require_perm("groups"))):
    # keep only groups that exist in `order`, and drop empty names
    order = [g.strip() for g in body.order if str(g).strip()]
    groups = {g: [m for m in body.groups.get(g, []) if str(m).strip()]
              for g in order}
    saved = {"sidebar_groups": groups, "sidebar_group_order": order}
    if body.view is None:
        # A rename or delete still has to carry the settings along, or moving a
        # character from the sidebar would quietly reset every group's view.
        view = _clean_group_view(app_settings.get().get("sidebar_group_view", {}),
                                 order)
    else:
        view = _clean_group_view(body.view, order)
    saved["sidebar_group_view"] = view
    app_settings.save(saved)
    return {"groups": groups, "order": order, "view": view}


@app.get("/api/settings")
def get_settings(admin: dict = Depends(auth.require_admin)):
    # cpu_count is not a setting, but the parallelism field needs an upper
    # bound the user can see rather than a number pulled out of the air
    return {**app_settings.get(), "cpu_count": os.cpu_count() or 0}


class BackupBody(BaseModel):
    pass


def _backup_once(reason: str) -> dict:
    """One backup run, with the outcome written back into settings.

    The result is stored rather than logged only: a backup that has been
    silently failing for three weeks is worse than no backup, because you
    believe you have one. The settings page reads these two fields.
    """
    cfg = app_settings.get()
    if not (cfg.get("backup_dir") or "").strip():
        # Not a failed backup, just an unfinished setup. Recording it made the
        # page say "上次失敗" before a single backup had ever been attempted.
        raise RuntimeError("請先指定備份資料夾")
    try:
        info = backup.run(cfg.get("backup_dir", ""), cfg.get("backup_keep", 14))
    except Exception as e:                       # noqa: BLE001 - reported, not raised
        log.warning("backup (%s) failed: %s", reason, e)
        app_settings.save({"backup_last_error": str(e)})
        raise
    app_settings.save({"backup_last_at": info["at"], "backup_last_error": ""})
    return info


def _backup_loop():
    """Runs one backup a day, and one at startup if the last is older than that.

    A thread rather than a scheduler: this machine is a desktop that gets
    rebooted, so "catch up when we start" matters more than an exact hour.
    """
    while True:
        try:
            cfg = app_settings.get_cached(30.0)
            if cfg.get("backup_enabled") and cfg.get("backup_dir"):
                if backup.due(cfg.get("backup_last_at")):
                    _backup_once("排程")
        except Exception:                        # noqa: BLE001 - never kill the thread
            pass
        time.sleep(600)


@app.get("/api/backup")
def backup_status(admin: dict = Depends(auth.require_admin)):
    cfg = app_settings.get()
    dest = cfg.get("backup_dir", "")
    return {
        "enabled": bool(cfg.get("backup_enabled")),
        "dir": dest,
        "keep": cfg.get("backup_keep", backup.KEEP_DEFAULT),
        "last_at": cfg.get("backup_last_at", ""),
        "last_error": cfg.get("backup_last_error", ""),
        "same_drive": bool(dest) and backup.same_drive_as_data(dest),
        "data_drive": os.path.splitdrive(str(DATA_DIR.resolve()))[0],
        "runs": backup.existing(dest) if dest else [],
        "sources": [name for name, _ in backup.SOURCES],
    }


@app.post("/api/backup/run")
def backup_now(admin: dict = Depends(auth.require_admin)):
    """Back up now. Also the only way to find out a destination is unusable."""
    try:
        return {"ok": True, **_backup_once("手動")}
    except Exception as e:                       # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"備份失敗：{e}")


class SettingsBody(BaseModel):
    inbox_paths: Optional[list] = None
    archive_roots: Optional[list] = None
    default_archive_root: Optional[str] = None
    default_browse_tab: Optional[str] = None
    seek_seconds: Optional[int] = None
    sheet_interval: Optional[int] = None
    sheet_tile_width: Optional[int] = None
    sheet_workers: Optional[int] = None
    sheet_hwaccel: Optional[str] = None
    thumb_limit_gb: Optional[float] = None
    sheet_limit_gb: Optional[float] = None
    photo_limit_gb: Optional[float] = None
    trust_proxy: Optional[bool] = None
    lan_only: Optional[bool] = None
    backup_enabled: Optional[bool] = None
    backup_dir: Optional[str] = None
    backup_keep: Optional[int] = None


@app.post("/api/settings")
def update_settings(body: SettingsBody, admin: dict = Depends(auth.require_admin)):
    patch = {k: v for k, v in body.dict().items() if v is not None}
    if "default_browse_tab" in patch and patch["default_browse_tab"] not in ("videos", "profile"):
        raise HTTPException(status_code=400, detail="invalid default_browse_tab")
    if "seek_seconds" in patch:
        patch["seek_seconds"] = max(1, min(300, int(patch["seek_seconds"])))
    if "sheet_tile_width" in patch:
        patch["sheet_tile_width"] = max(240, min(1200, int(patch["sheet_tile_width"])))
    for key in ("thumb_limit_gb", "sheet_limit_gb", "photo_limit_gb"):
        if key in patch:
            # below ~0.1 GB the cache evicts faster than it fills
            patch[key] = max(0.1, min(200.0, float(patch[key])))
    if "sheet_workers" in patch:
        # more workers than cores just adds context switching
        patch["sheet_workers"] = max(1, min(os.cpu_count() or 12,
                                            int(patch["sheet_workers"])))
    if "sheet_hwaccel" in patch and patch["sheet_hwaccel"] not in ("cpu", "gpu"):
        raise HTTPException(status_code=400, detail="invalid sheet_hwaccel")
    if "backup_keep" in patch:
        patch["backup_keep"] = max(backup.KEEP_MIN,
                                   min(backup.KEEP_MAX, int(patch["backup_keep"])))
    if patch.get("backup_dir"):
        # Checked here rather than at the next scheduled run: a path that
        # cannot be written to must fail while the person who typed it is
        # still looking at it.
        problem = backup.problem_with(patch["backup_dir"])
        if problem:
            raise HTTPException(status_code=400, detail=problem)
    if ("backup_dir" in patch
            and patch["backup_dir"] != app_settings.get().get("backup_dir")):
        # A failure recorded against the old folder says nothing about the
        # new one; leaving it would greet a fresh setup with "上次失敗".
        patch["backup_last_error"] = ""
    if "sheet_interval" in patch:
        # below ~5 s a two-hour film needs hundreds of sheets, which is not a
        # faster way to skim anything
        patch["sheet_interval"] = max(5, min(600, int(patch["sheet_interval"])))
    return app_settings.save(patch)


@app.get("/api/users")
def list_users(admin: dict = Depends(auth.require_admin)):
    users = auth.list_users()
    for u in users:
        # normalise NULL / bad JSON into a plain list for the UI
        u["allowed_roots"] = app_settings.user_root_strings(u) or []
    return {"users": users,
            "roots": [str(r) for r in app_settings.allowed_roots()],
            "inbox": [str(r) for r in app_settings.inbox_paths()]}


@app.post("/api/users")
def add_user(body: NewUserBody, request: Request,
             admin: dict = Depends(auth.require_admin)):
    if not roles.exists(body.role):
        raise HTTPException(status_code=400, detail="沒有這個權限組")
    if auth.get_user_by_name(body.username):
        raise HTTPException(status_code=409, detail="username taken")
    problem = security.password_problem(body.password, body.username)
    if problem:
        raise HTTPException(status_code=400, detail=problem)
    uid = auth.create_user(body.username, body.password, body.role)
    if body.allowed_roots:
        auth.update_user(uid, allowed_roots=_checked_roots(body.allowed_roots))
    if body.allowed_ips:
        auth.update_user(uid, allowed_ips=_checked_ips(body.allowed_ips, request,
                                                       target_is_self=False))
    return {"id": uid, "username": body.username, "role": body.role}


class EditUserBody(BaseModel):
    role: Optional[str] = None
    password: Optional[str] = None
    disabled: Optional[bool] = None         # True = keep the row, refuse sign-in
    reset_2fa: Optional[bool] = None        # clear two-factor (lost phone)
    allowed_roots: Optional[list] = None    # [] clears the restriction
    allowed_ips: Optional[list] = None      # [] clears the restriction
    set_ips: bool = False                  # mirrors set_roots
    set_roots: bool = False                 # only touch scope when asked to


def _checked_ips(items, request: Request, target_is_self: bool) -> list:
    """Normalise an IP allowlist, refusing one that would lock the admin out.

    The guard only applies when an admin is restricting *their own* account:
    saving a list that does not include the address they are sitting at would
    take effect on the very next request, and the only way back would be to
    edit the database by hand. Restricting someone else is a normal thing to
    do, so it is left alone.
    """
    clean, problem = security.validate_allowlist(items)
    if problem:
        raise HTTPException(status_code=400, detail=problem)
    if clean and target_is_self:
        here = security.client_ip(request,
                                  app_settings.get().get("trust_proxy", False))
        if not security.ip_allowed(here, clean):
            raise HTTPException(
                status_code=400,
                detail=f"這份清單不包含你目前的位址（{here}），"
                       f"儲存後你會立刻被鎖在外面")
    return clean


def _checked_roots(paths: list) -> list:
    """Keep only values that are actually configured roots — a user must never
    be granted a folder that Settings does not list.

    An empty input means "no restriction". An input that asked for a
    restriction but survives validation as empty is rejected rather than
    silently granting everything.
    """
    configured = {str(r) for r in app_settings.allowed_roots()}
    out = []
    for raw in paths or []:
        try:
            rp = str(Path(str(raw)).resolve())
        except OSError:
            continue
        if rp in configured:
            out.append(rp)
    if paths and not out:
        raise HTTPException(
            status_code=400,
            detail="指定的資料夾都不在設定的根目錄清單中，無法套用限制")
    return out


@app.patch("/api/users/{uid}")
def edit_user(uid: int, body: EditUserBody, request: Request,
              admin: dict = Depends(auth.require_admin)):
    target = auth.get_user_by_id(uid)
    if not target:
        raise HTTPException(status_code=404, detail="user not found")
    if body.role is not None:
        if not roles.exists(body.role):
            raise HTTPException(status_code=400, detail="沒有這個權限組")
        # Losing the last admin would lock everyone out of settings for good.
        if (target["role"] == "admin" and body.role != "admin"
                and auth.count_admins() <= 1):
            raise HTTPException(status_code=400, detail="必須至少保留一個管理員")
    if body.disabled:
        # Same two guards as deleting: an admin must not be able to lock
        # themselves — or the last way in — out of the service.
        if target["id"] == admin["id"]:
            raise HTTPException(status_code=400, detail="不能停用自己的帳號")
        if target["role"] == "admin" and auth.count_admins() <= 1:
            raise HTTPException(status_code=400, detail="必須至少保留一個可用的管理員")
    if body.password is not None:
        problem = security.password_problem(body.password, target["username"])
        if problem:
            raise HTTPException(status_code=400, detail=problem)
    kw = {}
    if body.set_roots:
        kw["allowed_roots"] = _checked_roots(body.allowed_roots)
    if body.set_ips:
        kw["allowed_ips"] = _checked_ips(body.allowed_ips, request,
                                         target_is_self=target["id"] == admin["id"])
    if body.reset_2fa:
        # The lost-phone path. It hands the account back to whoever knows the
        # password, so it is admin-only and it is logged; a required role then
        # has to set two-factor up again before it can do anything else.
        auth.set_totp(uid, secret=None, pending=None, last=0, recovery=None)
        log.warning("2FA reset by admin: user=%r by=%r",
                    target["username"], admin["username"])
        _twofa_alert(target["username"],
                     f"兩步驟驗證被 {admin['username']} 重設")
    auth.update_user(uid, role=body.role, password=body.password,
                     disabled=body.disabled, **kw)
    return {"ok": True, **auth.public_user(**auth.get_user_by_id(uid))}


def _twofa_alert(username: str, detail: str):
    """Two-factor coming or going is exactly what an account takeover looks
    like, so it goes on the admin's list either way."""
    conn = db.connect()
    try:
        alerts.raise_alert(conn, alerts.TWOFA, username, "", detail)
    finally:
        conn.close()


def _raise_login_alerts(conn, username: str, ip: str, exists: bool):
    """Turn a failed attempt into an alert when it looks like more than a typo."""
    if not exists:
        alerts.raise_alert(conn, alerts.UNKNOWN_USER, username, ip,
                           "這個帳號不存在 —— 有人在猜帳號名稱")
    fails = security.recent_failures(conn, username, ip)
    if fails == security.FREE_TRIES:
        # Exactly at the threshold: the moment the lock closes, once.
        alerts.raise_alert(conn, alerts.LOCKED, username, ip,
                           f"連續 {fails} 次失敗，已開始鎖定")
    n_ip, _ = security._failures_since(conn, "ip", ip)
    if n_ip >= security.IP_FREE_TRIES:
        alerts.raise_alert(conn, alerts.SPRAY, "", ip,
                           f"這個位址 15 分鐘內失敗 {n_ip} 次")


# ── this account's own sessions (E: 登入中的裝置) ───────────────────────────

@app.get("/api/auth/sessions")
def my_sessions(request: Request, user: dict = Depends(auth.current_user)):
    """Where this account is signed in — one row per device, mine marked.

    The point is the lost phone: sign that one out without changing the
    password and without disturbing anything else.
    """
    token = request.cookies.get(SESSION_COOKIE, "")
    out = []
    for row in auth.list_sessions(user["id"], token):
        out.append({**row, "device": alerts.describe_agent(row["agent"])})
    return {"sessions": out}


@app.delete("/api/auth/sessions/{sid}")
def revoke_session(sid: str, request: Request,
                   user: dict = Depends(auth.current_user)):
    if not auth.destroy_session_id(user["id"], sid):
        raise HTTPException(status_code=404, detail="找不到這個登入")
    log.info("session revoked by its owner: user=%r", user["username"])
    return {"ok": True}


# ── security alerts (5: 異常通知) ───────────────────────────────────────────

@app.get("/api/security/alerts")
def security_alerts(limit: int = 50, admin: dict = Depends(auth.require_admin)):
    conn = db.connect()
    try:
        return {"alerts": alerts.recent(conn, limit),
                "unseen": alerts.unseen_count(conn, admin["id"])}
    finally:
        conn.close()


@app.post("/api/security/alerts/seen")
def security_alerts_seen(admin: dict = Depends(auth.require_admin)):
    conn = db.connect()
    try:
        return {"ok": True, "last_id": alerts.mark_seen(conn, admin["id"])}
    finally:
        conn.close()


# ── permission groups (admin only) ──────────────────────────────────────────

class RoleBody(BaseModel):
    name: Optional[str] = None
    perms: Optional[list] = None


@app.get("/api/roles")
def list_roles(user: dict = Depends(auth.current_user)):
    """Every group and the permission catalogue.

    Readable by any signed-in account: the names are shown next to your own
    account, and the catalogue is what the settings page renders. Only admins
    can change anything (below).
    """
    return {"roles": [{"key": roles.ADMIN, "name": "管理員",
                       "perms": list(roles.PERM_KEYS), "builtin": True,
                       "fixed": True}] + roles.all_roles(),
            "catalogue": roles.catalogue()}


@app.post("/api/roles")
def create_role(body: RoleBody, admin: dict = Depends(auth.require_admin)):
    try:
        r = roles.create(body.name or "", body.perms or [])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    log.info("role created: %s %s by=%r", r["name"], r["perms"], admin["username"])
    return r


@app.patch("/api/roles/{key}")
def edit_role(key: str, body: RoleBody, admin: dict = Depends(auth.require_admin)):
    try:
        r = roles.update(key, name=body.name, perms=body.perms)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    log.info("role changed: %s %s by=%r", r["name"], r["perms"], admin["username"])
    return r


@app.delete("/api/roles/{key}")
def remove_role(key: str, admin: dict = Depends(auth.require_admin)):
    try:
        roles.delete(key)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@app.get("/api/security/logins")
def login_history(limit: int = 200, admin: dict = Depends(auth.require_admin)):
    """Recent sign-in attempts, newest first.

    Admin only, and it deliberately shows the username as typed — a run of
    failures against a name that does not exist is the clearest sign that
    someone is guessing rather than that you mistyped your own.
    """
    limit = max(1, min(1000, int(limit)))
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT at, username, ip, ok, reason FROM login_events "
            "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        fails = conn.execute(
            "SELECT COUNT(*) FROM login_events WHERE ok = 0 AND at > ?",
            (since,)).fetchone()[0]
    finally:
        conn.close()
    return {"events": [dict(r) for r in rows], "failed_7d": fails,
            "policy": security.POLICY_TEXT}


@app.delete("/api/users/{uid}")
def remove_user(uid: int, admin: dict = Depends(auth.require_admin)):
    target = auth.get_user_by_id(uid)
    if not target:
        raise HTTPException(status_code=404, detail="user not found")
    if target["id"] == admin["id"]:
        raise HTTPException(status_code=400, detail="不能刪除自己的帳號")
    if target["role"] == "admin" and auth.count_admins() <= 1:
        raise HTTPException(status_code=400, detail="必須至少保留一個管理員")
    auth.delete_user(uid)
    return {"ok": True, "deleted": uid}


# ── media routes (require login) ─────────────────────────────────────────────

@app.get("/api/health")
def health():
    # unauthenticated: must not leak where the media lives
    return {"status": "ok"}


@app.get("/api/config")
def public_config(user: dict = Depends(auth.current_user)):
    """Non-sensitive settings any logged-in user needs."""
    s = app_settings.get()
    return {"default_browse_tab": s["default_browse_tab"],
            "seek_seconds": s.get("seek_seconds", 10),
            "sheet_interval": s.get("sheet_interval", 60),
            "sheet_tile_width": s.get("sheet_tile_width", 480),
            "cache_limit_gb": s.get("cache_limit_gb", 2.0),
            "has_inbox": bool(app_settings.inbox_paths_for(user))}


def _attach_marks(item: dict, marks: dict, others: Optional[dict] = None):
    """Add `watched`/`fav`/`fav_others` to one listing row.

    Always present, never omitted when false: the frontend filters on these,
    and a missing key and a false one would have to be handled identically
    anyway -- so say it once here rather than at every reader.
    `fav_others` is a count, not names (see videoflags.fav_count).
    """
    k = videoflags.key(item["path"])
    m = marks.get(k)
    item["watched"] = bool(m and m["watched"])
    item["fav"] = bool(m and m["fav"])
    item["fav_others"] = int((others or {}).get(k, 0))


@app.get("/api/videos")
def list_videos(root: Optional[str] = Query(None),
                scope: Optional[str] = Query(None),   # "inbox" = 待分類資料夾
                merged: Optional[str] = Query(None),  # merge-group base name
                user: dict = Depends(auth.current_user)):
    per_char = False
    # One query for the whole listing, then a dict lookup per file -- the same
    # shape as the title map below, and for the same reason.
    marks = videoflags.flags_for(user["id"])
    others = videoflags.others_fav_counts(user["id"])
    if scope == "inbox":
        # grouped by first-level subfolder; loose files have group=None
        items = media.scan_inbox_grouped(app_settings.inbox_paths_for(user))
        for it in items:
            _attach_marks(it, marks, others)
        return {"count": len(items), "videos": items}
    elif merged:
        # same-name folders spread across several archive roots
        scan_roots = [Path(p) for c in media.list_characters(app_settings.archive_roots_for(user))
                      if c["name"] == merged for p in c["paths"]]
        if not scan_roots:
            raise HTTPException(status_code=404, detail="merge group not found")
        per_char = True
    elif root:
        base = Path(root)
        if not is_allowed(base, user):
            raise HTTPException(status_code=403, detail="root not allowed")
        scan_roots = [base]
        per_char = True
    else:
        scan_roots = app_settings.archive_roots_for(user)

    # one dict lookup per file; titles are keyed by catalogue number so every
    # copy of the same film gets one without any per-file bookkeeping
    known = javdb.titles()
    # Same shape for the release date. It answers a question the file's mtime
    # cannot: mtime is when this copy arrived, not when the film came out.
    when = javdb.released()
    # Everything without a catalogue number -- 95% of the library -- keys its
    # title off the path instead. One dict for the whole listing, same as above.
    typed = filetitle.all_titles()
    # Same for the release date. An IAFD import writes years here because those
    # films have no catalogue number to key `movie_title.released` off.
    typed_when = filetitle.all_released()
    # Which films this one is cut from, compiled into, or holds (AVBase's
    # relations): part of knowing what a film is, so the card can say so.
    related = avbase.relations_by_work()
    items = []
    # covers seen on the same walk, so the card's URL can change when one
    # appears (media.scan_videos_with_stat)
    posters: dict = {}
    for base in scan_roots:
        for path, size, mtime in media.scan_videos_with_stat(base, posters):
            # when browsing one character folder, exclude its IG videos
            # (those belong to the profile tab)
            if per_char and media.is_under_ig(path, base):
                continue
            vid = media.detect_video_id(path.name)
            it = {"path": str(path), "name": path.name,
                  "size": size, "mtime": mtime,
                  "video_id": vid or "",
                  "title": (known.get(vid, "") if vid
                            else typed.get(videoflags.key(path), "")),
                  "released": (when.get(vid, "") if vid
                               else typed_when.get(videoflags.key(path), ""))}
            if vid and vid.upper() in related:
                it["rel"] = related[vid.upper()]
            pm = media.poster_stamp(path, posters)
            if pm:
                it["pm"] = pm
            _attach_marks(it, marks, others)
            items.append(it)
    items.sort(key=lambda x: x["mtime"], reverse=True)
    out = {"count": len(items), "videos": items}

    # When a wiki or AVBase page has been imported for this character, say how
    # much of their output is here — both sources merged into one list
    # (core/catalog.py). Only for a single folder: a merged view spans
    # several and the counts would be about none of them.
    if root and not merged:
        wp = catalog.summary(videoflags.key(Path(root)))
        if wp:
            wp.pop("unnumbered", None)     # the missing list's, not the panel's
            wp.pop("section_of", None)
            # Counted over `items`, which is what the grid shows -- IG
            # subfolder videos are excluded from this tab and belong to the
            # profile page. Counting the folder instead reported 102 files
            # where the screen said 87, and the panel's arithmetic has to
            # reconcile against the number the user can see.
            here = {v["video_id"] for v in items if v["video_id"]}
            listed = {i.upper() for i in wp["ids"]}
            # Distinct catalogue numbers on both sides, not file counts: two
            # copies of one film are one work, and comparing files to works
            # would make the column mean two different things.
            wp["have"] = len(here & listed)
            # Films in this folder the page never listed. Not an error on
            # either side -- this wiki does not catalogue every label.
            wp["extra"] = len(here - listed)
            wp["no_number"] = sum(1 for v in items if not v["video_id"])
            # The fourth category, and the one nobody thinks of: two files of
            # the same film. They are one work in both columns above but two
            # rows in the grid, so without naming them the panel's numbers
            # come up short and look wrong.
            wp["dupes"] = (len(items) - wp["no_number"]) - len(here)
            wp["files"] = len(items)

            # Which section each catalogue number belongs to, built before the
            # id lists are dropped below.
            sec_of = {}
            for sec in wp.get("sections", []):
                for i in sec.get("ids", []):
                    sec_of[i.upper()] = sec["name"]

            # Tag every row so the grid can be filtered down to one of these
            # groups. That is the point of the panel: the three numbers under
            # the total are all "files that need something doing to them", and
            # a number you cannot act on is trivia.
            counts = Counter(v["video_id"] for v in items if v["video_id"])
            for it in items:
                vid = it["video_id"]
                it["wiki_state"] = ("none" if not vid else
                                    "have" if vid in listed else "extra")
                # Both copies are flagged, not just the surplus one: deciding
                # which to keep means seeing them together. So this filter
                # shows more rows than the panel's count, which counts only
                # the extra copies -- the chip says so.
                it["wiki_dupe"] = bool(vid and counts[vid] > 1)
                it["wiki_section"] = sec_of.get(vid, "")
            wp["dupe_files"] = sum(1 for it in items if it["wiki_dupe"])

            for sec in wp.get("sections", []):
                sec["have"] = len({i.upper() for i in sec.get("ids", [])} & here)
                sec.pop("ids", None)     # the UI needs the count, not the list
            out["wiki"] = wp
    return out


@app.get("/api/characters")
def list_characters(user: dict = Depends(auth.current_user)):
    chars = media.list_characters(app_settings.archive_roots_for(user))
    # attach avatar (profile_photo) path when set, for the home grid
    for c in chars:
        folder = Path(c["path"])
        info = media.load_person_info(folder)
        rel = info.get("profile_photo", "")
        c["avatar"] = None
        c["avatar_pos"] = info.get("profile_photo_pos") or {"x": 50, "y": 50, "zoom": 1}
        # free of charge here (person_info is already open) and it lets the
        # sidebar find 星川優子 when you type "Yuko Hoshikawa"
        c["aliases"] = [a for a in info.get("aliases", []) if str(a).strip()]
        # Also free, and it is what makes tags worth having: the home grid can
        # filter on them and the editor can offer every tag already in use,
        # without a second endpoint or a cache to keep in step. Links are
        # dropped -- nobody filters characters by a URL.
        c["tags"] = media.split_tags(info.get("tags"))[0]
        if rel:
            ap = folder / rel
            if ap.exists():
                c["avatar"] = str(ap)
        # Fallback picture for the 89% with no chosen 大頭照. Only looked for
        # when there is no avatar — it can never win, so scanning anyway would
        # be 623 pointless scandirs. Always returned regardless of any group
        # setting: whether to *use* it is the frontend's decision, and making
        # the endpoint's answer depend on group settings would mean a settings
        # change silently invalidating a response nothing re-fetches.
        c["poster"] = None
        if not c["avatar"]:
            fp = media.folder_poster(folder, c["name"])
            if fp is not None:
                c["poster"] = str(fp)
    return {"characters": chars}


@app.get("/api/characters/counts")
def character_counts(user: dict = Depends(auth.current_user)):
    """Per-character video count, total size and newest-video time.

    Cached in `core.charstats` — walking every folder costs seconds on a NAS,
    and the sidebar and home grid both want the same numbers. A `stale` flag in
    the response means a background recount is running and it is worth asking
    again in a moment.
    """
    return charstats.get(app_settings.archive_roots_for(user), scoped=bool(
        app_settings.user_root_strings(user)))


@app.get("/api/profile")
def get_profile(folder: str = Query(...),
                user: dict = Depends(auth.current_user)):
    base = Path(folder)
    if not is_allowed(base, user):
        raise HTTPException(status_code=403, detail="folder not allowed")
    # `album` / `in_set` feed the album view; the flat grid drops `in_set`
    # videos so it stays what it was. See media.scan_profile_albums.
    mediae = [{**r, "path": str(r["path"])} for r in media.scan_profile_albums(base)]
    info = media.load_person_info(base)
    # Same fallback the home grid uses, so the header does not drop back to a
    # person icon on a character whose folder clearly has a picture for it.
    # Kept separate from `profile_photo`: that field is the one the crop editor
    # writes a framing for, and a folder cover has no stored framing.
    poster = None
    if not info.get("profile_photo"):
        fp = media.folder_poster(base, base.name)
        if fp is not None:
            poster = str(fp)
    return {"info": _profile_out(info), "poster": poster,
            "media": mediae,
            "count": sum(1 for r in mediae if not r["in_set"]),
            # None until an editor applies 相似 here; browse mode hides the
            # button in that case rather than offering an untuned default.
            "similar_threshold": (photosimilar.saved(base) or {}).get("threshold")}


def _profile_out(info: dict) -> dict:
    """The wire shape of `person_info.json`: `tags` and `links` as two lists.

    Every endpoint that returns a person_info must go through here. Splitting
    it at one of two call sites and not the other is exactly the bug this
    replaced: the reader and the writer disagreed about the shape, and because
    the frontend assigns a save response straight onto its state, a link came
    back as a tag until the page was reloaded.

    A copy, not the dict itself: the caller may be about to write it to disk,
    and the stored file keeps one combined list. See `media.split_tags`.
    """
    out = dict(info)
    out["tags"], out["links"] = media.split_tags(info.get("tags"))
    return out


class ProfileBody(BaseModel):
    aliases: list = []
    tags: list = []
    # Sent separately by the editor, merged back into `tags` on the way to
    # disk. `None` rather than `[]` as the default so an older client -- or any
    # caller that still posts one combined list -- does not silently wipe every
    # link the character had.
    links: Optional[list] = None
    notes: str = ""
    # the javdatabase block, editable by hand: the data comes from a third
    # party and is sometimes wrong, sometimes absent. None = leave it alone.
    profile: Optional[dict] = None


@app.post("/api/profile")
def save_profile(body: ProfileBody, folder: str = Query(...),
                 user: dict = Depends(auth.require_perm("edit_info"))):
    base = Path(folder)
    if not is_allowed(base, user):
        raise HTTPException(status_code=403, detail="folder not allowed")
    info = media.load_person_info(base)
    info["aliases"] = [a.strip() for a in body.aliases if str(a).strip()]
    if body.links is None:
        # one combined list, the old shape
        info["tags"] = [t.strip() for t in body.tags if str(t).strip()]
    else:
        info["tags"] = media.join_tags(body.tags, body.links)
    info["notes"] = body.notes.strip()
    if body.profile is not None:
        info["profile"] = idolprofile.clean_profile(body.profile)
        if not info["profile"]:
            # cleared by hand — drop the provenance too, or it would claim
            # javdatabase supplied fields that are no longer there
            info.pop("profile", None)
            info.pop("profile_source", None)
    media.save_person_info(base, info)
    # The same shape the GET returns. It used to hand back `info` as stored --
    # one combined list, no `links` key -- and the editor assigns the response
    # straight onto its state, so a freshly saved link reappeared as a tag and
    # the links row emptied. The file on disk was right the whole time; only
    # this reply was wrong, so a reload "fixed" it and made it look intermittent.
    return {"ok": True, "info": _profile_out(info)}


class ProfilePhotoBody(BaseModel):
    photo_path: Optional[str] = None      # set/replace the avatar image
    pos: Optional[dict] = None            # {x, y, zoom} framing from the cropper


@app.post("/api/profile/photo")
def set_profile_photo(body: ProfilePhotoBody, folder: str = Query(...),
                      user: dict = Depends(auth.require_perm("edit_info"))):
    base = Path(folder)
    if not is_allowed(base, user):
        raise HTTPException(status_code=403, detail="not allowed")
    info = media.load_person_info(base)

    if body.photo_path:
        photo = Path(body.photo_path)
        if not is_allowed(photo, user):
            raise HTTPException(status_code=403, detail="not allowed")
        try:
            rel = photo.relative_to(base)
        except ValueError:
            raise HTTPException(status_code=400, detail="photo not inside folder")
        info["profile_photo"] = str(rel).replace("\\", "/")
        # a new image starts from the default framing
        info["profile_photo_pos"] = {"x": 50, "y": 50, "zoom": 1}

    if body.pos:
        def _num(v, lo, hi, default):
            try:
                return max(lo, min(hi, float(v)))
            except (TypeError, ValueError):
                return default
        info["profile_photo_pos"] = {
            "x": _num(body.pos.get("x"), 0, 100, 50),
            "y": _num(body.pos.get("y"), 0, 100, 50),
            "zoom": _num(body.pos.get("zoom"), 1, 5, 1),
        }

    media.save_person_info(base, info)
    return {"ok": True, "profile_photo": info["profile_photo"],
            "profile_photo_pos": info["profile_photo_pos"]}


@app.get("/api/image/thumb")
def image_thumb(path: str = Query(...), user: dict = Depends(auth.current_user)):
    """A downscaled copy for the grids, falling back to the original.

    The grid cell is 280x350; the sources reach 3024x4032, which is ~29x more
    pixels than it can show. Falling back rather than failing matters: an
    unreadable or animated file still has to appear in the grid, and a broken
    image there would look like the photo itself is gone.
    """
    p = _checked_path(path, user)
    if p.suffix.lower() not in media.IMAGE_EXT:
        raise HTTPException(status_code=400, detail="not an image")
    if photothumb.wanted(p):
        t = photothumb.get(p, CACHE_DIR)
        if t:
            cachelimit.touch(t)          # LRU recency; nothing else updates it
            try:
                data = t.read_bytes()    # read now, not during the send — the
            except OSError:              # sweep runs in another thread
                data = None
            if data:
                cachelimit.maybe_enforce(
                    CACHE_DIR, "photos",
                    _limit_bytes(app_settings.get(), "photos"))
                return Response(data, media_type="image/jpeg",
                                headers=_IMG_CACHE_HEADERS)
    return image(path, user)


@app.get("/api/image")
def image(path: str = Query(...), user: dict = Depends(auth.current_user)):
    """Serve an image file at full size (the viewer, the avatar cropper)."""
    p = _checked_path(path, user)
    if p.suffix.lower() not in media.IMAGE_EXT:
        raise HTTPException(status_code=400, detail="not an image")
    # Same reopen-per-chunk streaming as /api/stream, for the same reason: a
    # client that stalls mid-download must not pin the file. Photos reach
    # 27 MB in this library and image de-duplication deletes them, so this is
    # not a theoretical concern — it is the WinError 32 bug wearing a hat.
    st = p.stat()
    return StreamingResponse(
        _chunked_file(p, 0, st.st_size),
        media_type=mimetypes.guess_type(p.name)[0] or "application/octet-stream",
        headers={"Content-Length": str(st.st_size), "Accept-Ranges": "bytes",
                 **_IMG_CACHE_HEADERS, **_file_validators(st)})


# ── duplicate detection (editor only) ────────────────────────────────────────

class DedupBody(BaseModel):
    roots: Optional[list] = None    # list of folder paths; null = all media roots
    tier2: bool = False
    threshold: int = 1


@app.get("/api/scope")
def folder_scope(user: dict = Depends(auth.require_perm("dedup", "convert", "shrink", "person"))):
    """Folders a bulk operation may cover, for the scope picker.

    Shared by the duplicate scan and the converter. Merge-group characters get
    one row per path so the user can pick just the copy on one drive.
    """
    inbox = [{"path": str(p), "name": str(p)} for p in app_settings.inbox_paths_for(user)]
    roots = list(app_settings.archive_roots_for(user))
    chars = []
    for c in media.list_characters(roots):
        for p in c["paths"]:
            root = _owning_root(p, roots)
            chars.append({"name": c["name"], "path": p,
                          "root": str(root) if root else "",
                          "root_label": _root_label(root) if root else "?"})
    chars.sort(key=lambda c: (c["name"].lower(), c["path"]))
    return {"inbox": inbox, "chars": chars}


def _owning_root(path: str, roots: list) -> Optional[Path]:
    """Which configured root a folder came from.

    Decided here rather than by prefix-matching in the browser: the roots
    overlap in ways string comparison gets wrong (I:\\JAV and I:\\Web Cam
    share a drive), and only the server knows the list it actually scanned.
    """
    best = None
    for r in roots:
        rs = os.path.normcase(str(r))
        if os.path.normcase(str(path)).startswith(rs):
            # longest match wins, in case one root sits inside another
            if best is None or len(rs) > len(os.path.normcase(str(best))):
                best = r
    return best


def _root_label(root: Path) -> str:
    """Short, unique-enough name for a root, for the picker's filter chips.

    The last component alone is useless — seven of the roots are called JAV.
    Drive letter plus that component separates them, and a UNC path collapses
    to NAS\\<share folder> instead of \\\\192.168.1.10\\share\\...
    """
    s = str(root)
    name = Path(s).name or s
    if s.startswith("\\\\") or s.startswith("//"):
        return f"NAS\\{name}"
    drive = os.path.splitdrive(s)[0]
    return f"{drive}\\{name}" if drive else name


@app.get("/api/charscope")
def char_scope(user: dict = Depends(auth.require_perm("lookup"))):
    """Characters grouped by sidebar category, for the profile-fill picker.

    Grouped by category rather than by root because that is how the user
    thinks about which people are JAV performers — and the catalogue-number
    detection is only trustworthy for those. Measured: 90% of the JAV
    category has a detectable id, against 2-11% of the social-media ones.
    """
    roots = list(app_settings.archive_roots_for(user))
    cfg = app_settings.get()
    groups = cfg.get("sidebar_groups", {})
    order = cfg.get("sidebar_group_order", [])
    of = {}
    for g in order:
        for n in groups.get(g, []):
            of[n] = g
    # Reading every person_info.json to show "already done" costs 0.2 s for
    # 617 characters — cheap enough to do on every open, and without it the
    # user has no way to tell which ones still need attention.
    dec = idolprofile.decisions()

    # Title progress is a different question from profile progress, and the
    # picker is shared by both dialogs — showing one feature's completion in
    # the other's dialog is worse than showing none. Counted off the cached
    # search index, which already knows every file's title, so this costs a
    # dictionary walk rather than another pass over the library.
    idx = searchindex.get(roots, list(app_settings.inbox_paths_for(user)),
                          scoped=bool(user.get("allowed_roots")))
    tstat: dict = {}
    for row in idx.get("v", []):
        name, char = row[0], row[1]
        if not char or row[3]:
            continue
        if not media.detect_video_id(name):
            continue                    # no catalogue number, no title to want
        have, want = tstat.setdefault(char, [0, 0])
        tstat[char] = [have + (1 if (len(row) > 4 and row[4]) else 0), want + 1]

    def title_state(name: str) -> str:
        have, want = tstat.get(name, [0, 0])
        if not want:
            return "none"               # nothing here a title could attach to
        if have >= want:
            return "done"
        return "partial" if have else "none"

    out = []
    for c in media.list_characters(roots):
        info = media.load_person_info(Path(c["paths"][0]))
        d = dec.get(c["name"]) or {}
        out.append({"name": c["name"], "paths": c["paths"],
                    "group": of.get(c["name"], "未分類"),
                    "state": idolprofile.character_state(c["name"], info, dec),
                    "title_state": title_state(c["name"]),
                    "last": d.get("status", "")})
    out.sort(key=lambda c: c["name"].lower())
    return {"chars": out, "order": order + ["未分類"]}


class IdolScanBody(BaseModel):
    names: list
    # 「也查詢已有的個資與大頭照」: offer replacements for what is already there
    recheck: bool = False


@app.post("/api/idolprofile/scan")
def idolprofile_scan(body: IdolScanBody,
                     user: dict = Depends(auth.require_perm("lookup"))):
    """Build a preview of what would be written. Writes nothing."""
    if not body.names:
        raise HTTPException(status_code=400, detail="請至少選擇一位角色")
    roots = list(app_settings.archive_roots_for(user))
    wanted = set(body.names)
    targets = [(c["name"], [Path(p) for p in c["paths"]])
               for c in media.list_characters(roots) if c["name"] in wanted]
    if not targets:
        raise HTTPException(status_code=404, detail="找不到選取的角色")

    rejected = {k: v.get("rejected", [])
                for k, v in idolprofile.decisions().items()}

    def _task(progress):
        rows = []
        for i, (name, folders) in enumerate(targets, 1):
            progress(f"({i}/{len(targets)}) {name}")
            try:
                row = idolprofile.build_proposal(name, folders, body.recheck)
                # remembered so the picker can say "already looked at this"
                # without re-running the whole vote on every dialog open
                idolprofile.record_scan(
                    name,
                    "ok" if row["ok"] else
                    ("suggest" if row["vote"].get("suggest") else "unresolved"),
                    row["vote"].get("slug", ""))
                row["rejected"] = row["vote"].get("slug", "") in rejected.get(name, [])
                rows.append(row)
            except Exception as e:
                log.exception("profile proposal failed for %s", name)
                rows.append({"name": name, "ok": False,
                             "vote": {"reason": f"查詢失敗：{e}"}})
        ok = sum(1 for r in rows if r.get("ok"))
        log.info("idol profile scan: %d chars, %d resolved", len(rows), ok)
        return rows

    return {"job_id": jobs.start(_task, kind="idolprofile", label="JAV個資查詢",
                                 resume=_resumable("idolprofile_scan", body, user))}


class TitleScanBody(BaseModel):
    names: list
    # 「同時下載封面」: DMM's sleeve saved as <番號>-poster.jpg (core/dmmcover.py)
    covers: bool = False


@app.post("/api/movietitle/scan")
def movietitle_scan(body: TitleScanBody,
                    user: dict = Depends(auth.require_perm("lookup"))):
    """Fetch titles for the catalogue numbers under these characters.

    DMM first, for the original Japanese (core/dmm.py); javdatabase's
    machine-translated English only where DMM has nothing. A film that
    already has a Japanese title — from a wiki page, an NFO or DMM before —
    is not asked again, and a title typed by hand always stays the one shown.

    No preview step, unlike the profile tool: a catalogue number identifies
    exactly one film, so there is no identity to get wrong.
    """
    if not body.names:
        raise HTTPException(status_code=400, detail="請至少選擇一位角色")
    roots = list(app_settings.archive_roots_for(user))
    wanted = set(body.names)
    ids = []
    where: Dict[str, list] = {}          # every copy of each film, for covers
    for c in media.list_characters(roots):
        if c["name"] not in wanted:
            continue
        for p in c["paths"]:
            for path, _sz, _mt in media.scan_videos_with_stat(Path(p)):
                vid = media.detect_video_id(path.name)
                if vid and vid not in ids:
                    ids.append(vid)
                if vid:
                    where.setdefault(vid, []).append(path)
    if not ids:
        raise HTTPException(status_code=404, detail="選取的角色底下沒有可辨識的番號")

    def _task(progress):
        got, missing = [], []
        dmm_new = 0
        dmm_down = ""                     # DMM unreachable: stop asking it this run
        covers = {"new": [], "had": 0, "none": [], "failed": []}
        for i, vid in enumerate(ids, 1):
            progress(f"({i}/{len(ids)}) {vid}")
            row = javdb.title_row(vid)
            source = ""
            hit = None
            if (not row.get("ja") and not dmm_down and dmm.askable(vid)
                    and not dmm.recently_missed(vid)):
                try:
                    hit = dmm.lookup(vid)
                    dmm.remember(vid, hit)
                except RuntimeError as e:
                    # not fatal: javdatabase still answers, and one failure
                    # is enough to know the rest of this run would fail too
                    log.warning("DMM lookup %s failed, using javdatabase: %s", vid, e)
                    dmm_down, hit = str(e), None
                if hit and hit["title"]:
                    javdb.set_title(vid, ja=hit["title"])
                    if hit["released"] and not (row.get("released")
                                                or row.get("released_manual")):
                        javdb.set_released({vid: hit["released"]})
                    dmm_new += 1
                    source = "dmm"
            try:
                t = javdb.fetch_title(vid)
            except Exception as e:
                log.info("title lookup %s failed: %s", vid, e)
                t = ""
            if t:
                # where the title *shown* came from: a hand-typed one still
                # wins over the Japanese DMM just gave
                r = javdb.title_row(vid)
                source = ("manual" if r.get("manual") else source
                          or ("ja" if r.get("ja") else "en"))
            (got if t else missing).append({"id": vid, "title": t, "source": source})
            if body.covers:
                dmm_down = _fetch_cover(vid, where.get(vid, []), hit, covers,
                                        ask_dmm=not dmm_down) or dmm_down
        log.info("movie titles: %d ids, %d found, %d new from DMM%s", len(ids),
                 len(got), dmm_new, f" (DMM stopped: {dmm_down})" if dmm_down else "")
        searchindex.invalidate()          # titles are part of the search index
        out = {"found": got, "missing": missing, "total": len(ids),
               "dmm_new": dmm_new, "dmm_error": dmm_down}
        if body.covers:
            log.info("covers: %d new, %d already there, %d not on DMM, %d failed",
                     len(covers["new"]), covers["had"], len(covers["none"]),
                     len(covers["failed"]))
            if covers["new"]:
                _files_changed()
            out["covers"] = covers
        return out

    return {"job_id": jobs.start(_task, kind="movietitle", label="JAV片名查詢",
                                 resume=_resumable("movietitle_scan", body, user))}


def _fetch_cover(vid: str, videos: list, hit: Optional[dict], tally: dict,
                 ask_dmm: bool = True) -> Optional[str]:
    """The sleeve (`-fanart`) and front (`-poster`) for one film, into every
    folder holding it (core/dmmcover.py). Only what is missing is written.

    DMM's catalogue first; for a film it does not list, the pictures
    javdatabase's page links to — DMM's image server under the film's real
    id, or MGS's (`dmmcover.from_javdatabase`). Returns an error string when
    DMM itself could not be reached: the caller stops asking DMM for the rest
    of the run, as it does for titles, but the javdatabase route goes on.
    """
    todo = dmmcover.plan(videos, vid)
    if not todo:
        tally["had"] += 1
        return None
    sleeve = front = b""
    dmm_error = None
    if ask_dmm and dmm.askable(vid):
        try:
            cid = (hit or {}).get("cid") or dmm.remembered_cid(vid)
            if not cid and not dmm.recently_missed(vid):
                # a film whose Japanese title came from a wiki or NFO was never
                # asked, so its DMM id is not known yet
                found = dmm.lookup(vid)
                dmm.remember(vid, found)
                cid = (found or {}).get("cid") or ""
            sleeve_url, front_url = dmm.package_images(cid) if cid else ("", "")
        except RuntimeError as e:
            log.warning("DMM cover %s failed, not asking DMM again this run: %s", vid, e)
            dmm_error, sleeve_url, front_url = str(e), "", ""
        if sleeve_url:
            try:
                sleeve = dmmcover.fetch(sleeve_url)
                front = dmmcover.front(sleeve, front_url)
            except Exception as e:
                log.info("DMM cover %s not usable: %s", vid, e)
                sleeve = front = b""
    via = "dmm"
    if not sleeve and not front:
        via = "javdatabase"
        try:
            sleeve, front = dmmcover.from_javdatabase(vid)
        except Exception as e:
            log.info("javdatabase cover %s failed: %s", vid, e)
    if not sleeve and not front:
        if dmm_error:
            tally["failed"].append({"id": vid, "error": dmm_error})
        else:
            tally["none"].append(vid)
        return dmm_error
    try:
        wrote = dmmcover.save(todo, vid, sleeve, front)
    except Exception as e:
        log.info("cover %s not saved: %s", vid, e)
        tally["failed"].append({"id": vid, "error": str(e)})
        return dmm_error
    if wrote:
        tally["new"].append(vid)
        if via != "dmm":
            tally.setdefault("javdatabase", []).append(vid)
    else:
        tally["had"] += 1
    return dmm_error


class TitleSetBody(BaseModel):
    video_id: str = ""
    path: str = ""
    manual: str = ""


@app.post("/api/movietitle/set")
def movietitle_set(body: TitleSetBody,
                   user: dict = Depends(auth.require_perm("edit_info"))):
    """Type a title by hand. An empty string clears it rather than storing "".

    Two identities, and which one applies is not a preference:

    * a **catalogue number** names the film, so the title covers every copy and
      survives any rename. Beats every fetched variant.
    * a **path** names one file, and is all there is for the 95% of this
      library with no number. The caller sends whichever it has; `video_id`
      wins when both arrive, because it is the better key.

    The response says which identity was used so the caller can update the
    right rows -- one path, or every file sharing a number.
    """
    vid = body.video_id.strip().upper()
    if vid:
        javdb.set_title(vid, manual=body.manual.strip())
        searchindex.invalidate()
        return {"video_id": vid, "path": "",
                "title": javdb.best_title(javdb.title_row(vid))}

    if not body.path.strip():
        raise HTTPException(status_code=400, detail="缺少番號或檔案路徑")
    p = _checked_path(body.path, user)
    title = filetitle.set_title(p, body.manual)
    searchindex.invalidate()
    return {"video_id": "", "path": str(p), "title": title}


class ReleasedSetBody(BaseModel):
    video_id: str = ""
    path: str = ""
    released: str = ""


@app.post("/api/released/set")
def released_set(body: ReleasedSetBody,
                 user: dict = Depends(auth.require_perm("edit_info"))):
    """Type a release date by hand, for any video (2026-10-22).

    Whatever was typed is turned into one shape — `YYYY-MM-DD`, or `YYYY` /
    `YYYY-MM` when that is all that is known — by `releasedate.normalize`;
    anything it cannot read is a 400 saying why. Same two identities as the
    title: a catalogue number covers every copy, a path covers one file. The
    typed date wins over an imported one and an empty string falls back to it.
    """
    try:
        value = releasedate.normalize(body.released)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    vid = body.video_id.strip().upper()
    if vid:
        r = javdb.set_released_manual(vid, value)
        return {"video_id": vid, "path": "", **r}
    if not body.path.strip():
        raise HTTPException(status_code=400, detail="缺少番號或檔案路徑")
    p = _checked_path(body.path, user)
    r = filetitle.set_released_manual(p, value)
    return {"video_id": "", "path": str(p), **r}


# ── NFO (Kodi / Jellyfin / Emby) ─────────────────────────────────────────────
# See core/nfo.py for the format and the merge rules. Both directions preview
# first and write only on the second request (the same two steps as the wiki
# import): reading fills VAM's own tables, writing touches files on the NAS —
# backed up first, and the last batch can be undone.

NFO_BACKUPS = DATA_DIR / "nfo_backups"
_NFO_KEEP = 10            # batches kept for undo; older ones are pruned
_NFO_ROWS = 2000          # rows a preview sends; the counts cover everything


class NfoBody(BaseModel):
    names: list           # characters
    mode: str = "import"  # import (NFO → VAM) | export (VAM → NFO)


def _nfo_scope(names: list, user: dict) -> list:
    """[(video path, character name)] under these characters.

    From the search index rather than a walk: it already holds every video
    with its character, cached, and filtered to this account's roots.
    """
    idx = searchindex.get(list(app_settings.archive_roots_for(user)),
                          list(app_settings.inbox_paths_for(user)),
                          scoped=bool(user.get("allowed_roots")))
    wanted = set(names or [])
    return [(Path(idx["dirs"][r[2]]) / r[0], r[1]) for r in idx["v"]
            if not r[3] and r[1] in wanted]


class _NfoLibrary:
    """What one run needs to know about the library, worked out once."""

    def __init__(self, user: dict):
        self.chars = media.list_characters(app_settings.archive_roots_for(user))
        self.by_name = {c["name"]: c for c in self.chars}
        self.index = performers.index(self.chars)
        self.name_of = {costar.folder_key(p): c["name"]
                        for c in self.chars for p in (c.get("paths") or [c["path"]])}
        self.id_released = javdb.released()
        self.file_released = filetitle.all_released()

    def person(self, name: str) -> Optional[str]:
        """The folder key of the character this name (or alias) belongs to."""
        c = self.index.get(performers.norm(name)) if name else None
        return costar.folder_key(c["path"]) if c else None

    def same_person(self, a: str, b: str) -> bool:
        ka = self.person(a)
        return ka is not None and ka == self.person(b)

    def lead_keys(self, char: str) -> set:
        c = self.by_name.get(char)
        return {costar.folder_key(p) for p in (c.get("paths") or [c["path"]])} if c else set()

    def by_number(self, video: Path, num_in_file: str = "") -> str:
        """The catalogue number to key this film's facts on, or "".

        Only one VAM already knows as a film, or one the file itself names:
        the number detection misfires on webcam names (guoguo-77 → GUOGUO-077),
        and keying those by a number would give every one of them the same
        title.
        """
        vid = media.detect_video_id(video.name)
        if not vid:
            return ""
        if javdb.title_row(vid) or (num_in_file and num_in_file.strip().upper()
                                    == vid.upper()):
            return vid.upper()
        return ""

    def want(self, video: Path, char: str) -> dict:
        """What VAM would write for this video."""
        vid = self.by_number(video)
        row = javdb.title_row(vid) if vid else {}
        title = javdb.best_title(row) if vid else ""
        released = self.id_released.get(vid, "") if vid else ""
        title = title or filetitle.get(video)
        released = released or self.file_released.get(videoflags.key(video), "")
        cast = [char] + [self.name_of[k] for k in costar.for_video(video)
                         if k in self.name_of and self.name_of[k] != char]
        # and the ones marked by name only, unless a name is one already listed
        cast += [n for n in costar.names_for(video)
                 if not any(self.same_person(n, c) or n == c for c in cast)]
        return {"title": title, "originaltitle": (row.get("ja") or "") if vid else "",
                "released": released, "num": vid, "actors": cast}


def _nfo_files(scope: list) -> dict:
    """{video path: its .nfo path} for the videos that have one."""
    by_dir: dict = {}
    for video, _char in scope:
        by_dir.setdefault(video.parent, []).append(video)
    out = {}
    for folder, videos in by_dir.items():
        names = nfo.nfo_names(folder)
        for v in videos:
            hit = names.get(v.stem.lower())
            if hit:
                out[v] = folder / hit
    return out


def _nfo_import_plan(scope: list, lib: _NfoLibrary, progress=None) -> dict:
    """For each video with an NFO: the facts VAM does not have yet.

    Only fills what is empty. A title typed by hand or imported from a wiki
    page outranks a file whichever tool wrote it, so it is never replaced.
    """
    files = _nfo_files(scope)
    rows, errors, unmatched = [], [], {}
    for i, (video, char) in enumerate(scope, 1):
        f = files.get(video)
        if not f:
            continue
        if progress and i % 50 == 0:
            progress(f"讀取 NFO：{i}/{len(scope)}")
        try:
            n = nfo.read(f.read_bytes())
        except (OSError, ValueError) as e:
            errors.append({"path": str(f), "error": str(e)})
            continue
        vid = lib.by_number(video, n["num"])
        fills = []
        # the original-language title when there is one; a bare number or the
        # file's own name is not a title
        t = n["originaltitle"] if nfo.title_lang(n["originaltitle"] or "a") == "ja" \
            else (n["title"] or n["originaltitle"])
        if t and t.upper() not in (vid, video.stem.upper()):
            if vid:
                lang = nfo.title_lang(t)
                if not javdb.title_row(vid).get(lang):
                    fills.append({"field": "title", "lang": lang, "value": t})
            elif not filetitle.get(video):
                fills.append({"field": "title", "lang": "", "value": t})
        try:
            d = releasedate.normalize(n["premiered"] or n["year"])
        except ValueError:
            d = ""
        if d and not (lib.id_released.get(vid) if vid
                      else lib.file_released.get(videoflags.key(video))):
            fills.append({"field": "released", "value": d})
        lead = lib.lead_keys(char)
        have = set(costar.for_video(video)) | set(
            costar.for_video(video, costar.REJECTED))     # turned down stays so
        for name in n["actors"]:
            k = lib.person(name)
            if not k:
                unmatched[name] = unmatched.get(name, 0) + 1
            elif k not in lead and k not in have:
                have.add(k)
                fills.append({"field": "costar", "value": lib.name_of.get(k, name),
                              "folder": k})
        if fills:
            rows.append({"path": str(video), "name": video.name, "char": char,
                         "nfo": str(f), "vid": vid, "fills": fills})
    return {"videos": len(scope), "found": len(files), "rows": rows,
            "errors": errors,
            "unmatched": sorted(({"name": k, "count": v} for k, v in unmatched.items()),
                                key=lambda x: -x["count"])}


def _nfo_import_apply(plan: dict) -> dict:
    titles = released = cast = 0
    for row in plan["rows"]:
        video, vid = Path(row["path"]), row["vid"]
        for f in row["fills"]:
            if f["field"] == "title":
                if vid:
                    javdb.set_title(vid, **{f["lang"]: f["value"]})
                else:
                    filetitle.set_title(video, f["value"])
                titles += 1
            elif f["field"] == "released":
                if vid:
                    javdb.set_released({vid: f["value"]})
                else:
                    filetitle.set_released(video, f["value"])
                released += 1
        add = [f["folder"] for f in row["fills"] if f["field"] == "costar"]
        if add:
            costar.set_for_video(video, costar.for_video(video) + add)
            cast += len(add)
    if titles:
        searchindex.invalidate()          # titles are part of the search index
    return {"titles": titles, "released": released, "costars": cast}


def _nfo_export_plan(scope: list, lib: _NfoLibrary, progress=None) -> dict:
    """For each video: what its NFO would become — new, changed or unchanged."""
    files = _nfo_files(scope)
    items, counts = [], {"new": 0, "changed": 0, "same": 0, "error": 0}
    for i, (video, char) in enumerate(scope, 1):
        if progress and i % 50 == 0:
            progress(f"比對 NFO：{i}/{len(scope)}")
        f = files.get(video) or nfo.nfo_for(video)
        try:
            old = f.read_bytes() if video in files else None
            data, changed = nfo.render(old, lib.want(video, char), lib.same_person)
        except (OSError, ValueError) as e:
            counts["error"] += 1
            items.append({"path": str(video), "nfo": str(f), "status": "error",
                          "error": str(e)})
            continue
        status = "same" if data is None else ("new" if old is None else "changed")
        counts[status] += 1
        if data is not None:
            items.append({"path": str(video), "nfo": str(f), "status": status,
                          "fields": changed, "data": data})
    return {"videos": len(scope), "counts": counts, "items": items}


def _nfo_prune_backups():
    try:
        batches = sorted(p for p in NFO_BACKUPS.iterdir() if p.is_dir())
    except OSError:
        return
    for old in batches[:-_NFO_KEEP]:
        shutil.rmtree(old, ignore_errors=True)


def _nfo_export_apply(plan: dict, user: dict, progress=None) -> dict:
    """Write what the plan says, keeping each file's previous version.

    One line per file in `manifest.jsonl`, written as it goes, so a batch cut
    short by a restart can still be undone as far as it got.
    """
    # a sequence number too: two batches in one second (write, undo, write
    # again) would otherwise share a folder — and the second inherit the
    # first one's "undone" mark
    NFO_BACKUPS.mkdir(parents=True, exist_ok=True)
    stamp, n = time.strftime("%Y%m%d-%H%M%S"), 0
    while True:
        batch = NFO_BACKUPS / f"{stamp}-{n:02d}"
        try:
            batch.mkdir()
            break
        except FileExistsError:
            n += 1
    (batch / "owner").write_text(str(user.get("id")), encoding="utf-8")
    created = updated = 0
    errors = []
    todo = [it for it in plan["items"] if it["status"] in ("new", "changed")]
    with open(batch / "manifest.jsonl", "a", encoding="utf-8") as man:
        for i, it in enumerate(todo, 1):
            video, f = Path(it["path"]), Path(it["nfo"])
            if progress:
                progress(f"寫入 NFO：{i}/{len(todo)} {f.name}")
            try:
                if not video.is_file():
                    raise OSError("影片已經不在了")
                backup = None
                if it["status"] == "changed":
                    backup = f"{i:06d}.nfo"
                    shutil.copyfile(f, batch / backup)
                elif f.exists():
                    raise OSError("預覽之後有人建立了這個檔，沒有覆蓋")
                # the scratch file is recorded, so a restart mid-write removes it
                nfo.write_atomic(f, it["data"],
                                 before_replace=lambda tmp: jobs.writing(video, tmp))
                man.write(json.dumps({"nfo": str(f), "backup": backup,
                                      "sha": hashlib.sha256(it["data"]).hexdigest()},
                                     ensure_ascii=False) + "\n")
                man.flush()
                created += it["status"] == "new"
                updated += it["status"] == "changed"
            except OSError as e:
                errors.append({"path": str(f), "error": str(e)})
    _nfo_prune_backups()
    log.info("nfo export: %d created, %d updated, %d failed (batch %s)",
             created, updated, len(errors), batch.name)
    return {"created": created, "updated": updated, "errors": errors,
            "batch": batch.name}


@app.post("/api/nfo/scan")
def nfo_scan(body: NfoBody, user: dict = Depends(auth.require_perm("lookup"))):
    """Preview either direction. Writes nothing."""
    if body.mode not in ("import", "export"):
        raise HTTPException(status_code=400, detail="mode 必須是 import 或 export")
    if not body.names:
        raise HTTPException(status_code=400, detail="請至少選擇一位角色")

    def _task(progress):
        progress("整理影片清單…")
        scope = _nfo_scope(body.names, user)
        lib = _NfoLibrary(user)
        if body.mode == "import":
            plan = _nfo_import_plan(scope, lib, progress)
            return {**plan, "rows": plan["rows"][:_NFO_ROWS],
                    "more": max(0, len(plan["rows"]) - _NFO_ROWS)}
        plan = _nfo_export_plan(scope, lib, progress)
        shown = [{k: v for k, v in it.items() if k != "data"}
                 for it in plan["items"][:_NFO_ROWS]]
        return {**plan, "items": shown,
                "more": max(0, len(plan["items"]) - _NFO_ROWS)}

    return {"job_id": jobs.start(_task, kind="nfo_scan", label="NFO 比對",
                                 owner=user.get("id"))}


@app.post("/api/nfo/apply")
def nfo_apply(body: NfoBody, user: dict = Depends(auth.require_perm("lookup"))):
    """Do what the preview showed — worked out again now, so a file changed in
    between is compared as it is, not as it was."""
    if body.mode not in ("import", "export"):
        raise HTTPException(status_code=400, detail="mode 必須是 import 或 export")
    if not body.names:
        raise HTTPException(status_code=400, detail="請至少選擇一位角色")

    def _task(progress):
        progress("整理影片清單…")
        scope = _nfo_scope(body.names, user)
        lib = _NfoLibrary(user)
        if body.mode == "import":
            return _nfo_import_apply(_nfo_import_plan(scope, lib, progress))
        return _nfo_export_apply(_nfo_export_plan(scope, lib, progress), user,
                                 progress)

    export = body.mode == "export"
    return {"job_id": jobs.start(
        _task, kind="nfo_apply",
        label=(f"寫入 NFO（{len(body.names)} 位角色）" if export
               else f"匯入 NFO（{len(body.names)} 位角色）"),
        # writing files on the NAS: a restart leaves it as a notice
        resume=_resumable("nfo_apply", body, user) if export else None,
        owner=user.get("id"))}


def _nfo_last_batch(user: dict) -> Optional[Path]:
    try:
        batches = sorted((p for p in NFO_BACKUPS.iterdir() if p.is_dir()),
                         reverse=True)
    except OSError:
        return None
    for b in batches:
        try:
            mine = (b / "owner").read_text(encoding="utf-8").strip() == str(user.get("id"))
        except OSError:
            mine = False
        if mine and not (b / "undone").exists():
            return b
    return None


def _nfo_manifest(batch: Path) -> list:
    try:
        lines = (batch / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [json.loads(l) for l in lines if l.strip()]


@app.get("/api/nfo/last")
def nfo_last(user: dict = Depends(auth.require_perm("lookup"))):
    """This account's latest batch that can still be undone."""
    b = _nfo_last_batch(user)
    if not b:
        return {"batch": None}
    items = _nfo_manifest(b)
    return {"batch": b.name, "count": len(items),
            "created": sum(1 for i in items if not i.get("backup")),
            "updated": sum(1 for i in items if i.get("backup"))}


@app.post("/api/nfo/undo")
def nfo_undo(user: dict = Depends(auth.require_perm("lookup"))):
    """Put the last batch back: files it created are removed, files it changed
    get their previous version.

    Only a file still exactly as the batch wrote it. One changed since — by
    Jellyfin refreshing it, or by hand — is somebody's newer work, and is left
    and listed rather than overwritten with an older copy.
    """
    b = _nfo_last_batch(user)
    if not b:
        raise HTTPException(status_code=404, detail="沒有可以復原的寫入")
    gate = app_settings.path_gate(user)
    restored = removed = 0
    skipped = []
    for it in reversed(_nfo_manifest(b)):
        f = Path(it["nfo"])
        try:
            if not gate(f):
                raise OSError("沒有這個資料夾的權限")
            now = f.read_bytes()
            if hashlib.sha256(now).hexdigest() != it["sha"]:
                raise OSError("寫入之後被改過，保留現在的內容")
            if it.get("backup"):
                nfo.write_atomic(f, (b / it["backup"]).read_bytes())
                restored += 1
            else:
                f.unlink()
                removed += 1
        except OSError as e:
            skipped.append({"path": str(f), "error": str(e)})
    (b / "undone").write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
    log.info("nfo undo %s: %d restored, %d removed, %d skipped",
             b.name, restored, removed, len(skipped))
    return {"restored": restored, "removed": removed, "skipped": skipped}


# ── wiki import ──────────────────────────────────────────────────────────────
# A saved seesaawiki performer page carries the one thing neither javdatabase
# nor DMM can give us: the original Japanese title of every release, keyed by
# catalogue number. See `core/wikiimport.py` for what is read and, more to the
# point, for what deliberately cannot be.

def _ids_by_character(user: dict) -> dict:
    """`{character name: {catalogue numbers in their folder}}`.

    Built from the search index rather than by walking the library again: that
    index already holds every filename with the character it belongs to, is
    cached on disk, and is refreshed by the same `_files_changed()` that every
    move and delete goes through. Detecting the id is a regex per row, no I/O.
    """
    idx = searchindex.get(app_settings.archive_roots_for(user),
                          app_settings.inbox_paths_for(user),
                          scoped=bool(user.get("allowed_roots")))
    out: dict = {}
    for row in idx.get("v", []):
        name, char = row[0], row[1]
        if not char:
            continue                      # inbox files belong to nobody yet
        vid = media.detect_video_id(name)
        if vid:
            out.setdefault(char, set()).add(vid.upper())
    return out


def _iafd_proposal(raw: bytes, chars: list, user: dict) -> dict:
    """What importing one saved IAFD page would change.

    Shaped like `_wiki_proposal` on purpose — same dialog, same preview-then-
    confirm flow — but the two sources disagree about almost everything else:

    * **The folder is found by content, not by name.** This page is titled
      `Rosalie`; the folder is `Elena Varga`. Name matching finds nothing.
    * **Titles are not offered.** The user's call: matching is fuzzy here, and a
      wrong title is visible and annoying. Only the release year of a matched
      film is written, into `file_title.released` — those films have no
      catalogue number, which is exactly what that table is for.
    * **Only four profile fields.** `idolprofile.PROFILE_KEYS` already allows
      them. Hair colour, eye colour, nationality and the rest are reported but
      not written: widening the shape of `person_info.json` is a decision of its
      own, and those files live on the NAS where git cannot protect them.
    """
    parsed = iafd.parse(raw)

    # Every character's video filenames, from the cached search index rather
    # than a fresh walk — this is 703 characters and 29,265 files, and the
    # index already holds exactly the pair we need.
    idx = searchindex.get(list(app_settings.archive_roots_for(user)),
                          list(app_settings.inbox_paths_for(user)),
                          scoped=bool(user.get("allowed_roots")))
    by_char: dict = {}
    for row in idx.get("v", []):
        if row[1]:
            by_char.setdefault(row[1], []).append(row[0])

    matches = iafd.folder_scores(parsed["works"], by_char,
                                 names=[parsed["name"]])

    # Files for the best candidate only. Pairing every character's videos
    # against the credits would be answering a question nobody asked.
    files, matched = [], []
    if matches:
        best = next((c for c in chars if c["name"] == matches[0]["name"]), None)
        if best:
            for p in best["paths"]:
                files.extend(path for path, _s, _m
                             in media.scan_videos_with_stat(Path(p)))
            matched = iafd.match(parsed["works"], files,
                                 names=[best["name"], parsed["name"]])

    have = {}
    if matches:
        base = next((Path(c["paths"][0]) for c in chars
                     if c["name"] == matches[0]["name"]), None)
        if base:
            info = media.load_person_info(base)
            have = dict(info.get("profile") or {})

    # "Already filled in" is not "no change" — the user needs to see which of
    # the four this page would actually add, because the existing rule is that
    # a hand-typed value always wins and is never overwritten.
    fresh = {k: v for k, v in parsed["profile"].items() if not have.get(k)}

    # `folder_scores` works off the search index, which is keyed by character
    # name — but the client has to send a path, so resolve them here rather
    # than making the browser guess.
    paths = {c["name"]: c["paths"][0] for c in chars
             if c["name"] in {m["name"] for m in matches}}

    return {
        "site": "iafd.com",
        "match_paths": paths,
        "name": parsed["name"],
        "aliases": parsed["aka"],
        "works": len(parsed["works"]),
        "matches": matches,
        "profile": fresh,
        "profile_existing": {k: have[k] for k in parsed["profile"] if have.get(k)},
        "extra": parsed["extra"],
        "matched": matched,
        "files_total": len(files),
        "unmatched_works": len(parsed["works"]) - len({m["title"] for m in matched}),
    }


def _avbase_proposal(raw: bytes, by_char: dict, chars: list) -> dict:
    """What importing one saved AVBase page would change (core/avbase.py).

    Shaped like the wiki proposal — same dialog, same confirm-then-write — and
    with the same rule as the NFO import: **fill only what is empty**. A title
    from a wiki page, DMM or the user's own hand is never replaced; AVBase's
    are product titles, often longer and sales-flavoured.
    """
    parsed = avbase.parse(raw)
    lib_ids = set().union(*by_char.values()) if by_char else set()
    works = parsed["works"]
    page_ids = {w["id"] for w in works}

    # Who the page is about: a talent page names her (and her other names);
    # a work page only has the film's cast. Shared catalogue numbers are the
    # evidence, a name alone is a question (the wiki import's rule).
    names = ([parsed["talent"]] + parsed["aliases"]) if parsed["kind"] == "talent" \
        else (works[0]["cast"] if works else [])
    folded = {media.fold_name(n) for n in names}
    index = performers.index(chars)
    by_alias = {id(index[performers.norm(n)]) for n in names if performers.norm(n) in index}
    scores = []
    for c in chars:
        shared = sorted(page_ids & by_char.get(c["name"], set()))
        by_name = media.fold_name(c["name"]) in folded or id(c) in by_alias
        if shared or by_name:
            scores.append({"name": c["name"], "path": c["path"], "score": len(shared),
                           "shared": shared[:8], "by_name": by_name})
    scores.sort(key=lambda x: (-x["score"], not x["by_name"], x["name"]))

    # Every work goes into the shared catalogue (core/catalog.py), so a film
    # downloaded later shows its title too. `titles` / `released` are the
    # films here whose shown value changes — what the preview counts; the
    # row already includes what a wiki page or an earlier file gave.
    titles, replacing, released, cast = {}, {}, {}, {}
    for w in works:
        if w["cast"] and not javdb.wiki_cast(w["id"]):
            cast[w["id"]] = w["cast"]
        if w["id"] not in lib_ids:
            continue
        row = javdb.title_row(w["id"])
        if w["title"] and not row.get("ja"):
            titles[w["id"]] = w["title"]
            if javdb.best_title(row):
                replacing[w["id"]] = javdb.best_title(row)
        if w["date"] and not (row.get("released") or row.get("released_manual")):
            released[w["id"]] = w["date"]

    have_profile = {}
    if scores and parsed["profile"]:
        info = media.load_person_info(Path(scores[0]["path"]))
        have_profile = dict(info.get("profile") or {})

    rels = [{**r, "have_parent": r["parent"] in lib_ids, "have_child": r["child"] in lib_ids}
            for r in parsed["relations"]]
    return {
        "site": "avbase.net", "kind": parsed["kind"],
        "name": parsed["name"], "url": parsed["url"],
        "page": parsed["page"], "total": parsed["total"],
        # the whole list, for the catalogue: number, title, date, and the
        # section it is filed under when only AVBase lists it
        "works": [{"i": w["id"], "t": w["title"], "d": w["date"],
                   "s": catalog.AVBASE_SOLO if len(w["cast"]) <= 1 else catalog.AVBASE_GROUP}
                  for w in works],
        "aliases": parsed["aliases"], "matches": scores[:5],
        "titles": titles, "titles_total": sum(1 for w in works if w["title"]),
        "replacing": replacing, "released": released, "cast": cast,
        "profile": {k: v for k, v in parsed["profile"].items() if not have_profile.get(k)},
        "profile_existing": {k: have_profile[k] for k in parsed["profile"]
                             if have_profile.get(k)},
        "relations": rels,
        "rel_counts": avbase.relation_counts(parsed["relations"], lib_ids, page_ids),
    }


def _avbase_apply(body, user: dict) -> dict:
    """Write what the preview showed. Titles and dates go into the shared
    catalogue only, never over `movie_title`: they are read *below* anything
    VAM already has there (a title typed, a wiki's, DMM's), so a title typed
    between preview and confirm still wins without being checked again."""
    written = {"titles": 0, "released": 0, "catalog": 0, "cast": 0, "relations": 0,
               "aliases": 0, "profile": []}
    works = [w for w in (body.works or []) if isinstance(w, dict) and w.get("i")]
    if body.catalog and works:
        written["catalog"] = catalog.put_works("avbase", works)
        written["titles"] = len(body.titles or {})       # the films here it shows on
        written["released"] = len(body.released or {})
    if body.folder and works:
        base = Path(body.folder)
        if not is_allowed(base, user) or not base.is_dir():
            raise HTTPException(status_code=403, detail="folder not allowed")
        # One file is often one page of sixteen, so this only adds members;
        # the wiki's list, being the whole of it, replaces its own.
        catalog.set_page(videoflags.key(base), "avbase", body.page_name, body.source,
                         len(works), [(w["i"], w.get("s") or "") for w in works],
                         replace=False)
    cast = {vid: names for vid, names in (body.cast or {}).items()
            if names and not javdb.wiki_cast(vid)}
    if cast:
        written["cast"] = javdb.set_wiki_cast(cast, source=body.source)
    if body.relations:
        written["relations"] = avbase.save_relations(body.relations, source=body.source)

    if body.folder and (body.aliases or body.profile):
        base = Path(body.folder)
        if not is_allowed(base, user) or not base.is_dir():
            raise HTTPException(status_code=403, detail="folder not allowed")
        info = media.load_person_info(base)
        changed = False
        if body.aliases:
            # the wiki import's rule: compared folded (淺 ＝ 浅), stored as typed
            have = {media.fold_name(a) for a in info.get("aliases", [])}
            have.add(media.fold_name(base.name))
            added = []
            for raw in (str(x).strip() for x in body.aliases):
                if raw and media.fold_name(raw) not in have:
                    have.add(media.fold_name(raw))
                    added.append(raw)
            if added:
                info["aliases"] = list(info.get("aliases", [])) + added
                changed = True
            written["aliases"] = len(added)
            written["added_aliases"] = added
        if body.profile:
            profile = dict(info.get("profile") or {})
            added = [k for k, v in idolprofile.clean_profile(body.profile).items()
                     if v and not profile.get(k)]
            for k in added:
                profile[k] = body.profile[k]
            if added:
                info["profile"] = profile
                info["profile_source"] = {"site": "avbase.net", "at": time.time()}
                changed = True
            written["profile"] = added
        if changed:
            media.save_person_info(base, info)
    if written["catalog"]:
        _files_changed()        # titles and dates ride in the search index
    log.info("avbase import %s: %s", body.source or "-", written)
    return {"ok": True, "written": written}


def _wiki_proposal(parsed: dict, by_char: dict, chars: list) -> dict:
    """What one imported page would change, and who it appears to be about."""
    scores = []
    page_names = {parsed["name"]} | {a["name"] for a in parsed["aliases"]}
    for c in chars:
        s = wikiimport.match_score(parsed, by_char.get(c["name"], ()))
        by_name = c["name"] in page_names
        if s["score"] or by_name:
            scores.append({"name": c["name"], "path": c["path"],
                           "score": s["score"], "shared": s["shared"][:8],
                           "by_name": by_name})
    # Catalogue overlap first, name agreement only as a tie-breaker: a shared
    # id is evidence, a shared name is a coincidence waiting to happen.
    scores.sort(key=lambda x: (-x["score"], not x["by_name"], x["name"]))

    titles = wikiimport.titles_by_id(parsed)
    known = javdb.titles()
    lib_ids = set().union(*by_char.values()) if by_char else set()
    # Only offer titles for films that are actually here. Importing the other
    # seventy would fill the table with rows for videos the user does not own.
    useful = {k: v for k, v in titles.items() if k in lib_ids}
    return {
        "name": parsed["name"], "url": parsed["url"],
        "aliases": [a["name"] for a in parsed["aliases"]],
        "works": len(parsed["works"]),
        "matches": scores[:5],
        "titles": useful,
        "titles_total": len(titles),
        "replacing": {k: known[k] for k in useful if k in known},
        "cast": wikiimport.performers_by_id(parsed),
        # Only for films that are here, like the titles: the rest are already
        # in the stored release list, which is what the "not yet acquired"
        # dialog reads.
        "released": {k: v for k, v in wikiimport.released_by_id(parsed).items()
                     if k in lib_ids},
        # Per-section counts, because a bare total is misread. One page listed
        # 750 releases: 543 単体, 81 VR, 113 配信, 5 総集編 -- "she filmed 750
        # films" is not what that number says, and the split is what makes it
        # readable. Photobook and appearance sections are already excluded.
        "summary": wikiimport.summarise(parsed),
        "all_ids": sorted(titles),
        # The whole release list, so the "not yet acquired" dialog can name
        # what is missing. It cannot come from `movie_title`: that holds only
        # the films the user owns, deliberately.
        "works": [{"i": w["video_id"], "t": w["title_ja"], "d": w["date"],
                   "s": w.get("section") or "単体作品"}
                  for w in parsed["works"]
                  if wikiimport.is_video_section(w.get("section"))],
    }


# ── co-stars (共演) ───────────────────────────────────────────────────────────
# A video stays in its lead's folder; the others in it are marked here and
# listed under a 「共演」 filter on their own video tab. See core/costar.py.

def _costar_chars(user: dict) -> list:
    return media.list_characters(app_settings.archive_roots_for(user))


def _costar_names(keys, chars: list) -> list:
    """[{folder, name}] for folder keys the user can see, in the given order.
    A folder that no longer exists or is out of reach is left out."""
    by_key = {}
    for c in chars:
        for p in c.get("paths") or [c["path"]]:
            by_key[costar.folder_key(p)] = {"folder": p, "name": c["name"]}
    return [by_key[k] for k in keys if k in by_key]


# {normalised name → character} reads every person_info.json (on the NAS) for
# the aliases; playback asks for it on every video, so it is kept a minute.
_PERF_TTL = 60.0
_perf_cache: dict = {}


def _perf_index(chars: list) -> dict:
    k = tuple(sorted(c["path"] for c in chars))
    hit = _perf_cache.get(k)
    if hit and time.time() - hit[0] < _PERF_TTL:
        return hit[1]
    idx = performers.index(chars)
    _perf_cache.clear()          # one library; a different key is another account
    _perf_cache[k] = (time.time(), idx)
    return idx


def _jdb_people(vid: str, cached: Optional[dict] = None) -> list:
    """javdatabase's performers for a film, when already cached: one list of
    names per person (Japanese first, then romaji) — `白石美里` and `Karin Morita`
    are one performer, not two."""
    try:
        if (cached if cached is not None else javdb.cached_ids()).get(vid) != "ok":
            return []
        return [names for names in ([n for n in (pf.get("jp"), pf.get("romaji")) if n]
                                    for pf in javdb.lookup_movie(vid)["performers"]) if names]
    except Exception:
        log.exception("costar: performers for %s", vid)
        return []


def _cast_names(vid: str, cached: Optional[dict] = None) -> list:
    """Every name the stored cast lists give a film: the imported wiki /
    AVBase table, then javdatabase's. For matching to folders, where more
    spellings find more people. No network request."""
    return list(javdb.wiki_cast(vid) or []) + [n for p in _jdb_people(vid, cached) for n in p]


def _cast_people(vid: str) -> tuple:
    """([names of one person], source) for the player's 演出 line — from ONE
    list. Mixing them listed MDTM-511's lead three times: 森田花梨 (the wiki),
    白石美里 and Karin Morita (javdatabase's two spellings of her former name).
    The imported table when there is one, it is the Japanese the wiki writes;
    javdatabase only for a film it does not cover."""
    wiki = javdb.wiki_cast(vid)
    if wiki:
        return [[n] for n in wiki], "wiki / AVBase"
    people = _jdb_people(vid)
    return people, "javdatabase" if people else ""


def _lead_keys(lead: Optional[dict]) -> set:
    return ({costar.folder_key(f) for f in lead.get("paths") or [lead["path"]]}
            if lead else set())


def _costar_view(p: Path, chars: list, user: dict) -> dict:
    """Everything the player's 共演 row shows for one video.

    `costars`: folder marks, then name marks — a name that now matches a
    folder or an alias shows as that character (it links up by itself once
    the folder exists). `cast`: the film's whole cast as the stored lists
    name it, each with whether it is the lead, already marked, or which
    character it would be.
    """
    marked = costar.for_video(p)
    names = costar.names_for(p)
    vid = media.detect_video_id(p.name)
    cast, cast_source = _cast_people(vid) if vid else ([], "")
    index = _perf_index(chars) if (names or cast) else {}
    norm = performers.norm
    lead = costar.owner_folder(p, chars)
    lead_keys = _lead_keys(lead)

    out = [{**c, "kind": "folder"} for c in _costar_names(marked, chars)]
    shown = {costar.folder_key(c["folder"]) for c in out}
    for n in names:
        c = index.get(norm(n))
        if c:
            fk = costar.folder_key(c["path"])
            if fk in shown or fk in lead_keys:
                continue              # already there, or it is the lead herself
            shown.add(fk)
            out.append({"folder": c["path"], "name": c["name"], "kind": "name", "typed": n})
        else:
            out.append({"folder": "", "name": n, "kind": "name", "typed": n})
    marked_norms = {norm(c["typed"]) for c in out if c.get("typed")}

    rows, seen = [], set()
    for person in cast:
        n = person[0]
        # any of a person's spellings may be the one a folder or alias has
        c = next((index[norm(x)] for x in person if norm(x) in index), None)
        key = ("f", costar.folder_key(c["path"])) if c else ("n", norm(n))
        if not norm(n) or key in seen:
            continue
        seen.add(key)
        fk = key[1] if c else ""
        rows.append({"name": n, "folder": c["path"] if c else "",
                     "char": c["name"] if c else "",
                     "lead": bool(c and fk in lead_keys),
                     "marked": bool((c and fk in shown)
                                    or any(norm(x) in marked_norms for x in person))})
    return {"costars": out, "cast": rows, "key": costar.video_key(p),
            "cast_source": cast_source}


@app.get("/api/costar")
def costar_get(path: str = Query(...), user: dict = Depends(auth.current_user)):
    """The co-stars marked on a video, and the film's whole cast from the
    stored lists (`_costar_view`). No network request; this runs on every
    playback."""
    p = _checked_path(path, user)
    return _costar_view(p, _costar_chars(user), user)


class CostarSetBody(BaseModel):
    path: str
    folders: list = []
    # Co-stars with no folder, by name. None leaves the stored names alone
    # (an older page that only knows folders).
    names: Optional[list] = None


@app.post("/api/costar/set")
def costar_set(body: CostarSetBody,
               user: dict = Depends(auth.require_perm("edit_info"))):
    p = _checked_path(body.path, user)
    for f in body.folders:
        fp = Path(str(f))
        if not is_allowed(fp, user) or not fp.is_dir():
            raise HTTPException(status_code=403, detail=f"folder not allowed: {f}")
    costar.set_for_video(p, body.folders)
    if body.names is not None:
        costar.set_names(p, [str(n)[:80] for n in body.names])
        searchindex.invalidate()     # the names are searchable
    return _costar_view(p, _costar_chars(user), user)


class CostarResolveBody(BaseModel):
    name: str


@app.post("/api/costar/resolve")
def costar_resolve(body: CostarResolveBody,
                   user: dict = Depends(auth.require_perm("edit_info"))):
    """Which character a typed name is — folder name or any alias — or none.
    The box used to know folder names only, so an alias found nobody."""
    c = _perf_index(_costar_chars(user)).get(performers.norm(body.name))
    return {"folder": c["path"], "name": c["name"],
            "paths": c.get("paths") or [c["path"]]} if c else {"folder": ""}


# ── co-stars from the imported cast lists, in bulk ──────────────────────────
# After a wiki / AVBase import: every film of that page in the library, its
# cast matched to character folders (names and aliases), the lead and what is
# already marked or was turned down left out. Preview, then add — never
# removes a mark. Names with no folder are not written: the player lists the
# whole cast anyway, and a compilation would gain dozens of name chips.

class CostarFillBody(BaseModel):
    ids: list = []


@app.post("/api/costar/fill/preview")
def costar_fill_preview(body: CostarFillBody,
                        user: dict = Depends(auth.require_perm("edit_info"))):
    wanted = {str(i).strip().upper() for i in body.ids if str(i).strip()}
    chars = _costar_chars(user)
    by_name = {c["name"]: c for c in chars}
    index = _perf_index(chars)
    cached = javdb.cached_ids()
    idx = searchindex.get(app_settings.archive_roots_for(user),
                          list(app_settings.inbox_paths_for(user)),
                          scoped=bool(user.get("allowed_roots")))
    rows, seen, unmatched = [], set(), Counter()
    for r in idx.get("v", []):
        if r[3]:
            continue                  # the inbox: no lead to tell the cast from
        vid = media.detect_video_id(r[0])
        vid = vid.upper() if vid else ""
        if not vid or vid not in wanted or vid in seen:
            continue
        full = Path(idx["dirs"][r[2]]) / r[0]
        if not is_allowed(full, user):
            continue
        seen.add(vid)
        names = _cast_names(vid, cached)
        lead = by_name.get(r[1])
        exclude = (set(costar.for_video(full)) | set(costar.for_video(full, costar.REJECTED))
                   | _lead_keys(lead))
        add = costar.suggest(names, index, exclude, performers.norm)
        for n in names:
            if performers.norm(n) not in index and not n.isascii():
                unmatched[n] += 1
        if add:
            rows.append({"video_id": vid, "path": str(full), "name": r[0],
                         "lead": r[1], "add": add})
    rows.sort(key=lambda x: x["video_id"])
    return {"rows": rows, "checked": len(seen),
            "marks": sum(len(x["add"]) for x in rows),
            "unmatched": [{"name": n, "films": k} for n, k in unmatched.most_common(30)],
            "unmatched_total": len(unmatched)}


class CostarFillItem(BaseModel):
    path: str
    folders: list = []


class CostarFillApplyBody(BaseModel):
    items: List[CostarFillItem] = []
    undo: bool = False


@app.post("/api/costar/fill")
def costar_fill(body: CostarFillApplyBody,
                user: dict = Depends(auth.require_perm("edit_info"))):
    """Add these marks (or, with `undo`, take exactly these back off)."""
    videos = marks = 0
    for it in body.items:
        p = _checked_path(it.path, user)
        keys = [costar.folder_key(f) for f in it.folders]
        for f in it.folders:
            fp = Path(str(f))
            if not is_allowed(fp, user) or not fp.is_dir():
                raise HTTPException(status_code=403, detail=f"folder not allowed: {f}")
        have = costar.for_video(p)
        if body.undo:
            new = [k for k in have if k not in keys]
        else:
            new = have + [k for k in keys if k not in have]
        if new != have:
            costar.set_for_video(p, new)
            videos += 1
            marks += abs(len(new) - len(have))
    log.info("costar fill%s: %d videos, %d marks", " (undo)" if body.undo else "",
             videos, marks)
    return {"videos": videos, "marks": marks}


class CostarRejectBody(BaseModel):
    path: str
    folder: str


@app.post("/api/costar/reject")
def costar_reject(body: CostarRejectBody,
                  user: dict = Depends(auth.require_perm("edit_info"))):
    p = _checked_path(body.path, user)
    costar.reject(p, body.folder)
    return {"ok": True}


@app.get("/api/costar/videos")
def costar_videos(root: Optional[str] = Query(None),
                  merged: Optional[str] = Query(None),
                  user: dict = Depends(auth.current_user)):
    """The videos other characters' folders hold that this one co-stars in.

    Resolved through the library's search index, which already lists every
    file with its character: a numbered mark matches every copy of the film,
    a path mark matches its one file. Files in the character's own folder are
    left out — those are in the normal listing already.
    """
    if merged:
        folders = [p for c in _costar_chars(user) if c["name"] == merged
                   for p in c["paths"]]
    elif root:
        if not is_allowed(Path(root), user):
            raise HTTPException(status_code=403, detail="root not allowed")
        folders = [root]
    else:
        raise HTTPException(status_code=400, detail="root or merged required")
    wanted = set(costar.videos_for(folders))
    # films marked with a name only, before this folder (or that alias) existed
    chars = _costar_chars(user)
    fkeys = {costar.folder_key(f) for f in folders}
    mine = {k for k, c in _perf_index(chars).items()
            if any(costar.folder_key(p) in fkeys for p in c.get("paths") or [c["path"]])}
    wanted |= set(costar.videos_named(mine, performers.norm))
    if not wanted:
        return {"count": 0, "videos": []}
    own = [costar.folder_key(f) for f in folders]
    own = [k if k.endswith(os.sep) else k + os.sep for k in own]
    idx = searchindex.get(app_settings.archive_roots_for(user),
                          list(app_settings.inbox_paths_for(user)),
                          scoped=bool(user.get("allowed_roots")))
    marks = videoflags.flags_for(user["id"])
    others = videoflags.others_fav_counts(user["id"])
    known, when = javdb.titles(), javdb.released()
    typed, typed_when = filetitle.all_titles(), filetitle.all_released()
    related = avbase.relations_by_work()
    dirs = idx.get("dirs", [])
    items = []
    for row in idx.get("v", []):
        name, char, d = row[0], row[1], row[2]
        full = Path(dirs[d]) / name
        vid = media.detect_video_id(name)
        k = f"id:{vid.upper()}" if vid else f"path:{videoflags.key(full)}"
        if k not in wanted:
            continue
        fk = videoflags.key(full)
        if any(fk.startswith(o) for o in own) or not is_allowed(full, user):
            continue
        try:
            st = full.stat()
        except OSError:
            continue                    # the index is a little behind the disk
        it = {"path": str(full), "name": name, "size": st.st_size,
              "mtime": st.st_mtime, "video_id": vid or "",
              "title": known.get(vid, "") if vid else typed.get(fk, ""),
              "released": when.get(vid, "") if vid else typed_when.get(fk, ""),
              "lead": char or "待分類"}
        if vid and vid.upper() in related:
            it["rel"] = related[vid.upper()]
        _attach_marks(it, marks, others)
        items.append(it)
    return {"count": len(items), "videos": items}


_IMPORT_MAX = 64 * 1024 * 1024


@app.post("/api/wiki/preview")
async def wiki_preview(files: List[UploadFile] = File(...),
                       user: dict = Depends(auth.require_perm("lookup"))):
    """Parse saved pages and report what importing them would do.

    Nothing is written here. `person_info.json` lives on the NAS where git
    cannot protect it, so this follows the same two-step the catalogue lookup
    uses: propose, let the user look, then write only what they confirmed.
    """
    by_char = _ids_by_character(user)
    chars = media.list_characters(app_settings.archive_roots_for(user))
    out = []
    for f in files:
        raw = await f.read()
        # 64 MB: an AVBase talent page with every appearance merged onto it is
        # big — 森田花梨's 1,764 works saved at 17.5 MB (a wiki page is ~1 MB)
        if len(raw) > _IMPORT_MAX:
            out.append({"file": f.filename,
                        "error": f"檔案過大（超過 {_IMPORT_MAX >> 20} MB）"})
            continue
        # Three very different sites through one upload box: the user should
        # not have to know which parser their saved page needs.
        if avbase.looks_like_avbase(raw):
            try:
                out.append({"file": f.filename,
                            **_avbase_proposal(raw, by_char, chars)})
            except Exception as e:
                log.exception("avbase parse failed for %s", f.filename)
                out.append({"file": f.filename, "error": f"解析失敗：{e}"})
            continue
        if iafd.looks_like_iafd(raw):
            try:
                out.append({"file": f.filename,
                            **_iafd_proposal(raw, chars, user)})
            except Exception as e:
                log.exception("iafd parse failed for %s", f.filename)
                out.append({"file": f.filename, "error": f"解析失敗：{e}"})
            continue
        try:
            parsed = wikiimport.parse(raw)
        except Exception as e:
            log.exception("wiki parse failed for %s", f.filename)
            out.append({"file": f.filename, "error": f"解析失敗：{e}"})
            continue
        if not parsed["works"] and not parsed["aliases"]:
            out.append({"file": f.filename,
                        "error": "這個檔案裡找不到作品或藝名，可能不是演員頁"})
            continue
        out.append({"file": f.filename, **_wiki_proposal(parsed, by_char, chars)})
    return {"pages": out}


class WikiApplyBody(BaseModel):
    # The client sends back exactly what it showed the user. That is the point:
    # what gets written is what was on screen when they pressed the button, not
    # a re-parse that might have drifted.
    titles: dict = {}
    aliases: list = []
    folder: str = ""
    cast: dict = {}
    released: dict = {}
    source: str = ""
    # The per-section counts and the full catalogue list, so the video list can
    # later say "the wiki lists 750" without the page being re-read.
    summary: Optional[dict] = None
    all_ids: list = []
    works: list = []
    page_name: str = ""
    # ── IAFD ───────────────────────────────────────────────────────────────
    # Which parser produced this. The two sources write different things, and
    # branching on a field that happens to be present would break the moment
    # one of them legitimately sends an empty one.
    site: str = ""
    # Bio fields, already filtered to `idolprofile.PROFILE_KEYS` by the preview.
    profile: dict = {}
    # {video path: release year}. Keyed by path, not catalogue number — an IAFD
    # credit has no number, which is the whole reason `file_title` exists.
    file_released: dict = {}
    # ── AVBase ─────────────────────────────────────────────────────────────
    # [{parent, child, parent_date, child_date, parent_title, child_title}]
    relations: list = []
    # ── both ───────────────────────────────────────────────────────────────
    # Store every listed work's title and date in the shared catalogue
    # (core/catalog.py), so a film downloaded later shows them too. Follows
    # the titles checkbox: unticked, the page's titles show nowhere.
    catalog: bool = True


@app.post("/api/wiki/apply")
def wiki_apply(body: WikiApplyBody, user: dict = Depends(auth.require_perm("lookup"))):
    written = {"titles": 0, "aliases": 0, "cast": 0}

    if body.site == "avbase.net":
        return _avbase_apply(body, user)
    if body.site == "iafd.com":
        # A separate branch rather than more `if` inside the wiki one: the two
        # sources share the dialog and nothing else. IAFD writes a bio and some
        # release years; it never writes a title, an alias or a cast list.
        if body.file_released:
            paths = {}
            for raw_path, year in body.file_released.items():
                p = _checked_path(raw_path, user)   # never trust a path from the client
                y = iafd.released_from(year)
                if y:
                    paths[str(p)] = y
            written["file_released"] = filetitle.set_many_released(paths)
        if body.profile and body.folder:
            # Not `_checked_path`: that one insists on `is_file()`, so handing
            # it a character folder raised a bare 404 "not found" — which reads
            # like a missing route, not like "you passed a directory".
            base = Path(body.folder)
            if not is_allowed(base, user) or not base.is_dir():
                raise HTTPException(status_code=403, detail="folder not allowed")
            info = media.load_person_info(base)
            profile = dict(info.get("profile") or {})
            # Never overwrite: a value the user typed always wins, the same
            # rule the catalogue lookup follows.
            added = [k for k, v in idolprofile.clean_profile(body.profile).items()
                     if v and not profile.get(k)]
            for k in added:
                profile[k] = body.profile[k]
            if added:
                info["profile"] = profile
                info["profile_source"] = {"site": "iafd.com", "at": time.time()}
                media.save_person_info(base, info)
            written["profile"] = added
        _files_changed()        # release dates ride in the search index
        return {"ok": True, "written": written}

    if body.titles:
        written["titles"] = javdb.set_ja_titles(body.titles)
    if body.cast:
        written["cast"] = javdb.set_wiki_cast(body.cast, source=body.source)
    # Rides along with the titles rather than having its own checkbox: it is
    # the same rows from the same page, and a date nobody asked to withhold.
    if body.released:
        written["released"] = javdb.set_released(body.released)

    if body.aliases and body.folder:
        base = Path(body.folder)
        if not is_allowed(base, user) or not base.is_dir():
            raise HTTPException(status_code=403, detail="folder not allowed")
        info = media.load_person_info(base)
        # Compared on the folded form, stored as typed. `淺井花梨` (the user's
        # Traditional spelling) and `浅井花梨` (the wiki's shinjitai) are one
        # name written two ways, and plain string equality let both in.
        have = {media.fold_name(a) for a in info.get("aliases", [])}
        have.add(media.fold_name(base.name))     # not an alias of itself
        added, dup = [], []
        for raw in (str(x).strip() for x in body.aliases):
            if not raw:
                continue
            key = media.fold_name(raw)
            if key in have:
                dup.append(raw)
                continue
            have.add(key)                        # and not twice in one import
            added.append(raw)
        if added:
            info["aliases"] = list(info.get("aliases", [])) + added
            media.save_person_info(base, info)
        written["aliases"] = len(added)
        written["added_aliases"] = added
        # Reported rather than silently dropped: "it added nothing" and "it
        # decided those were the same name" look identical otherwise, and only
        # the user can tell whether the second one is right.
        written["duplicate_aliases"] = dup

    # Stored against the folder so the character's video list can show it, and
    # kept even when nothing else was imported: knowing how much of a
    # performer's output is here is the point of having looked at the page.
    if body.summary and body.folder:
        base = Path(body.folder)
        if not is_allowed(base, user):
            raise HTTPException(status_code=403, detail="folder not allowed")
        javdb.set_wiki_profile(videoflags.key(base), body.page_name,
                               body.source, body.summary, body.all_ids,
                               works=body.works, catalog_titles=body.catalog)
        written["summary"] = body.summary.get("total", 0)
    elif body.catalog and body.works:
        # no character chosen: nothing to count against, but the titles are
        # still the films' and a later download should get them
        catalog.put_works("wiki", body.works)
    if body.catalog and body.works:
        written["catalog"] = sum(1 for w in body.works if w.get("i"))
        _files_changed()        # titles and dates ride in the search index

    if written["titles"]:
        # titles ride along in the search index, so it now describes films by
        # names that are no longer current
        searchindex.invalidate()
    log.info("wiki import: %s (folder=%s, source=%s)",
             written, body.folder or "-", body.source or "-")
    return {"ok": True, **written}


@app.get("/api/wiki/missing")
def wiki_missing(root: str = Query(...), section: str = Query(""),
                 user: dict = Depends(auth.current_user)):
    """Releases the imported page lists that are not in this folder.

    A separate endpoint rather than part of `/api/videos`, because this is the
    large half: one page carried 750 releases of which 695 were missing, and
    the video list asks for its summary on every load while this is only wanted
    when the dialog is opened.

    Not filterable in the grid, unlike the other numbers in that panel: these
    are films the user does *not* have, so there is no card to show. The point
    of the list is the catalogue number -- something to go and search with.
    """
    base = Path(root)
    if not is_allowed(base, user):
        raise HTTPException(status_code=403, detail="root not allowed")
    s = catalog.summary(videoflags.key(base))
    if not s:
        return {"works": [], "stale": False}

    here = set()
    for path, _size, _mtime in media.scan_videos_with_stat(base):
        if media.is_under_ig(path, base):
            continue           # profile-tab media, as in the video list
        vid = media.detect_video_id(path.name)
        if vid:
            here.add(vid.upper())

    # A missing film whose footage is in one the user owns anywhere — the
    # original it was cut from, or an uncut compilation holding it whole —
    # is not really missing (AVBase's relations, core/avbase.py).
    owned = set()
    rels = avbase.relations()
    if rels:
        idx = searchindex.get(app_settings.archive_roots_for(user),
                              list(app_settings.inbox_paths_for(user)),
                              scoped=bool(user.get("allowed_roots")))
        owned = {v.upper() for v in (media.detect_video_id(r[0])
                                     for r in idx.get("v", [])) if v}
    covered = {}
    for r in rels:
        if r["kind"] in ("original", "uncut") and r["parent"] in owned:
            covered.setdefault(r["child"], r["parent"])

    known = catalog.best()
    out = []
    for vid, sec in s["section_of"].items():
        if (section and sec != section) or vid in here:
            continue
        e = known.get(vid) or {}
        out.append({"video_id": vid, "title": e.get("title") or "",
                    "date": e.get("released") or "", "section": sec,
                    "covered_by": covered.get(vid, "")})
    for w in s["unnumbered"]:
        if section and (w.get("s") or "") != section:
            continue
        out.append({"video_id": "", "title": w.get("t") or "", "date": w.get("d") or "",
                    "section": w.get("s") or "", "covered_by": ""})
    # Newest first, which is the order the wiki itself uses and the order in
    # which someone filling gaps is likely to care.
    out.sort(key=lambda w: w["date"], reverse=True)
    # Imported before the release list was stored: the numbers are there but
    # no title at all, and only a re-import can supply them.
    stale = bool(out) and not any(w["title"] for w in out)
    return {"works": out, "stale": stale}


class IdolRejectBody(BaseModel):
    name: str
    slug: str
    rejected: bool = True


@app.post("/api/idolprofile/reject")
def idolprofile_reject(body: IdolRejectBody,
                       user: dict = Depends(auth.require_perm("lookup"))):
    """Record that a suggested performer is (not) this character.

    Rejections are remembered but the row is still listed next time, marked —
    people change their minds, and a row that vanishes looks like a bug.
    """
    return {"rejected": idolprofile.set_rejected(body.name, body.slug,
                                                 body.rejected)}


class IdolApplyBody(BaseModel):
    rows: list


def _install_gfriends_avatar(folder: Path, pick: dict) -> Optional[str]:
    """Copy a Gfriends picture into the folder and make it the avatar.

    Only while the avatar is still what the scan saw: none, or — on a
    recheck — the one the user chose to replace (`replaces`). A scan an hour
    old must not replace one set since. The old picture is not deleted; the
    avatar just points at the new one.
    """
    cat, f = str(pick.get("cat") or ""), str(pick.get("file") or "")
    if not gfriends.known(cat, f):
        raise ValueError(f"圖庫裡沒有這張：{cat}/{f}")
    info = media.load_person_info(folder)
    if (info.get("profile_photo") or "") != str(pick.get("replaces") or ""):
        return None
    rel = gfriends.install(folder, cat, f,
                           cachelimit.kind_dir(CACHE_DIR, "gfriends"))
    info = media.load_person_info(folder)   # apply_proposal may have just saved
    info["profile_photo"] = rel
    # A headshot: the face is in the upper part, so the default centre would
    # put the chin in the middle of the circle.
    info["profile_photo_pos"] = {"x": 50, "y": 30, "zoom": 1}
    media.save_person_info(folder, info)
    _files_changed()
    log.info("avatar from gfriends for %s: %s/%s", folder.name, cat, f)
    return rel


@app.get("/api/gfriends/image")
def gfriends_image(c: str = Query(...), f: str = Query(...),
                   user: dict = Depends(auth.require_perm("lookup"))):
    """A Gfriends picture, fetched by the server and cached.

    Only names in the index are fetched, so this cannot be pointed at an
    arbitrary URL. The browser caches it for a month: the files never change
    under the same name.
    """
    if not gfriends.known(c, f):
        raise HTTPException(status_code=404, detail="圖庫裡沒有這張")
    cache = cachelimit.kind_dir(CACHE_DIR, "gfriends")
    try:
        p = gfriends.image(c, f, cache)
        cachelimit.touch(p)
        data = p.read_bytes()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"圖庫下載失敗：{e}")
    cachelimit.maybe_enforce(CACHE_DIR, "gfriends",
                             _limit_bytes(app_settings.get(), "gfriends"))
    kind = "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"
    return Response(data, media_type=kind, headers=_IMG_CACHE_HEADERS)


@app.post("/api/idolprofile/apply")
def idolprofile_apply(body: IdolApplyBody,
                      user: dict = Depends(auth.require_perm("lookup"))):
    """Write the approved rows. Adds what is empty; replaces only what the
    user ticked on a recheck (see idolprofile.build_proposal)."""
    written, errors = [], []
    for row in body.rows:
        try:
            paths = row.get("paths") or []
            if not paths or not is_allowed(Path(paths[0]), user):
                errors.append({"name": row.get("name"), "error": "path not allowed"})
                continue
            done = idolprofile.apply_proposal(row)
            pick = row.get("avatar_pick")
            if pick:
                done["avatar"] = _install_gfriends_avatar(Path(paths[0]), pick)
            written.append(done)
        except Exception as e:
            log.exception("profile apply failed for %s", row.get("name"))
            errors.append({"name": row.get("name"), "error": str(e)})
    return {"written": written, "errors": errors,
            "untranslated": idolprofile.missing_terms()}


@app.get("/api/search/index")
def search_index(user: dict = Depends(auth.current_user)):
    """Every video filename this user may see, for client-side search.

    The whole index rather than a `?q=` endpoint: measured at 16,204 videos,
    0.20 MB gzipped, and about a millisecond to scan in full (once the
    lowercased and normalised strings are prepared — see videosearch.js). One download
    beats a round trip per keystroke, and the search needs no debounce.

    Readable by any signed-in role — it lists nothing a user could not
    already reach by clicking through the sidebar.
    """
    roots = list(app_settings.archive_roots_for(user))
    inbox = list(app_settings.inbox_paths_for(user))
    scoped = bool(user.get("allowed_roots"))
    return searchindex.get(roots, inbox, scoped=scoped)


@app.get("/api/dedup/scope")
def dedup_scope(user: dict = Depends(auth.require_perm("dedup"))):
    return folder_scope(user)


# ── JAV catalogue lookup (editor only) ───────────────────────────────────────

def _suggestions_for(vid: str, user: dict, refresh: bool = False) -> dict:
    """Look a catalogue id up and point each performer at the user's folder."""
    res = javdb.lookup_movie(vid, refresh=refresh)
    idx = performers.index(media.list_characters(
        app_settings.archive_roots_for(user)))
    out = []
    for p in res["performers"]:
        hit = performers.match(p, idx)
        out.append({
            "jp": p["jp"], "romaji": p["romaji"], "slug": p["slug"],
            # what the archive field should be filled with when clicked
            "name": hit["name"] if hit else (p["jp"] or p["romaji"]),
            "known": bool(hit),
        })
    return {"id": res["id"], "status": res["status"],
            "cached": res.get("cached", False), "suggestions": out}


def _learn_performer(src: Path, folder: Path):
    """Remember which folder a catalogue number belongs in, after an archive.

    javdatabase does not have a Japanese name for every performer (it returned
    only "Reina Kiritani", for instance), so the romaji would never match a
    folder named in kanji. Filing the video *is* the answer, so record the
    spellings as aliases on the destination and the next lookup resolves by
    itself. Cache-only — this must not put a network call in the archive path.
    """
    try:
        vid = media.detect_video_id(src.name)
        if not vid:
            return
        cached = javdb.cached_ids()
        if cached.get(vid) != "ok":
            return
        res = javdb.lookup_movie(vid)      # served from cache, no request
        names = [n for p in res["performers"] for n in (p["jp"], p["romaji"]) if n]
        # only safe when the film has a single credited performer; with several
        # we cannot tell which one the folder is for
        if len(res["performers"]) == 1 and names:
            added = performers.add_aliases(folder, names)
            if added:
                log.info("learned %s → %s %s", vid, folder.name, added)
    except Exception:
        log.exception("could not learn the performer for %s", src)


@app.get("/api/javdb/match")
def javdb_match(path: str = Query(...), refresh: bool = Query(False),
                user: dict = Depends(auth.require_perm("lookup"))):
    p = _checked_path(path, user)
    vid = media.detect_video_id(p.name)
    if not vid:
        return {"id": None, "status": "no_id", "suggestions": []}
    return _suggestions_for(vid, user, refresh=refresh)


@app.get("/api/javdb/stats")
def javdb_stats(user: dict = Depends(auth.require_perm("lookup"))):
    return javdb.stats()


@app.post("/api/javdb/seed")
def javdb_seed(user: dict = Depends(auth.require_perm("lookup"))):
    """Learn performer spellings from videos that are already filed.

    Every archived file whose name carries a catalogue id tells us "this id
    belongs in this folder". Looking the id up then yields the romaji and
    Japanese spellings, which get written into that folder's aliases — which is
    what lets a future lookup resolve to a folder the user actually has.
    """
    roots = app_settings.archive_roots_for(user)
    chars = media.list_characters(roots)

    def _task(progress):
        # collect (id → folder) from the archive, ignoring catch-all folders
        todo = {}
        for c in chars:
            for base in c["paths"]:
                for f, _s, _m in media.scan_videos_with_stat(Path(base)):
                    vid = media.detect_video_id(f.name)
                    if vid:
                        todo.setdefault(vid, Path(base))

        done = skipped = 0
        added_total = []
        for i, (vid, folder) in enumerate(sorted(todo.items()), 1):
            progress({"index": i, "total": len(todo), "id": vid,
                      "folder": folder.name})
            try:
                res = javdb.lookup_movie(vid)
            except Exception:
                log.exception("seed lookup failed for %s", vid)
                skipped += 1
                continue
            if res["status"] != "ok":
                skipped += 1
                continue
            names = [n for p in res["performers"]
                     for n in (p["jp"], p["romaji"]) if n]
            added = performers.add_aliases(folder, names)
            if added:
                added_total.append({"folder": folder.name, "added": added})
            done += 1
        return {"ids": len(todo), "resolved": done, "skipped": skipped,
                "updated": added_total}

    return {"job_id": jobs.start(_task, kind="javdb_seed", label="建立演員對應表")}


@app.get("/api/javdb/job/{job_id}")
def javdb_job(job_id: str, user: dict = Depends(auth.require_perm("lookup"))):
    j = jobs.get(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job not found")
    return j


# ── format conversion (editor only) ──────────────────────────────────────────

class ConvertScanBody(BaseModel):
    roots: list
    # Off by default: it costs an ffprobe on every MP4 in the library, and a
    # sampled survey put 95.7% of them at plain h264 -- so almost all of that
    # work finds nothing. Worth having at all because the extension says
    # nothing about the codec: an `.mp4` holding `mpeg4` video plays nowhere.
    include_mp4: bool = False
    # Separate from include_mp4 on the user's request: finding a codec
    # problem and finding a frame-timing one are different errands, and
    # the timing check roughly triples the scan.
    check_timing: bool = False
    # Fully decode every video in scope to find damaged picture data, which
    # every other check passes (they read the wrapper, not the frames). Slow
    # -- median 4.3 s a file, CPU-bound -- so opt-in, cached, and stoppable.
    check_decode: bool = False
    # Recordings that stopped receiving for a while with the clock still
    # running (no video and no audio for minutes). Reads only the packet
    # index, about 0.5 s a file, but opt-in: it is a different errand.
    check_blanks: bool = False
    # Every check remembers its answer per file and skips a file that has not
    # changed since (core/checkcache.py). This ignores what is remembered.
    # Not remembered by the dialog, so it never rides along on the next scan.
    recheck: bool = False


DECODE_CHECK_DB = DATA_DIR / "decode_check.sqlite3"
CONVERT_CHECK_DB = DATA_DIR / "convert_checks.sqlite3"


@app.post("/api/convert/scan")
def convert_scan(body: ConvertScanBody, user: dict = Depends(auth.require_perm("convert"))):
    if not body.roots:
        raise HTTPException(status_code=400, detail="請至少選擇一個資料夾")
    roots = []
    for r in body.roots:
        p = Path(r)
        if not is_allowed(p, user):
            raise HTTPException(status_code=403, detail=f"root not allowed: {r}")
        roots.append(p)

    def _task(progress):
        stats = {}
        rows = convert.scan(roots, progress=progress,
                            check_cache=CONVERT_CHECK_DB,
                            recheck=bool(body.recheck), stats=stats,
                            include_mp4=bool(body.include_mp4),
                            check_timing=bool(body.check_timing),
                            check_decode=bool(body.check_decode),
                            decode_cache=DECODE_CHECK_DB,
                            # same "who pays for the decoding" choice as the
                            # contact sheets, so one setting covers both
                            decode_gpu=app_settings.get().get("sheet_hwaccel") == "gpu",
                            check_blanks=bool(body.check_blanks))
        _save_convert_history(roots, body, rows,
                              partial=bool(getattr(progress, "cancelled", False)))
        # how many files each check reused, so the dialog can say so
        return {"rows": rows, "checks": stats}

    bits = ["轉檔掃描"]
    if body.include_mp4:
        bits.append("含 MP4")
    if body.check_timing:
        bits.append("檢查影格時序")
    if body.check_decode:
        bits.append("完整解碼檢查")
    if body.check_blanks:
        bits.append("檢查錄影中斷")
    if body.recheck:
        bits.append("全部重新檢查")
    label = bits[0] + ("（%s）" % "、".join(bits[1:]) if len(bits) > 1 else "")
    # A separate kind so only the scans that actually read the stop flag
    # (the decode pass does; the probe pass is minutes at most) accept it.
    kind = ("convert_decode" if body.check_decode or body.check_blanks
            else "convert_scan")
    jid = jobs.start(_task, kind=kind, label=label,
                     resume=_resumable("convert_scan", body, user))
    with _convert_scans_lock:
        _convert_scans[jid] = {"user": user.get("id"),
                               "options": body.dict(exclude={"roots"})}
    return {"job_id": jid}


# Running convert scans by owner, so a reopened dialog can pick its own scan
# back up. Per user, not the generic /api/jobs: a scan's rows are paths, and
# an account limited to some roots must not attach to another account's scan.
_convert_scans: dict = {}
_convert_scans_lock = threading.Lock()


@app.get("/api/convert/scan/active")
def convert_scan_active(user: dict = Depends(auth.require_perm("convert"))):
    """This user's newest convert scan that is still running, or null."""
    found = None
    with _convert_scans_lock:
        for jid in list(_convert_scans):
            j = jobs.get(jid)
            if not j or j["status"] != "running":
                _convert_scans.pop(jid, None)       # finished: in the history
                continue
            meta = _convert_scans[jid]
            if meta["user"] != user.get("id"):
                continue
            if found is None or j["started_at"] > found["started_at"]:
                found = {"job_id": jid, "progress": j.get("progress", ""),
                         "started_at": j["started_at"],
                         "stopping": bool(j.get("cancel")),
                         "options": meta["options"]}
    return {"scan": found}


@app.get("/api/convert/locate")
def convert_locate(path: str = Query(...),
                   user: dict = Depends(auth.require_perm("convert"))):
    """Where a damaged file's damage is, for the player in the convert dialog.

    Before this the times only appeared in the pending list, after a repair —
    the moment someone presses ▶ on a damaged row is when they want them.
    Decodes the file once (seconds for a clip, about a full decode for a
    film) and remembers the answer in the check cache.
    """
    p = _checked_path(path, user)
    return {"regions": decodecheck.locate_cached(p, DECODE_CHECK_DB)}


# ── convert scan history ─────────────────────────────────────────────────────
# Same shape as the duplicate-scan history (core/reporthistory.py): every scan
# kept, the last KEEP of them, shared by everyone with the convert permission
# and filtered per user on the way out. A scan with the full decode check can
# run for an hour, and the result used to live only in the open dialog —
# a page reload (or a restart for an update) meant running it again.

_CONVERT_HISTORY = DATA_DIR / "convert_history"


def _save_convert_history(roots, body, rows, partial: bool = False) -> None:
    # size and mtime at scan time, so opening the report later can tell a
    # file that has been dealt with (converted, fixed in place) from one
    # that is still waiting
    out = []
    for r in rows:
        try:
            st = Path(r["path"]).stat()
            r = {**r, "mtime": st.st_mtime, "size": st.st_size}
        except OSError:
            pass
        out.append(r)
    opts = [label for flag, label in (
        (body.include_mp4, "含 MP4"), (body.check_timing, "影格時序"),
        (body.check_decode, "完整解碼"), (body.check_blanks, "錄影中斷"))
        if flag]
    try:
        reporthistory.save(_CONVERT_HISTORY, {
            "at": time.time(), "roots": [str(r) for r in roots],
            "options": opts, "partial": partial, "rows": out})
    except Exception:
        log.exception("could not save the convert scan to history")


def _path_gate(user: dict):
    """`is_allowed` for many paths: the roots worked out once (see
    settings.path_gate). Tests swap `is_allowed` out, so honour that."""
    if is_allowed is not app_settings.is_allowed:
        return lambda p: is_allowed(p, user)
    return app_settings.path_gate(user)


def _convert_rows_now(raw: dict, user: dict, actionable: bool = False,
                      gate=None) -> list:
    """A saved scan's rows as they stand now, for this user.

    Dropped: files this user may not see, files that are gone (converted to
    .mp4, deleted) or changed since the scan (fixed in place), and rows whose
    kept-beside-it output already exists (a repair or a concealed re-encode).
    What is left is what the report still has to offer. `actionable` looks
    only at the rows with something to do — all the history list counts.

    The files are checked in parallel: they are mostly on NAS shares, a round
    trip each, and opening the dialog waits for the newest scan's count.
    """
    gate = gate or _path_gate(user)
    rows = [r for r in raw.get("rows", [])
            if not actionable or r.get("action") != "skip"]

    def still(r) -> bool:
        p = Path(r["path"])
        if not gate(p):
            return False
        try:
            st = p.stat()
        except OSError:
            return False
        if "mtime" in r and (st.st_size != r.get("size")
                             or abs(st.st_mtime - r["mtime"]) > 1e-3):
            return False
        if r.get("action") in ("repair", "damaged") and repair.target_for(p).exists():
            return False
        return True

    if not rows:
        return []
    with ThreadPoolExecutor(max_workers=16) as ex:
        keep = list(ex.map(still, rows))
    return [r for r, k in zip(rows, keep) if k]


@app.get("/api/convert/history")
def convert_history(kind: str = Query("scan"),
                    user: dict = Depends(auth.require_perm("convert"))):
    """Saved convert scans, newest first. `remaining` counts, now, the rows
    that still have something to do — "dealt with" needs no bookkeeping, the
    files on disk say it. `kind` is accepted for the shared history list."""
    out = []
    gate = _path_gate(user)          # once: it resolves every root
    for i, rid in enumerate(reporthistory.ids(_CONVERT_HISTORY)):
        path = _CONVERT_HISTORY / f"{rid}.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        roots = [r for r in raw.get("roots", []) if gate(Path(r))]
        rows = raw.get("rows", [])
        now = _convert_rows_now(raw, user, actionable=True, gate=gate)
        out.append({"id": rid, "at": raw.get("at"), "latest": i == 0,
                    "roots": [Path(r).name or r for r in roots],
                    "tags": raw.get("options", []),
                    "partial": bool(raw.get("partial")),
                    "groups": sum(1 for r in rows if r.get("action") != "skip"),
                    "remaining": sum(1 for r in now if r.get("action") != "skip")})
    return {"entries": out, "keep": reporthistory.KEEP}


@app.get("/api/convert/history/{kind}/{rid}")
def convert_history_report(kind: str, rid: str,
                           user: dict = Depends(auth.require_perm("convert"))):
    path = reporthistory.path_of(_CONVERT_HISTORY, rid)
    if not path:
        raise HTTPException(status_code=404, detail="這份紀錄已不存在")
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {"id": rid, "at": raw.get("at"), "options": raw.get("options", []),
            "partial": bool(raw.get("partial")),
            "rows": _convert_rows_now(raw, user)}


@app.delete("/api/convert/history/{kind}/{rid}")
def convert_history_delete(kind: str, rid: str,
                           user: dict = Depends(auth.require_perm("convert"))):
    """Removes the record only — never any video."""
    if not reporthistory.delete(_CONVERT_HISTORY, rid):
        raise HTTPException(status_code=404, detail="這份紀錄已不存在")
    return {"deleted": rid}


# ── shrinking (re-encode to reclaim space) ───────────────────────────────────
# Deliberately separate from converting: that asks "can a browser play this?",
# this asks "is this paying for quality nobody asked for?".

class ShrinkScanBody(BaseModel):
    roots: list
    min_mbps: float = shrink.DEFAULT_MIN_MBPS


@app.post("/api/shrink/scan")
def shrink_scan(body: ShrinkScanBody,
                user: dict = Depends(auth.require_perm("shrink"))):
    """Candidates at or above the bitrate threshold, worst offender first.

    The threshold does not speed the scan up — a bitrate is only knowable by
    probing — it decides how long the list is. Measured on this library:
    18 Mbps gives 125 rows, 6 Mbps gives 977, no threshold gives 5,580.
    """
    if not body.roots:
        raise HTTPException(status_code=400, detail="請至少選擇一個資料夾")
    roots = []
    for r in body.roots:
        p = Path(r)
        if not is_allowed(p, user):
            raise HTTPException(status_code=403, detail=f"root not allowed: {r}")
        roots.append(p)
    mbps = max(0.0, float(body.min_mbps))

    def _task(progress):
        return shrink.scan(roots, mbps, progress=progress)

    return {"job_id": jobs.start(_task, kind="shrink_scan", label="壓縮掃描")}


class ShrinkSampleBody(BaseModel):
    path: str
    crf: int = shrink.DEFAULT_CRF
    max_edge: int = 0


@app.post("/api/shrink/sample")
def shrink_sample(body: ShrinkSampleBody,
                  user: dict = Depends(auth.require_perm("shrink"))):
    """A short before/after pair, so the choice is made by eye, not by number.

    This is the only exact figure available for a given file — the estimate in
    the candidate list is interpolated from eight measured points and is good
    enough to sort by, nothing more.
    """
    p = _checked_path(body.path, user)
    crf = int(body.crf)
    if crf not in shrink.CRF_CHOICES:
        raise HTTPException(status_code=400, detail="無效的 crf")
    try:
        out = shrink.sample(p, crf, int(body.max_edge), CACHE_DIR)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    # A repeat request reuses the clips rather than re-encoding, so they have
    # to look recently used or the sweep will evict the pair you are watching.
    for f in (out["before"], out["after"]):
        cachelimit.touch(Path(f))
    cachelimit.maybe_enforce(CACHE_DIR, "shrink_samples",
                             _limit_bytes(app_settings.get(), "shrink_samples"))
    return out


class ShrinkRunBody(BaseModel):
    paths: list
    crf: int = shrink.DEFAULT_CRF
    max_edge: int = 0


@app.post("/api/shrink/run")
def shrink_run(body: ShrinkRunBody, user: dict = Depends(auth.require_perm("shrink"))):
    """Encode each file to a *new* file. The original is never touched.

    That is the whole safety story here: this is lossy and irreversible, so
    the user compares the two and deletes the one they do not want, in delete
    mode, like every other deletion in this app.
    """
    crf = int(body.crf)
    if crf not in shrink.CRF_CHOICES:
        raise HTTPException(status_code=400, detail="無效的 crf")
    max_edge = int(body.max_edge)
    if max_edge not in shrink.EDGE_CHOICES:
        raise HTTPException(status_code=400, detail="無效的解析度上限")

    items = []
    for raw in body.paths:
        src = Path(raw)
        if not is_allowed(src, user) or not src.is_file():
            raise HTTPException(status_code=403, detail=f"path not allowed: {raw}")
        items.append(src)
    if not items:
        raise HTTPException(status_code=400, detail="沒有選取任何檔案")

    def _task(progress):
        done, failed, saved = [], [], 0
        # each file is recorded once finished, so a restart continues with
        # the rest instead of encoding these again (see jobs.each)
        for idx, src in enumerate(jobs.each(progress, items, key=str), 1):
            info = shrink.probe(src)
            if not info:
                failed.append({"path": str(src), "error": "無法讀取影片資訊"})
                continue
            dst = shrink.output_path(src, crf, max_edge, info)
            jobs.writing(src, dst)    # a fresh name: anything there is ours
            progress({"index": idx, "total": len(items), "name": src.name,
                      "pct": 0.0})
            try:
                shrink.run(src, dst, crf, max_edge, info,
                           progress=lambda f, i=idx, n=src.name: progress(
                               {"index": i, "total": len(items), "name": n,
                                "pct": f}))
            except Exception as e:
                dst.unlink(missing_ok=True)
                failed.append({"path": str(src), "error": str(e)})
                continue
            problem = shrink.verify(info, dst)
            if problem:
                dst.unlink(missing_ok=True)
                failed.append({"path": str(src), "error": problem})
                continue
            got = dst.stat().st_size
            saved += info["size"] - got
            # both files now exist and only a person can pick — see core/pairs
            pairs.record(_PAIRS_PATH, src, dst, "shrink", crf=crf,
                         max_edge=max_edge)
            done.append({"src": str(src), "dst": str(dst), "name": dst.name,
                         "before": info["size"], "after": got,
                         "ratio": round(1 - got / info["size"], 4)})
        _files_changed()
        return {"done": done, "failed": failed, "saved": saved}

    return {"job_id": jobs.start(_task, kind="shrink_run",
                                 label=f"壓縮 {len(items)} 個檔案",
                                 resume=_resumable("shrink_run", body, user))}


@app.get("/api/shrink/sample/play")
def shrink_sample_play(request: Request, path: str = Query(...),
                       user: dict = Depends(auth.require_perm("shrink"))):
    """Play a preview clip.

    These live in the cache, which `is_allowed` rightly refuses — it only
    knows about media roots. So the check here is different but no weaker:
    the resolved path must sit inside the sample directory and nowhere else.
    """
    p = Path(path)
    try:
        rp = p.resolve()
        root = (CACHE_DIR / "shrink_samples").resolve()
        rp.relative_to(root)
    except (OSError, ValueError):
        raise HTTPException(status_code=403, detail="path not allowed")
    if not rp.is_file():
        raise HTTPException(status_code=404, detail="sample not found")
    cachelimit.touch(rp)        # watching it counts as using it
    return _serve_video(request, rp)


# ── pending pairs (decide which of the two to keep) ──────────────────────────

@app.get("/api/pairs")
def list_pairs(user: dict = Depends(auth.require_perm("shrink", "convert"))):
    """Files this app produced beside an original, still awaiting a decision.

    Deliberately not part of the duplicate scan. Tier 1 could never pair these
    (measured — the hashes differ, because the size is mixed into them), Tier 2
    would but is expensive and opt-in, and its keep-ladder points the wrong way
    anyway: it prefers the higher resolution and then the older file, which is
    the original in both cases. These pairs are not accidental duplicates —
    they were made on purpose and the app knows it, so it can just say so.
    """
    pairs.prune(_PAIRS_PATH)
    return {"pairs": pairs.pending(_PAIRS_PATH,
                                   allowed=lambda p: is_allowed(p, user))}


class PairResolveBody(BaseModel):
    original: str
    produced: str
    action: str                 # keep_original | keep_produced | keep_both
    rename: bool = False        # only for keep_produced


@app.post("/api/pairs/resolve")
def resolve_pair(body: PairResolveBody,
                 user: dict = Depends(auth.require_perm("delete"))):
    """Delete one side, or keep both and stop asking."""
    src, dst = Path(body.original), Path(body.produced)
    for p in (src, dst):
        if not is_allowed(p, user):
            raise HTTPException(status_code=403, detail="path not allowed")

    if body.action == "keep_both":
        pairs.resolve(_PAIRS_PATH, src, dst, kept_both=True)
        return {"ok": True, "kept": "both"}

    if body.action == "keep_original":
        doomed, kept = dst, src
    elif body.action == "keep_produced":
        doomed, kept = src, dst
    else:
        raise HTTPException(status_code=400, detail="invalid action")

    if not doomed.is_file():
        pairs.resolve(_PAIRS_PATH, src, dst)
        return {"ok": True, "kept": str(kept), "note": "另一個檔案已經不在了"}

    # This one deletes outside /api/media/delete, so it needs the guard of its
    # own -- and it is refused rather than skipped, because a pending pair is a
    # single decision: there is no partial outcome to report.
    #
    # Transferring the mark to the survivor was considered and rejected: the
    # rule is worth more when it has no exceptions ("nothing deletes a
    # favourite, anywhere") than the two clicks it saves here. The cost is
    # real, though -- unmark, resolve, mark the new file.
    refusal = _fav_refusal(doomed, user, False, True)
    if refusal:
        raise HTTPException(status_code=409, detail=refusal)

    # the same teardown every other delete does — a file removed without it
    # leaves thumbnails and face vectors pointing at nothing
    contactsheet.cancel_prefetch(doomed)
    try:
        _unlink(doomed)
    except Exception as e:
        raise HTTPException(status_code=500,
                            detail=_busy_reason(doomed) or str(e))
    log.info("pair resolved: kept %s, deleted %s", kept.name, doomed.name)
    media.purge_derived(doomed, CACHE_DIR)
    videoflags.forget(doomed)
    filetitle.forget(doomed)
    costar.forget(doomed)
    try:
        faces.forget_video(FACE_DB, doomed)
    except Exception:
        log.exception("could not clear the face index for %s", doomed)

    final = kept
    if body.rename and body.action == "keep_produced":
        # Take over the original's name, keeping the produced file's suffix —
        # `.ts` repaired into `.mp4` must stay `.mp4`.
        target = src.with_suffix(dst.suffix)
        if target.exists() and target != dst:
            log.warning("cannot rename %s to %s: name taken", dst.name,
                        target.name)
        else:
            try:
                # the note is keyed by path, so carry it across before the
                # rename invalidates it; thumbnails and sheets are cheap to
                # rebuild and are simply dropped
                text = media.read_note(dst, CACHE_DIR)
                sidecar = any(_under(dst, r)
                              for r in app_settings.archive_roots_for(user))
                media.purge_derived(dst, CACHE_DIR)
                dst.rename(target)
                if text.strip():
                    media.write_note(target, CACHE_DIR, text, sidecar=sidecar)
                faces.rename_video(FACE_DB, dst, target)
                videoflags.move(dst, target)
                filetitle.move(dst, target)
                costar.move(dst, target)
                final = target
            except Exception as e:
                log.exception("rename failed for %s", dst)
                pairs.resolve(_PAIRS_PATH, src, dst)
                _files_changed()
                return {"ok": True, "kept": str(dst),
                        "note": f"已刪除原檔，但改名失敗：{e}"}

    pairs.resolve(_PAIRS_PATH, src, dst)
    _files_changed()
    return {"ok": True, "kept": str(final),
            "renamed": str(final) != str(kept)}


@app.get("/api/shrink/job/{job_id}")
def shrink_job(job_id: str, user: dict = Depends(auth.require_perm("shrink"))):
    j = jobs.get(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job not found")
    return j


class ConvertRunBody(BaseModel):
    items: list          # [{ path, action }]
    crf: int = 20
    gpu: bool = False


@app.post("/api/convert/run")
def convert_run(body: ConvertRunBody, user: dict = Depends(auth.require_perm("convert"))):
    items = []
    for it in body.items:
        src = Path(it.get("path", ""))
        action = it.get("action")
        if action not in convert.RUNNABLE:
            raise HTTPException(status_code=400, detail=f"invalid action: {action}")
        if not is_allowed(src, user) or not src.is_file():
            raise HTTPException(status_code=403, detail=f"path not allowed: {src}")
        items.append((src, action))

    crf = max(0, min(51, int(body.crf)))
    gpu = bool(body.gpu)
    archive_roots = app_settings.archive_roots_for(user)

    def _task(progress):
        # Logged because a failure here left no trace at all: the user saw
        # "轉檔失敗：undefined" and the log had nothing to say about the run —
        # not even that one had started. A job this slow and this destructive
        # (it deletes the source on success) should be answerable afterwards.
        log.info("convert run: %d items %s crf=%d gpu=%s",
                 len(items), [a for _s, a in items], crf, gpu)
        done, failed, saved = [], [], 0
        # each file is recorded once finished, so a restart continues with
        # the rest — not with a second, lossy pass over these (see jobs.each)
        for idx, (src, action) in enumerate(
                jobs.each(progress, items, key=lambda it: str(it[0])), 1):
            # repair keeps the original, so its output cannot take the plain
            # `.mp4` name — see `repair.target_for`
            # An MP4 whose codec a browser cannot decode has to end up under
            # the same name it already has, so it is encoded to a staging file
            # beside the source and swapped in once verified.
            # `conceal` keeps the original too: it is lossy, and the damaged
            # moment still looks damaged — whether that is better is a call
            # for the pending list, made by someone looking at both
            keeps_source = action in ("repair", "conceal")
            in_place = not keeps_source and convert.is_mp4(src)
            dst = (repair.target_for(src) if keeps_source
                   else convert.staging_for(src) if in_place
                   else convert.target_for(src))
            progress({"index": idx, "total": len(items), "name": src.name,
                      "pct": 0.0, "action": action})
            if dst.exists():
                failed.append({"path": str(src),
                               "error": "同名的 .mp4 已存在" if not in_place
                               else "上一次的暫存檔還在，請先手動刪除"})
                continue
            # checked just above that nothing is there: from here on a file at
            # `dst` is this job's, and a restart may remove a half-written one
            jobs.writing(src, dst)

            # Repair does NOT follow the convert rules below, and deliberately
            # so. Those end by deleting the source once `verify()` matches its
            # duration — but a file that needs repairing cannot be read at all,
            # so there is nothing to compare against, and the damaged original
            # is the only complete copy of the data. It stays; removing it is
            # the user's call in delete mode.
            if action == "repair":
                try:
                    res = repair.repair(
                        src, dst,
                        progress=lambda d, i=idx, n=src.name: progress(
                            {"index": i, "total": len(items), "name": n,
                             "pct": 0.0, "action": "repair", **d}))
                except Exception as e:
                    dst.unlink(missing_ok=True)
                    failed.append({"path": str(src), "error": str(e)})
                    continue
                pairs.record(_PAIRS_PATH, src, dst, "repair",
                             dropped=res.get("dropped"),
                             lost_seconds=res.get("lost_seconds"))
                done.append({"src": str(src), "dst": str(dst),
                             "action": "repair", "kept_source": True,
                             **res})
                continue

            if action == "conceal":
                info = convert.probe(src)
                # Where the damage is, so the pending list can take both
                # players straight there — on a side-by-side of two whole
                # films nobody can find a one-second glitch by eye.
                progress({"index": idx, "total": len(items), "name": src.name,
                          "pct": 0.0, "action": action, "stage": "定位損壞處"})
                regions = decodecheck.locate_cached(src, DECODE_CHECK_DB)
                try:
                    convert.run_one(
                        src, dst, action, info,
                        progress=lambda f, i=idx, n=src.name: progress(
                            {"index": i, "total": len(items), "name": n,
                             "pct": f, "action": "conceal"}))
                except Exception as e:
                    dst.unlink(missing_ok=True)
                    failed.append({"path": str(src), "error": str(e)})
                    continue
                progress({"index": idx, "total": len(items), "name": src.name,
                          "pct": 1.0, "action": action, "stage": "完整解碼驗證"})
                problem = convert.verify(info, dst, action)
                if problem:
                    dst.unlink(missing_ok=True)
                    failed.append({"path": str(src),
                                   "error": f"已保留原檔：{problem}"})
                    continue
                pairs.record(_PAIRS_PATH, src, dst, "conceal", regions=regions,
                             crf=convert.CONCEAL_CRF)
                done.append({"src": str(src), "dst": str(dst), "action": action,
                             "kept_source": True, "regions": regions})
                continue

            if action == "retime":
                # Not an ffmpeg job — the whole point is that `-c copy` cannot
                # do this (five routes measured, all failed). MP4Box rebuilds
                # the composition times from the bitstream instead. It has no
                # progress output, so the stages are reported as they start.
                try:
                    retime.retime(
                        src, dst,
                        progress=lambda d, i=idx, n=src.name: progress(
                            {"index": i, "total": len(items), "name": n,
                             "action": "retime", **d}))
                except Exception as e:
                    dst.unlink(missing_ok=True)
                    failed.append({"path": str(src), "error": str(e)})
                    continue
                # Its own verify: the picture data is copied, so what could go
                # wrong is losing frames while the timestamps are recomputed —
                # and whether the defect is actually gone rather than faithfully
                # reproduced. `convert.verify` cannot answer either.
                problem = retime.verify(src, dst)
            else:
                info = convert.probe(src)
                if action in ("remux", "remux_audio"):
                    # A remux copies a corrupt audio packet into the new MP4
                    # untouched, and the browser fails on it there exactly as
                    # on the source (MTALL-120: converted, still unseekable).
                    # A few seconds to look first; if any are bad, the audio
                    # is re-encoded instead of copied.
                    progress({"index": idx, "total": len(items),
                              "name": src.name, "pct": 0.0, "action": action,
                              "stage": "檢查聲音"})
                    bad_audio = convert.audio_needs_fix(src)
                    if bad_audio:
                        log.info("convert: %s has %d corrupt audio packets; "
                                 "re-encoding the audio", src.name, bad_audio)
                        action = "audiofix"
                if action == "gapfix":
                    # Recomputed here, not trusted from the scan: the packet
                    # indices are what gets dropped, and the file may have
                    # changed since.
                    progress({"index": idx, "total": len(items),
                              "name": src.name, "pct": 0.0, "action": action,
                              "stage": "分析斷訊"})
                    plan = convert.gap_plan(src)
                    if not plan:
                        failed.append({"path": str(src),
                                       "error": "找不到可修復的斷訊（檔案可能已經變了）"})
                        continue
                    info = {**info, "drop": plan["drop"],
                            "fix_audio": convert.audio_needs_fix(src) > 0}
                    log.info("convert: %s gapfix, %d dropouts, fix_audio=%s",
                             src.name, plan["gaps"], info["fix_audio"])
                if action == "gapcut":
                    # Recomputed here too: the cut points are what get written.
                    progress({"index": idx, "total": len(items),
                              "name": src.name, "pct": 0.0, "action": action,
                              "stage": "分析空白"})
                    plan = convert.blank_plan(src)
                    if not plan or not plan["blanks"]:
                        failed.append({"path": str(src),
                                       "error": "找不到可去除的空白（檔案可能已經變了）"})
                        continue
                    # the expected length, for progress and for `verify`
                    info = {**info, "blanks": plan["blanks"],
                            "frames": plan["frames"],
                            "duration": max(info["duration"] - plan["removed"], 0)}
                    log.info("convert: %s gapcut, %d blanks, %.0f s removed",
                             src.name, len(plan["blanks"]), plan["removed"])
                try:
                    convert.run_one(
                        src, dst, action, info, crf=crf, gpu=gpu,
                        progress=lambda f, i=idx, n=src.name, a=action: progress(
                            {"index": i, "total": len(items), "name": n,
                             "pct": f, "action": a}))
                except Exception as e:
                    dst.unlink(missing_ok=True)
                    failed.append({"path": str(src), "error": str(e)})
                    continue

                if action == "gapfix":
                    # the verify decodes the whole output — minutes on a long film
                    progress({"index": idx, "total": len(items),
                              "name": src.name, "pct": 1.0, "action": action,
                              "stage": "完整解碼驗證（需要幾分鐘）"})
                problem = convert.verify(info, dst, action)
            if problem:
                # keep both files and let the user look — never delete on doubt
                if in_place:
                    # ...but the staging file is not something to look at: it
                    # sits beside the source under a name that is not a video
                    # anyone asked for, and leaving it would block the retry.
                    dst.unlink(missing_ok=True)
                failed.append({"path": str(src),
                               "error": f"驗證失敗，已保留原檔：{problem}"})
                continue

            if in_place:
                # The path does not change, so notes, face vectors and the
                # watched/favourite marks all stay correct by definition --
                # this is the same film under the same name. What is now wrong
                # is everything derived from the *content*: thumbnails and
                # contact sheets still key to this path and would keep serving
                # frames from a file that no longer exists.
                before = src.stat().st_size
                try:
                    contactsheet.cancel_prefetch(src)
                    convert.replace_original(src, dst)
                except Exception as e:
                    dst.unlink(missing_ok=True)
                    failed.append({"path": str(src),
                                   "error": f"轉檔成功但無法取代原檔：{e}"})
                    continue
                try:
                    text = media.read_note(src, CACHE_DIR)
                    sidecar = any(_under(src, r) for r in archive_roots)
                    media.purge_derived(src, CACHE_DIR)
                    if text.strip():
                        media.write_note(src, CACHE_DIR, text, sidecar=sidecar)
                except Exception:
                    log.exception("post-convert bookkeeping failed for %s", src)
                saved += before - src.stat().st_size
                done.append({"src": str(src), "dst": str(src), "action": action,
                             "in_place": True})
                continue

            before = src.stat().st_size
            try:
                # Read the note first: purging the source wipes the sidecar,
                # and .ts/.mp4 share a stem so it is literally the same file
                # the new note would live in.
                text = media.read_note(src, CACHE_DIR)
                sidecar = any(_under(src, r) for r in archive_roots)
                media.purge_derived(src, CACHE_DIR)
                if text.strip():
                    media.write_note(dst, CACHE_DIR, text, sidecar=sidecar)
                faces.rename_video(FACE_DB, src, dst)
                videoflags.move(src, dst)
                filetitle.move(src, dst)
                costar.move(src, dst)
            except Exception:
                log.exception("post-convert bookkeeping failed for %s", src)

            try:
                _unlink(src)
            except Exception as e:
                failed.append({"path": str(src),
                               "error": f"轉檔成功但原檔刪除失敗：{e}"})
                continue
            saved += before - dst.stat().st_size
            done.append({"src": str(src), "dst": str(dst), "action": action})

        _files_changed()
        log.info("convert done: %d converted, %d failed, saved %.0f MB%s",
                 len(done), len(failed), saved / 1048576,
                 "".join(f"\n    FAILED {f['path']}: {f['error']}"
                         for f in failed))
        return {"converted": done, "failed": failed, "saved": saved}

    return {"job_id": jobs.start(_task, kind="convert_run",
                                 label=f"轉檔 {len(items)} 個檔案",
                                 resume=_resumable("convert_run", body, user))}


@app.get("/api/convert/job/{job_id}")
def convert_job(job_id: str, user: dict = Depends(auth.require_perm("convert"))):
    j = jobs.get(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job not found")
    return j


@app.post("/api/dedup/scan")
def dedup_scan(body: DedupBody, user: dict = Depends(auth.require_perm("dedup"))):
    inbox = {str(p) for p in app_settings.inbox_paths_for(user)}
    if body.roots is not None:
        if not body.roots:
            raise HTTPException(status_code=400, detail="請至少選擇一個資料夾")
        roots = []
        for r in body.roots:
            p = Path(r)
            if not is_allowed(p, user):
                raise HTTPException(status_code=403, detail=f"root not allowed: {r}")
            roots.append(p)
    else:
        roots = list(app_settings.archive_roots_for(user))

    # Where each file lives — the report's first tie-breaker for "which copy to
    # keep" is 歸檔 over 待分類.
    labels = {str(p): ("待分類" if str(p.resolve()) in inbox else f"歸檔/{p.name}")
              for p in roots}

    def _task(progress):
        reused: dict = {}
        groups = dedup.find_duplicates(roots, tier2=body.tier2,
                                       threshold=body.threshold,
                                       root_labels=labels, progress=progress,
                                       count_db=_SCAN_COUNTS,
                                       cache_db=VIDEO_HASH_DB, stats=reused)
        stopped = bool(getattr(progress, "cancelled", False))
        t1 = sum(1 for g in groups if g["tier"] == 1)
        log.info("dedup scan: %d roots, tier2=%s → %d groups "
                 "(%d tier1, %d tier2)%s; reused %s",
                 len(roots), body.tier2, len(groups), t1, len(groups) - t1,
                 " [stopped early]" if stopped else "", reused or "nothing")
        # A stopped scan still found real duplicates, so the report is worth
        # keeping — but it must say it is partial, or reopening it tomorrow
        # looks like a complete scan that found suspiciously little.
        report = {"at": time.time(), "tier2": body.tier2,
                  "roots": [str(r) for r in roots], "groups": groups,
                  "partial": stopped}
        # history first: on the first run after upgrading, its migration
        # copies the *previous* report out of the legacy file -- written the
        # other way round, it would copy this same scan in twice
        _save_history("video", report, user)
        _save_report(report, _REPORT_PATH)
        return groups

    job_id = jobs.start(_task, kind="dedup", label="重複掃描",
                        resume=_resumable("dedup_scan", body, user))
    return {"job_id": job_id}


# A scan over a large library costs minutes of ffprobe and frame hashing, and
# a report of several hundred groups is not something anyone works through in
# one sitting. Keeping it on disk means a refresh — or coming back tomorrow —
# does not throw that work away.
_REPORT_PATH = DATA_DIR / "dedup_report.json"
_IMG_REPORT_PATH = DATA_DIR / "imgdedup_report.json"
# perceptual hashes, keyed by path+size+mtime; a re-scan of an
# unchanged library is ~45x faster with it than without
IMG_HASH_DB = DATA_DIR / "img_hash.sqlite3"
# the video scan's head hashes, durations and frame hashes (dedup.CACHE_VERSIONS)
VIDEO_HASH_DB = DATA_DIR / "video_hash.sqlite3"
# verdicts from the person detector, same (path, size, mtime) key as the
# image hashes so a replaced photo re-scans itself
PERSON_DB = DATA_DIR / "person.sqlite3"
# Gfriends' file index (6.5 MB, refreshed at most daily); the pictures
# themselves go in the size-limited cache, see core/gfriends.py
gfriends.INDEX_PATH = DATA_DIR / "gfriends-filetree.json"
# how many files each root held last time — the only way the folder-walk phase
# can show progress at all, since its size is unknown until it finishes
_SCAN_COUNTS = DATA_DIR / "scan_counts.json"


def _save_report(data: dict, path: Path):
    # No default for `path`. A default argument binds at import time, so
    # pointing _REPORT_PATH somewhere safe before a test run had no effect and
    # the real saved report was overwritten. Every caller now says where.
    try:
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        log.exception("could not save the duplicate report")


class _PathFacts:
    """Whether each path is the user's to see and still on disk — worked out
    once per request, many at a time.

    Opening the history took 51 s and one saved report 39 s (2026-10-26):
    every file asked `is_allowed` on its own, which re-reads the settings and
    resolves every root (~16 ms, settings.path_gate), then resolved itself and
    stat'ed itself over the NAS — one after the other, and for the history
    again for each of twenty reports. Now the roots are worked out once, the
    per-file checks (network round trips, so waiting not working) run sixteen
    at a time, and a file in several reports is checked once.
    """

    def __init__(self, user: dict):
        self.gate = _path_gate(user)
        self.facts: dict = {}

    def learn(self, paths) -> None:
        todo = [p for p in set(paths) if p not in self.facts]
        if not todo:
            return

        def one(p):
            pp = Path(p)
            ok = self.gate(pp)
            return p, (ok, ok and pp.is_file())
        with ThreadPoolExecutor(max_workers=16) as ex:
            self.facts.update(ex.map(one, todo))

    def allowed(self, p: str) -> bool:
        return self.facts[p][0]

    def exists(self, p: str) -> bool:
        return self.facts[p][1]


def _report_paths(data: dict) -> list:
    return [f["path"] for g in data.get("groups", []) for f in g.get("files", [])]


def _load_report(path: Path, user: dict,
                 facts: Optional[_PathFacts] = None) -> Optional[dict]:
    """A saved scan, filtered to what this user may see.

    Files deleted since the scan are flagged rather than dropped, so the report
    still shows what was done instead of silently shrinking.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    facts = facts or _PathFacts(user)
    facts.learn(_report_paths(data))

    groups = []
    for g in data.get("groups", []):
        files = [f for f in g.get("files", []) if facts.allowed(f["path"])]
        if len(files) < 2:
            continue                    # nothing left to compare for this user
        for f in files:
            f["deleted"] = not facts.exists(f["path"])
        if sum(1 for f in files if not f["deleted"]) < 2:
            continue                    # already dealt with
        groups.append({**g, "files": files})

    return {**{k: v for k, v in data.items() if k != "groups"},
            "groups": groups}


# ── report history ───────────────────────────────────────────────────────────
# Every scan is kept (the last reporthistory.KEEP per kind), not just the
# latest: "重新掃描" followed by closing the dialog used to leave the user one
# scan away from losing the report, with no way back to it from the picker.
# Shared by every account with the dedup permission, like the single report
# before it, and filtered per user on the way out by `_load_report`.

_HISTORY_DIR = {"video": DATA_DIR / "dedup_history" / "video",
                "image": DATA_DIR / "dedup_history" / "image"}
_LEGACY_REPORT = {"video": _REPORT_PATH, "image": _IMG_REPORT_PATH}


def _history_dir(kind: str) -> Path:
    if kind not in _HISTORY_DIR:
        raise HTTPException(status_code=400, detail="kind must be video or image")
    folder = _HISTORY_DIR[kind]
    reporthistory.migrate(_LEGACY_REPORT[kind], folder)   # first run only
    return folder


def _save_history(kind: str, report: dict, user: Optional[dict] = None) -> None:
    try:
        folder = _history_dir(kind)
        rid = reporthistory.save(folder, report)
        if user:
            # the one who ran it is looking at it now: as good as opened
            reporthistory.remember(folder, rid, user["id"], len(report.get("groups", [])))
    except Exception:
        log.exception("could not save %s scan to history", kind)


def _latest_report_path(kind: str) -> Optional[Path]:
    """The report to bring back when the tab opens: the last scan's.

    The newest history entry — unless the last scan's own entry has been
    deleted from the history. Then there is nothing to bring back: the user
    deleted xiaojie1218_'s two reports and the tab opened on Chelsealynn's
    from five days earlier (2026-10-27), a report they had not asked for.
    Older ones stay one click away in the history. The legacy single file is
    rewritten by every scan, so its time is the last scan's."""
    folder = _history_dir(kind)
    legacy = _LEGACY_REPORT[kind]
    newest = reporthistory.ids(folder)
    if not newest:
        return legacy
    path = folder / f"{newest[0]}.json"
    try:
        last_scan = json.loads(legacy.read_text(encoding="utf-8")).get("at") or 0
        entry = json.loads(path.read_text(encoding="utf-8")).get("at") or 0
    except Exception:
        return path                 # no legacy file to compare: as before
    return None if last_scan - entry > 1 else path


@app.get("/api/dedup/history")
def dedup_history(kind: str = Query("video"),
                  user: dict = Depends(auth.require_perm("dedup"))):
    """Saved scans, newest first — read from the records alone.

    No file is checked here (the user's call, 2026-10-27): counting what is
    left in every report meant checking every file of twenty reports on the
    NAS just to draw the list, 51 s once. A report's files are checked when
    it is opened, and what that found is shown here as `remaining` /
    `remaining_at` — as of that opening; None if this account never opened it.
    """
    folder = _history_dir(kind)
    seen = reporthistory.remembered(folder, user["id"])
    gate = _path_gate(user)
    root_ok: dict = {}           # a handful of roots, each asked once

    def allowed(r):
        if r not in root_ok:
            root_ok[r] = gate(Path(r))
        return root_ok[r]
    out = []
    for i, rid in enumerate(reporthistory.ids(folder)):
        try:
            raw = json.loads((folder / f"{rid}.json").read_text(encoding="utf-8"))
        except Exception:
            continue
        roots = [r for r in raw.get("roots", []) if allowed(r)]
        last = seen.get(rid)
        out.append({"id": rid, "at": raw.get("at"), "latest": i == 0,
                    "roots": [Path(r).name or r for r in roots],
                    "tier2": raw.get("tier2"),
                    "max_distance": raw.get("max_distance"),
                    "partial": bool(raw.get("partial")),
                    "groups": len(raw.get("groups", [])),
                    "remaining": last["n"] if last else None,
                    "remaining_at": last["at"] if last else None})
    return {"entries": out, "keep": reporthistory.KEEP}


def _load_and_remember(kind: str, path: Optional[Path], user: dict) -> Optional[dict]:
    """Open a saved report (its files checked now) and note what was left,
    for the history list to show without checking anything."""
    if path is None:
        return None
    rep = _load_report(path, user)
    folder = _history_dir(kind)
    if rep is not None and path.parent == folder:
        reporthistory.remember(folder, path.stem, user["id"], len(rep["groups"]))
    return rep


@app.get("/api/dedup/history/{kind}/{rid}")
def dedup_history_report(kind: str, rid: str,
                         user: dict = Depends(auth.require_perm("dedup"))):
    path = reporthistory.path_of(_history_dir(kind), rid)
    if not path:
        raise HTTPException(status_code=404, detail="這份紀錄已不存在")
    return {"report": _load_and_remember(kind, path, user), "id": rid}


@app.delete("/api/dedup/history/{kind}/{rid}")
def dedup_history_delete(kind: str, rid: str,
                         user: dict = Depends(auth.require_perm("dedup"))):
    """Removes the record only — never any video or photo."""
    if not reporthistory.delete(_history_dir(kind), rid):
        raise HTTPException(status_code=404, detail="這份紀錄已不存在")
    return {"deleted": rid}


@app.get("/api/relations")
def video_relations(path: str = Query(...), user: dict = Depends(auth.current_user)):
    """Everything known about how this film relates to others (AVBase): what
    it is cut from, what compilations hold it, what is cut from it — with the
    other film's title and date, and where it is in the library if it is.

    For the player's 收錄 row. Shown to everyone who can play the film: it is
    information about the film, not an action."""
    p = _checked_path(path, user)
    vid = media.detect_video_id(p.name)
    rels = avbase.relations_of(vid) if vid else []
    if not rels:
        return {"relations": []}
    idx = searchindex.get(app_settings.archive_roots_for(user),
                          list(app_settings.inbox_paths_for(user)),
                          scoped=bool(user.get("allowed_roots")))
    wanted = {r["other"] for r in rels}
    here: dict = {}
    for row in idx.get("v", []):
        o = media.detect_video_id(row[0])
        o = o.upper() if o else ""
        if o in wanted and o not in here:
            here[o] = {"path": str(Path(idx["dirs"][row[2]]) / row[0]), "name": row[0],
                       "char": "待分類" if row[3] else (row[1] or "")}
    for r in rels:
        row = javdb.title_row(r["other"])       # with the shared catalogue's
        r["title"] = javdb.best_title(row) or r["other_title"]
        r["date"] = row.get("released_manual") or row.get("released") or r["other_date"]
        r["have"] = here.get(r["other"])
    # this film's own side first: what it comes from, then what it holds
    rels.sort(key=lambda r: (r["this_is"] != "child", r["date"] or "9999"))
    return {"relations": rels}


@app.get("/api/dedup/relations")
def dedup_relations(user: dict = Depends(auth.require_perm("dedup"))):
    """Films whose footage is held whole by another film here — the same
    footage under another number (AVBase's relations, core/avbase.py).

    No scan (the user's call, 2026-10-27): the relation is already stored, so
    this only looks up which numbers the library has, in the search index
    that is already cached. Only the files of the groups found are probed,
    through the scan's own cache."""
    rels = avbase.relations()
    if not rels:
        return {"groups": [], "relations": 0}
    idx = searchindex.get(app_settings.archive_roots_for(user),
                          list(app_settings.inbox_paths_for(user)),
                          scoped=bool(user.get("allowed_roots")))
    owned: dict = {}
    for r in idx.get("v", []):
        vid = media.detect_video_id(r[0])
        if vid:
            owned.setdefault(vid.upper(), []).append(
                (Path(idx["dirs"][r[2]]) / r[0], "待分類" if r[3] else (r[1] or "")))
    found = avbase.redundant_groups(owned, rels)
    store = dedup.Store(VIDEO_HASH_DB)
    try:
        def files(vid, role):
            out = []
            for path, where in owned[vid]:
                try:
                    st = path.stat()
                except OSError:
                    continue            # the index is a little behind the disk
                dur, w, h = dedup._probe(store, path, st.st_size, st.st_mtime)
                out.append({"path": str(path), "name": path.name, "size": st.st_size,
                            "mtime": st.st_mtime, "duration": dur, "width": w,
                            "height": h, "label": where, "vid": vid, "role": role})
            return out
        groups = []
        for g in found:
            fs = files(g["parent"], "parent")
            for c in g["children"]:
                fs += files(c["id"], "child")
            if any(f["role"] == "parent" for f in fs) and any(f["role"] == "child" for f in fs):
                groups.append({"relation": g, "files": fs})
    finally:
        store.close()
    return {"groups": groups, "relations": len(rels)}


@app.get("/api/dedup/report")
def dedup_report(user: dict = Depends(auth.require_perm("dedup"))):
    return {"report": _load_and_remember("video", _latest_report_path("video"), user)}


class ImgDedupBody(BaseModel):
    roots: Optional[list] = None
    max_distance: int = imgdedup.DEFAULT_MAX_DISTANCE


@app.post("/api/imgdedup/scan")
def imgdedup_scan(body: ImgDedupBody,
                  user: dict = Depends(auth.require_perm("dedup"))):
    inbox = {str(p) for p in app_settings.inbox_paths_for(user)}
    if body.roots is not None:
        if not body.roots:
            raise HTTPException(status_code=400, detail="請至少選擇一個資料夾")
        roots = []
        for r in body.roots:
            p = Path(r)
            if not is_allowed(p, user):
                raise HTTPException(status_code=403, detail=f"root not allowed: {r}")
            roots.append(p)
    else:
        roots = list(app_settings.archive_roots_for(user))

    labels = {str(p): ("待分類" if str(p.resolve()) in inbox else f"歸檔/{p.name}")
              for p in roots}
    # clamped so a hand-crafted request cannot ask for "everything matches"
    max_distance = max(0, min(int(body.max_distance), 40))

    def _task(progress):
        groups = imgdedup.find_duplicates(
            roots, tier2=True, max_distance=max_distance,
            cache_db=IMG_HASH_DB, root_labels=labels, progress=progress,
            count_db=_SCAN_COUNTS)
        stopped = bool(getattr(progress, "cancelled", False))
        t1 = sum(1 for g in groups if g["tier"] == 1)
        log.info("image dedup: %d roots, distance<=%d → %d groups (%d exact)%s",
                 len(roots), max_distance, len(groups), t1,
                 " [stopped early]" if stopped else "")
        report = {"at": time.time(), "max_distance": max_distance,
                  "roots": [str(r) for r in roots], "groups": groups,
                  "partial": stopped}
        # history first: on the first run after upgrading, its migration
        # copies the *previous* report out of the legacy file -- written the
        # other way round, it would copy this same scan in twice
        _save_history("image", report, user)
        _save_report(report, _IMG_REPORT_PATH)
        return groups

    return {"job_id": jobs.start(_task, kind="imgdedup", label="圖片重複掃描",
                                 resume=_resumable("imgdedup_scan", body, user))}


@app.get("/api/imgdedup/report")
def imgdedup_report(user: dict = Depends(auth.require_perm("dedup"))):
    return {"report": _load_and_remember("image", _latest_report_path("image"), user),
            "safe_distance": imgdedup.SAFE_BATCH_DISTANCE,
            "default_distance": imgdedup.DEFAULT_MAX_DISTANCE}


# ── person scan ──────────────────────────────────────────────────────────────
# Flags photos with nobody in them so they can be reviewed in the ordinary
# photo grid. It never deletes: the verdict comes from a model, and the whole
# point of showing them in the grid is that you check them yourself.
#
# Scoped to one character's folder rather than the library. Measured: 1,136
# photos take 72 s on the GPU, and a second pass over the same folder is 0.1 s
# because the verdicts are cached on (path, size, mtime).

@app.get("/api/person/status")
def person_status(user: dict = Depends(auth.current_user)):
    return personscan.probe(DATA_DIR)


class PersonScanBody(BaseModel):
    folder: str
    # clamped server-side; a hand-made request must not be able to ask for
    # a threshold that flags a fifth of the library as person-free
    conf: Optional[float] = None


@app.post("/api/person/scan")
def person_scan(body: PersonScanBody,
                user: dict = Depends(auth.require_perm("person"))):
    # _checked_path insists on a file; this takes a folder, so the pair of
    # checks is spelled out the way the other folder endpoints do it
    folder = Path(body.folder)
    if not is_allowed(folder, user) or not folder.is_dir():
        raise HTTPException(status_code=404, detail="資料夾不存在或無權存取")
    p = personscan.probe(DATA_DIR)
    if not (p["deps"] and p["model"]):
        raise HTTPException(status_code=503,
                            detail=p["reason"] or "人物偵測不可用")

    photos = []
    for f in folder.rglob("*"):
        if f.suffix.lower() in media.IMAGE_EXT and not media.in_vam_dir(f, folder):
            try:
                st = f.stat()
            except OSError:
                continue
            photos.append((f, st.st_size, st.st_mtime))
    if not photos:
        raise HTTPException(status_code=400, detail="這個資料夾裡沒有照片")

    conf = personscan.clamp_conf(body.conf if body.conf is not None
                                 else personscan.DEFAULT_CONF)

    def _task(progress):
        r = personscan.scan(photos, DATA_DIR, PERSON_DB, progress, conf)
        log.info("person scan: %s → %d/%d 沒有人物"
                 "（device=%s，conf=%.2f，實算 %d）",
                 folder.name, len(r["no_person"]), r["total"], r["device"],
                 r["conf"], r["scanned"])
        return r

    return {"job_id": jobs.start(_task, kind="personscan",
                                 label=f"人物辨識：{folder.name}",
                                 resume=_resumable("person_scan", body, user))}


@app.get("/api/person/job/{job_id}")
def person_job(job_id: str, user: dict = Depends(auth.require_perm("person"))):
    """Every long task in this app has its own job-status route; there is no
    generic `/api/jobs/{id}`. Polling the one that does not exist returns a
    plain 404, which the UI would have shown as "後端需要重啟"."""
    j = jobs.get(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job not found")
    return j


# ── 相似 sort on the profile photo grid (core/photosimilar.py) ───────────────

# One running hash job per folder. Closing the dialog leaves it running (the
# user's call: the hashes are cached, so finishing is never wasted), and
# reopening the dialog has to find that job again rather than start a second
# one reading the same files.
_similar_jobs: Dict[str, str] = {}
_similar_lock = threading.Lock()


def _similar_folder(folder: str, user: dict) -> Path:
    base = Path(folder)
    if not is_allowed(base, user) or not base.is_dir():
        raise HTTPException(status_code=404, detail="資料夾不存在或無權存取")
    return base


def _similar_photos(base: Path) -> list:
    return [(p, sz, m) for p, is_vid, m, sz in media.scan_profile_media_stat(base)
            if not is_vid]


def _similar_running(base: Path) -> Optional[str]:
    with _similar_lock:
        jid = _similar_jobs.get(str(base))
        j = jobs.get(jid) if jid else None
        if j and j["status"] == "running":
            return jid
        _similar_jobs.pop(str(base), None)
        return None


@app.get("/api/profile/similar")
def profile_similar(folder: str = Query(...), threshold: int = Query(None),
                    color: int = Query(None),
                    user: dict = Depends(auth.current_user)):
    """Groups at `threshold`, from cached hashes only — never reads a photo.

    Cheap enough to call on every slider change: 0.25 s to read the cache and
    about 0.5 s to group 4,392 photos. `missing` says how many photos are not
    hashed yet; they are simply absent from the groups until a scan fills them.
    """
    base = _similar_folder(folder, user)
    photos = _similar_photos(base)
    have, colors, missing = photosimilar.split_cached(photos, IMG_HASH_DB)
    stored = photosimilar.saved(base) or {}
    th = photosimilar.clamp(threshold if threshold is not None
                            else stored.get("threshold",
                                            photosimilar.DEFAULT_THRESHOLD))
    # A folder applied before the colour layer existed has color NULL; it
    # keeps its old framing-only order until an editor applies again.
    if color is not None:
        co = photosimilar.clamp_color(color)
    elif stored:
        co = stored.get("color") or 0
    else:
        co = photosimilar.COLOR_DEFAULT
    gmap, n_groups = photosimilar.group_map(have, th, colors, co)
    return {"threshold": th, "total": len(photos), "cached": len(have),
            "missing": len(missing), "groups": gmap, "group_count": n_groups,
            "grouped": len(gmap), "running": _similar_running(base),
            "color": co, "saved": stored or None,
            "limits": {"min": photosimilar.MIN_THRESHOLD,
                       "max": photosimilar.MAX_THRESHOLD,
                       "default": photosimilar.DEFAULT_THRESHOLD,
                       "color_min": photosimilar.COLOR_MIN,
                       "color_max": photosimilar.COLOR_MAX,
                       "color_default": photosimilar.COLOR_DEFAULT}}


class SimilarScanBody(BaseModel):
    folder: str


@app.post("/api/profile/similar/scan")
def profile_similar_scan(body: SimilarScanBody,
                         user: dict = Depends(auth.require_perm("person"))):
    base = _similar_folder(body.folder, user)
    running = _similar_running(base)
    if running:
        return {"job_id": running, "reattached": True}
    _have, _colors, missing = photosimilar.split_cached(_similar_photos(base),
                                                        IMG_HASH_DB)

    def _task(progress):
        r = photosimilar.fill(missing, IMG_HASH_DB, progress)
        log.info("similar scan: %s → hashed %d, unreadable %d in %.1fs",
                 base.name, r["hashed"], r["unreadable"], r["seconds"])
        return r

    jid = jobs.start(_task, kind="photosimilar", label=f"相似掃描：{base.name}",
                     resume=_resumable("profile_similar_scan", body, user))
    with _similar_lock:
        _similar_jobs[str(base)] = jid
    return {"job_id": jid, "reattached": False}


class SimilarApplyBody(BaseModel):
    folder: str
    threshold: int
    color: int = 0          # whole percent; 0 = colour layer off


@app.post("/api/profile/similar/apply")
def profile_similar_apply(body: SimilarApplyBody,
                          user: dict = Depends(auth.require_perm("person"))):
    """Store the threshold for this folder. Editor only: it changes the order
    every other viewer of the folder gets, in browse mode included."""
    base = _similar_folder(body.folder, user)
    return photosimilar.save(base, body.threshold, body.color)


@app.get("/api/profile/similar/job/{job_id}")
def profile_similar_job(job_id: str, user: dict = Depends(auth.current_user)):
    j = jobs.get(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job not found")
    return j


@app.get("/api/person/flags")
def person_flags(folder: str = Query(...),
                 user: dict = Depends(auth.current_user)):
    """Cached verdicts for one folder — what the grid filters on.

    Reports `pending` (photos never scanned) so the UI can say "12 張還沒判斷"
    instead of quietly implying the answer covers everything.
    """
    d = Path(folder)
    if not is_allowed(d, user) or not d.is_dir():
        raise HTTPException(status_code=404, detail="資料夾不存在或無權存取")
    # Read at the threshold this folder was actually scanned with, not at
    # whatever the slider happens to show: the verdicts exist, they are just
    # filed under another key, and asking with the wrong one would report the
    # whole folder as "never scanned".
    probe_cache = personscan.Cache(PERSON_DB)
    used = probe_cache.last_conf(d)
    probe_cache.close()
    cache = personscan.Cache(PERSON_DB,
                             used if used is not None else personscan.DEFAULT_CONF)
    try:
        no_person, pending, total = [], 0, 0
        for f in d.rglob("*"):
            if f.suffix.lower() not in media.IMAGE_EXT or media.in_vam_dir(f, d):
                continue
            try:
                st = f.stat()
            except OSError:
                continue
            total += 1
            n = cache.get(f, st.st_size, st.st_mtime)
            if n is None:
                pending += 1
            elif n == 0:
                no_person.append(str(f))
    finally:
        cache.close()
    return {"total": total, "pending": pending, "no_person": no_person,
            "conf": cache.conf, "scanned_conf": used,
            "conf_min": personscan.CONF_MIN, "conf_max": personscan.CONF_MAX,
            "conf_default": personscan.DEFAULT_CONF}


@app.get("/api/jobs")
def list_jobs(user: dict = Depends(auth.require_editor)):
    """What is still running, so a reloaded page can pick the thread back up.

    Without this a refresh strands the job: it keeps burning CPU and NAS I/O
    server-side while the browser has forgotten its id and shows nothing.
    """
    return {"jobs": [{k: j[k] for k in
                      ("id", "kind", "label", "status", "progress", "started_at")}
                     for j in jobs.active()]}


@app.get("/api/dedup/job/{job_id}")
def dedup_job(job_id: str, user: dict = Depends(auth.require_perm("dedup"))):
    j = jobs.get(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job not found")
    return j


# Only these scans check the flag. Accepting it for any job would report
# "已停止" on work that in fact ran to completion — a lie the UI cannot detect.
_CANCELLABLE = {"dedup", "imgdedup", "personscan", "convert_decode"}


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str, user: dict = Depends(auth.require_editor)):
    j = jobs.get(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job not found")
    if j["kind"] not in _CANCELLABLE:
        raise HTTPException(status_code=400, detail="這個工作無法中途停止")
    return {"stopping": jobs.cancel(job_id)}


# ── jobs a restart interrupted ───────────────────────────────────────────────
# See jobs.py for how they are recorded. What happens to each, by endpoint:
#
#   scans (read only)  rerun by themselves at startup, under the same job id —
#                      a page still polling it carries on. The per-file caches
#                      (hashes, probes, decode checks) make the part already
#                      done nearly free, so "rerun" is in effect "continue".
#   writes             never by themselves: you may have restarted precisely
#                      to stop one. They become a notice — "轉檔：完成 12／40" —
#                      and continue, with only the rest, when you say so.
#
# Not here: the 壓縮 scan (its list exists only in the dialog that asked, so a
# rerun would have nowhere to go) and the javdb seed (do not run it at all).

JOBS_RUNNING = DATA_DIR / "jobs_running.json"
JOBS_INTERRUPTED = DATA_DIR / "jobs_interrupted.json"

# A scan that keeps dying with the service — say it takes the machine down —
# must not come back forever. After this many automatic reruns it waits for you.
_AUTO_RESUMES = 2


def _rest_by_path(field: str):
    """The items of a many-file job that are neither finished nor gone."""
    def rest(body: dict, done: dict):
        def key(it):
            return it.get("path", "") if isinstance(it, dict) else str(it)
        left = [it for it in body.get(field) or []
                if key(it) not in done and Path(key(it)).is_file()]
        return ({**body, field: left}, len(left)) if left else None
    return rest


def _rest_if(field: str, test):
    """A one-shot job: all of it again, while its source is still there."""
    def rest(body: dict, done: dict):
        return (body, None) if test(Path(body.get(field) or "")) else None
    return rest


def _rest_merge(body: dict, done: dict):
    # the moves already made took their source with them
    left = [it for it in body.get("plan") or []
            if Path(it.get("src", "")).exists()]
    if not any(it.get("action") != "merge_info" for it in left):
        return None
    return {**body, "plan": left}, None


def _resume_table() -> dict:
    """endpoint → (function, body model, permission, what is left or None).

    `None` for the last means a scan: rerun as is, automatically. Built when
    asked, because the endpoints are defined further down this module.
    """
    return {
        "dedup_scan": (dedup_scan, DedupBody, "dedup", None),
        "imgdedup_scan": (imgdedup_scan, ImgDedupBody, "dedup", None),
        "convert_scan": (convert_scan, ConvertScanBody, "convert", None),
        "person_scan": (person_scan, PersonScanBody, "person", None),
        "profile_similar_scan": (profile_similar_scan, SimilarScanBody,
                                 "person", None),
        "face_index": (face_index, FaceIndexBody, "person", None),
        "idolprofile_scan": (idolprofile_scan, IdolScanBody, "lookup", None),
        "movietitle_scan": (movietitle_scan, TitleScanBody, "lookup", None),
        "convert_run": (convert_run, ConvertRunBody, "convert",
                        _rest_by_path("items")),
        "shrink_run": (shrink_run, ShrinkRunBody, "shrink",
                       _rest_by_path("paths")),
        "archive_video": (archive_video, ArchiveBody, "archive",
                          _rest_if("path", Path.is_file)),
        "archive_folder": (archive_folder, FolderArchiveBody, "archive",
                           _rest_if("folder", Path.is_dir)),
        "merge_run": (merge_run, MergeRunBody, "rename_merge", _rest_merge),
        "clip_run": (clip_run, ClipBody, "clip", _rest_if("path", Path.is_file)),
        # all of it again: what is already written compares as unchanged
        "nfo_apply": (nfo_apply, NfoBody, "lookup", lambda body, done: (body, None)),
    }


def _job_owner(entry: dict) -> Optional[dict]:
    """The account the job ran for, as it is *now* — removed, disabled or
    stripped of the permission means the job does not run again."""
    u = auth.get_user_by_id(entry.get("user")) if entry.get("user") else None
    return u if u and not u.get("disabled") else None


def _run_entry(entry: dict, body: dict, user: dict) -> dict:
    """Start the endpoint again, exactly as a request would — every check of
    roots and paths is made again against today's settings."""
    fn, model, perm, _rest = _resume_table()[entry["endpoint"]]
    if perm not in auth.perms_of(user):
        raise HTTPException(status_code=403, detail="已沒有這項功能的權限")
    return fn(model(**body), user)


def _recover_jobs():
    """Startup: rerun the interrupted scans, keep the rest for their owners."""
    # repair works in the system temp folder; nothing of ours is running yet,
    # so whatever it left there belongs to a run that was killed
    for d in Path(tempfile.gettempdir()).glob("vamrepair-*"):
        shutil.rmtree(d, ignore_errors=True)
    table = _resume_table()
    for entry in jobs.recover():
        name = entry.get("endpoint")
        if name not in table:
            continue
        user = _job_owner(entry)
        if not user:
            log.info("interrupted job %s not resumed: its account is gone", name)
            continue
        if table[name][3] is not None:
            jobs.add_notice(entry)
            log.info("interrupted job kept for its owner: %s", entry.get("label"))
            continue
        if entry.get("attempt", 0) >= _AUTO_RESUMES:
            jobs.add_notice(entry, reason=f"重啟後已自動接續 {_AUTO_RESUMES} 次"
                                          "又被中斷，這次等你決定")
            continue
        try:
            with jobs.resuming(entry):
                _run_entry(entry, entry.get("body") or {}, user)
            log.info("resumed after restart: %s (attempt %d)",
                     entry.get("label"), entry.get("attempt", 0) + 1)
        except HTTPException as e:
            jobs.add_notice(entry, reason=f"無法接續：{e.detail}")
        except Exception as e:
            log.exception("could not resume %s", name)
            jobs.add_notice(entry, reason=f"無法接續：{e}")


def _notice_view(n: dict) -> dict:
    """What the page shows: no request bodies (paths, plans) beyond counts."""
    rest = _resume_table().get(n.get("endpoint"), (None,) * 4)[3]
    body, done = n.get("body") or {}, n.get("done") or {}
    total = None
    for field in ("items", "paths"):
        if isinstance(body.get(field), list):
            total = len(body[field])
    if rest is None:
        left = -1                 # a scan: all of it again
    else:
        r = rest(body, done)
        left = 0 if r is None else r[1]   # None: a one-shot job, still to do
    return {"id": n["id"], "kind": n.get("kind"), "label": n.get("label"),
            "started_at": n.get("started_at"),
            "interrupted_at": n.get("interrupted_at"),
            "reason": n.get("reason", ""), "total": total,
            "finished": len(done),
            "failed": sum(1 for v in done.values() if v),
            # -1 a scan, run again; 0 nothing left; N files left; None the
            # whole of a one-shot job (an archive, a clip) is still to do
            "left": left}


@app.get("/api/jobs/interrupted")
def interrupted_jobs(user: dict = Depends(auth.current_user)):
    """This account's jobs that a restart cut short and that wait for it.

    Only the owner's own: the entries hold paths, and an account restricted
    to some folders must not learn of work done elsewhere.
    """
    return {"jobs": [_notice_view(n) for n in jobs.notices_for(user["id"])]}


@app.post("/api/jobs/interrupted/{notice_id}/resume")
def resume_interrupted(notice_id: str, user: dict = Depends(auth.current_user)):
    n = jobs.take_notice(notice_id, user["id"])
    if not n:
        raise HTTPException(status_code=404, detail="找不到這個中斷的工作")
    rest = _resume_table().get(n.get("endpoint"), (None,) * 4)[3]
    if rest is None:
        body = n.get("body") or {}
    else:
        r = rest(n.get("body") or {}, n.get("done") or {})
        if r is None:
            return {"job_id": None, "detail": "已經全部完成，不需要繼續"}
        body = r[0]
    # a fresh run, not a continuation of the attempts: you asked for it
    res = _run_entry(n, body, user)
    return {"job_id": res.get("job_id"), "kind": n.get("kind"),
            "label": n.get("label")}


@app.post("/api/jobs/interrupted/{notice_id}/dismiss")
def dismiss_interrupted(notice_id: str, user: dict = Depends(auth.current_user)):
    return {"ok": jobs.take_notice(notice_id, user["id"]) is not None}


@app.get("/api/jobs/{job_id}")
def own_job(job_id: str, user: dict = Depends(auth.current_user)):
    """A job continued from a notice, followed from the status bar.

    Owner only, and only jobs that know their owner (the resumable ones):
    the result lists paths.
    """
    j = jobs.get(job_id)
    if not j or jobs.owner(job_id) != user["id"]:
        raise HTTPException(status_code=404, detail="job not found")
    return j


# ── archive workflow (editor only) ───────────────────────────────────────────

def _resolve_person_dir(person: str, root: Optional[str], user: dict) -> Path:
    """Folder for `person`: existing character folder, else create under a root."""
    person = person.strip()
    if not person:
        raise HTTPException(status_code=400, detail="person name required")
    if any(ch in person for ch in '\\/:*?"<>|'):
        raise HTTPException(status_code=400, detail="invalid characters in name")

    for c in media.list_characters(app_settings.archive_roots_for(user)):
        if c["name"] == person:
            return Path(c["path"])

    roots = app_settings.archive_roots_for(user)
    if not roots:
        raise HTTPException(status_code=400,
                            detail="尚未設定歸檔根目錄（⚙ 設定）")

    if root:
        base = Path(root)
        if not is_allowed(base, user):
            raise HTTPException(status_code=400,
                                detail=f"根目錄不在允許清單中：{root}")
    else:
        # prefer the configured default, but fall back to the first archive root
        # if it isn't one of them (avoids a dead end when settings disagree)
        default = (app_settings.get().get("default_archive_root") or "").strip()
        base = Path(default) if default and is_allowed(Path(default), user) else roots[0]
    return base / person


class ArchiveCheckBody(BaseModel):
    path: str
    person: str
    root: Optional[str] = None


@app.post("/api/archive/check")
def archive_check(body: ArchiveCheckBody, user: dict = Depends(auth.require_perm("archive"))):
    src = _checked_path(body.path, user)
    dest_dir = _resolve_person_dir(body.person, body.root, user)
    size = src.stat().st_size
    dup = media.find_duplicate(src, dest_dir) if dest_dir.exists() else None
    try:
        free = shutil.disk_usage(dest_dir if dest_dir.exists()
                                 else dest_dir.parent).free
    except Exception:
        free = -1
    return {"dest_dir": str(dest_dir),
            "dest_exists": dest_dir.exists(),
            "duplicate": dup.name if dup else None,
            "size": size, "free": free,
            "fits": free < 0 or size <= free}


class ArchiveBody(BaseModel):
    path: str
    person: str
    root: Optional[str] = None


@app.post("/api/archive")
def archive_video(body: ArchiveBody, user: dict = Depends(auth.require_perm("archive"))):
    src = _checked_path(body.path, user)
    dest_dir = _resolve_person_dir(body.person, body.root, user)
    dst = dest_dir / src.name
    if dst.exists():
        dst = dest_dir / f"{src.stem}_{int(src.stat().st_mtime)}{src.suffix}"

    def _task(progress):
        def cb(done, total, speed):
            progress({"done": done, "total": total, "speed": speed,
                      "name": src.name})
        media.move_with_progress(src, dst, cb)
        media.move_note(src, dst, CACHE_DIR)   # note travels with the video
        media.move_companions(src, dst)        # .nfo, posters, subtitles
        # ...and so do the watched/favourite marks. Archiving is the single
        # most likely moment for a video to already carry them: you watched it
        # in the inbox, which is how you decided where it belongs.
        videoflags.move(src, dst)
        filetitle.move(src, dst)
        costar.move(src, dst)
        _files_changed()
        _learn_performer(src, dst.parent)
        # Index just this one video, detached — the archive job must finish as
        # soon as the file has moved (indexing a whole folder here would leave
        # the UI stuck at 100%).
        def _index_later():
            try:
                if faces.available():
                    faces.index_single(FACE_DB, body.person, dst)
            except Exception:
                log.exception("post-archive face index failed")
        threading.Thread(target=_index_later, daemon=True).start()
        return {"src": str(src), "dst": str(dst), "person": body.person}

    return {"job_id": jobs.start(_task, kind="archive", label=f"歸檔 {src.name}",
                                 resume=_resumable("archive_video", body, user))}


@app.get("/api/archive/roots")
def archive_roots_info(user: dict = Depends(auth.current_user)):
    """Archive roots with free space — drives the batch-archive destination picker."""
    out = []
    for r in app_settings.archive_roots_for(user):
        try:
            du = shutil.disk_usage(r if r.exists() else r.parent)
            free, total = du.free, du.total
        except Exception:
            free = total = -1
        out.append({"path": str(r), "free": free, "total": total,
                    "exists": r.exists()})
    return {"roots": out}


@app.get("/api/inbox/folder")
def inbox_folder_info(folder: str = Query(...),
                      user: dict = Depends(auth.current_user)):
    """Stats for one inbox subfolder + where a same-named character already lives."""
    p = Path(folder)
    if not is_allowed(p, user) or not p.is_dir():
        raise HTTPException(status_code=403, detail="folder not allowed")
    stats = media.folder_stats(p)
    existing = [c for c in media.list_characters(app_settings.archive_roots_for(user))
                if c["name"].lower() == p.name.lower()]
    return {"path": str(p), "name": p.name, **stats,
            "existing": [{"name": c["name"], "paths": c["paths"]} for c in existing]}


class FolderArchiveBody(BaseModel):
    folder: str          # inbox subfolder to archive
    person: str          # character name (defaults to the folder name in the UI)
    root: str            # chosen archive root


@app.post("/api/archive/folder")
def archive_folder(body: FolderArchiveBody,
                   user: dict = Depends(auth.require_perm("archive"))):
    src = Path(body.folder)
    if not is_allowed(src, user) or not src.is_dir():
        raise HTTPException(status_code=403, detail="folder not allowed")
    root = Path(body.root)
    if not is_allowed(root, user):
        raise HTTPException(status_code=400, detail="root not allowed")
    person = body.person.strip()
    if not person or any(ch in person for ch in '\\/:*?"<>|'):
        raise HTTPException(status_code=400, detail="invalid character name")
    dst = root / person
    if dst.resolve() == src.resolve():
        raise HTTPException(status_code=400, detail="來源與目標相同")

    def _task(progress):
        dst.mkdir(parents=True, exist_ok=True)
        res = media.move_tree(src, dst, progress_cb=progress)
        _files_changed()
        def _index_later():
            try:
                if faces.available():
                    faces.index_folders(FACE_DB, {person: str(dst)})
            except Exception:
                log.exception("post-batch face index failed")
        threading.Thread(target=_index_later, daemon=True).start()
        return {**res, "person": person, "dst": str(dst)}

    return {"job_id": jobs.start(_task, kind="archive_folder",
                                 label=f"整包歸檔 {src.name} → {person}",
                                 resume=_resumable("archive_folder", body, user))}


@app.get("/api/archive/job/{job_id}")
def archive_job(job_id: str, user: dict = Depends(auth.require_perm("archive"))):
    j = jobs.get(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job not found")
    return j


# ── face recognition (GPU only) ──────────────────────────────────────────────

FACE_DB = DATA_DIR / "faces.db"


@app.get("/api/face/status")
def face_status(user: dict = Depends(auth.current_user)):
    p = faces.probe()
    return {**p, "available": p["deps"] and p["gpu"],
            "stats": faces.stats(FACE_DB)}


def _require_faces():
    p = faces.probe()
    if not (p["deps"] and p["gpu"]):
        raise HTTPException(status_code=503,
                            detail=p["reason"] or "人臉辨識不可用")


class FaceIndexBody(BaseModel):
    person: Optional[str] = None    # None = index every character


@app.post("/api/face/index")
def face_index(body: FaceIndexBody, user: dict = Depends(auth.require_perm("person"))):
    _require_faces()
    chars = media.list_characters(app_settings.archive_roots_for(user))
    if body.person:
        chars = [c for c in chars if c["name"] == body.person]
        if not chars:
            raise HTTPException(status_code=404, detail="character not found")
    folders = {c["name"]: c["path"] for c in chars}

    def _task(progress):
        return faces.index_folders(FACE_DB, folders, progress=progress)

    return {"job_id": jobs.start(_task, kind="face_index", label="人臉索引",
                                 resume=_resumable("face_index", body, user))}


@app.get("/api/face/job/{job_id}")
def face_job(job_id: str, user: dict = Depends(auth.require_perm("person"))):
    j = jobs.get(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job not found")
    return j


@app.get("/api/face/match")
def face_match(path: str = Query(...), user: dict = Depends(auth.current_user)):
    _require_faces()
    p = _checked_path(path, user)
    return {"matches": faces.match(FACE_DB, p)}


# ── character folder operations (editor only) ────────────────────────────────

class RenameBody(BaseModel):
    path: str        # current character folder
    new_name: str


@app.post("/api/character/rename")
def rename_character(body: RenameBody, user: dict = Depends(auth.require_perm("rename_merge"))):
    folder = Path(body.path)
    if not is_allowed(folder, user) or not folder.is_dir():
        raise HTTPException(status_code=403, detail="folder not allowed")
    new_name = body.new_name.strip()
    if not new_name or any(ch in new_name for ch in '\\/:*?"<>|'):
        raise HTTPException(status_code=400, detail="invalid folder name")
    target = folder.parent / new_name
    if target.exists():
        raise HTTPException(status_code=409, detail=f"「{new_name}」已存在")
    try:
        folder.rename(target)
    except OSError as e:
        raise HTTPException(status_code=500, detail=str(e))
    # Hundreds of files just changed path in one operation, so this is a prefix
    # rewrite rather than a walk.
    videoflags.move_tree(folder, target)
    filetitle.move_tree(folder, target)
    costar.move_tree(folder, target)
    photosimilar.move_folder(folder, target)
    javdb.move_wiki_profile(videoflags.key(folder), videoflags.key(target))
    _files_changed()
    return {"ok": True, "path": str(target), "name": new_name}


class MergePlanBody(BaseModel):
    src: str
    dst: str


@app.post("/api/character/merge/plan")
def merge_plan(body: MergePlanBody, user: dict = Depends(auth.require_perm("rename_merge"))):
    src, dst = Path(body.src), Path(body.dst)
    if not (is_allowed(src, user) and is_allowed(dst, user)):
        raise HTTPException(status_code=403, detail="folder not allowed")
    if src == dst:
        raise HTTPException(status_code=400, detail="來源與目標相同")
    plan = dedup.build_merge_plan(src, dst)
    need = sum(p["size"] for p in plan if p["action"] != "delete")

    # On one volume a move is a rename: instant, and it needs no room at all.
    # Only a genuine cross-volume merge has to fit.
    same_vol = media.same_volume(src, dst)
    free = -1
    if not same_vol:
        try:
            free = shutil.disk_usage(dst if dst.exists() else dst.parent).free
        except Exception:
            free = -1

    return {"plan": plan,
            "same_volume": same_vol,
            "free": free,
            # unknown free space must not read as "full" and block the merge
            "fits": True if same_vol or free < 0 else free >= need,
            "summary": {
                "move":   sum(1 for p in plan if p["action"] == "move"),
                "rename": sum(1 for p in plan if p["action"] == "rename"),
                "delete": sum(1 for p in plan if p["action"] == "delete"),
                "bytes":  need,
            }}


class MergeRunBody(BaseModel):
    src: str
    dst: str
    plan: list


@app.post("/api/character/merge")
def merge_run(body: MergeRunBody, user: dict = Depends(auth.require_perm("rename_merge"))):
    src, dst = Path(body.src), Path(body.dst)
    if not (is_allowed(src, user) and is_allowed(dst, user)):
        raise HTTPException(status_code=403, detail="folder not allowed")
    # every path in the plan must stay inside the two approved folders
    for item in body.plan:
        if not is_allowed(Path(item["src"]), user):
            raise HTTPException(status_code=403, detail="plan contains a bad path")

    def _task(progress):
        res = dedup.run_merge(body.plan, src, dst, progress=progress)
        # The merge preserves relative paths, so a prefix rewrite covers it.
        # Files renamed on a name collision fall outside and lose their marks —
        # rare, and the alternative is threading a callback through the planner.
        videoflags.move_tree(src, dst)
        filetitle.move_tree(src, dst)
        costar.move_tree(src, dst)
        photosimilar.move_folder(src, dst)
        javdb.move_wiki_profile(videoflags.key(src), videoflags.key(dst))
        _files_changed()
        return res

    return {"job_id": jobs.start(_task, kind="merge",
                                 label=f"資料夾整合 {src.name} → {dst.name}",
                                 resume=_resumable("merge_run", body, user))}


@app.get("/api/character/merge/job/{job_id}")
def merge_job(job_id: str, user: dict = Depends(auth.require_perm("rename_merge"))):
    j = jobs.get(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job not found")
    return j


class DeleteBody(BaseModel):
    path: Optional[str] = None
    paths: Optional[list] = None     # batch mode (dedup report "執行建議")
    # Admin only, single path only: delete although *other* accounts have it as
    # a favourite. The confirmation that sets this shows how many.
    force_fav: bool = False


def _unlink(p: Path):
    """Delete a file, working around the two things that block it on Windows.

    A read-only flag is cleared outright. A sharing violation (WinError 32) is
    usually a stream response that has not finished tearing down yet, so it is
    worth a short retry before giving up.
    """
    # Backing off rather than a flat 0.4 s: the original window was tuned for a
    # browser finishing a stream teardown (milliseconds). A NAS indexing a
    # batch of freshly-moved files holds them far longer than any retry here
    # should wait, so give it a few seconds and then report honestly.
    waits = (0.3, 0.6, 1.2, 2.4)
    for attempt in range(len(waits) + 1):
        try:
            p.unlink()
            return
        except PermissionError as e:
            if getattr(e, "winerror", None) == 32:      # in use by another process
                log.warning("WinError 32 第 %d 次 %s；%s",
                            attempt + 1, p.name, _holders(p))
                if attempt == len(waits):
                    # Say what the system said. Guessing at the cause ("可能仍
                    # 在播放") sent the last investigation the wrong way.
                    detail = e.strerror or str(e)
                    raise RuntimeError(
                        f"{detail}（WinError 32，重試 {len(waits)} 次後仍失敗）"
                    ) from e
                time.sleep(waits[attempt])
                continue
            # read-only: clear the flag and try once more
            try:
                p.chmod(stat.S_IWRITE)
            except OSError:
                pass
            p.unlink()
            return


# One wording for every path that refuses a delete, because the user reads it
# in three different places (a card, a duplicate row, a pending pair) and it
# has to say the same thing each time. It states the remedy, not just the
# refusal: the duplicate scan's keep-ladder does not know about favourites, so
# it can genuinely recommend deleting one, and "不能刪" alone would read as the
# tool contradicting itself.
_FAV_LOCKED = "已標為我的最愛 —— 請先在播放器取消最愛再刪除"


def _fav_refusal(p: Path, user: dict, force: bool, single: bool) -> Optional[str]:
    """Why this delete must not go ahead because of favourites, or None.

    Three cases, and only the last used to exist:
    * your own favourite — un-favourite it yourself first, same as always;
    * only other accounts' — an admin may go ahead after confirming (single
      file only, never a batch), an editor is told who can;
    * nobody's — no objection.
    """
    if videoflags.get(user["id"], p).get("fav"):
        return _FAV_LOCKED
    n = videoflags.fav_count(p, exclude_user=user["id"])
    if not n:
        return None
    if user["role"] == "admin" and force and single:
        return None
    if user["role"] == "admin":
        return f"有 {n} 位使用者標為我的最愛 —— 需要確認後才能刪除"
    return f"有 {n} 位使用者標為我的最愛 —— 只有管理員可以確認後刪除"


def _is_fav_refusal(msg: str) -> bool:
    return msg == _FAV_LOCKED or "標為我的最愛" in msg


@app.post("/api/media/delete")
def delete_media(body: DeleteBody, user: dict = Depends(auth.require_perm("delete"))):
    targets = body.paths if body.paths is not None else (
        [body.path] if body.path else [])
    if not targets:
        raise HTTPException(status_code=400, detail="no path given")
    for raw in targets:
        if not is_allowed(Path(raw), user):
            raise HTTPException(status_code=403, detail="path not allowed")

    deleted, errors = [], []
    for raw in targets:
        p = Path(raw)
        if not p.is_file():
            errors.append({"path": raw, "error": "not found"})
            continue
        # The favourite guard lives here rather than on the delete button
        # because there are ten call sites and one endpoint -- and the ones
        # that would hurt most are the batch deletes in the duplicate and
        # image-duplicate dialogs, which nobody would think to patch.
        #
        # Skipped, never fatal: a batch of forty must not abort because one
        # file is protected. It becomes an entry in `errors`, which the
        # existing "mark the failed rows, retry all failures" UI already
        # renders. `_FAV_LOCKED` is matched by the frontend, so keep the two
        # in step if it ever changes.
        refusal = _fav_refusal(p, user, body.force_fav, body.paths is None)
        if refusal:
            errors.append({"path": raw, "error": refusal})
            log.info("delete refused, favourite: %s", p)
            continue
        forced = videoflags.fav_count(p, exclude_user=user["id"])
        if forced:
            # Only reachable by an admin who confirmed. Written down twice:
            # the log for whoever debugs, the alert list for whoever asks
            # "where did my favourite go".
            log.warning("favourite deleted by admin: %s (%d users) by=%r",
                        p, forced, user["username"])
            conn = db.connect()
            try:
                alerts.raise_alert(conn, alerts.FAV_FORCED, user["username"], "",
                                   f"刪除了 {forced} 位使用者的最愛：{p.name}",
                                   dedupe=False)
            finally:
                conn.close()
        # Get our own speculative work out of the way first. Prefetching sheets
        # for this video keeps a child process on it on and off for a minute,
        # and nobody asked for those sheets — the delete is the clearer signal.
        contactsheet.cancel_prefetch(p)
        # Before the unlink, not after: a photo thumbnail's cache key holds the
        # file's size and mtime, so once the original is gone the entry cannot
        # be located again. Anything missed is only wasted space that the
        # sweep reclaims, but the common path should clean up exactly.
        if p.suffix.lower() in media.IMAGE_EXT:
            photothumb.purge(p, CACHE_DIR)
        try:
            _unlink(p)
            deleted.append(str(p))
            log.info("deleted %s", p)
            # notes, cached thumbnails and face embeddings all key off the path,
            # so they would otherwise linger and point at a file that is gone
            media.purge_derived(p, CACHE_DIR)
            # Not only tidiness: a new file landing on this exact path would
            # otherwise arrive already marked as watched.
            videoflags.forget(p)
            filetitle.forget(p)
            costar.forget(p)
            # Same reasoning for the person verdict — and worse here, because a
            # stale "沒有人物" is a standing suggestion to delete a photo the
            # detector never actually looked at.
            if p.suffix.lower() in media.IMAGE_EXT:
                try:
                    c = personscan.Cache(PERSON_DB)
                    c.forget(p)
                    c.close()
                except Exception:
                    log.exception("could not clear the person verdict for %s", p)
            try:
                faces.forget_video(FACE_DB, p)
            except Exception:
                log.exception("could not clear the face index for %s", p)
        except Exception as e:
            # This used to be invisible, so a wave of failures could only be
            # diagnosed by guessing at what had the files open.
            log.warning("delete failed %s: %s", p, e)
            # If it is our own ffmpeg still on the file, say so — "in use by
            # another process" reads as antivirus or the NAS and sends people
            # looking in entirely the wrong place.
            msg = _busy_reason(p) or str(e)
            errors.append({"path": raw, "error": msg})

    if deleted:
        _files_changed()
    # single-path callers still expect a hard failure rather than an error list
    if body.paths is None and errors:
        first = errors[0]["error"]
        # 409, not 500: nothing went wrong: the server understood and refused.
        # A 500 would send the next investigation looking for a crash.
        code = (404 if first == "not found"
                else 409 if _is_fav_refusal(first)
                else 500)
        raise HTTPException(status_code=code, detail=first)
    return {"ok": not errors, "deleted": deleted, "errors": errors}


@app.get("/api/thumb")
def thumbnail(path: str = Query(...),
              pct: float = Query(0.1),      # position in the video (0–1)
              size: int = Query(640),       # width in px
              poster: int = Query(0),       # prefer a same-named image
              # "portrait": a 4:5 / 9:16 card, which shows only the front of
              # a JAV sleeve (photothumb.front_box); the whole sleeve otherwise
              shape: str = Query(""),
              user: dict = Depends(auth.current_user)):
    p = _checked_path(path, user)

    # Opt-in, not automatic. Every other caller of this endpoint — the compare
    # view, the duplicate reports — is asking "what does this file look like",
    # and answering with a picture that came from a *different* file would make
    # two different videos able to show the same image. Only the grid, told to
    # by its group's setting, asks for the poster.
    if poster:
        # by the card's shape: a 16:9 card wants the landscape `-fanart`, a
        # portrait one the `-poster` front (media.poster_candidates)
        portrait = shape == "portrait"
        side = media.poster_for(p, landscape=not portrait)
        if side is not None:
            # photothumb rather than a second resize path: its cache key already
            # carries the image's size and mtime, so re-saving the poster shows
            # the new picture instead of a stale one.
            # only a sleeve is cut (photothumb.front_box): a portrait card
            # that found no `-poster` shows the front of the `-fanart`
            t = photothumb.get(side, CACHE_DIR, front=portrait)
            if t:
                cachelimit.touch(t)
                try:
                    data = t.read_bytes()
                except OSError:
                    data = None
                if data:
                    cachelimit.maybe_enforce(
                        CACHE_DIR, "photos",
                        _limit_bytes(app_settings.get(), "photos"))
                    return Response(data, media_type="image/jpeg",
                                    headers=_IMG_CACHE_HEADERS)
        # An unreadable or missing poster falls through to the frame grab
        # deliberately: the card must show *something*, and a broken image in
        # the grid reads as "this video is gone".

    pct = min(max(pct, 0.0), 0.99)
    size = min(max(size, 120), 1920)
    thumb = media.get_or_make_thumb(p, CACHE_DIR, pct=pct, size=size)
    if not thumb:
        raise HTTPException(status_code=404, detail="thumbnail unavailable")
    # eviction is least-recently-used by mtime, and nothing else updates it —
    # without this a thumbnail you look at daily still ranks as ancient
    cachelimit.touch(Path(thumb))
    # Read it now rather than handing the path to FileResponse, which stats and
    # opens the file later, during the send. The eviction sweep runs in a
    # background thread and can delete the file in that gap — the browser then
    # gets a 500 and shows a broken image. Thumbnails are ~25 KB, so holding
    # one in memory costs nothing. (`cachelimit.enforce` also re-checks mtime
    # now, but closing the window here means the ordering cannot matter.)
    try:
        data = Path(thumb).read_bytes()
    except OSError:
        raise HTTPException(status_code=404, detail="thumbnail unavailable")
    cachelimit.maybe_enforce(CACHE_DIR, "thumbs",
                             _limit_bytes(app_settings.get(), "thumbs"))
    # Let the browser keep it: the cache key already includes the full path, so
    # a different video is always a different URL — stale hits aren't possible.
    return Response(data, media_type="image/jpeg", headers=_IMG_CACHE_HEADERS)


@app.get("/api/subtitle")
def subtitle(path: str = Query(...),
             user: dict = Depends(auth.current_user)):
    """The video's sidecar subtitle, as WebVTT.

    404 when there is none, which is the answer for 99.86% of the library — the
    player asks first and only draws a `<track>` on a 200. Pointing a `<track>`
    at a URL that 404s would still give every video a subtitle button that does
    nothing.

    Embedded subtitle streams are deliberately NOT offered. About 60 JAV files
    carry a `mov_text` track, and sampling three of them found the same 45-byte
    payload in each: a 20-second `www.t66y.com` forum watermark. Serving those
    would put an advertisement on screen, not a subtitle.
    """
    p = _checked_path(path, user)
    sub = media.subtitle_for(p)
    if sub is None:
        raise HTTPException(status_code=404, detail="no subtitle")
    vtt = subtitles.to_vtt(sub)
    if not vtt:
        raise HTTPException(status_code=404, detail="subtitle unreadable")
    # NOT the images' month-long cache, and the difference matters. A thumbnail
    # is derived from the video, which nobody edits in place; a subtitle is its
    # own file, and correcting a typo in the .ass leaves the URL unchanged — so
    # a cached copy would keep serving the old text for a month. Caught in
    # testing: a conversion change did not reach the player until the cache was
    # bypassed. Converting costs 111 ms and ~25 KB, so there is nothing to save.
    return Response(vtt, media_type="text/vtt; charset=utf-8",
                    headers={"Cache-Control": "no-store"})


@app.get("/api/note")
def get_note(path: str = Query(...), user: dict = Depends(auth.current_user)):
    p = _checked_path(path, user)
    return {"path": str(p), "note": media.read_note(p, CACHE_DIR)}


class NoteBody(BaseModel):
    path: str
    note: str = ""


class FlagBody(BaseModel):
    path: str
    on: bool = True


# Deliberately `current_user`, not `require_editor`. These marks are the
# reader's own record of what they have seen and liked -- a viewer account that
# could not keep them would be missing the point of having the feature. Nothing
# here touches a file, and each row is scoped to the caller's own user id, so
# there is nothing an editor-only gate would be protecting. It also has to work
# on the phone, where the whole editing surface is hidden by design.
@app.post("/api/flags/watched")
def set_watched(body: FlagBody, user: dict = Depends(auth.current_user)):
    p = _checked_path(body.path, user)
    return videoflags.set_watched(user["id"], p, body.on)


@app.post("/api/flags/fav")
def set_fav(body: FlagBody, user: dict = Depends(auth.current_user)):
    p = _checked_path(body.path, user)
    return videoflags.set_fav(user["id"], p, body.on)


@app.get("/api/flags")
def get_flags(path: str = Query(...), user: dict = Depends(auth.current_user)):
    p = _checked_path(path, user)
    return {**videoflags.get(user["id"], p),
            "fav_others": videoflags.fav_count(p, exclude_user=user["id"])}


@app.post("/api/note")
def save_note(body: NoteBody, user: dict = Depends(auth.require_perm("edit_info"))):
    p = _checked_path(body.path, user)
    # archived videos keep the note beside the file; inbox videos use the cache
    in_archive = any(_under(p, r) for r in app_settings.archive_roots_for(user))
    media.write_note(p, CACHE_DIR, body.note, sidecar=in_archive)
    return {"ok": True, "sidecar": in_archive}


# mimetypes reads the Windows registry, which hands back things like
# "video/vnd.dlna.mpeg-tts" for .ts — a type no browser recognises. Pin the
# ones we serve so the player at least gets a fair chance.
_VIDEO_MIME = {
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime",
    ".webm": "video/webm", ".ogv": "video/ogg",
    ".ts": "video/mp2t", ".mkv": "video/x-matroska", ".avi": "video/x-msvideo",
    ".flv": "video/x-flv", ".wmv": "video/x-ms-wmv",
    ".mpg": "video/mpeg", ".mpeg": "video/mpeg", ".3gp": "video/3gpp",
}


def _video_mime(p: Path) -> str:
    ext = p.suffix.lower()
    return (_VIDEO_MIME.get(ext)
            or mimetypes.guess_type(str(p))[0]
            or "application/octet-stream")


def _under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
        return True
    except (ValueError, OSError):
        return False


# Kinds absent from here have no settings field and simply use their default.
# `shrink_samples` is one: it needs a ceiling so it cannot grow without bound,
# but it is scratch space nobody thinks about, and a fourth number to tune
# would cost more attention than it saves.
_LIMIT_KEY = {"thumbs": "thumb_limit_gb", "sheets": "sheet_limit_gb",
              "photos": "photo_limit_gb"}


def _limit_bytes(cfg: dict, kind: str) -> int:
    key = _LIMIT_KEY.get(kind)
    default = cachelimit.DEFAULT_LIMIT_GB[kind]
    return int(float(cfg.get(key, default) if key else default) * (1 << 30))


@app.get("/api/cache/usage")
def cache_usage(admin: dict = Depends(auth.require_admin)):
    """Per kind, because they have separate budgets now.

    `notes/` is reported too but has no limit — those are the user's own memos
    for inbox videos, and the sweep must never touch them.
    """
    cfg = app_settings.get()
    out = {}
    for kind in cachelimit.KINDS:
        out[kind] = {
            "used": cachelimit.usage(cachelimit.kind_dir(CACHE_DIR, kind)),
            # via _limit_bytes, which knows that some kinds have no settings
            # field and fall back to their default
            "limit_gb": _limit_bytes(cfg, kind) / (1 << 30),
        }
    out["notes"] = {"used": cachelimit.usage(CACHE_DIR / "notes"),
                    "limit_gb": None}
    return out


@app.post("/api/cache/sweep")
def cache_sweep(admin: dict = Depends(auth.require_admin)):
    """Apply the limit right now, rather than waiting for the next sweep.

    Reports the limit it actually used. The dialog's field may hold an unsaved
    value, and a message built from that said "407 MB does not exceed 0.1 GB".
    """
    cfg = app_settings.get()
    out = {}
    for kind in cachelimit.KINDS:
        res = cachelimit.enforce(cachelimit.kind_dir(CACHE_DIR, kind),
                                 _limit_bytes(cfg, kind))
        out[kind] = {**res, "limit_gb": _limit_bytes(cfg, kind) / (1 << 30)}
    return out


@app.get("/api/sheet/info")
def sheet_info(path: str = Query(...),
               user: dict = Depends(auth.current_user)):
    """How many contact sheets this video has, and what each one covers."""
    p = _checked_path(path, user)
    cfg = app_settings.get()
    interval = int(cfg.get("sheet_interval", 60))
    dur = contactsheet.video_duration(p)     # memoised; /api/sheet/status polls
    total = contactsheet.sheet_count(dur, interval)
    return {"duration": dur, "interval": interval, "total": total,
            "per_sheet": contactsheet.PER_SHEET,
            # cells in the whole set; the viewer uses this rather than its own
            # arithmetic so the two can never disagree on the last sheet
            "frames": contactsheet.frame_count(dur, interval),
            # The grid is published rather than assumed by the frontend: it has
            # to turn a click into a cell, and a hardcoded 4x3 would not fail
            # loudly if this changed — it would point at the wrong frame.
            "cols": contactsheet.COLS, "rows": contactsheet.ROWS,
            "pad": contactsheet.PAD,
            "tile_width": int(cfg.get("sheet_tile_width", 480)),
            "workers": int(cfg.get("sheet_workers", contactsheet.DEFAULT_WORKERS)),
            "span": contactsheet.PER_SHEET * interval}


@app.get("/api/sheet/status")
def sheet_status(path: str = Query(...),
                 user: dict = Depends(auth.current_user)):
    """Progress for the whole set, polled while the viewer is open.

    Cheap enough to poll: the duration is memoised, so this is `total` stat
    calls — a median of 20 — and no ffprobe. Without the memo it would spawn
    one ffprobe per poll (107 ms median locally) alongside the four ffmpeg
    grabs actually building the sheets.
    """
    p = _checked_path(path, user)
    cfg = app_settings.get()
    return contactsheet.status(p, int(cfg.get("sheet_interval", 60)), CACHE_DIR,
                               int(cfg.get("sheet_tile_width", 480)))


@app.post("/api/sheet/cancel")
def sheet_cancel(path: str = Query(...),
                 user: dict = Depends(auth.current_user)):
    """Stop building this video's sheets — the rest of the set, and the one
    still being waited on.

    Called when the viewer closes. Prefetching exists to make *browsing* the
    sheets instant; once nobody is looking, finishing the set is speculative
    work that competes with whatever the machine is asked to do next — and on
    a 2-hour film that is twenty more sheets. Cooperative, like the scans: the
    grab in flight completes, nothing is killed.
    """
    p = _checked_path(path, user)
    return {"stopped": contactsheet.cancel_prefetch(p)}


@app.get("/api/sheet")
def sheet(path: str = Query(...), index: int = Query(0),
          user: dict = Depends(auth.current_user)):
    """One contact sheet, built on demand and cached.

    Asking for a sheet also starts building the rest of that video in the
    background: the whole point is to judge a video quickly, and waiting 2.5 s
    on every page turn would undo that.
    """
    p = _checked_path(path, user)
    cfg = app_settings.get()
    interval = int(cfg.get("sheet_interval", 60))
    tile_w = int(cfg.get("sheet_tile_width", 480))
    workers = int(cfg.get("sheet_workers", contactsheet.DEFAULT_WORKERS))
    gpu = cfg.get("sheet_hwaccel") == "gpu"
    # closing the viewer (/api/sheet/cancel) stops this one too, so a slow
    # video does not hold the renderer while the next one waits behind it
    ticket = contactsheet.ticket(p)
    contactsheet.yield_to(p)                # don't queue behind another video
    out = contactsheet.render(
        p, index, interval, CACHE_DIR, tile_w, workers, gpu=gpu,
        should_stop=lambda: contactsheet.cancelled_since(p, ticket))
    if not out:
        if contactsheet.cancelled_since(p, ticket):
            raise HTTPException(status_code=409, detail="縮圖表已取消")
        raise HTTPException(status_code=404, detail="無法產生縮圖表")
    cachelimit.touch(out)                   # keep what is being looked at
    contactsheet.prefetch(p, interval, CACHE_DIR, tile_w, workers, gpu=gpu)
    # read before kicking off the sweep, for the reason in /api/thumb — this
    # route starts the sweep itself, so the window is wide open here
    try:
        data = out.read_bytes()
    except OSError:
        raise HTTPException(status_code=404, detail="無法產生縮圖表")
    cachelimit.maybe_enforce(CACHE_DIR, "sheets", _limit_bytes(cfg, "sheets"))
    return Response(data, media_type="image/jpeg", headers=_IMG_CACHE_HEADERS)


# ── clipping ─────────────────────────────────────────────────────────────────
# The clip lands next to its source and is treated as an ordinary video: the
# expected workflow is to cut the good part out and then delete the original
# in delete mode, so hiding it in a subfolder would make the film disappear
# from the UI the moment the original went.

class ClipBody(BaseModel):
    path: str
    segments: list        # [[start, end], ...] — several are joined into one
    precise: bool = False
    crf: int = 20
    gpu: bool = False


def _clip_plan(body: ClipBody, user: dict):
    """Shared by preview and run, so the two can never disagree."""
    src = Path(body.path)
    if not is_allowed(src, user) or not src.is_file():
        raise HTTPException(status_code=403, detail="path not allowed")
    if not body.segments:
        raise HTTPException(status_code=400, detail="沒有選取任何片段")
    try:
        segs = [(float(a), float(b)) for a, b in body.segments]
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="片段格式錯誤")
    info = convert.probe(src)
    if not info:
        raise HTTPException(status_code=400, detail="無法讀取影片資訊")
    plan = clip.plan(src, segs, body.precise, info.get("duration") or 0.0,
                     src.stat().st_size)
    return src, info, plan


@app.post("/api/clip/plan")
def clip_plan(body: ClipBody, user: dict = Depends(auth.require_perm("clip"))):
    """Where each cut would actually begin, and what the result would be.

    POST rather than GET because the input is a list; nothing is written.
    """
    return _clip_plan(body, user)[2]


@app.post("/api/clip/run")
def clip_run(body: ClipBody, user: dict = Depends(auth.require_perm("clip"))):
    src, info, plan = _clip_plan(body, user)
    if not plan.get("ok"):
        raise HTTPException(status_code=400, detail=plan.get("error", "無效的片段"))
    if not plan["enough_space"]:
        raise HTTPException(
            status_code=400,
            detail=f"空間不足：預估需要 {plan['est_size'] / (1<<30):.1f} GB，"
                   f"剩餘 {plan['free'] / (1<<30):.1f} GB")

    crf = max(0, min(51, int(body.crf)))
    precise, gpu = bool(body.precise), bool(body.gpu)
    parts, span = plan["parts"], plan["duration"]
    dst = src.with_name(plan["out_name"])

    def _task(progress):
        # The name was chosen milliseconds ago, but ffmpeg runs with -y and
        # would overwrite without a word if two cuts of the same range raced.
        nonlocal dst
        if dst.exists():
            dst = clip.output_path(src, [(p["start"], p["end"])
                                         for p in parts])
        jobs.writing(src, dst)        # a restart mid-cut removes the half file
        progress({"pct": 0.0, "name": dst.name})
        try:
            clip.run(
                src, dst, parts, precise, info, crf=crf, gpu=gpu,
                progress=lambda f: progress({"pct": f, "name": dst.name}))
        except Exception:
            dst.unlink(missing_ok=True)     # a half-written clip looks playable
            raise
        problem = clip.verify(dst, span, clip.tolerance_for(len(parts)))
        if problem:
            dst.unlink(missing_ok=True)
            raise RuntimeError(problem)
        _files_changed()
        st = dst.stat()
        return {"path": str(dst), "name": dst.name, "size": st.st_size,
                "mtime": st.st_mtime, "duration": span,
                "segments": len(parts),
                "video_id": media.detect_video_id(dst.name)}

    label = (f"剪輯 {src.name}" if len(parts) == 1
             else f"剪輯 {src.name}（{len(parts)} 段）")
    return {"job_id": jobs.start(_task, kind="clip", label=label,
                                 resume=_resumable("clip_run", body, user))}


@app.get("/api/clip/job/{job_id}")
def clip_job(job_id: str, user: dict = Depends(auth.require_perm("clip"))):
    j = jobs.get(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job not found")
    return j


@app.get("/api/videoinfo")
def video_info(path: str = Query(...), user: dict = Depends(auth.current_user)):
    p = _checked_path(path, user)
    dur, w, h = media.probe_video(p)
    st = p.stat()
    return {"path": str(p), "name": p.name, "size": st.st_size,
            "mtime": st.st_mtime, "duration": dur, "width": w, "height": h,
            "video_id": media.detect_video_id(p.name)}


# A browser asks for `bytes=0-` — the entire rest of the file — and then reads
# only as much as its buffer wants. Anything that streams from an open file
# therefore sits blocked mid-send with the handle alive for as long as the
# connection lasts, and Windows will not delete a file in that state. Measured:
# a client that stops reading but keeps the socket open holds the handle
# indefinitely; the moment it closes, the handle drops within a second.
#
# So never hold the file across a network wait. The first fix read each slice
# whole into memory before sending; that closed the leak but made every
# response wait for its entire slice to come off the disk before its first
# byte left — and after a seek a phone sends several ranges at once, which then
# queue on the same drive. Measured on 33 real files: first byte 55 ms alone,
# **189 ms (p90 275) four at a time**; the phone's seek logs showed single
# ranges taking 560–822 ms before the response even started.
#
# Now each response is sent in `_PIECE`-sized reads, reopening the file for
# every piece (`_chunked_file`). The handle still only exists while a read is
# in flight — a generator suspended in `yield` has no file open — so the leak
# test is unchanged, and the first byte goes out after one small read.
#
# Returning less than was asked for is explicitly allowed (RFC 7233) and every
# browser handles it; the player just asks for the next slice. The cap was
# 4 MB because the slice sat in memory; with 256 KB in memory at a time it can
# be larger, and it should be: Safari asks for 5+ MB after a seek and, capped,
# had to come back for the remainder — one more round trip on a phone.
_RANGE_CAP = 16 << 20
_PIECE = 256 << 10

# ...but never truncate a range that runs to the end of the file when the
# remainder is small. An MP4 without `faststart` keeps its `moov` index at the
# tail, and the player must read that index *whole* before it can seek at all.
# Measured on a 721 MB file whose moov is 4.19 MB: the player asked for
# `bytes=749174784-756529103` (7.35 MB, the whole tail), the cap handed back
# 4 MB, and — this is the part that makes it fatal — **Chrome does not come
# back for the rest**. It retried the tail from a slightly different offset,
# got truncated again, and looped: tail, playback, tail, playback, dozens of
# times per second, until it gave up and restarted the video from 0. That is
# the "seeking sometimes jumps back to the beginning" report.
#
# 64 MB rather than something tighter, because the allowance has to clear the
# largest index in the library or that one file stays broken. Surveyed 3,758
# files: 1,630 keep `moov` at the tail, median 7.31 MB, largest **41.64 MB**
# (a 20 GB film). 1,174 of them exceed the 4 MB cap, so this was never about
# one video — it is roughly a third of the library.
#
# It is streamed in `_PIECE`s like everything else, so the allowance costs no
# memory and holds no handle across a network wait — the leak this cap
# originally existed to stop was a handle held open *indefinitely* while a
# player sat idle on the socket. `tests/test_stream.py::leak_test` covers that.
_TAIL_WHOLE = 64 << 20


# Diagnostics for "seeking sometimes restarts the video from the beginning".
# Two guesses have already been wrong, so instead of a third, record what the
# player actually asks for. One line per range request; the interesting one is
# `RESTART`, which marks a request that jumps back to the head of the file
# after we had been serving from much further in — the log then shows exactly
# what preceded it. Cheap enough to leave on, and the log rotates at 2 MB.
_last_range: dict = {}
_RANGE_LOG_LOCK = threading.Lock()


def _trace_range(p: Path, start: int, end: int, size: int, asked: str):
    key = str(p)
    with _RANGE_LOG_LOCK:
        prev = _last_range.get(key)
        _last_range[key] = (start, end, time.time())
    if prev is None:
        log.info("stream OPEN  %s  asked=%s -> %d-%d/%d", p.name, asked,
                 start, end, size)
        return
    pstart, pend, pat = prev
    gap = start - (pend + 1)
    age = time.time() - pat
    # a jump back to the first megabyte after we were deep into the file is
    # the browser starting the whole thing over
    if start < (1 << 20) and pstart > (8 << 20):
        log.warning("stream RESTART %s  asked=%s -> %d-%d  "
                    "(previous was %d-%d, %.1fs ago)", p.name, asked,
                    start, end, pstart, pend, age)
    elif gap:
        # a normal seek — dozens per second while scrubbing, so DEBUG
        log.debug("stream SEEK  %s  asked=%s -> %d-%d  (jumped %+d from %d)",
                  p.name, asked, start, end, gap, pend + 1)
    else:
        log.debug("stream cont  %s  %d-%d", p.name, start, end)


def _chunked_file(path: Path, start: int, length: int, chunk: int = _PIECE):
    """Yield `length` bytes from `start`, holding the handle only while reading.

    The close-between-chunks is the entire point — see `/api/stream`. An
    ordinary open-once-and-stream generator would sit blocked in `yield` with
    the file open, which is the handle leak this exists to avoid. A small
    chunk is the other half: the first byte leaves after one small read
    instead of after the whole slice.
    """
    pos, stop = start, start + length
    while pos < stop:
        with open(path, "rb") as f:
            f.seek(pos)
            data = f.read(min(chunk, stop - pos))
        if not data:
            break                       # truncated under us; stop cleanly
        pos += len(data)
        yield data


def _file_validators(st) -> dict:
    """ETag and Last-Modified, byte-identical to what `FileResponse` sends.

    Both must be derived exactly the way Starlette does it, or the 200 and the
    206 for the same file disagree and the browser treats them as different
    resources.
    """
    base = f"{st.st_mtime}-{st.st_size}"
    return {
        "etag": '"' + hashlib.md5(base.encode(), usedforsecurity=False).hexdigest() + '"',
        "last-modified": formatdate(st.st_mtime, usegmt=True),
    }


def _parse_range(header: str, size: int):
    """`(start, end)` inclusive, or None if unsatisfiable.

    Note `bytes=-50` means the LAST 50 bytes, not "0 through 50". The old
    hand-rolled version read it the second way and silently served the wrong
    part of the file.
    """
    m = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip())
    if not m or not (m.group(1) or m.group(2)):
        return None
    if not m.group(1):
        n = int(m.group(2))
        if n == 0:
            return None
        start, end = max(0, size - n), size - 1
    else:
        start = int(m.group(1))
        end = min(int(m.group(2)) if m.group(2) else size - 1, size - 1)
    if start > end or start >= size:
        return None
    return start, end


@app.get("/api/stream")
def stream(request: Request, path: str = Query(...),
           user: dict = Depends(auth.current_user)):
    """Serve a video, holding the file open only long enough to read from it."""
    return _serve_video(request, _checked_path(path, user))


def _serve_video(request: Request, p: Path):
    """The whole Range/handle-lifetime story, shared by every video route.

    Always pass `media_type` explicitly — guessing reintroduces the `.ts` →
    video/vnd.dlna.mpeg-tts problem that no browser can play.
    """
    st = p.stat()
    ctype = _video_mime(p)

    header = request.headers.get("range")
    if header is None:
        # A plain GET must get 200 with the whole entity, so this cannot serve
        # a bounded slice like the Range path does. It used to hand the file to
        # `FileResponse`, on the grounds that players always send a Range —
        # and that hole is exactly what came back as WinError 32 on three
        # 6 GB files, with the log reporting "this process still holds 1
        # handle" hours later.
        #
        # So: stream it, but reopen per chunk. The handle exists only while a
        # read is in flight, never across a network wait, so a client that
        # buffers and then goes quiet pins nothing. Costs one `open()` per
        # 4 MB — about 1,500 for a 6 GB file, which is free next to the I/O.
        return StreamingResponse(
            _chunked_file(p, 0, st.st_size), media_type=ctype,
            headers={"Content-Length": str(st.st_size),
                     "Accept-Ranges": "bytes", **_file_validators(st)})

    # The 200 path above is Starlette's FileResponse, which attaches ETag and
    # Last-Modified. A 206 must carry the *same* validators: the browser uses
    # them to confirm the file has not changed between range requests, and one
    # response having them while the next does not reads as "this resource was
    # replaced" — Chrome then tears down what it has and starts the video over
    # from the beginning. Reported as "seeking sometimes jumps back to 0", and
    # intermittent because it depends on whether a 200 was seen first.
    validators = _file_validators(st)

    rng = _parse_range(header, st.st_size)
    if rng is None:
        return Response(status_code=416,
                        headers={"Content-Range": f"bytes */{st.st_size}",
                                 **validators})

    start, end = rng
    # `end` is already clamped to the last byte by `_parse_range`, so this asks
    # "does the client want everything from here to EOF, and is that little
    # enough to hand over in one piece?"
    tail_whole = (end == st.st_size - 1) and (st.st_size - start) <= _TAIL_WHOLE
    if not tail_whole:
        end = min(end, start + _RANGE_CAP - 1)

    _trace_range(p, start, end, st.st_size, header)
    length = end - start + 1
    return StreamingResponse(
        _chunked_file(p, start, length), status_code=206, media_type=ctype,
        headers={"Content-Range": f"bytes {start}-{end}/{st.st_size}",
                 "Content-Length": str(length),
                 "Accept-Ranges": "bytes", **validators})


# ── the built frontend ───────────────────────────────────────────────────────
#
# Mounted last, and that is not a style choice: a mount at "/" matches
# everything, so anything declared after it would never be reached. Every API
# route above is registered by the time this line runs.
#
# Serving the app from here rather than from a second process is what removes
# the Vite dev server from everyday use — one process, one port, and no Node at
# runtime. `start.bat` rebuilds `dist/` when the sources are newer, so the two
# cannot drift apart. Development still uses Vite (start-dev.bat) for hot
# reload; nothing about that changes.
#
# `html=True` serves index.html for "/" and 404s. This app has no router — the
# view is `store.view`, not the URL — so there are no client-side paths that
# need rewriting to index.html; the flag is here for "/" alone.
# An explicitly declared route WINS over a mount at the same path — measured,
# after `GET /` kept returning a JSON banner with the mount sitting right below
# it. So "/" must not be a route at all when there is a build to serve; the
# fallback is registered only when there is not.
class _AppFiles(StaticFiles):
    """index.html must be revalidated on every load; hashed assets never.

    Served bare, index.html carried an ETag but no Cache-Control, which lets
    a browser cache it heuristically. An iPhone did exactly that: after a
    rebuild and a restart it kept loading the old index.html — and with it
    the old bundle — and the server log showed it never asked for either.
    The fix looked like it did nothing. `no-cache` still allows the cached
    copy, but only after a 304 says it is current. Files under assets/ have
    the content hash in their name, so they can be kept for good.
    """
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        # Starlette normalises with os.path, so on Windows this is "assets\\…"
        if path.replace("\\", "/").startswith("assets/"):
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif resp.media_type == "text/html" or path in ("", ".", "index.html"):
            resp.headers["Cache-Control"] = "no-cache"
        return resp


if DIST.is_dir():
    app.mount("/", _AppFiles(directory=str(DIST), html=True), name="app")
    log.info("serving the built frontend from %s", DIST)
else:
    log.warning("no built frontend at %s — run build.bat", DIST)

    @app.get("/", response_class=HTMLResponse)
    def _no_frontend():
        """Better than a bare 404, which reads as "the server is broken"."""
        return HTMLResponse(
            "<h2>前端還沒有建置</h2>"
            "<p>請執行 <code>build.bat</code>（或 <code>cd frontend &amp;&amp; "
            "npm run build</code>），然後重新整理。</p>"
            "<p>開發時請改用 <code>start-dev.bat</code>，"
            "由 Vite 在 5173 提供前端。</p>", status_code=503)
