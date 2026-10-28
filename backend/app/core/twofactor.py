"""Time-based one-time passwords (RFC 6238) and single-use recovery codes.

Written here rather than pulled in as a dependency: the whole algorithm is
HMAC-SHA1 over a counter, and the standard library already has both halves.
The only outside piece is `segno`, used to draw the QR code locally — the
secret must never leave this machine, which rules out any chart service.

What the rest of the app needs to know:

* `verify()` accepts the previous, current and next 30-second step, because
  phone clocks drift and people type slowly. It returns the step it matched
  so the caller can store it and refuse that code a second time — without
  that, a code read over someone's shoulder stays usable for a full minute.
* Recovery codes are stored as SHA-256 hashes. They are 50 bits of randomness
  (not a memorable password), so a fast hash is the right choice here;
  argon2 would only make ten comparisons slow for no attacker-facing gain.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import struct
import time
from typing import List, Optional, Tuple

DIGITS = 6
STEP = 30                # seconds per code
WINDOW = 1               # how many steps either side to accept
ISSUER = "VAM"
RECOVERY_COUNT = 10
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no O/0, I/1, easy to read back


def new_secret() -> str:
    """A fresh base32 secret, the format every authenticator app expects."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10 ** DIGITS)).zfill(DIGITS)


def code_now(secret: str, at: Optional[float] = None) -> str:
    return _code(secret, int((at if at is not None else time.time()) // STEP))


def verify(secret: str, code: str, last_step: int = 0,
           at: Optional[float] = None) -> Tuple[bool, int]:
    """(accepted, step). A step at or below `last_step` is refused as reused."""
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    if len(code) != DIGITS or not secret:
        return False, last_step
    now = int((at if at is not None else time.time()) // STEP)
    for step in range(now - WINDOW, now + WINDOW + 1):
        if step <= last_step:
            continue
        if hmac.compare_digest(_code(secret, step), code):
            return True, step
    return False, last_step


def otpauth_uri(secret: str, username: str, issuer: str = ISSUER) -> str:
    from urllib.parse import quote
    # The colon stays literal: it is the separator every app expects
    # between issuer and account name.
    label = quote(f"{issuer}:{username}", safe=":")
    return (f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}"
            f"&algorithm=SHA1&digits={DIGITS}&period={STEP}")


def qr_svg(uri: str) -> Optional[str]:
    """An inline SVG for the setup page, or None when segno is missing.

    None rather than an exception: the secret is shown as text beside it, so a
    missing package costs convenience, not the feature.
    """
    try:
        import io
        import segno
    except ImportError:
        return None
    buf = io.BytesIO()          # segno writes bytes even for SVG
    segno.make(uri, error="m").save(buf, kind="svg", scale=5, border=2,
                                    dark="#cdd6f4", light=None, xmldecl=False)
    return buf.getvalue().decode("utf-8")


# ── recovery codes ──────────────────────────────────────────────────────────

def _hash(code: str) -> str:
    return hashlib.sha256(normalise(code).encode()).hexdigest()


def normalise(code: str) -> str:
    """Upper-cased, punctuation and spaces removed — people retype these."""
    return "".join(ch for ch in (code or "").upper() if ch in _ALPHABET)


def new_recovery() -> Tuple[List[str], str]:
    """(codes to show once, JSON of their hashes to store)."""
    codes = []
    for _ in range(RECOVERY_COUNT):
        raw = "".join(secrets.choice(_ALPHABET) for _ in range(10))
        codes.append(f"{raw[:5]}-{raw[5:]}")
    return codes, json.dumps([_hash(c) for c in codes])


def use_recovery(stored: Optional[str], code: str) -> Tuple[bool, str]:
    """(accepted, JSON to store back). A used code is removed, not marked."""
    try:
        hashes = json.loads(stored or "[]")
    except ValueError:
        hashes = []
    want = _hash(code)
    if not want or want not in hashes:
        return False, json.dumps(hashes)
    hashes = [h for h in hashes if not hmac.compare_digest(h, want)]
    return True, json.dumps(hashes)


def recovery_left(stored: Optional[str]) -> int:
    try:
        return len(json.loads(stored or "[]"))
    except ValueError:
        return 0
