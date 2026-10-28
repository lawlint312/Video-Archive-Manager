"""
Runtime app settings, stored in the DB and editable via the admin Settings UI.
Replaces the static env-var media roots (env is now only the first-run seed).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from . import db
from .config import INITIAL_MEDIA_ROOTS

DEFAULTS = {
    "inbox_paths": [],                               # 待分類資料夾
    "archive_roots": list(INITIAL_MEDIA_ROOTS),      # 歸檔根目錄
    "default_archive_root": "",                      # 新角色預設建在哪
    "default_browse_tab": "videos",                  # videos | profile
    "seek_seconds": 10,                              # 方向鍵快進/快退秒數
    "sheet_interval": 60,                            # 縮圖表的取樣間隔（秒）
    "sheet_tile_width": 480,                         # 縮圖表每格寬度（px）
    "sheet_workers": 4,                              # 同時抓幾格（1 = 不平行）
    # "cpu" or "gpu". Not about speed — GPU is ~29% slower here — but about
    # who pays: the CPU path burns ~10 cores, the GPU path ~0.7, which is what
    # matters when other people are using the machine at the same time.
    "sheet_hwaccel": "cpu",
    # Three separate budgets — see core/cachelimit.py for why sharing one was a
    # trap. `cache_limit_gb` is the old shared key, kept only so `get()` can
    # migrate it once.
    "thumb_limit_gb": 2.0,                           # 影片縮圖快取上限（GB）
    "sheet_limit_gb": 2.0,                           # 縮圖表快取上限（GB）
    "photo_limit_gb": 3.0,                           # 照片縮圖快取上限（GB）
    # Off by default, and it must stay that way unless a reverse proxy you
    # control sits in front: with it on, anyone can send an X-Forwarded-For
    # header and walk straight through a per-account IP allowlist.
    "trust_proxy": False,
    # Refuse any request whose source is a public internet address. On by
    # default: the service is meant to be reached from home or over WireGuard,
    # and a port forward re-opened by mistake should fail closed, not expose a
    # plain-HTTP login page (which is what happened until 2026-09-15).
    "lan_only": True,
    # Backups of vam.db / faces.db / person.sqlite3 — off until someone picks
    # a folder, because the only sensible destination is a *different* disk
    # and no default can know where that is on this machine.
    "backup_enabled": False,
    "backup_dir": "",
    "backup_keep": 14,           # how many timestamped runs to keep
    "backup_last_at": "",        # last success (ISO); shown in settings
    "backup_last_error": "",     # last failure, cleared by a success
    "sidebar_groups": {},        # { group_name: [character_name, ...] }
    "sidebar_group_order": [],   # display order of the groups
}


def _migrate(cfg: dict) -> dict:
    """Fold the retired single cache limit into the two that replaced it.

    The old field lived on the 縮圖表 settings tab, so people read it as "the
    contact sheet limit" — that misreading is exactly how it ended up at
    0.2 GB while quietly capping 404 MB of thumbnails too. So the old value
    becomes the *sheet* limit (what the user believed they were setting) and
    thumbnails start from the default rather than inheriting a number that was
    never meant for them.
    """
    old = cfg.pop("cache_limit_gb", None)
    if old is not None and "sheet_limit_gb" not in cfg.get("_migrated", []):
        cfg["sheet_limit_gb"] = float(old)
        cfg["_migrated"] = sorted(set(cfg.get("_migrated", [])) | {"sheet_limit_gb"})
    return cfg


def get() -> dict:
    conn = db.connect()
    try:
        row = conn.execute("SELECT data FROM app_settings WHERE id=1").fetchone()
        if row:
            return _migrate({**DEFAULTS, **json.loads(row["data"])})
        return dict(DEFAULTS)
    except Exception:
        return dict(DEFAULTS)
    finally:
        conn.close()


_cached = {"at": 0.0, "cfg": None}


def get_cached(ttl: float = 2.0) -> dict:
    """`get()` for code that runs on every request.

    The `lan_only` guard sits in front of every thumbnail and video range
    request, and `get()` is a database read — measured 0.46 ms. A two-second
    cache removes that from the hot path; `save()` clears it, so a change made
    in this process applies on the very next request.
    """
    import time as _t
    now = _t.monotonic()
    if _cached["cfg"] is None or now - _cached["at"] > ttl:
        _cached["cfg"], _cached["at"] = get(), now
    return _cached["cfg"]


def save(patch: dict) -> dict:
    _cached["cfg"] = None
    merged = {**get(), **patch}
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO app_settings (id, data) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET data = excluded.data",
            (json.dumps(merged, ensure_ascii=False),))
        conn.commit()
    finally:
        conn.close()
    return merged


# ── path helpers ─────────────────────────────────────────────────────────────

def _resolve_list(paths: List[str]) -> List[Path]:
    out = []
    for p in paths:
        if p and str(p).strip():
            try:
                out.append(Path(p).resolve())
            except OSError:
                continue
    return out


def archive_roots() -> List[Path]:
    return _resolve_list(get()["archive_roots"])


def inbox_paths() -> List[Path]:
    return _resolve_list(get()["inbox_paths"])


def allowed_roots() -> List[Path]:
    """Every folder the server may read from = archive roots + inbox folders."""
    s = get()
    return _resolve_list(list(s["archive_roots"]) + list(s["inbox_paths"]))


# ── per-user scope ───────────────────────────────────────────────────────────
# A user row may carry `allowed_roots` (a JSON list). NULL/absent means "no
# restriction". The stored list is always intersected with the roots that are
# currently configured, so removing a root from Settings also removes it from
# every user without a second edit.

def user_root_strings(user: Optional[dict]) -> Optional[List[str]]:
    """The raw list stored on the user, or None when unrestricted."""
    if not user:
        return None
    raw = user.get("allowed_roots")
    if not raw:
        return None
    try:
        vals = json.loads(raw)
    except (TypeError, ValueError):
        return None
    vals = [v for v in vals if isinstance(v, str) and v.strip()]
    return vals or None


def roots_for(user: Optional[dict] = None) -> List[Path]:
    """The roots this user may read from."""
    every = allowed_roots()
    picked = user_root_strings(user)
    if picked is None:
        return every
    wanted = {str(p) for p in _resolve_list(picked)}
    scoped = [r for r in every if str(r) in wanted]
    # A restricted user whose roots have all been deleted from Settings gets
    # nothing — failing closed is the only safe choice here.
    return scoped


def archive_roots_for(user: Optional[dict] = None) -> List[Path]:
    allowed = {str(r) for r in roots_for(user)}
    return [r for r in archive_roots() if str(r) in allowed]


def inbox_paths_for(user: Optional[dict] = None) -> List[Path]:
    allowed = {str(r) for r in roots_for(user)}
    return [p for p in inbox_paths() if str(p) in allowed]


def is_allowed(path: Path, user: Optional[dict] = None) -> bool:
    """Security gate for every path the API touches.

    Passing `user` narrows the check to that user's roots; omitting it checks
    against the global configuration only.
    """
    return path_gate(user)(path)


def path_gate(user: Optional[dict] = None):
    """`is_allowed` bound to one user, for checking many paths in a row.

    The same check, with the roots worked out once: `roots_for` resolves every
    root (NAS shares included) and reads the settings, about 16 ms a call —
    445 rows of a saved convert scan took 8 s in `is_allowed` alone.
    """
    roots = roots_for(user)

    def allowed(path: Path) -> bool:
        try:
            rp = path.resolve()
        except OSError:
            return False
        for root in roots:
            try:
                rp.relative_to(root)
                return True
            except ValueError:
                continue
        return False
    return allowed
