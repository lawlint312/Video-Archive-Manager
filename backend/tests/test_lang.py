"""Interface language: one column on the account, set from the account menu.

  * an existing database has a `users` table without the column, and
    CREATE TABLE IF NOT EXISTS will not add it -- the migration must
  * any signed-in account sets its own, nobody else's, and only to a
    language the frontend has strings for
  * it must not sign the account out (update_user would have)

Run:  python tests/test_lang.py        (from backend/)
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# before any app module is imported: every path derives from these
_TMP = Path(tempfile.mkdtemp(prefix="lang_"))
os.environ["VAM_DATA_DIR"] = str(_TMP / "data")
os.environ["VAM_CACHE_DIR"] = str(_TMP / "cache")

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def test_migration():
    print("舊資料庫：users 表沒有 lang 欄位")
    from app import db
    db.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db.DB_PATH)
    # the shape every install had before this column existed
    conn.execute("""CREATE TABLE users (
        id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'viewer',
        created_at TEXT NOT NULL)""")
    conn.execute("INSERT INTO users (username, password_hash, role, created_at) "
                 "VALUES ('old', 'x', 'viewer', '2026-01-01')")
    conn.commit()
    conn.close()
    db.init_db()
    conn = sqlite3.connect(db.DB_PATH)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
    row = conn.execute("SELECT lang FROM users WHERE username = 'old'").fetchone()
    conn.close()
    check("lang" in cols, "遷移補上 lang 欄位")
    check(row == (None,), "舊帳號是 NULL（沒選過）")


def test_endpoint():
    print("\nHTTP：自己設定自己的語言")
    from fastapi.testclient import TestClient
    from app import auth, main
    auth.create_user("viewer1", "OldPass123", "viewer")
    auth.create_user("viewer2", "OldPass123", "viewer")
    c = TestClient(main.app, client=("192.168.1.123", 5555))
    check(c.post("/api/auth/lang", json={"lang": "en"}).status_code == 401,
          "沒登入不能設定")
    c.post("/api/auth/login", json={"username": "viewer1", "password": "OldPass123"})
    check(c.get("/api/auth/me").json()["lang"] == "", "沒選過：空字串")
    r = c.post("/api/auth/lang", json={"lang": "zh-CN"})
    check(r.status_code == 200, f"設成簡中（{r.status_code}）")
    check(c.get("/api/auth/me").status_code == 200, "設定之後還是登入著")
    check(c.get("/api/auth/me").json()["lang"] == "zh-CN", "me 回傳 zh-CN")
    r = c.post("/api/auth/lang", json={"lang": "ja"})
    check(r.status_code == 400, f"不認得的語言：{r.status_code}")
    check(c.get("/api/auth/me").json()["lang"] == "zh-CN", "被拒絕時不變")
    check(auth.get_user_by_name("viewer2").get("lang") is None, "別的帳號沒被動到")

    print("\n必須先改密碼的帳號也能換語言（看得懂那個畫面）")
    uid = auth.get_user_by_name("viewer1")["id"]
    auth.set_must_change(uid, "test")
    check(c.post("/api/auth/lang", json={"lang": "en"}).status_code == 200, "可以換")
    check(c.get("/api/characters").status_code == 403, "其他端點照樣擋")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for fn in (test_migration, test_endpoint):
        fn()
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
