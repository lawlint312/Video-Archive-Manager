"""Database backups: a consistent copy, on a schedule, that reports its own failures.

The assertions that earn their keep are about the two ways a backup betrays you:
a folder that looks like a finished backup but is half-written, and a run that
has been failing for weeks while the settings page still says "on".

Run:  python tests/test_backup.py        (from backend/)
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_TD = tempfile.mkdtemp(prefix="backup_test_")
os.environ["VAM_DATA_DIR"] = os.path.join(_TD, "data")
os.environ["VAM_CACHE_DIR"] = os.path.join(_TD, "cache")

from app import db                                        # noqa: E402
from app.config import DATA_DIR                           # noqa: E402
from app.core import backup                               # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def test_copies_what_cannot_be_rebuilt():
    print("\n備份：複製救不回來的那幾個資料庫")
    db.init_db()
    conn = db.connect()
    conn.execute("INSERT INTO users (username, password_hash, role, created_at) "
                 "VALUES ('someone', 'x', 'viewer', '2026-09-16')")
    conn.commit()
    conn.close()
    # faces.db is optional; make it exist so the copy path is exercised too
    sqlite3.connect(DATA_DIR / "faces.db").close()

    dest = os.path.join(_TD, "backups")
    info = backup.run(dest, keep=3)
    names = {f["name"] for f in info["files"]}
    check("vam.db" in names, f"vam.db 有備份到（{sorted(names)}）")

    folder = Path(info["folder"])
    check(folder.is_dir() and folder.name.startswith("vam-"),
          f"備份放在有時間戳的資料夾裡：{folder.name}")
    copied = sqlite3.connect(folder / "vam.db")
    rows = copied.execute("SELECT username FROM users").fetchall()
    copied.close()
    check(rows and rows[0][0] == "someone",
          "複製出來的是可以打開的資料庫，而且內容在")

    check(not list(Path(dest).glob("*.part")),
          "沒有留下 .part 半成品 —— 完成前的名字不會被當成一份備份")


def test_missing_required_source_is_an_error():
    print("\n必要的資料庫不在就要失敗，不能產生一份空的備份")
    td = tempfile.mkdtemp(prefix="backup_empty_")
    saved = backup.DATA_DIR
    try:
        backup.DATA_DIR = Path(td)               # no vam.db in here
        dest = os.path.join(td, "out")
        try:
            backup.run(dest)
            check(False, "應該要丟出錯誤")
        except RuntimeError as e:
            check("vam.db" in str(e), f"說得出缺什麼：{e}")
        check(not list(Path(dest).iterdir()) if Path(dest).exists() else True,
              "失敗時不留下半個備份資料夾")
    finally:
        backup.DATA_DIR = saved


def test_prune_keeps_the_newest():
    print("\n只保留最近幾份，而且留下的是最新的")
    dest = Path(_TD) / "prune"
    dest.mkdir()
    made = []
    for day in range(1, 8):
        d = dest / f"vam-2026-09-{day:02d}_0300"
        d.mkdir()
        (d / "vam.db").write_bytes(b"x")
        made.append(d.name)
    # something that is not a backup must survive: the folder may be shared
    (dest / "我自己的東西").mkdir()
    gone = backup.prune(dest, keep=3)
    left = sorted(d.name for d in dest.iterdir() if d.name.startswith("vam-"))
    check(gone == 4 and left == made[-3:], f"刪掉最舊的 4 份，留下 {left}")
    check((dest / "我自己的東西").is_dir(), "不屬於備份的資料夾原封不動")


def test_due_and_problem_messages():
    print("\n排程判斷與設定檢查")
    check(backup.due(None), "從來沒備份過 = 該跑")
    check(backup.due("2026-09-01T00:00:00+00:00"), "上次是兩週前 = 該跑")
    from datetime import datetime, timezone
    check(not backup.due(datetime.now(timezone.utc).isoformat()),
          "剛剛才跑過 = 不用再跑")
    check(backup.due("垃圾字串"), "讀不懂的時間當成沒備份過，不是當成剛跑完")

    check(backup.problem_with("") and "指定" in backup.problem_with(""),
          "沒填資料夾時說得出要填什麼")
    check(backup.problem_with("VAM備份"), "相對路徑要擋下來")
    ok_dir = os.path.join(_TD, "writable")
    check(backup.problem_with(ok_dir) is None, "可以寫入的完整路徑就過")
    check(backup.same_drive_as_data(str(DATA_DIR)),
          "跟資料同一顆磁碟機代號要認得出來 —— 設定畫面要據此提醒")


def test_no_failure_before_setup():
    print("\n還沒設定資料夾時，不能顯示「上次失敗」")
    from fastapi.testclient import TestClient
    from app import auth, main
    from app import settings as app_settings
    auth.TWOFA_ENFORCED = False
    auth.create_user("boss", "Str0ngPass98", "admin")
    c = TestClient(main.app, client=("192.168.1.20", 5555))
    c.post("/api/auth/login", json={"username": "boss", "password": "Str0ngPass98"})

    r = c.post("/api/backup/run")
    check(r.status_code == 400 and "指定" in r.text, "沒資料夾就按立即備份：當下說明原因")
    check(not c.get("/api/backup").json()["last_error"],
          "但不記成「上次失敗」—— 那不是一次失敗的備份，只是還沒設定好")

    app_settings.save({"backup_last_error": "舊資料夾寫不進去"})
    c.post("/api/settings", json={"backup_dir": os.path.join(_TD, "newdest")})
    check(not c.get("/api/backup").json()["last_error"],
          "換了資料夾，舊資料夾的錯誤就清掉")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for fn in (test_copies_what_cannot_be_rebuilt,
               test_missing_required_source_is_an_error,
               test_prune_keeps_the_newest,
               test_due_and_problem_messages,
               test_no_failure_before_setup):
        fn()
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
