"""The shared catalogue (core/catalog.py): moving earlier wiki imports in.

Built from the *old* shape on purpose — a database with `wiki_profile` rows
and no catalogue tables, as the user's real one is. A test that starts from
the current schema cannot catch a missing migration (CLAUDE.md).

    python tests/test_catalog.py
"""
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import catalog, javdb     # noqa: E402

FAIL = []


def check(ok, msg):
    print(("  ok   " if ok else "  FAIL ") + msg)
    if not ok:
        FAIL.append(msg)


def old_database(path: Path):
    conn = sqlite3.connect(str(path))
    # wiki_profile before `works` existed, and movie_title before its dates
    conn.executescript("""
        CREATE TABLE movie_title (video_id TEXT PRIMARY KEY, manual TEXT, ja TEXT,
                                  zh TEXT, en TEXT, checked_at REAL);
        CREATE TABLE wiki_profile (folder TEXT PRIMARY KEY, page_name TEXT, url TEXT,
                                   total INTEGER NOT NULL, no_id INTEGER NOT NULL,
                                   sections TEXT, ids TEXT, at REAL NOT NULL);
    """)
    conn.execute("INSERT INTO movie_title (video_id, en) VALUES ('NSPS-694', 'English')")
    # imported before the release list was stored: numbers only
    conn.execute("INSERT INTO wiki_profile VALUES (?,?,?,?,?,?,?,?)",
                 ("c:\\lib\\old", "舊的", "", 2, 0,
                  json.dumps([{"name": "単体作品", "count": 2, "ids": ["AAA-001", "AAA-002"]}]),
                  "AAA-001,AAA-002", 1.0))
    conn.commit()
    conn.close()


def main(tmp: Path):
    saved = javdb.DB_PATH, javdb._migrated
    try:
        javdb.DB_PATH = tmp / "javdb.sqlite3"
        javdb._migrated = False
        old_database(javdb.DB_PATH)

        # a second row with a release list, written the way the later code did
        conn = javdb._connect()          # adds `works`, not yet the catalogue
        conn.execute("INSERT INTO wiki_profile (folder, page_name, url, total, no_id,"
                     " sections, ids, works, at) VALUES (?,?,?,?,?,?,?,?,?)",
                     ("c:\\lib\\new", "新的", "https://wiki/p", 3, 1, "[]", "NSPS-694,OBT-024",
                      json.dumps([{"i": "NSPS-694", "t": "日文片名", "d": "2018-04-13",
                                   "s": "単体作品"},
                                  {"i": "OBT-024", "t": "", "d": "2019-01-01", "s": "VR作品"},
                                  {"i": "", "t": "無番號", "d": "2010-01-01", "s": "単体作品"}],
                                 ensure_ascii=False), 2.0))
        conn.commit()
        conn.close()

        print("舊資料搬進共用目錄")
        s = catalog.summary("c:\\lib\\new")
        check(s and s["total"] == 3 and s["no_id"] == 1,
              f"有作品清單的：兩個番號＋一部無番號（{s and s['total']}）")
        check({x["name"]: x["count"] for x in s["sections"]} == {"単体作品": 2, "VR作品": 1},
              f"區段照舊（{s['sections']}）")
        check(javdb.titles()["NSPS-694"] == "日文片名", "片名搬過來，壓過英文")
        check(javdb.released().get("OBT-024") == "2019-01-01", "沒有片名的也帶著發行日")
        o = catalog.summary("c:\\lib\\old")
        check(o and sorted(o["ids"]) == ["AAA-001", "AAA-002"],
              "只存了數量的舊匯入：番號從區段裡拿回來")

        print("\n只搬一次")
        conn = javdb._connect()
        conn.execute("UPDATE wiki_profile SET page_name = '改過' WHERE folder = 'c:\\lib\\new'")
        conn.commit()
        conn.close()
        check(catalog.summary("c:\\lib\\new")["page_name"] == "新的",
              "第二次連線不再從 wiki_profile 搬（不會蓋掉之後的匯入）")
        conn = javdb._connect()
        n = conn.execute("SELECT COUNT(*) FROM catalog_member").fetchone()[0]
        conn.close()
        check(n == 4, f"成員沒有重複（{n}）")
    finally:
        javdb.DB_PATH, javdb._migrated = saved


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    d = Path(tempfile.mkdtemp())
    try:
        main(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
