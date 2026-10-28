"""Background jobs survive a restart of the service.

They used to live only in memory: an update, a crash or a reboot ended every
scan and every conversion without a trace. Now the resumable ones are written
down while they run, and the next start

  * reruns the scans by itself, under the same job id (a page still polling
    that id simply carries on) — the per-file caches make the part already
    done nearly free
  * keeps the jobs that write files as a notice for their owner — never
    continued by itself — and continues only what is left when asked
  * removes the file a killed job was halfway through writing, but only
    while its source is still there; never a file the job did not create
  * stops bringing back a scan that keeps getting interrupted
  * drops jobs that were being stopped, and jobs whose account is gone

The video duplicate scan now remembers its hashes too, or a rerun would start
from nothing.

Run:  python tests/test_jobresume.py      (from backend/)
"""
import json
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import jobs                           # noqa: E402
from app.core import dedup, media              # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def wait(jid, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = jobs.get(jid)
        if j and j["status"] != "running":
            return j
        time.sleep(0.05)
    return jobs.get(jid)


def registry():
    return json.loads(jobs.REGISTRY.read_text(encoding="utf-8"))


def restart():
    """What a new process sees: nothing in memory, the files on disk."""
    with jobs._lock:
        jobs._jobs.clear()
        jobs._records.clear()


tmp = Path(tempfile.mkdtemp(prefix="vam-resume-"))
saved_paths = (jobs.REGISTRY, jobs.NOTICES)
try:
    jobs.REGISTRY = tmp / "jobs_running.json"
    jobs.NOTICES = tmp / "jobs_interrupted.json"

    print("a resumable job is written down while it runs, and only then")
    gate = threading.Event()
    jid = jobs.start(lambda p: gate.wait(10), kind="dedup", label="重複掃描",
                     resume={"endpoint": "dedup_scan", "body": {"a": 1}, "user": 7})
    time.sleep(0.1)
    rec = registry()
    check(len(rec) == 1 and rec[0]["id"] == jid and rec[0]["body"] == {"a": 1}
          and rec[0]["user"] == 7, "listed with its request and owner")
    plain = jobs.start(lambda p: gate.wait(10), kind="job")
    time.sleep(0.1)
    check(len(registry()) == 1, "a job without resume= is not listed")
    gate.set()
    wait(jid); wait(plain)
    time.sleep(0.1)
    check(registry() == [], "finished jobs leave the list")

    print("\nmany-file jobs record each finished item")
    gate = threading.Event()
    reached = threading.Event()

    def task(progress):
        for x in jobs.each(progress, ["a", "b", "c"], key=str):
            if x == "c":
                reached.set()
                gate.wait(10)      # killed here, in the middle of "c"
    jid = jobs.start(task, kind="convert_run", label="轉檔",
                     resume={"endpoint": "convert_run", "body": {}, "user": 7})
    reached.wait(5)
    time.sleep(0.1)
    check(registry()[0]["done"] == {"a": "", "b": ""},
          "a and b are done, c (in progress) is not")
    gate.set(); wait(jid)

    print("\na killed job's half-written file is removed — only that")
    src = tmp / "src.ts"; src.write_bytes(b"x" * 100)
    half = tmp / "src.mp4"; half.write_bytes(b"x" * 10)
    gone_src = tmp / "moved.ts"                   # the source already removed
    only_copy = tmp / "moved.mp4"; only_copy.write_bytes(b"x" * 100)
    before = tmp / "before.mp4"; before.write_bytes(b"keep")
    scratch = tmp / ".vamclip-abc"; scratch.mkdir(); (scratch / "000.ts").write_bytes(b"x")
    other_dir = tmp / "Some folder"; other_dir.mkdir()
    gate = threading.Event()

    def writer(progress):
        jobs.writing(src, half)
        jobs.writing(gone_src, only_copy)
        jobs.writing(src, scratch)
        jobs.writing(src, other_dir)
        gate.wait(10)
    jid = jobs.start(writer, kind="clip", label="剪輯",
                     resume={"endpoint": "clip_run", "body": {}, "user": 7})
    time.sleep(0.2)
    snapshot = jobs.REGISTRY.read_text(encoding="utf-8")
    gate.set(); wait(jid)
    jobs.REGISTRY.write_text(snapshot, encoding="utf-8")   # as if killed
    restart()
    back = jobs.recover()
    check([e["id"] for e in back] == [jid], "the interrupted job comes back")
    check(not half.exists(), "half-written output removed (source still there)")
    check(only_copy.exists(), "kept when its source is gone — the only copy")
    check(before.exists(), "a file the job never claimed is untouched")
    check(not scratch.exists(), "VAM's scratch folder removed")
    check(other_dir.exists(), "any other folder is untouched")
    check(registry() == [], "the registry is cleared for the new process")

    print("\na job being stopped does not come back")
    jobs.REGISTRY.write_text(json.dumps([
        {"id": "x1", "endpoint": "dedup_scan", "cancel": True, "writing": []},
        {"id": "x2", "endpoint": "dedup_scan", "cancel": False, "writing": []},
    ]), encoding="utf-8")
    check([e["id"] for e in jobs.recover()] == ["x2"], "only the one not stopped")

    print("\na cross-volume move records the file it is copying")
    s2 = tmp / "a" / "big.mp4"; s2.parent.mkdir(); s2.write_bytes(b"y" * (9 << 20))
    d2 = tmp / "b" / "big.mp4"
    seen = []
    real_rename = Path.rename

    def no_rename(self, target):
        raise OSError("pretend another drive")

    def mover(progress):
        Path.rename = no_rename
        try:
            media.move_with_progress(
                s2, d2, lambda done, size, speed: seen.append(
                    [w[1] for r in registry() for w in r["writing"]]),
                chunk=4 << 20)
        finally:
            Path.rename = real_rename
    jid = jobs.start(mover, kind="archive", label="歸檔",
                     resume={"endpoint": "archive_video", "body": {}, "user": 7})
    wait(jid)
    check(seen and seen[0] == [str(d2)], "listed while the copy runs")
    check(d2.exists() and not s2.exists(), "the move itself still works")

    print("\nnotices belong to their owner and survive another restart")
    jobs.add_notice({"id": "n1", "user": 7, "label": "轉檔"})
    jobs.add_notice({"id": "n2", "user": 8, "label": "壓縮"})
    check([n["id"] for n in jobs.notices_for(7)] == ["n1"], "only your own")
    check(jobs.take_notice("n1", 8) is None, "someone else cannot take it")
    check(jobs.take_notice("n1", 7)["id"] == "n1", "the owner can")
    check(jobs.notices_for(7) == [], "and then it is gone")

    # ── main.py: what is rerun, what waits ──────────────────────────────────
    print("\nafter a restart: scans rerun, writes wait")
    from fastapi.testclient import TestClient      # noqa: E402
    from app import auth, db, main                 # noqa: E402

    saved_main = (db.DB_PATH, main._REPORT_PATH, main._SCAN_COUNTS,
                  main.VIDEO_HASH_DB, dict(main._HISTORY_DIR),
                  dict(main._LEGACY_REPORT), main.is_allowed,
                  auth.get_user_by_id)
    db.DB_PATH = tmp / "vam.db"
    db.init_db()
    main._REPORT_PATH = tmp / "report.json"
    main._SCAN_COUNTS = tmp / "counts.json"
    main.VIDEO_HASH_DB = tmp / "video_hash.sqlite3"
    main._HISTORY_DIR.update(video=tmp / "hist" / "v", image=tmp / "hist" / "i")
    main._LEGACY_REPORT.update(video=tmp / "r.json", image=tmp / "ri.json")
    main.is_allowed = lambda p, u=None: True
    admin = {"id": 1, "role": "admin", "disabled": 0}
    auth.get_user_by_id = lambda uid: dict(admin) if uid == 1 else None
    main.app.dependency_overrides[auth.current_user] = lambda: dict(admin)
    try:
        lib = tmp / "lib"; lib.mkdir()
        for i in range(4):
            (lib / f"v{i}.mp4").write_bytes(b"same" * 1000)   # one big group
        conv = [lib / "c1.ts", lib / "c2.ts", lib / "c3.ts"]
        for n, f in enumerate(conv):
            f.write_bytes(b"t" * (10 + n))       # unique sizes: not duplicates
        scan = {"id": "scan01", "kind": "dedup", "label": "重複掃描",
                "endpoint": "dedup_scan", "user": 1, "attempt": 0,
                "body": {"roots": [str(lib)], "tier2": False, "threshold": 1},
                "done": {}, "writing": []}
        run = {"id": "conv01", "kind": "convert_run", "label": "轉檔 3 個檔案",
               "endpoint": "convert_run", "user": 1, "attempt": 0,
               "body": {"items": [{"path": str(f), "action": "remux"} for f in conv],
                        "crf": 20, "gpu": False},
               "done": {str(conv[0]): ""}, "writing": []}
        tired = {**scan, "id": "scan02", "attempt": 2}
        orphan = {**scan, "id": "scan03", "user": 99}
        jobs.REGISTRY.write_text(json.dumps([scan, run, tired, orphan]),
                                 encoding="utf-8")
        restart()
        main._recover_jobs()
        j = wait("scan01")
        check(j and j["status"] == "done", "the scan ran again, under its old id")
        check(j and len(j["result"]) == 1, "and found the duplicates")
        check(jobs.get("conv01") is None, "the conversion did not start by itself")
        ids = [n["id"] for n in jobs.notices_for(1)]
        check(ids == ["conv01", "scan02"], "it waits as a notice, so does the "
                                            "scan interrupted too often")
        check(jobs.get("scan03") is None and "scan03" not in ids,
              "a job whose account is gone is dropped")

        c = TestClient(main.app)
        view = {n["id"]: n for n in c.get("/api/jobs/interrupted").json()["jobs"]}
        v = view["conv01"]
        check((v["total"], v["finished"], v["left"]) == (3, 1, 2),
              "the notice says 1 of 3 done, 2 left")
        check("body" not in v, "and does not hand the request (paths) back")
        check(view["scan02"]["left"] == -1 and "自動接續" in view["scan02"]["reason"],
              "the scan says why it waited")

        conv[1].unlink()                          # converted and gone meanwhile
        rest = main._rest_by_path("items")(run["body"], run["done"])
        check(rest and [it["path"] for it in rest[0]["items"]] == [str(conv[2])],
              "continuing does only what is neither done nor gone")

        r = c.post("/api/jobs/interrupted/scan02/resume").json()
        check(r.get("job_id") and r["kind"] == "dedup", "a waiting scan can be rerun")
        check(wait(r["job_id"])["status"] == "done", "and it finishes")
        check(c.get(f"/api/jobs/{r['job_id']}").status_code == 200,
              "its owner can follow it")
        check(c.post("/api/jobs/interrupted/conv01/dismiss").json()["ok"],
              "a notice can be dismissed")
        check(c.get("/api/jobs/interrupted").json()["jobs"] == [], "nothing left")

        print("\nthe video duplicate scan remembers its hashes")
        calls = []
        cache = tmp / "fresh_hash.sqlite3"     # the scans above filled the other
        real_hash = dedup.partial_hash
        dedup.partial_hash = lambda p: (calls.append(p), real_hash(p))[1]
        try:
            stats = {}
            dedup.find_duplicates([lib], cache_db=cache, stats=stats)
            first = len(calls)
            stats = {}
            g = dedup.find_duplicates([lib], cache_db=cache, stats=stats)
            check(first == 4 and len(calls) == 4, "second scan hashes nothing again")
            check(stats.get("head") == 4 and len(g) == 1, "same answer, from the cache")
            (lib / "v0.mp4").write_bytes(b"diff" * 1000)   # replaced, same size
            dedup.find_duplicates([lib], cache_db=cache)
            check(len(calls) == 5, "a replaced file is hashed again")
        finally:
            dedup.partial_hash = real_hash
    finally:
        main.app.dependency_overrides.clear()
        (db.DB_PATH, main._REPORT_PATH, main._SCAN_COUNTS, main.VIDEO_HASH_DB,
         hist, legacy, main.is_allowed, auth.get_user_by_id) = saved_main
        main._HISTORY_DIR.clear(); main._HISTORY_DIR.update(hist)
        main._LEGACY_REPORT.clear(); main._LEGACY_REPORT.update(legacy)
finally:
    jobs.REGISTRY, jobs.NOTICES = saved_paths
    shutil.rmtree(tmp, ignore_errors=True)

print("\nFAIL: %d" % len(FAIL) if FAIL else "\nPASS")
sys.exit(1 if FAIL else 0)
