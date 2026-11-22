"""The converter can be stopped, even on a file that never reports progress.

On 2026-11-21 a re-encode of AVOP-071.mp4 (VC-1 ffmpeg cannot decode) sat at
0% for minutes, writing nothing and reading the source off the NAS, and the
dialog had no way to end it.

  * `run_one(cancelled=…)` ends ffmpeg within a second although it prints
    nothing at all, and raises `Stopped`
  * a run that is not cancelled is untouched
  * over HTTP: 停止 ends the job, the file in hand keeps its source, its
    half-written output is deleted, the rest are not started, the result
    says so, and the job is not one a restart would bring back

A stand-in for ffmpeg (python sleeping) is used; only temporary files.

Run:  python tests/test_convertstop.py      (from backend/)
"""
import _tmpdata  # noqa: F401  (first: never the real data/ or cache/)
import shutil
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


def silent_cmd(dst):
    # writes the start of an output, then reads "for ever" with no progress
    return [sys.executable, "-c",
            f"open({str(dst)!r}, 'wb').write(b'half'); import time; time.sleep(60)"]


def test_run_one(tmp: Path):
    print("run_one ends a silent ffmpeg when asked")
    from app.core import convert
    real = convert._build_cmd
    convert._build_cmd = lambda src, dst, *a, **k: silent_cmd(dst)
    try:
        t0 = time.perf_counter()
        ask = lambda: time.perf_counter() - t0 > 0.3
        try:
            convert.run_one(tmp / "a.avi", tmp / "a.mp4", "reencode",
                            {"duration": 100}, cancelled=ask)
            got = "returned"
        except convert.Stopped:
            got = "stopped"
        took = time.perf_counter() - t0
        check(got == "stopped", f"raises Stopped ({got})")
        check(took < 3, f"within a second or so of the ask ({took:.1f} s)")

        print("\nnot cancelled: runs to the end as before")
        convert._build_cmd = lambda src, dst, *a, **k: [
            sys.executable, "-c", "print('out_time_ms=1000000')"]
        seen = []
        convert.run_one(tmp / "a.avi", tmp / "b.mp4", "reencode",
                        {"duration": 2}, progress=seen.append,
                        cancelled=lambda: False)
        check(seen == [0.5], f"progress still reported ({seen})")
    finally:
        convert._build_cmd = real


def test_over_http(tmp: Path):
    print("\nHTTP: 停止 ends the job")
    from fastapi.testclient import TestClient            # noqa: E402
    from app import auth, db, jobs, main                 # noqa: E402
    from app.core import convert                         # noqa: E402
    db.DB_PATH = tmp / "vam.db"
    db.init_db()
    saved_paths = (jobs.REGISTRY, jobs.NOTICES)
    jobs.REGISTRY = tmp / "jobs_running.json"           # never the real data/
    jobs.NOTICES = tmp / "jobs_interrupted.json"
    srcs = []
    for n in ("a", "b", "c"):
        p = tmp / f"{n}.avi"
        p.write_bytes(b"source " + n.encode())
        srcs.append(p)
    saved = (main.is_allowed, convert._build_cmd, convert.probe)
    main.is_allowed = lambda p, u: True
    convert._build_cmd = lambda src, dst, *a, **k: silent_cmd(dst)
    convert.probe = lambda p: {"duration": 100}
    main.app.dependency_overrides[auth.current_user] = lambda: {
        "id": 1, "role": "admin", "username": "a"}
    try:
        c = TestClient(main.app)
        r = c.post("/api/convert/run", json={"items": [
            {"path": str(p), "action": "reencode"} for p in srcs],
            "crf": 20, "gpu": False})
        check(r.status_code == 200, f"started: {r.status_code} {r.text[:120]}")
        jid = r.json().get("job_id")
        out = tmp / "a.mp4"
        for _ in range(100):
            if out.exists():
                break
            time.sleep(0.05)
        check(out.exists(), "the first file is being written")

        r = c.post(f"/api/jobs/{jid}/cancel")
        check(r.status_code == 200 and r.json().get("stopping"),
              f"cancel accepted: {r.status_code} {r.text[:120]}")
        t0 = time.perf_counter()
        j = None
        for _ in range(100):
            j = jobs.get(jid)
            if j and j["status"] != "running":
                break
            time.sleep(0.1)
        took = time.perf_counter() - t0
        check(j and j["status"] == "cancelled", f"job ends as cancelled ({j and j['status']})")
        check(took < 3, f"promptly ({took:.1f} s)")
        res = (j or {}).get("result") or {}
        check(res.get("stopped") is True and res.get("left") == 3,
              f"result says stopped with 3 not converted: {res}")
        check(not res.get("converted") and not res.get("failed"),
              "the cut-short file is neither done nor failed")
        check(all(p.exists() for p in srcs), "every source is still there")
        check(not out.exists(), "the half-written output is deleted")
        check(not (tmp / "b.mp4").exists(), "the next file was never started")
        check(not jobs._records.get(jid), "nothing left for a restart to resume")
    finally:
        main.is_allowed, convert._build_cmd, convert.probe = saved
        jobs.REGISTRY, jobs.NOTICES = saved_paths
        main.app.dependency_overrides.clear()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for fn in (test_run_one, test_over_http):
        d = Path(tempfile.mkdtemp())
        try:
            fn(d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
    print("\nFAIL (%d)" % len(FAIL) if FAIL else "\nPASS")
    sys.exit(1 if FAIL else 0)
