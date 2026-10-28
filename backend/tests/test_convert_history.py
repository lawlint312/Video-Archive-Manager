"""Convert scan history: a scan's result survives a reload or a restart.

A scan with the full decode check can run for an hour, and the result used to
live only in the open dialog. Now every scan is saved (core/reporthistory.py,
the same history the duplicate scans keep) and can be opened again.

  * the scan job saves itself; the list says what it found and what is left
  * "left" is worked out against the disk now: a file converted away, or
    changed since (fixed in place), drops out — nothing to do for it
  * deleting a record removes only the record

Everything on throwaway directories, real data/ untouched.

Run:  python tests/test_convert_history.py      (from backend/)
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def ts_clip(dst: Path):
    subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc=size=320x180:rate=25:duration=2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-f", "mpegts", str(dst)],
                   check=True)


def wait(jobs, jid):
    for _ in range(240):
        j = jobs.get(jid)
        if j and j["status"] != "running":
            return j
        time.sleep(0.25)
    return None


def test_history(tmp: Path):
    print("\n轉檔掃描的結果會存下來，已處理的檔案不再列出")
    from fastapi.testclient import TestClient            # noqa: E402
    from app import auth, db, jobs, main                 # noqa: E402
    db.DB_PATH = tmp / "vam.db"
    db.init_db()
    lib = tmp / "lib"
    lib.mkdir()
    a, b = lib / "a.ts", lib / "b.ts"
    ts_clip(a)
    ts_clip(b)
    saved = (main.is_allowed, main._CONVERT_HISTORY, main.DECODE_CHECK_DB,
             main.CONVERT_CHECK_DB)
    main.is_allowed = lambda p, u: True
    main._CONVERT_HISTORY = tmp / "convert_history"
    main.DECODE_CHECK_DB = tmp / "decode_check.sqlite3"
    main.CONVERT_CHECK_DB = tmp / "convert_checks.sqlite3"
    main.app.dependency_overrides[auth.current_user] = lambda: {
        "id": 1, "role": "admin", "username": "a"}
    try:
        c = TestClient(main.app)
        r = c.post("/api/convert/scan", json={"roots": [str(lib)]})
        j = wait(jobs, r.json()["job_id"])
        rows = (j or {}).get("result", {}).get("rows", [])
        check(len(rows) == 2, f"掃描找到 2 個要換殼的：{len(rows)}")
        h = c.get("/api/convert/history").json()
        e = h["entries"][0] if h["entries"] else {}
        check(len(h["entries"]) == 1 and e.get("groups") == 2 and e.get("remaining") == 2,
              f"存了一筆：找到 2、還剩 2：{e}")
        check(e.get("roots") == ["lib"], f"範圍寫資料夾名：{e.get('roots')}")

        a.unlink()                                  # as a remux would
        b.write_bytes(b.read_bytes() + b"\0" * 188)  # as a fix in place would
        os.utime(b, (time.time() + 5, time.time() + 5))
        e = c.get("/api/convert/history").json()["entries"][0]
        check(e["remaining"] == 0, f"一個轉走、一個被就地修過 → 還剩 0：{e['remaining']}")
        rep = c.get(f"/api/convert/history/scan/{e['id']}").json()
        check(rep["rows"] == [], "打開報告也不再列出它們")

        ts_clip(lib / "c.ts")
        r = c.post("/api/convert/scan", json={"roots": [str(lib)], "check_blanks": True})
        wait(jobs, r.json()["job_id"])
        h = c.get("/api/convert/history").json()["entries"]
        check(len(h) == 2 and h[0]["latest"] and "錄影中斷" in h[0]["tags"],
              f"新的一筆排最前面、標出勾了哪些檢查：{[x['tags'] for x in h]}")
        rep = c.get(f"/api/convert/history/scan/{h[0]['id']}").json()
        # b.ts was only touched, not converted, so the new scan lists it again
        check(sorted(Path(x["path"]).name for x in rep["rows"]) == ["b.ts", "c.ts"],
              f"打開新的那筆：{[Path(x['path']).name for x in rep['rows']]}")

        r = c.delete(f"/api/convert/history/scan/{h[1]['id']}")
        check(r.status_code == 200 and len(c.get("/api/convert/history").json()["entries"]) == 1,
              "刪除只刪紀錄")
        check((lib / "c.ts").exists() and b.exists(), "檔案都還在")
        r = c.get("/api/convert/history/scan/..%2F..%2Fvam")
        check(r.status_code == 404, f"不合格式的 id 一律拒絕：{r.status_code}")
    finally:
        (main.is_allowed, main._CONVERT_HISTORY, main.DECODE_CHECK_DB,
         main.CONVERT_CHECK_DB) = saved
        main.app.dependency_overrides.clear()


def test_reattach(tmp: Path):
    print("\n重開對話框接回進行中的掃描：只接得回自己的，跑完就不再列出")
    import threading
    from fastapi.testclient import TestClient            # noqa: E402
    from app import auth, db, jobs, main                 # noqa: E402
    from app.core import convert                         # noqa: E402
    db.DB_PATH = tmp / "vam.db"
    db.init_db()
    lib = tmp / "lib"
    lib.mkdir()
    gate = threading.Event()
    real_scan = convert.scan
    convert.scan = lambda *a, **kw: (gate.wait(30), [])[1]   # held until released
    saved = (main.is_allowed, main._CONVERT_HISTORY, main.DECODE_CHECK_DB,
             main.CONVERT_CHECK_DB)
    main.is_allowed = lambda p, u: True
    main._CONVERT_HISTORY = tmp / "convert_history"
    main.DECODE_CHECK_DB = tmp / "decode_check.sqlite3"
    main.CONVERT_CHECK_DB = tmp / "convert_checks.sqlite3"
    who = {"id": 1}
    main.app.dependency_overrides[auth.current_user] = lambda: {
        "id": who["id"], "role": "admin", "username": "a"}
    try:
        c = TestClient(main.app)
        r = c.post("/api/convert/scan", json={"roots": [str(lib)],
                                               "check_blanks": True})
        jid = r.json()["job_id"]
        got = c.get("/api/convert/scan/active").json()["scan"]
        check(got and got["job_id"] == jid, f"自己的掃描接得回：{got and got['job_id']}")
        check(got and got["options"].get("check_blanks") is True
              and "roots" not in got["options"],
              f"帶著那次勾的選項（不含路徑）：{got and got['options']}")
        who["id"] = 2
        other = c.get("/api/convert/scan/active").json()["scan"]
        check(other is None, f"別的帳號接不到：{other}")
        who["id"] = 1
        gate.set()
        wait(jobs, jid)
        done = c.get("/api/convert/scan/active").json()["scan"]
        check(done is None, f"跑完就不再列出（結果在歷史紀錄）：{done}")
    finally:
        gate.set()
        convert.scan = real_scan
        (main.is_allowed, main._CONVERT_HISTORY, main.DECODE_CHECK_DB,
         main.CONVERT_CHECK_DB) = saved
        main.app.dependency_overrides.clear()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for fn in (test_history, test_reattach):
        d = Path(tempfile.mkdtemp())
        try:
            fn(d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
