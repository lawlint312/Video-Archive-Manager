"""Convert on a local disk first, copy the result back once (core/localstage.py).

On the NAS each file of a 23-file batch took 16–23 minutes, ~10 of them in
the muxer's faststart pass rewriting 3 GB over SMB at 5.5 MB/s; the same
file on a local disk took 6 (2026-11-22).

  * pick: off when unset, off on the output's own volume, off without room
    (twice the source + RESERVE), otherwise a unique name in the folder
  * problem_with: network paths, relative paths, writable folders
  * bring_back: same bytes, local removed; stop → no partial copy left;
    a file already at the destination is never touched
  * over HTTP, a real re-encode of a generated clip in place: encoded in the
    staging folder, copied back, the original replaced, nothing left behind;
    with no room it is written beside the source as before
  * the setting refuses a network path

Only temporary files; every data path main.py would write is redirected.

Run:  python tests/test_localstage.py      (from backend/)
"""
import _tmpdata  # noqa: F401  (first: never the real data/ or cache/)
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


def unit(tmp: Path):
    from app.core import localstage as ls
    src = tmp / "lib" / "a.mp4"
    src.parent.mkdir()
    src.write_bytes(b"x" * 1000)
    stage = tmp / "stage"

    print("pick")
    check(ls.pick(src, src, "") is None, "no folder set → None")
    check(ls.pick(src, src, str(stage)) is None, "same volume as the output → None")
    real_vol, real_du = ls._volume, ls.shutil.disk_usage
    try:
        ls._volume = lambda p: "stage:" if "stage" in str(p) else "nas:"
        got = ls.pick(src, src.with_name("a.converting.mp4"), str(stage))
        check(got is not None and got.parent == stage and got.name.endswith("-a.converting.mp4"),
              f"another volume with room → a name in the folder ({got})")
        other = ls.pick(src, src.with_name("a.converting.mp4"), str(stage))
        check(other != got, "unique per call")

        class DU:
            free = 2 * 1000 + ls.RESERVE - 1
        ls.shutil.disk_usage = lambda p: DU
        check(ls.pick(src, src, str(stage)) is None, "one byte short of 2×source + reserve → None")
        DU.free += 1
        check(ls.pick(src, src, str(stage)) is not None, "exactly enough → staged")
    finally:
        ls._volume, ls.shutil.disk_usage = real_vol, real_du

    print("\nproblem_with")
    check(ls.problem_with("") is None, "empty is allowed (off)")
    check("網路" in (ls.problem_with(r"\\nas\share\tmp") or ""), "UNC refused")
    check("完整路徑" in (ls.problem_with("relative\\dir") or ""), "relative refused")
    check(ls.problem_with(str(tmp / "new" / "deep")) is None and (tmp / "new" / "deep").is_dir(),
          "a writable folder is fine (and created)")

    print("\nbring_back")
    local = stage / "1234-a.mp4"
    stage.mkdir(exist_ok=True)
    data = bytes(range(256)) * 300_000          # ~77 MB: several chunks
    local.write_bytes(data)
    dst = tmp / "lib" / "out.mp4"
    seen = []
    ls.bring_back(local, dst, progress=seen.append)
    check(dst.read_bytes() == data, "same bytes at the destination")
    check(not local.exists(), "local copy removed")
    check(seen and seen[-1] == 1.0 and len(seen) >= 4, f"progress reported ({len(seen)} steps)")

    local.write_bytes(data)
    dst2 = tmp / "lib" / "out2.mp4"
    n = [0]

    def stop_later():
        n[0] += 1
        return n[0] > 2
    try:
        ls.bring_back(local, dst2, cancelled=stop_later)
        got = "copied"
    except ls.Stopped:
        got = "stopped"
    check(got == "stopped" and not dst2.exists() and local.exists(),
          "stopped mid-copy → partial copy removed, local kept for the caller")

    keep = tmp / "lib" / "taken.mp4"
    keep.write_bytes(b"someone else's")
    try:
        ls.bring_back(local, keep)
        got = "copied"
    except FileExistsError:
        got = "refused"
    check(got == "refused" and keep.read_bytes() == b"someone else's",
          "a file already at the destination is left alone")


def over_http(tmp: Path):
    print("\nHTTP: a real in-place re-encode through the staging folder")
    from fastapi.testclient import TestClient
    from app import auth, db, jobs, main, settings as app_settings
    from app.core import localstage as ls

    db.DB_PATH = tmp / "vam.db"
    db.init_db()
    saved_paths = (jobs.REGISTRY, jobs.NOTICES)
    jobs.REGISTRY = tmp / "jobs_running.json"           # never the real data/
    jobs.NOTICES = tmp / "jobs_interrupted.json"
    redirect = {}
    for name in ("CACHE_DIR", "DECODE_CHECK_DB", "CONVERT_CHECK_DB", "FACE_DB",
                 "_CONVERT_HISTORY", "_SHRINK_RESULTS", "VIDEO_HASH_DB"):
        if hasattr(main, name):
            redirect[name] = getattr(main, name)
            setattr(main, name, tmp / "data" / name.lower())
    (tmp / "data").mkdir(exist_ok=True)
    (tmp / "data" / "cache_dir").mkdir(exist_ok=True)

    lib, stage = tmp / "nas", tmp / "stage"
    lib.mkdir()
    src = lib / "clip.mp4"
    r = subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y",
                        "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=3",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                        "-c:v", "mpeg4", "-c:a", "aac", "-shortest", str(src)],
                       capture_output=True)
    check(r.returncode == 0, "made a source clip")

    saved = (main.is_allowed, ls._volume)
    main.is_allowed = lambda p, u=None: True
    ls._volume = lambda p: "local:" if "stage" in str(p) else "nas:"
    main.app.dependency_overrides[auth.current_user] = lambda: {
        "id": 1, "role": "admin", "username": "a"}
    seen_out = []
    from app.core import convert
    real_run = convert._run_ffmpeg

    def spy(cmd, *a, **k):
        seen_out.append(cmd[-1])
        return real_run(cmd, *a, **k)
    convert._run_ffmpeg = spy
    try:
        c = TestClient(main.app)

        def run():
            r = c.post("/api/convert/run", json={"items": [
                {"path": str(src), "action": "reencode"}], "crf": 28, "gpu": False})
            jid = r.json().get("job_id")
            for _ in range(600):
                j = jobs.get(jid)
                if j and j["status"] != "running":
                    return j
                time.sleep(0.1)
            return jobs.get(jid)

        r = c.post("/api/settings", json={"convert_staging_dir": r"\\nas\share\tmp"})
        check(r.status_code == 400 and "網路" in r.text, f"setting refuses a network path ({r.status_code})")
        r = c.post("/api/settings", json={"convert_staging_dir": str(stage)})
        check(r.status_code == 200, f"setting accepts a local folder ({r.status_code})")

        before = src.stat().st_size
        j = run()
        res = (j or {}).get("result") or {}
        check(j and j["status"] == "done" and len(res.get("converted") or []) == 1,
              f"converted ({j and j['status']}, {res.get('failed')})")
        check(seen_out and "stage" in seen_out[0] and seen_out[0].endswith("clip.converting.mp4"),
              f"ffmpeg wrote into the staging folder ({seen_out[:1]})")
        check(src.exists() and src.stat().st_size != before, "the original was replaced")
        check(convert.probe(src).get("vcodec") == "h264", "with the H.264 result")
        check(not list(stage.iterdir()), f"nothing left in the staging folder ({list(stage.iterdir())})")
        check(not (lib / "clip.converting.mp4").exists(), "no staging file beside it either")

        print("\nno room → written beside the source, as before")
        seen_out.clear()
        real_du = ls.shutil.disk_usage

        class DU:
            free = 0
        ls.shutil.disk_usage = lambda p: DU
        try:
            j = run()
        finally:
            ls.shutil.disk_usage = real_du
        res = (j or {}).get("result") or {}
        check(j and j["status"] == "done" and len(res.get("converted") or []) == 1, "converted")
        check(seen_out and str(lib) in seen_out[0] and "stage" not in seen_out[0],
              f"ffmpeg wrote beside the source ({seen_out[:1]})")
    finally:
        convert._run_ffmpeg = real_run
        main.is_allowed, ls._volume = saved
        for k, v in redirect.items():
            setattr(main, k, v)
        jobs.REGISTRY, jobs.NOTICES = saved_paths
        main.app.dependency_overrides.clear()


def turns_and_registry(tmp: Path):
    import threading
    from app.core import localstage as ls
    from app import jobs

    print("\nNAS turns: one at a time, copies back first")
    t = ls._Turns()
    log, inside = [], [0]
    peak = [0]

    def worker(name, first, hold):
        with t.turn(first):
            inside[0] += 1
            peak[0] = max(peak[0], inside[0])
            log.append(name)
            time.sleep(hold)
            inside[0] -= 1

    a = threading.Thread(target=worker, args=("download-1", False, 0.4))
    a.start()
    time.sleep(0.1)                                   # download-1 holds the turn
    b = threading.Thread(target=worker, args=("download-2", False, 0.05))
    b.start()
    time.sleep(0.05)
    c = threading.Thread(target=worker, args=("copy-back", True, 0.05))
    c.start()
    for th in (a, b, c):
        th.join()
    check(peak[0] == 1, "never two transfers at once")
    check(log == ["download-1", "copy-back", "download-2"],
          f"the copy back waiting goes before the download waiting ({log})")
    check(ls.nas(tmp / "x").__class__.__name__ == "nullcontext", "local paths take no turn")

    print("\nitem_done(only_its_own) leaves the other files in flight listed")
    saved = (jobs.REGISTRY, jobs.NOTICES)
    jobs.REGISTRY, jobs.NOTICES = tmp / "reg.json", tmp / "notices.json"
    try:
        gate = threading.Event()
        seen = {}

        def task(progress):
            jobs.writing("A", "A.out")
            jobs.writing("B", "B.out")
            jobs.writing("B", "B.local")
            progress.item_done("A", only_its_own=True)
            seen["after"] = [w for r in jobs._records.values() for w in r["writing"]]
            gate.wait(5)
        jid = jobs.start(task, kind="t", resume={"endpoint": "x", "body": {}, "user": 1})
        for _ in range(50):
            if "after" in seen:
                break
            time.sleep(0.05)
        check(seen.get("after") == [["B", "B.out"], ["B", "B.local"]],
              f"A's file cleared, B's kept ({seen.get('after')})")
        gate.set()
        while (jobs.get(jid) or {}).get("status") == "running":
            time.sleep(0.05)
    finally:
        jobs.REGISTRY, jobs.NOTICES = saved


def relay(tmp: Path):
    """Three sources 'on the NAS' (is_remote patched to a folder name): each is
    downloaded, encoded in the staging folder and copied back in the
    background while the next one encodes; NAS transfers never overlap."""
    print("\nHTTP: the relay")
    import threading
    from fastapi.testclient import TestClient
    from app import auth, db, jobs, main
    from app.core import convert, localstage as ls

    db.DB_PATH = tmp / "vam.db"
    db.init_db()
    saved_paths = (jobs.REGISTRY, jobs.NOTICES)
    jobs.REGISTRY = tmp / "jobs_running.json"
    jobs.NOTICES = tmp / "jobs_interrupted.json"
    redirect = {}
    for name in ("CACHE_DIR", "DECODE_CHECK_DB", "CONVERT_CHECK_DB", "FACE_DB",
                 "_CONVERT_HISTORY", "_SHRINK_RESULTS", "VIDEO_HASH_DB"):
        if hasattr(main, name):
            redirect[name] = getattr(main, name)
            setattr(main, name, tmp / "data" / name.lower())
    (tmp / "data" / "cache_dir").mkdir(parents=True)

    lib, stage = tmp / "nas", tmp / "stage"
    lib.mkdir()

    def make(name, seconds=4):
        p = lib / name
        r = subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y",
                            "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=25:duration={seconds}",
                            "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                            "-c:v", "mpeg4", "-c:a", "aac", "-shortest", str(p)],
                           capture_output=True)
        assert r.returncode == 0, r.stderr
        return p

    events = []                    # (kind, name, start, end)
    elock = threading.Lock()

    def timed(kind, fn):
        def wrap(*a, **k):
            t0 = time.time()
            try:
                return fn(*a, **k)
            finally:
                if kind == "encode":
                    cmd = a[0]
                    what = (cmd[cmd.index("-i") + 1], cmd[-1])      # (input, output)
                else:
                    what = str(a[1])                                 # where it copies to
                with elock:
                    events.append((kind, what, t0, time.time()))
        return wrap

    real = {"is_remote": ls.is_remote, "_volume": ls._volume, "fetch": ls.fetch,
            "bring_back": ls.bring_back, "run": convert._run_ffmpeg,
            "allowed": main.is_allowed}
    hooks = {"after_fetch": None, "on_encode": None}

    def slow_back(local, dst, *a, **k):
        time.sleep(0.8)                         # a NAS that takes its time
        return real["bring_back"](local, dst, *a, **k)

    def fetch_hook(src, local, *a, **k):
        r = real["fetch"](src, local, *a, **k)
        if hooks["after_fetch"]:
            hooks["after_fetch"](src)
        return r

    def run_hook(cmd, *a, **k):
        if hooks["on_encode"]:
            hooks["on_encode"](cmd)
        return real["run"](cmd, *a, **k)

    ls.is_remote = lambda p: str(lib) in str(p)
    ls._volume = lambda p: "local:" if "stage" in str(p) else "nas:"
    ls.fetch = timed("fetch", fetch_hook)
    ls.bring_back = timed("back", slow_back)
    convert._run_ffmpeg = timed("encode", run_hook)
    main.is_allowed = lambda p, u=None: True
    main.app.dependency_overrides[auth.current_user] = lambda: {
        "id": 1, "role": "admin", "username": "a"}
    try:
        c = TestClient(main.app)
        r = c.post("/api/settings", json={"convert_staging_dir": str(stage)})
        check(r.status_code == 200, "staging folder set")

        def run(paths):
            r = c.post("/api/convert/run", json={"items": [
                {"path": str(p), "action": "reencode"} for p in paths], "crf": 30, "gpu": False})
            jid = r.json().get("job_id")
            seen_bg = []
            for _ in range(1200):
                j = jobs.get(jid)
                p = (j or {}).get("progress") or {}
                if isinstance(p, dict) and p.get("bg"):
                    seen_bg.extend(b.get("stage") for b in p["bg"])
                if j and j["status"] != "running":
                    return j, jid, seen_bg
                time.sleep(0.05)
            return jobs.get(jid), jid, seen_bg

        srcs = [make(f"r{i}.mp4") for i in range(3)]
        sizes = [p.stat().st_size for p in srcs]
        j, _jid, seen_bg = run(srcs)
        res = (j or {}).get("result") or {}
        check(j and j["status"] == "done" and len(res.get("converted") or []) == 3,
              f"all three converted ({j and j['status']}, failed={res.get('failed')})")
        check(all(convert.probe(p).get("vcodec") == "h264" for p in srcs)
              and [p.stat().st_size for p in srcs] != sizes, "each original replaced by its H.264")
        check(not list(stage.iterdir()), f"staging folder empty ({list(stage.iterdir())})")
        check(not list(lib.glob("*.converting.mp4")), "nothing half-written beside them")
        fetches = [e for e in events if e[0] == "fetch"]
        encodes = [e for e in events if e[0] == "encode"]
        backs = [e for e in events if e[0] == "back"]
        check(len(fetches) == 3 and all(Path(e[1]).parent == stage and "-src-" in e[1]
                                        for e in fetches),
              f"every source downloaded into the staging folder first ({[Path(e[1]).name for e in fetches]})")
        check(len(encodes) == 3 and all(Path(i).parent == stage and Path(o).parent == stage
                                        for i, o in (e[1] for e in encodes)),
              f"ffmpeg read and wrote only the staging folder ({[e[1] for e in encodes][:1]})")
        nas_io = sorted(fetches + backs, key=lambda e: e[2])
        overlap = any(a[3] > b[2] + 0.01 for a, b in zip(nas_io, nas_io[1:]))
        check(not overlap, "no two NAS transfers overlapped (download / copy back)")
        relayed = any(e[2] < bk[3] and bk[2] < e[3] for e in encodes for bk in backs)
        check(relayed, "an encode ran while the previous file was copied back")
        check("下載原檔" in seen_bg or "複製回原資料夾" in seen_bg,
              f"the page saw the background work ({sorted(set(seen_bg))})")

        print("\nstop while the first one is copied back: that one finishes, the rest are left")
        events.clear()
        srcs = [make(f"s{i}.mp4") for i in range(3)]
        sizes = [p.stat().st_size for p in srcs]
        holder = {}
        n = [0]

        def stop_on_second(cmd):
            n[0] += 1
            if n[0] == 2:
                jobs.cancel(holder["jid"])
        hooks["on_encode"] = stop_on_second
        real_post = c.post

        def post_and_remember(*a, **k):
            r = real_post(*a, **k)
            if "job_id" in (r.json() or {}):
                holder["jid"] = r.json()["job_id"]
            return r
        c.post = post_and_remember
        j, _jid, _bg = run(srcs)
        c.post = real_post
        hooks["on_encode"] = None
        res = (j or {}).get("result") or {}
        conv = [Path(x["src"]).name for x in res.get("converted") or []]
        check(res.get("stopped") and conv == ["s0.mp4"],
              f"stopped, the one already encoded was finished ({conv}, left={res.get('left')})")
        check(res.get("left") == 2, f"two left ({res.get('left')})")
        check([p.stat().st_size for p in srcs[1:]] == sizes[1:], "the others untouched")
        check(not list(stage.iterdir()), f"staging folder empty ({list(stage.iterdir())})")
        check(not list(lib.glob("*.converting.mp4")), "nothing half-written left")

        print("\nan original changed after it was downloaded is not replaced")
        events.clear()
        src = make("m0.mp4")

        def touch(s):
            if s.name == "m0.mp4":
                with open(s, "ab") as f:
                    f.write(b"\0" * 10)
        hooks["after_fetch"] = touch
        changed_size = src.stat().st_size + 10
        j, _jid, _bg = run([src])
        hooks["after_fetch"] = None
        res = (j or {}).get("result") or {}
        check(not res.get("converted") and res.get("failed")
              and "修改" in res["failed"][0]["error"],
              f"refused ({res.get('failed')})")
        check(src.stat().st_size == changed_size, "the changed original is left as it is")
        check(not list(stage.iterdir()) and not list(lib.glob("*.converting.mp4")),
              "nothing left over")
    finally:
        ls.is_remote, ls._volume = real["is_remote"], real["_volume"]
        ls.fetch, ls.bring_back = real["fetch"], real["bring_back"]
        convert._run_ffmpeg = real["run"]
        main.is_allowed = real["allowed"]
        for k, v in redirect.items():
            setattr(main, k, v)
        jobs.REGISTRY, jobs.NOTICES = saved_paths
        main.app.dependency_overrides.clear()


def main_():
    tmp = Path(tempfile.mkdtemp())
    for sub, fn in (("u", unit), ("h", over_http), ("t", turns_and_registry), ("r", relay)):
        (tmp / sub).mkdir()
        fn(tmp / sub)
    print("\nFAIL (%d)" % len(FAIL) if FAIL else "\nPASS")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main_())
