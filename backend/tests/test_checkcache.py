"""The convert scan remembers each check's answer per file (core/checkcache.py).

Scanning the same character twice used to pay for every check twice — the
full decode check already remembered its answers, the other three did not.

  * a second scan of unchanged files runs no check at all, and lists the
    same rows — a problem found the first time is still listed
  * each check is remembered on its own: asking for one more check later
    runs only that one
  * a file whose mtime changed (downloaded again over the old one) is
    checked again; the others are not
  * "check everything again" runs everything, and a version bump does too
  * a file that could not be read is not remembered as "nothing found"
  * over HTTP the job says how many files each check reused

Everything on throwaway directories, real data/ untouched.

Run:  python tests/test_checkcache.py      (from backend/)
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import checkcache, convert, decodecheck, retime   # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def clip(dst: Path, fmt=None):
    cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-y",
           "-f", "lavfi", "-i", "testsrc=size=320x180:rate=25:duration=2",
           "-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if fmt:
        cmd += ["-f", fmt]
    subprocess.run(cmd + [str(dst)], check=True)


class Counter:
    """Wraps the four checks and counts the calls (or forbids them)."""

    def __init__(self):
        self.real = (convert.plan_for, retime.needs_retime,
                     convert._blank_check, decodecheck.decode)
        self.calls = {"probe": 0, "timing": 0, "blanks": 0, "decode": 0}

    def __enter__(self):
        p, t, b, d = self.real

        def wrap(kind, fn):
            def f(*a, **kw):
                self.calls[kind] += 1
                return fn(*a, **kw)
            return f
        convert.plan_for = wrap("probe", p)
        retime.needs_retime = wrap("timing", t)
        convert._blank_check = wrap("blanks", b)
        decodecheck.decode = wrap("decode", d)
        return self

    def __exit__(self, *a):
        (convert.plan_for, retime.needs_retime,
         convert._blank_check, decodecheck.decode) = self.real


def strip(rows):
    return sorted((Path(r["path"]).name, r["action"], r["reason"]) for r in rows)


def test_reuse(tmp: Path):
    print("\n第二次掃描：檔案沒變就沿用，結果一樣")
    lib = tmp / "lib"
    lib.mkdir()
    clip(lib / "a.ts", "mpegts")
    clip(lib / "b.mp4")
    clip(lib / "c.mp4")
    db, dc = tmp / "checks.sqlite3", tmp / "decode.sqlite3"
    opts = dict(include_mp4=True, check_timing=True, check_blanks=True,
                check_decode=True, decode_cache=dc, check_cache=db)

    stats = {}
    with Counter() as c:
        first = convert.scan([lib], stats=stats, **opts)
    check(c.calls == {"probe": 3, "timing": 2, "blanks": 3, "decode": 3},
          f"第一次每種檢查都跑：{c.calls}")
    check(all(s["reused"] == 0 for s in stats.values()), f"沒有沿用：{stats}")

    stats = {}
    with Counter() as c:
        again = convert.scan([lib], stats=stats, **opts)
    check(c.calls == {"probe": 0, "timing": 0, "blanks": 0, "decode": 0},
          f"第二次一個都不跑：{c.calls}")
    check(strip(again) == strip(first), f"列出的結果一樣：{strip(again)}")
    check(stats.get("probe") == {"reused": 3, "checked": 0}
          and stats.get("decode") == {"reused": 3, "checked": 0}
          and stats.get("blanks") == {"reused": 3, "checked": 0}
          and stats.get("timing") == {"reused": 2, "checked": 0},
          f"回報沿用了幾支：{stats}")

    print("\n每種檢查分開記：只做過格式檢查的，之後加勾錄影中斷只跑那一種")
    db2 = tmp / "checks2.sqlite3"
    convert.scan([lib], include_mp4=True, check_cache=db2)
    with Counter() as c:
        convert.scan([lib], include_mp4=True, check_blanks=True, check_cache=db2)
    check(c.calls["probe"] == 0 and c.calls["blanks"] == 3,
          f"格式沿用、錄影中斷第一次跑：{c.calls}")

    print("\n重新下載覆蓋（修改時間變了）的那支會重查，其他不會")
    st = (lib / "b.mp4").stat()
    os.utime(lib / "b.mp4", (st.st_atime, st.st_mtime + 10))
    with Counter() as c:
        convert.scan([lib], **opts)
    check(c.calls == {"probe": 1, "timing": 1, "blanks": 1, "decode": 1},
          f"只重查 b.mp4：{c.calls}")

    print("\n勾「全部重新檢查」：全部重跑")
    stats = {}
    with Counter() as c:
        convert.scan([lib], recheck=True, stats=stats, **opts)
    check(c.calls == {"probe": 3, "timing": 2, "blanks": 3, "decode": 3},
          f"每種都重跑：{c.calls}")
    check(all(s["reused"] == 0 for s in stats.values()), f"回報沒有沿用：{stats}")
    with Counter() as c:
        convert.scan([lib], **opts)
    check(sum(c.calls.values()) == 0, f"重跑的結果也記住了：{c.calls}")

    print("\n規則改了（版本號 +1）：那一種的舊結果作廢")
    real = dict(convert.CHECK_VERSIONS)
    convert.CHECK_VERSIONS["blanks"] += 1
    try:
        with Counter() as c:
            convert.scan([lib], **opts)
    finally:
        convert.CHECK_VERSIONS.clear()
        convert.CHECK_VERSIONS.update(real)
    check(c.calls == {"probe": 0, "timing": 0, "blanks": 3, "decode": 0},
          f"只有錄影中斷重跑：{c.calls}")


def test_found_problem_is_kept(tmp: Path):
    print("\n上次查出有問題的：沿用時照樣列出來")
    lib = tmp / "lib"
    lib.mkdir()
    clip(lib / "rec.mp4")
    db = tmp / "checks.sqlite3"
    fake = {"blanks": [{"start": 0.5, "end": 1.5, "cut": 0.96}], "skipped": 0,
            "frames": 50, "removed": 0.96}
    real = convert._blank_check
    convert._blank_check = lambda p: fake
    try:
        first = convert.scan([lib], check_blanks=True, check_cache=db)
    finally:
        convert._blank_check = real
    check([r["action"] for r in first] == ["gapcut"], f"第一次：{strip(first)}")
    convert._blank_check = lambda p: (_ for _ in ()).throw(AssertionError("又查了"))
    try:
        again = convert.scan([lib], check_blanks=True, check_cache=db)
    finally:
        convert._blank_check = real
    check(strip(again) == strip(first), f"第二次沒重查、照樣列出：{strip(again)}")


def test_unreadable_not_remembered(tmp: Path):
    print("\n讀不到的檔案不記成「沒問題」（NAS 一時斷線不該讓它從此被跳過）")
    lib = tmp / "lib"
    lib.mkdir()
    (lib / "junk.mp4").write_bytes(b"not a video" * 100)
    (lib / "junk.ts").write_bytes(b"not a video" * 100)
    db = tmp / "checks.sqlite3"
    convert.scan([lib], include_mp4=True, check_blanks=True, check_cache=db)
    with Counter() as c:
        convert.scan([lib], include_mp4=True, check_blanks=True, check_cache=db)
    check(c.calls["probe"] == 2 and c.calls["blanks"] == 2,
          f"兩支都再查一次：{c.calls}")
    store = checkcache.Store(db)
    try:
        hit = store.get("blanks", convert.CHECK_VERSIONS["blanks"], lib / "junk.mp4",
                        *convert._stat(lib / "junk.mp4"))
    finally:
        store.close()
    check(hit is checkcache.MISS, "資料庫裡沒有它")


def test_stop_keeps_what_was_checked(tmp: Path):
    print("\n中途停止：沒查到的不記，查過的記住")
    lib = tmp / "lib"
    lib.mkdir()
    for n in "abc":
        clip(lib / f"{n}.mp4")
    db = tmp / "checks.sqlite3"

    class Prog:
        cancelled = False

        def __call__(self, msg):
            self.cancelled = True          # stop after the first file

    # one worker, so "after the first file" is exact (8 would all have started)
    paths = sorted(lib.iterdir())
    store = checkcache.Store(db)
    try:
        got = convert._run_checks(paths, convert._blank_check, "blanks", "x",
                                  store=store, progress=Prog(), stoppable=True,
                                  keep=lambda v: v is not convert._UNREAD, workers=1)
    finally:
        store.close()
    check(got[0] is None and got[1] is checkcache.MISS and got[2] is checkcache.MISS,
          "停止後沒開始的是 MISS（不是「沒問題」）")
    stats = {}
    with Counter() as c:
        convert.scan([lib], check_blanks=True, check_cache=db, stats=stats)
    check(1 <= stats["blanks"]["reused"] < 3 and c.calls["blanks"] == 3 - stats["blanks"]["reused"],
          f"沿用停止前查過的、補查其餘：{stats['blanks']}")


def test_over_http(tmp: Path):
    print("\nHTTP：結果附上每種檢查沿用了幾支")
    from fastapi.testclient import TestClient            # noqa: E402
    from app import auth, db, jobs, main                 # noqa: E402
    db.DB_PATH = tmp / "vam.db"
    db.init_db()
    lib = tmp / "lib"
    lib.mkdir()
    clip(lib / "a.ts", "mpegts")
    saved = (main.is_allowed, main._CONVERT_HISTORY, main.DECODE_CHECK_DB,
             main.CONVERT_CHECK_DB)
    main.is_allowed = lambda p, u: True
    main._CONVERT_HISTORY = tmp / "convert_history"
    main.DECODE_CHECK_DB = tmp / "decode_check.sqlite3"
    main.CONVERT_CHECK_DB = tmp / "convert_checks.sqlite3"
    main.app.dependency_overrides[auth.current_user] = lambda: {
        "id": 1, "role": "admin", "username": "a"}

    def run(**kw):
        r = c.post("/api/convert/scan", json={"roots": [str(lib)], "check_blanks": True, **kw})
        jid = r.json()["job_id"]
        for _ in range(240):
            j = jobs.get(jid)
            if j and j["status"] != "running":
                return j["result"]
            time.sleep(0.25)

    try:
        c = TestClient(main.app)
        first = run()
        check(len(first["rows"]) == 1 and first["checks"]["probe"]["reused"] == 0,
              f"第一次：{first['checks']}")
        again = run()
        check(again["checks"]["probe"] == {"reused": 1, "checked": 0}
              and again["checks"]["blanks"] == {"reused": 1, "checked": 0},
              f"第二次沿用：{again['checks']}")
        forced = run(recheck=True)
        check(forced["checks"]["blanks"] == {"reused": 0, "checked": 1},
              f"全部重新檢查：{forced['checks']}")
        check(main.CONVERT_CHECK_DB.exists(), "記在暫存目錄，不是真的 data/")
    finally:
        (main.is_allowed, main._CONVERT_HISTORY, main.DECODE_CHECK_DB,
         main.CONVERT_CHECK_DB) = saved
        main.app.dependency_overrides.clear()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for fn in (test_reuse, test_found_problem_is_kept, test_unreadable_not_remembered,
               test_stop_keeps_what_was_checked, test_over_http):
        d = Path(tempfile.mkdtemp())
        try:
            fn(d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
