"""Permission groups: which features an account may use.

Replaces the fixed admin / editor / viewer trio. `users.role` still holds a
key, so nothing about sessions, the login flow or existing rows changes:

* `admin` is not a row here and never will be. It holds every permission plus
  the things no group can be given (settings, accounts, login log, backups).
  Keeping it outside the table is what makes "lock yourself out by editing a
  group" impossible.
* `editor` and `viewer` are seeded as groups on first start with exactly the
  permissions they had before, so the day this ships nobody can do more or
  less than the day before. After that they are ordinary, editable groups.

Enforced in the endpoints (`auth.require_perm`). The frontend reads `perms`
only to decide which buttons to draw.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

from . import db

# (key, label, section, what it covers) — the order is the order in the UI.
PERMS = [
    ("archive",      "歸檔",               "整理",     "待分類 → 角色資料夾"),
    ("rename_merge", "角色改名、整合資料夾", "整理",     "改名、合併同一人的資料夾"),
    ("groups",       "分類管理",           "整理",     "側邊欄分類"),
    ("edit_info",    "編輯資料",           "整理",     "個人資料、大頭照、備忘、片名"),
    ("delete",       "刪除檔案",           "清理",     "刪除模式、待決配對"),
    ("dedup",        "去重",               "清理",     "重複掃描（影片、圖片）"),
    ("person",       "人物辨識",           "清理",     "人物辨識、人臉索引、相似排序"),
    ("convert",      "轉檔、修復",         "影片處理", "轉檔分頁"),
    ("shrink",       "壓縮",               "影片處理", "壓縮分頁"),
    ("clip",         "剪輯",               "影片處理", "剪輯模式"),
    ("lookup",       "角色查詢",           "查詢",     "個資、片名、WIKI／IAFD 匯入、NFO 讀寫"),
]
PERM_KEYS = [p[0] for p in PERMS]

ADMIN = "admin"
BUILTIN = {
    # The old editor could do all of it; the old viewer none of it.
    "editor": ("可編輯", list(PERM_KEYS)),
    "viewer": ("唯讀", []),
}

_KEY_RE = re.compile(r"^[a-z0-9_]{1,32}$")


def _clean(perms) -> List[str]:
    """Known keys only, in catalogue order — never trust what was posted."""
    want = set(perms or [])
    return [k for k in PERM_KEYS if k in want]


def seed():
    """Create the two built-in groups if this database has never had them."""
    conn = db.connect()
    try:
        for key, (name, perms) in BUILTIN.items():
            conn.execute(
                "INSERT OR IGNORE INTO roles (key, name, perms, builtin, created_at) "
                "VALUES (?,?,?,1,?)",
                (key, name, json.dumps(perms),
                 datetime.now(timezone.utc).isoformat()))
        conn.commit()
    finally:
        conn.close()


def all_roles() -> List[dict]:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT r.key, r.name, r.perms, r.builtin, "
            "(SELECT COUNT(*) FROM users u WHERE u.role = r.key) AS members "
            "FROM roles r ORDER BY r.builtin DESC, r.id").fetchall()
    finally:
        conn.close()
    return [{"key": r["key"], "name": r["name"], "perms": _clean(json.loads(r["perms"] or "[]")),
             "builtin": bool(r["builtin"]), "members": r["members"]} for r in rows]


def get(key: str) -> Optional[dict]:
    if key == ADMIN:
        return {"key": ADMIN, "name": "管理員", "perms": list(PERM_KEYS),
                "builtin": True}
    conn = db.connect()
    try:
        r = conn.execute("SELECT key, name, perms, builtin FROM roles WHERE key = ?",
                         (key,)).fetchone()
    finally:
        conn.close()
    if not r:
        return None
    return {"key": r["key"], "name": r["name"],
            "perms": _clean(json.loads(r["perms"] or "[]")),
            "builtin": bool(r["builtin"])}


def perms_for(role_key: str) -> List[str]:
    """Every permission of this group; an unknown key is treated as none."""
    r = get(role_key or "")
    return r["perms"] if r else []


def name_of(role_key: str) -> str:
    r = get(role_key or "")
    return r["name"] if r else role_key


def exists(key: str) -> bool:
    return key == ADMIN or get(key) is not None


def create(name: str, perms) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("請輸入權限組名稱")
    if len(name) > 30:
        raise ValueError("名稱最多 30 個字")
    conn = db.connect()
    try:
        if conn.execute("SELECT 1 FROM roles WHERE name = ?", (name,)).fetchone() \
                or name == "管理員":
            raise ValueError("已經有同名的權限組")
        # The key is generated, not derived from the name: renaming a group
        # must not change what every member's row points at.
        n = conn.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM roles").fetchone()[0]
        key = f"g{n}"
        while conn.execute("SELECT 1 FROM roles WHERE key = ?", (key,)).fetchone():
            n += 1
            key = f"g{n}"
        conn.execute(
            "INSERT INTO roles (key, name, perms, builtin, created_at) VALUES (?,?,?,0,?)",
            (key, name, json.dumps(_clean(perms)),
             datetime.now(timezone.utc).isoformat()))
        conn.commit()
    finally:
        conn.close()
    return get(key)


def update(key: str, name: Optional[str] = None, perms=None) -> dict:
    if key == ADMIN:
        raise ValueError("管理員的權限固定，不能修改")
    current = get(key)
    if not current:
        raise LookupError("找不到這個權限組")
    sets, args = [], []
    if name is not None:
        name = name.strip()
        if not name:
            raise ValueError("請輸入權限組名稱")
        conn = db.connect()
        try:
            dup = conn.execute("SELECT 1 FROM roles WHERE name = ? AND key != ?",
                               (name, key)).fetchone()
        finally:
            conn.close()
        if dup or name == "管理員":
            raise ValueError("已經有同名的權限組")
        sets.append("name = ?"); args.append(name)
    if perms is not None:
        sets.append("perms = ?"); args.append(json.dumps(_clean(perms)))
    if sets:
        conn = db.connect()
        try:
            conn.execute(f"UPDATE roles SET {', '.join(sets)} WHERE key = ?", (*args, key))
            conn.commit()
        finally:
            conn.close()
    return get(key)


def delete(key: str):
    if key == ADMIN:
        raise ValueError("管理員不能刪除")
    current = get(key)
    if not current:
        raise LookupError("找不到這個權限組")
    if current["builtin"]:
        # Not a hard technical need — but new accounts default to viewer, and
        # a missing default would fail in a place nobody is looking.
        raise ValueError("內建的權限組不能刪除，可以改名或調整勾選")
    conn = db.connect()
    try:
        members = conn.execute("SELECT COUNT(*) FROM users WHERE role = ?",
                               (key,)).fetchone()[0]
        if members:
            raise ValueError(f"還有 {members} 個帳號屬於這個權限組，請先把他們改到別的組")
        conn.execute("DELETE FROM roles WHERE key = ?", (key,))
        conn.commit()
    finally:
        conn.close()


def catalogue() -> List[Dict[str, str]]:
    return [{"key": k, "label": label, "section": section, "hint": hint}
            for k, label, section, hint in PERMS]
