"""Phase progress and cooperative cancellation for the two duplicate scans.

The assertion that earns its keep is **"每個階段的 total 等於實際跑的次數"**.
The totals are computed separately from the loops that consume them (`sum(len(g)
for g in by_size...)` beside a `for` over the same groups), so any future skip
condition added to a loop — the way `.ts` is skipped in Tier 2 — silently makes
the bar stop at 80% or run past 100%. Nothing else would catch that: the scan
still returns correct groups, only the number on screen lies.

Videos are faked (`probe_video` / `video_phash` patched) because this tests
counting, not detection — `test_imgdedup.py` and the real library cover that.

Run:  python tests/test_scanprogress.py        (from backend/)
"""
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import jobs                                    # noqa: E402
from app.core import dedup, scancount                   # noqa: E402
from app.core.progress import Reporter                  # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


class Recorder:
    """Progress callback that keeps every snapshot, plus a cancel flag."""

    def __init__(self, stop_after=None):
        self.seen = []
        self.stop_after = stop_after

    def __call__(self, snap):
        self.seen.append(dict(snap))

    @property
    def cancelled(self):
        return self.stop_after is not None and len(self.seen) >= self.stop_after

    def phases(self):
        """{phase index: [snapshots]} in arrival order."""
        out = {}
        for s in self.seen:
            out.setdefault(s["phase"], []).append(s)
        return out


def make_tree(tmp: Path):
    """3 identical + 2 identical + 3 unique, all same-extension."""
    d = tmp / "lib"
    d.mkdir()
    for i in range(3):
        (d / f"same_a{i}.mp4").write_bytes(b"A" * 4096)
    for i in range(2):
        (d / f"same_b{i}.mp4").write_bytes(b"B" * 8192)
    for i in range(3):
        (d / f"uniq{i}.mp4").write_bytes(bytes([i]) * (1000 + i))
    (d / "skipme.ts").write_bytes(b"T" * 4096)      # Tier 2 skips .ts
    return d


def patch_probe():
    """Every file the same duration → one big Tier 2 cluster."""
    dedup.probe_video = lambda p: (100.0, 1920, 1080)
    dedup.video_phash = lambda p: [0] * 5        # list of per-frame 64-bit ints


# ── the one that matters ────────────────────────────────────────────────────

def test_totals_match_reality(tmp: Path):
    print("\n每個階段的 total 要等於實際處理的數量")
    root = make_tree(tmp)
    patch_probe()
    rec = Recorder()
    dedup.find_duplicates([root], tier2=True, progress=rec,
                          count_db=tmp / "counts.json")

    ph = rec.phases()
    check(sorted(ph) == [1, 2, 3, 4], f"四個階段都回報了：{sorted(ph)}")

    # phase 1 has no knowable total on a first run, only a running count
    last1 = ph[1][-1]
    check(last1["total"] is None, "階段 1 第一次跑沒有總數（誠實地留空）")
    check(last1["done"] == 9, f"階段 1 數到 9 個檔案：{last1['done']}")

    # 6 files share a size with another: 3 + 2, plus the .ts, which is the same
    # 4096 bytes as the first set. Tier 1 *does* hash .ts files — only Tier 2
    # skips them — so counting it here is correct, and this line is what would
    # catch someone "fixing" the total by excluding .ts in both places.
    last2 = ph[2][-1]
    check(last2["total"] == 6, f"階段 2 總數 = 同大小的檔案數：{last2['total']}")
    check(last2["done"] == last2["total"],
          f"階段 2 跑完剛好到底：{last2['done']}/{last2['total']}")

    # Tier 2 gets everything except the 5 Tier 1 hits and the .ts
    last3 = ph[3][-1]
    check(last3["total"] == 3, f"階段 3 排除 Tier 1 命中與 .ts：{last3['total']}")
    check(last3["done"] == last3["total"],
          f"階段 3 跑完剛好到底：{last3['done']}/{last3['total']}")

    last4 = ph[4][-1]
    check(last4["done"] == last4["total"] and last4["total"] > 0,
          f"階段 4 跑完剛好到底：{last4['done']}/{last4['total']}")


def test_last_update_is_not_swallowed(tmp: Path):
    print("\n節流不能把每個階段的最後一次更新吃掉")
    rp = Reporter(lambda s: seen.append(dict(s)), phases=2)
    seen = []
    rp.phase(1, "甲", total=3)
    for _ in range(3):
        rp.step("x")            # all within the throttle window
    rp.phase(2, "乙", total=1)
    rp.step("y")
    rp.finish()
    ends = {s["phase"]: s for s in seen}      # last snapshot per phase
    for s in seen:
        ends[s["phase"]] = s
    check(ends[1]["done"] == 3, f"階段 1 最後停在 3：{ends[1]['done']}")
    check(ends[2]["done"] == 1, f"階段 2 最後停在 1：{ends[2]['done']}")


# ── remaining-time estimate ─────────────────────────────────────────────────

def test_eta(tmp: Path):
    print("\n剩餘時間：證據不足時不猜，夠了才報")
    rp = Reporter(lambda s: None, phases=1)
    rp.phase(1, "測試", total=100)
    check(rp.eta() is None, "一開始沒有估計值")
    for _ in range(3):
        rp.done += 1
        rp.emit(force=True)
    check(rp.eta() is None, "只有幾個樣本仍然不報（早期速率不具代表性）")

    # 20 items over ~0.5s of wall clock, then ask about the remaining 80
    rp2 = Reporter(lambda s: None, phases=1)
    rp2.phase(1, "測試", total=100)
    start = time.monotonic()
    for _ in range(20):
        time.sleep(0.025)
        rp2.done += 1
        rp2.emit(force=True)
    elapsed = time.monotonic() - start
    eta = rp2.eta()
    # the window needs ETA_MIN_SECONDS of history, so 0.5s is deliberately
    # too short — that itself is the guarantee being checked
    check(eta is None, f"不到 {rp2.__class__.__name__} 的時間門檻就不報（{elapsed:.2f}s）")

    rp3 = Reporter(lambda s: None, phases=1)
    rp3.phase(1, "測試", total=100)
    rp3._samples.clear()
    now = time.monotonic()
    rp3._samples.append((now - 10.0, 0))       # 10 seconds ago, nothing done
    rp3.done = 20
    rp3._samples.append((now, 20))
    for _ in range(4):                          # pad to the sample minimum
        rp3._samples.append((now, 20))
    eta = rp3.eta()
    check(eta is not None and 35 < eta < 45,
          f"20 個花 10 秒 → 剩下 80 個約 40 秒：{eta}")


# ── cancellation ────────────────────────────────────────────────────────────

def test_cancel_keeps_partial(tmp: Path):
    print("\n停止之後要保留已經找到的結果")
    root = make_tree(tmp)
    patch_probe()
    # let phases 1 and 2 finish, then stop during Tier 2
    rec = Recorder()
    dedup.find_duplicates([root], tier2=True, progress=rec,
                          count_db=tmp / "c.json")
    upto_t1 = sum(1 for s in rec.seen if s["phase"] <= 2)

    stop = Recorder(stop_after=upto_t1 + 1)
    groups = dedup.find_duplicates([root], tier2=True, progress=stop,
                                   count_db=tmp / "c.json")
    check(len(groups) == 2, f"Tier 1 的兩組留著（{len(groups)} 組）")
    check(all(g["tier"] == 1 for g in groups), "留下的都是已經確定的 Tier 1")
    check(max(s["phase"] for s in stop.seen) < 4, "沒有繼續跑到最貴的階段")


def test_cancel_during_walk(tmp: Path):
    print("\n走訪階段就停止的話，沒有東西可以回報")
    root = make_tree(tmp)
    stop = Recorder(stop_after=1)
    groups = dedup.find_duplicates([root], tier2=False, progress=stop,
                                   count_db=tmp / "c.json")
    check(groups == [], "回空的，不是回一份看似完整卻只掃了一半的報告")


def test_job_cancel_flag(tmp: Path):
    print("\njobs：取消旗標要送到工作裡，狀態要說實話")
    started = []

    def task(progress):
        started.append(1)
        n = 0
        while not progress.cancelled and n < 500:
            progress({"n": n})
            n += 1
            time.sleep(0.005)
        return {"n": n}

    jid = jobs.start(task, kind="dedup", label="測試")
    for _ in range(200):                        # wait for it to be under way
        if started:
            break
        time.sleep(0.01)
    check(jobs.cancel(jid), "取消進行中的工作回報成功")
    for _ in range(200):
        if jobs.get(jid)["status"] != "running":
            break
        time.sleep(0.01)
    j = jobs.get(jid)
    check(j["status"] == "cancelled",
          f"狀態是 cancelled 不是 done，UI 才能說「已停止」：{j['status']}")
    check(j["result"] and j["result"]["n"] < 500, "工作真的提早結束了")
    check(not jobs.cancel(jid), "已結束的工作不能再取消")


def test_through_the_job_runner(tmp: Path):
    """The seam the unit tests miss: the *real* progress object, not a stub.

    `Reporter.cancelled` reads an attribute off whatever callback it was given,
    and `jobs` supplies a callable object rather than a function precisely so
    that attribute exists. If those two ever stop agreeing, every test above
    still passes and the stop button silently does nothing.
    """
    print("\n整條路徑：jobs 的進度物件真的能讓掃描停下來")
    root = make_tree(tmp)
    slow = []

    def slow_probe(p):
        slow.append(p)
        time.sleep(0.05)            # long enough to cancel mid-phase
        return (100.0, 1920, 1080)

    dedup.probe_video = slow_probe
    dedup.video_phash = lambda p: [0] * 5

    seen = []

    def task(progress):
        return dedup.find_duplicates([root], tier2=True, progress=progress,
                                     count_db=tmp / "c.json")

    jid = jobs.start(task, kind="dedup", label="測試")
    for _ in range(400):            # wait until it is into the slow phase
        p = jobs.get(jid)["progress"]
        if isinstance(p, dict):
            seen.append(p)
            if p.get("phase") == 3:
                break
        time.sleep(0.01)
    check(any(isinstance(s, dict) and s.get("phase") == 3 for s in seen),
          "進度是結構化的 dict，而且真的走到了階段 3")
    jobs.cancel(jid)
    for _ in range(400):
        if jobs.get(jid)["status"] != "running":
            break
        time.sleep(0.01)
    j = jobs.get(jid)
    check(j["status"] == "cancelled", f"工作狀態變成 cancelled：{j['status']}")
    check(j["result"] is not None, "而且保留了已經找到的結果")


def test_cancel_route_exists(tmp: Path):
    print("\n取消端點：只有掃描類的工作可以停")
    from app import main                      # noqa: E402
    paths = {getattr(r, "path", "") for r in main.app.routes}
    check("/api/jobs/{job_id}/cancel" in paths, "端點有註冊")
    check("dedup" in main._CANCELLABLE and "imgdedup" in main._CANCELLABLE,
          "兩種掃描都可以停")
    check("convert" not in main._CANCELLABLE,
          "轉檔不在名單內 —— 它不看旗標，接受了只會謊報「已停止」")


def test_over_http(tmp: Path):
    """The endpoints as the browser actually uses them.

    Also pins something that bit during development: `_save_report` used to
    take `path=_REPORT_PATH` as a *default argument*, which binds at import
    time — so redirecting the constant before a test run did nothing and the
    real saved report got overwritten. This test asserts the redirect works,
    which is the same thing as asserting nobody re-adds that default.
    """
    print("\nHTTP：掃描 → 進度 → 停止")
    from fastapi.testclient import TestClient      # noqa: E402
    from app import auth, main                     # noqa: E402

    lib = tmp / "lib"
    lib.mkdir()
    for i in range(60):
        (lib / f"v{i:03}.mp4").write_bytes(bytes([i % 251]) * (3000 + i))

    real_report, real_counts = main._REPORT_PATH, main._SCAN_COUNTS
    real_vhash = main.VIDEO_HASH_DB
    main._REPORT_PATH = tmp / "report.json"
    main._SCAN_COUNTS = tmp / "counts.json"
    # and the per-file hash cache, or the scan remembers these test files
    # in the user's real one
    main.VIDEO_HASH_DB = tmp / "video_hash.sqlite3"
    # the history folder too: every scan now also writes an entry there, and
    # left alone this test would add one to the user's real history
    real_hist, real_legacy = dict(main._HISTORY_DIR), dict(main._LEGACY_REPORT)
    main._HISTORY_DIR.update(video=tmp / "hist" / "video", image=tmp / "hist" / "image")
    main._LEGACY_REPORT.update(video=tmp / "report.json", image=tmp / "img.json")
    real_hist_ids = main.reporthistory.ids(real_hist["video"])
    real_allowed = main.is_allowed
    main.is_allowed = lambda p, u: True
    main.app.dependency_overrides[auth.current_user] = \
        lambda: {"id": 1, "role": "admin"}
    dedup.probe_video = lambda p: (time.sleep(0.03), (100.0, 1920, 1080))[1]
    dedup.video_phash = lambda p: (time.sleep(0.02), [0] * 5)[1]

    try:
        c = TestClient(main.app)
        r = c.post("/api/dedup/scan",
                   json={"roots": [str(lib)], "tier2": True, "threshold": 1})
        check(r.status_code == 200, f"掃描起得來：{r.status_code}")
        jid = r.json()["job_id"]

        shape = None
        for _ in range(300):
            time.sleep(0.05)
            j = c.get(f"/api/dedup/job/{jid}").json()
            if isinstance(j["progress"], dict) and j["progress"]["phase"] >= 3:
                shape = j["progress"]
                break
        check(shape is not None, "輪詢拿得到結構化進度")
        if shape:
            check(set(shape) >= {"phase", "phases", "label", "done", "total",
                                 "name", "eta"},
                  f"欄位齊全：{sorted(shape)}")

        check(c.post(f"/api/jobs/{jid}/cancel").json()["stopping"] is True,
              "停止端點回報已受理")
        for _ in range(300):
            time.sleep(0.05)
            j = c.get(f"/api/dedup/job/{jid}").json()
            if j["status"] != "running":
                break
        check(j["status"] == "cancelled", f"狀態變成 cancelled：{j['status']}")

        check((tmp / "report.json").is_file(),
              "報告寫到被指定的路徑 —— 預設參數會在載入時綁死，改不動")
        check(not real_report.exists() or
              real_report.stat().st_mtime < time.time() - 5,
              "真正的報告檔沒有被這個測試動到")
        check(len(main.reporthistory.ids(tmp / "hist" / "video")) == 1,
              "歷程寫進被指定的資料夾")
        check(main.reporthistory.ids(real_hist["video"]) == real_hist_ids,
              "真正的歷程沒有被這個測試動到")
        check(c.post(f"/api/jobs/{jid}/cancel").status_code == 200
              and c.post(f"/api/jobs/{jid}/cancel").json()["stopping"] is False,
              "已結束的工作再按停止不會出錯，只是沒有作用")
    finally:
        main._REPORT_PATH, main._SCAN_COUNTS = real_report, real_counts
        main.VIDEO_HASH_DB = real_vhash
        main._HISTORY_DIR.clear(); main._HISTORY_DIR.update(real_hist)
        main._LEGACY_REPORT.clear(); main._LEGACY_REPORT.update(real_legacy)
        main.is_allowed = real_allowed
        main.app.dependency_overrides.clear()


def test_plain_callback_still_works(tmp: Path):
    print("\n沒有 cancelled 屬性的舊式回呼不能爆掉")
    rp = Reporter(lambda s: None, phases=1)
    check(rp.cancelled is False, "一般函式當回呼時視為「沒有取消」")


# ── the phase-1 expectation ─────────────────────────────────────────────────

def test_scancount(tmp: Path):
    print("\n上次的檔案數：只在每個根目錄都有紀錄時才拿出來用")
    store = tmp / "counts.json"
    a, b = tmp / "A", tmp / "B"
    check(scancount.expected(store, "videos", [a]) is None, "還沒有紀錄時不給")
    scancount.save(store, "videos", {str(a): 100})
    check(scancount.expected(store, "videos", [a]) == 100, "有紀錄就給")
    check(scancount.expected(store, "videos", [a, b]) is None,
          "只認得其中一個根目錄時不給 —— 半個總和比沒有更誤導")
    scancount.save(store, "videos", {str(b): 50})
    check(scancount.expected(store, "videos", [a, b]) == 150, "兩個都有就相加")
    check(scancount.expected(store, "images", [a]) is None, "影片與圖片各自分開")
    store.write_text("{ not json", encoding="utf-8")
    check(scancount.expected(store, "videos", [a]) is None,
          "檔案壞掉只是失去提示，不會讓掃描失敗")


def test_counts_recorded(tmp: Path):
    print("\n掃完要把檔案數記下來給下一次用")
    root = make_tree(tmp)
    store = tmp / "counts.json"
    rec = Recorder()
    dedup.find_duplicates([root], tier2=False, progress=rec, count_db=store)
    check(scancount.expected(store, "videos", [root]) == 9,
          "記下了 9 個（含 .ts —— 走訪階段本來就會走到它）")

    rec2 = Recorder()
    dedup.find_duplicates([root], tier2=False, progress=rec2, count_db=store)
    first = rec2.phases()[1][0]
    check(first["total"] == 9 and "上次共" in first["hint"],
          f"第二次跑就有進度條了：{first['total']} / {first['hint']!r}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for fn in (test_totals_match_reality, test_last_update_is_not_swallowed,
               test_eta, test_cancel_keeps_partial, test_cancel_during_walk,
               test_job_cancel_flag, test_through_the_job_runner,
               test_cancel_route_exists, test_over_http,
               test_plain_callback_still_works,
               test_scancount, test_counts_recorded):
        d = Path(tempfile.mkdtemp())
        try:
            fn(d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
