"""Hand-typed release dates (2026-10-22).

  * whatever is typed comes out as YYYY-MM-DD (or YYYY / YYYY-MM when that is
    all that is known); impossible or ambiguous input is refused, not guessed
  * a typed date wins over an imported one, a later import does not overwrite
    it, and clearing it falls back to the import — for numbered films
    (`movie_title`) and for everything else (`file_title`)
  * both columns arrive by migration on databases built before them — a test
    on a fresh schema can never catch a missing one (CLAUDE.md, third time)
  * over HTTP: a bad date is a 400 with a reason, a good one is normalised

Everything runs on throwaway databases.

Run:  python tests/test_releasedate.py      (from backend/)
"""
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db                                       # noqa: E402
from app.core import filetitle, javdb, releasedate       # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def test_normalize():
    print("\n格式統一：各種寫法都變成 年-月-日")
    good = {
        "2026-10-22": "2026-10-22", "2026/10/22": "2026-10-22",
        "2026.1.5": "2026-01-05", "2026 10 22": "2026-10-22",
        "2026年10月22日": "2026-10-22", "20261022": "2026-10-22",
        "２０２６／１０／２２": "2026-10-22", " 2026-10-22 ": "2026-10-22",
        "22/10/2026": "2026-10-22", "10/22/2026": "2026-10-22",
        "2014": "2014", "2014-5": "2014-05", "2014年5月": "2014-05", "": "",
    }
    for raw, want in good.items():
        got = releasedate.normalize(raw)
        check(got == want, f"{raw!r} → {got!r}")
    for bad in ("2026-02-30", "2026-13", "261022", "05/06/2026", "abc",
                "1850", "2026-10-22-1"):
        try:
            releasedate.normalize(bad)
            check(False, f"{bad!r} 應該被拒絕")
        except ValueError as e:
            check("2026-10-22" in str(e), f"{bad!r} 拒絕，並附正確格式的例子")
    check(releasedate.normalize("2024-02-29") == "2024-02-29", "閏年 2/29 可以")


def test_precedence(tmp: Path):
    print("\n手動填的贏過匯入、重新匯入不會蓋掉、清空就還原")
    db.DB_PATH = tmp / "vam.db"
    db.init_db()
    javdb.DB_PATH = tmp / "javdb.sqlite3"
    javdb._migrated = False

    javdb.set_released({"ABC-123": "2018-04-13"})
    r = javdb.set_released_manual("abc-123", "2019-01-02")
    check(r == {"released": "2019-01-02", "imported": "2018-04-13"}, f"番號：手動贏 {r}")
    javdb.set_released({"ABC-123": "2018-04-14"})
    check(javdb.released()["ABC-123"] == "2019-01-02", "重新匯入不會蓋掉手動的")
    r = javdb.set_released_manual("ABC-123", "")
    check(r["released"] == "2018-04-14" and javdb.released()["ABC-123"] == "2018-04-14",
          "清空手動的 → 回到匯入的")
    javdb.set_released_manual("NEW-001", "2020")
    check(javdb.released().get("NEW-001") == "2020", "沒有匯入過的番號也能填")

    p = tmp / "lib" / "live-20260101.mp4"
    filetitle.set_many_released({p: "2014"})                  # an IAFD year
    r = filetitle.set_released_manual(p, "2014-05-03")
    check(r == {"released": "2014-05-03", "imported": "2014"}, f"沒番號：手動贏 {r}")
    filetitle.set_many_released({p: "2015"})
    check(filetitle.all_released()[filetitle.key(p)] == "2014-05-03",
          "IAFD 重新匯入不會蓋掉手動的")
    filetitle.set_title(p, "某個片名")
    filetitle.set_title(p, "")
    check(filetitle.all_released()[filetitle.key(p)] == "2014-05-03",
          "清空片名不會把發行日的那一列刪掉")
    r = filetitle.set_released_manual(p, "")
    check(r["released"] == "2015", "清空手動的 → 回到匯入的年份")

    q = tmp / "lib" / "only-typed.mp4"
    filetitle.set_released_manual(q, "2021-07-07")
    filetitle.set_released(q, "")                             # import clears its own
    check(filetitle.all_released().get(filetitle.key(q)) == "2021-07-07",
          "匯入端清掉自己的欄位不會連手動的一起刪")
    filetitle.set_released_manual(q, "")
    check(filetitle.key(q) not in filetitle.all_released(), "兩個都清空就沒有日期")
    moved = tmp / "lib" / "sub" / "only-typed.mp4"
    filetitle.set_released_manual(q, "2021-07-07")
    filetitle.move(q, moved)
    check(filetitle.all_released().get(filetitle.key(moved)) == "2021-07-07",
          "檔案搬走，手動的發行日跟著走")


def test_migration(tmp: Path):
    print("\n既有資料庫（沒有新欄位）會自動補上")
    old = tmp / "old_javdb.sqlite3"
    c = sqlite3.connect(old)
    c.execute("CREATE TABLE movie_title (video_id TEXT PRIMARY KEY, manual TEXT, "
              "ja TEXT, zh TEXT, en TEXT, released TEXT, checked_at REAL)")
    c.execute("INSERT INTO movie_title (video_id, released) VALUES ('OLD-001', '2010-01-01')")
    c.commit(); c.close()
    javdb.DB_PATH = old
    javdb._migrated = False
    r = javdb.set_released_manual("OLD-001", "2011-02-03")
    check(r["released"] == "2011-02-03" and r["imported"] == "2010-01-01",
          "movie_title 補上 released_manual")

    oldvam = tmp / "old_vam.db"
    c = sqlite3.connect(oldvam)
    c.execute("CREATE TABLE file_title (path TEXT PRIMARY KEY, title TEXT NOT NULL, "
              "released TEXT)")
    c.commit(); c.close()
    db.DB_PATH = oldvam
    db.init_db()
    r = filetitle.set_released_manual(tmp / "x.mp4", "2012")
    check(r["released"] == "2012", "file_title 補上 released_manual")


def test_over_http(tmp: Path):
    print("\nHTTP：格式錯回 400 並說原因，正確的統一格式後存起來")
    db.DB_PATH = tmp / "vam.db"
    db.init_db()
    javdb.DB_PATH = tmp / "javdb.sqlite3"
    javdb._migrated = False
    from fastapi.testclient import TestClient            # noqa: E402
    from app import auth, main                          # noqa: E402

    f = tmp / "lib" / "clip.mp4"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"x")
    saved = main.is_allowed
    main.is_allowed = lambda p, u: True
    main.app.dependency_overrides[auth.current_user] = lambda: {
        "id": 1, "role": "admin", "username": "a", "can_edit": True}
    try:
        c = TestClient(main.app)
        r = c.post("/api/released/set", json={"path": str(f), "released": "2026/10/22"})
        check(r.status_code == 200 and r.json()["released"] == "2026-10-22",
              f"沒番號：2026/10/22 → {r.json().get('released')}")
        r = c.post("/api/released/set", json={"path": str(f), "released": "2026-02-30"})
        check(r.status_code == 400 and "沒有 30 日" in r.json()["detail"],
              f"不存在的日期回 400：{r.json().get('detail')}")
        check(filetitle.all_released().get(filetitle.key(f)) == "2026-10-22",
              "被拒絕時原本的值不變")
        r = c.post("/api/released/set", json={"video_id": "abc-123", "path": str(f),
                                               "released": "2018年4月13日"})
        check(r.status_code == 200 and r.json()["video_id"] == "ABC-123"
              and r.json()["released"] == "2018-04-13", "有番號：存到番號，套用到所有副本")
    finally:
        main.is_allowed = saved
        main.app.dependency_overrides.clear()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    test_normalize()
    for fn in (test_precedence, test_migration, test_over_http):
        d = Path(tempfile.mkdtemp())
        try:
            fn(d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
