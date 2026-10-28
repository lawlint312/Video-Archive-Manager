"""Duplicate-scan report history.

  * each scan is its own entry, newest first, pruned to KEEP
  * the first run after upgrading copies the old single report in, once
  * ids come from URLs, so anything that is not an id is refused
  * the list says how much of each report is still left to decide — counted
    now, against the files on disk — and filters to what the user may see
  * deleting an entry removes the record and nothing else
  * "the latest report" is the newest history entry

Everything is redirected to a temp folder: these endpoints write, and a test
that writes into the real data/ is how a real report was lost before.

Run:  python tests/test_reporthistory.py        (from backend/)
"""
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import reporthistory as rh            # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def report(at, files, roots=("C:/lib/A",)):
    return {"at": at, "tier2": True, "roots": list(roots), "partial": False,
            "groups": [{"tier": 1, "files": [{"path": str(f), "name": f.name}
                                             for f in files]}]}


def test_store(tmp: Path):
    print("\n存檔：一次一筆、新的在前、只留 KEEP 筆、撞名加序號")
    d = tmp / "h"
    base = time.mktime((2026, 10, 1, 12, 0, 0, 0, 0, -1))
    for i in range(rh.KEEP + 5):
        rh.save(d, {"at": base + i * 60, "groups": []})
    ids = rh.ids(d)
    check(len(ids) == rh.KEEP, f"只留 {rh.KEEP} 筆：{len(ids)}")
    check(ids[0] > ids[-1], f"新的在前：{ids[0]} … {ids[-1]}")
    a = rh.save(d, {"at": base + 999, "groups": []})
    b = rh.save(d, {"at": base + 999, "groups": []})
    check(b == a + "-2", f"同一秒的第二筆加序號：{a} / {b}")
    ids = rh.ids(d)
    check(ids.index(b) == ids.index(a) - 1, "撞名的第二筆排在它的雙胞胎前面（它比較新）")
    check(not list(d.glob("*.part")), "沒有留下寫到一半的暫存檔")


def test_ids_are_checked(tmp: Path):
    print("\nid 來自網址，不是 id 的一律拒絕")
    d = tmp / "h"
    rid = rh.save(d, {"at": time.time(), "groups": []})
    (tmp / "secret.json").write_text("{}")
    for bad in ("../secret", "..\\secret", "20261001", "x.json", "", rid + "/../x"):
        check(rh.path_of(d, bad) is None, f"拒絕 {bad!r}")
    check(rh.path_of(d, rid) is not None, "真的 id 找得到")
    check(not rh.delete(d, "../secret") and (tmp / "secret.json").exists(),
          "刪除也一樣擋，資料夾外的檔案沒被動到")


def test_migrate(tmp: Path):
    print("\n升級後第一次：把舊的單一報告搬進歷程，只搬一次")
    legacy = tmp / "dedup_report.json"
    legacy.write_text(json.dumps({"at": time.time() - 3600, "groups": []}))
    d = tmp / "h"
    first = rh.migrate(legacy, d)
    check(first is not None and rh.ids(d) == [first], "搬進來成為第一筆")
    check(rh.migrate(legacy, d) is None and len(rh.ids(d)) == 1, "再跑一次不會重複搬")
    check(legacy.exists(), "舊檔留著（不刪使用者的東西）")


def test_endpoints(tmp: Path):
    print("\n端點：清單、還剩幾組、開啟、刪除、最新報告")
    from fastapi.testclient import TestClient
    from app import auth, main

    lib = tmp / "lib"
    lib.mkdir()
    a1, a2, b1, b2 = (lib / n for n in ("a1.mp4", "a2.mp4", "b1.mp4", "b2.mp4"))
    for f in (a1, a2, b1, b2):
        f.write_bytes(b"x")

    saved = dict(main._HISTORY_DIR), dict(main._LEGACY_REPORT), main.is_allowed
    main._HISTORY_DIR.update(video=tmp / "h" / "video", image=tmp / "h" / "image")
    main._LEGACY_REPORT.update(video=tmp / "none.json", image=tmp / "none2.json")
    main.is_allowed = lambda p, u: "secret" not in str(p)
    main.app.dependency_overrides[auth.current_user] = lambda: {"id": 1, "role": "admin"}
    try:
        old = rh.save(main._HISTORY_DIR["video"],
                      report(time.time() - 7200, [a1, a2], roots=("C:/lib/A", "C:/secret/S")))
        new = rh.save(main._HISTORY_DIR["video"], report(time.time() - 60, [b1, b2]))
        b2.unlink()                       # the newer one has since been dealt with
        c = TestClient(main.app)

        asked = []
        real_learn = main._PathFacts.learn
        main._PathFacts.learn = lambda self, paths: (asked.extend(paths),
                                                     real_learn(self, paths))[1]
        try:
            e = c.get("/api/dedup/history?kind=video").json()["entries"]
        finally:
            main._PathFacts.learn = real_learn
        check(asked == [], f"列清單不檢查任何檔案（問了 {len(asked)} 個）")
        check([x["id"] for x in e] == [new, old], f"新的在前：{[x['id'] for x in e]}")
        check(e[0]["latest"] and not e[1]["latest"], "標出最新那筆")
        check(e[0]["groups"] == 1 and e[0]["remaining"] is None and e[1]["remaining"] is None,
              "沒開過的不知道還剩幾組，就說不知道")
        check(e[1]["roots"] == ["A"], f"範圍只列這個帳號看得到的：{e[1]['roots']}")

        r = c.get(f"/api/dedup/history/video/{old}").json()["report"]
        check(len(r["groups"]) == 1, "開得起來，內容是那一份")
        c.get("/api/dedup/report")               # the latest one, opened too
        e = {x["id"]: x for x in c.get("/api/dedup/history?kind=video").json()["entries"]}
        check(e[old]["remaining"] == 1 and e[old]["remaining_at"]
              and e[new]["remaining"] == 0,
              f"開過之後：舊的還剩 1 組、新的已處理完（{e[old]['remaining']}、{e[new]['remaining']}）")
        main.app.dependency_overrides[auth.current_user] = lambda: {"id": 2, "role": "admin"}
        e = c.get("/api/dedup/history?kind=video").json()["entries"]
        check(all(x["remaining"] is None for x in e), "記錄是每個帳號各自的")
        main.app.dependency_overrides[auth.current_user] = lambda: {"id": 1, "role": "admin"}
        check(rh.ids(main._HISTORY_DIR["video"]) == [new, old],
              "記錄檔不會被當成一份報告")
        check(c.get("/api/dedup/history/video/..%2Fnone").status_code in (404, 400),
              "亂給 id 拿不到東西")
        check(c.get("/api/dedup/history?kind=bogus").status_code == 400, "不認得的種類回 400")

        latest = c.get("/api/dedup/report").json()["report"]
        check(latest is None or latest.get("groups") == [],
              "「最新報告」就是歷程最新那筆（它已處理完，所以是空的）")

        # every scan also rewrites the single legacy file, as the real ones do
        hist = main._HISTORY_DIR["video"]
        legacy = main._LEGACY_REPORT["video"]
        legacy.write_text((hist / f"{new}.json").read_text(encoding="utf-8"), encoding="utf-8")
        check(c.delete(f"/api/dedup/history/video/{new}").status_code == 200, "刪除一筆")
        check(all(f.exists() for f in (a1, a2, b1)), "影片一個都沒被動到")
        latest = c.get("/api/dedup/report").json()["report"]
        check(latest is None,
              "刪掉最後一次掃描的紀錄之後，打開分頁不會自己跳出更舊的那份")
        check(len(c.get(f"/api/dedup/history/video/{old}").json()["report"]["groups"]) == 1,
              "舊的照樣能從歷史紀錄開")
        legacy.unlink()
        latest = c.get("/api/dedup/report").json()["report"]
        check(latest and len(latest["groups"]) == 1,
              "沒有最近一次報告可以比對時（舊安裝），照舊用歷史最新那筆")
        check(c.delete(f"/api/dedup/history/video/{new}").status_code == 404,
              "刪過的再刪回 404")
    finally:
        main._HISTORY_DIR.clear(); main._HISTORY_DIR.update(saved[0])
        main._LEGACY_REPORT.clear(); main._LEGACY_REPORT.update(saved[1])
        main.is_allowed = saved[2]
        main.app.dependency_overrides.clear()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for fn in (test_store, test_ids_are_checked, test_migrate, test_endpoints):
        d = Path(tempfile.mkdtemp())
        try:
            fn(d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
