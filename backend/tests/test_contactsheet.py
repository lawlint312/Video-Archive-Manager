"""Contact sheets — the cached file must never be visible half-written.

`render()` opens with `if dst.exists(): return dst`, and the prefetch worker
writes sheets continuously in the background. So if the sheet is saved
straight to its final name, paging to the one currently being built hands back
an incomplete file and the browser shows a broken image.

That window is not theoretical or narrow: PIL creates the file, buffers, and
flushes at the end, so it sits at **zero bytes** for essentially the whole
write. Measured on a 1.5 MB sheet — 959 of 961 samples saw a 0-byte file
across the 64 ms save.

Run:  python tests/test_contactsheet.py        (from backend/)
"""
import os
import random
import shutil
import sys
import subprocess
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import contactsheet as cs               # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def frames(tmp: Path, n=12, w=480):
    """`n` tiles on disk, the shape `_compose` expects."""
    from PIL import Image
    out = []
    for i in range(n):
        p = tmp / f"f{i:02d}.jpg"
        im = Image.new("RGB", (w, int(w * 9 / 16)))
        px = im.load()
        random.seed(i)
        for x in range(0, im.width, 3):         # noise, so JPEG cannot cheat
            for y in range(0, im.height, 3):
                px[x, y] = (random.randint(0, 255),) * 3
        im.save(p, "JPEG", quality=90)
        out.append((p, i * 60))
    return out


def test_never_partial(tmp: Path):
    """Watch the destination while it is written; it must appear complete."""
    print("\n寫入期間不能看到殘缺的檔案")
    src = tmp / "src"
    src.mkdir()
    fr = frames(src, w=800)                     # big enough to take some ms
    dst = tmp / "out" / "sheet.jpg"

    sizes = []
    polls = [0]
    stop = threading.Event()

    def watch():
        while not stop.is_set():
            polls[0] += 1
            try:
                sizes.append(os.path.getsize(dst))
            except OSError:
                pass                # not there yet — which is the whole point

    w = threading.Thread(target=watch)
    w.start()
    ok = cs._compose(fr, dst)
    stop.set()
    w.join()

    check(ok and dst.exists(), "縮圖表產生成功")
    final = dst.stat().st_size
    bad = [s for s in sizes if s != final]
    # The poll count proves the watcher really was spinning during the save;
    # `sizes` being nearly empty is then the *result* — the name simply does
    # not exist until the rename. Under the old direct-to-dst save this ran
    # the other way round: hundreds of sightings, almost all of them 0 bytes.
    check(polls[0] > 200, f"監看執行緒在寫入期間輪詢了 {polls[0]} 次")
    check(not bad,
          f"每一次看到檔案時它都已經是完整大小"
          f"（{len(sizes)} 次看到，{len(bad)} 次不完整"
          f"{'，例如 ' + str(sorted(set(bad))[:4]) if bad else ''}）")

    from PIL import Image
    im = Image.open(dst)
    im.load()
    check(im.size[0] > 0 and im.size[1] > 0, f"成品可以解碼：{im.size}")

    leftovers = [p.name for p in dst.parent.iterdir() if ".part" in p.name]
    check(not leftovers, f"沒有殘留的暫存檔（{leftovers}）")


def test_failed_save_leaves_nothing(tmp: Path):
    """A save that blows up must not leave the name claimed."""
    print("\n產生失敗時不能留下半成品")
    src = tmp / "src2"
    src.mkdir()
    fr = frames(src, n=4, w=160)
    dst = tmp / "out2" / "sheet.jpg"
    dst.parent.mkdir(parents=True)

    from PIL import Image
    real = Image.Image.save

    def boom(self, fp, *a, **k):
        real(self, fp, *a, **k)                 # write the .part file...
        raise OSError("disk full")              # ...then fail before renaming

    Image.Image.save = boom
    cs.log.disabled = True          # the failure is expected; don't print it
    try:
        ok = cs._compose(fr, dst)
    finally:
        Image.Image.save = real
        cs.log.disabled = False

    check(not ok, "回報失敗")
    check(not dst.exists(), "目標檔名沒有被佔用（否則下次會回傳這個壞檔）")
    check(not list(dst.parent.glob("*.part")), "暫存檔已清掉")


def test_gpu_worker_cap():
    print("\nGPU 模式的平行度要另外設上限")
    # This exists because the machine went down. A batched version of the
    # prefetch opened twelve decoders per process across four processes —
    # 48 at once. Measured on this card a 4K NVDEC context costs ~400 MiB, so
    # that asked for ~19 GB of an 8 GB card; video memory ran out and the
    # driver took the whole machine with it. The CPU has no equivalent cliff.
    check(cs._worker_count(12, gpu=False) > cs.GPU_MAX_WORKERS,
          "CPU 模式照舊，可以開到核心數")
    check(cs._worker_count(12, gpu=True) == cs.GPU_MAX_WORKERS,
          f"GPU 模式夾在 {cs.GPU_MAX_WORKERS}（約 {cs.GPU_MAX_WORKERS * 400} MiB 顯示記憶體）")
    check(cs._worker_count(2, gpu=True) == 2, "低於上限時不會被拉高")


def test_worker_count():
    """The clamp, which is the only thing standing between a typo and 500
    concurrent ffmpeg processes."""
    print("\n平行度的夾限")
    cpu = os.cpu_count() or 1
    check(cs._worker_count(None) == cs.DEFAULT_WORKERS, "沒給值就用預設")
    check(cs._worker_count(0) == cs.DEFAULT_WORKERS, "0 視為沒給")
    check(cs._worker_count(-5) == cs.DEFAULT_WORKERS, "負數視為沒給")
    check(cs._worker_count(1) == 1, "1 就是 1（等於關閉平行）")
    check(cs._worker_count(500) == cpu, f"超過核心數會夾到 {cpu}")


def test_parallel_matches_sequential(tmp: Path):
    """Parallel grabs must produce exactly the same sheet as sequential ones.

    The pool finishes in whatever order it likes, so the results have to be
    put back in time order before composing — otherwise the tiles come out
    shuffled and the timestamps no longer march left-to-right.
    """
    print("\n平行與循序的產出必須一致")
    src = tmp / "src"
    src.mkdir()
    fr = frames(src, n=12, w=240)
    out1, out6 = tmp / "seq.jpg", tmp / "par.jpg"
    check(cs._compose(fr, out1), "循序合成")
    # shuffle the input the way a thread pool would, then sort it back the way
    # `render()` does, and the bytes must be identical
    import random as _r
    shuffled = list(enumerate(fr))
    _r.seed(1)
    _r.shuffle(shuffled)
    restored = [f for _i, f in sorted(shuffled)]
    check(cs._compose(restored, out6), "打亂後排序回來再合成")
    check(out1.read_bytes() == out6.read_bytes(),
          "兩張位元組完全相同（順序有還原）")


def test_cache_path_shape(tmp: Path):
    print("\n快取路徑")
    v = Path(r"X:\some\video.mp4")
    p = cs.cache_path(v, 3, 60, tmp, 480)
    # the leading part is the file's size and mtime (`media.file_stamp`); this
    # path does not exist, so it is "0"
    check(p.name == "0_60_3_4x3_480.jpg",
          f"檔名帶著檔案版本/間隔/張次/格數/寬度：{p.name}")
    check(cs.cache_path(v, 3, 60, tmp, 480, stamp="1a-2b").name == "1a-2b_60_3_4x3_480.jpg",
          "檔案版本不同就是另一個檔（外部覆蓋後不會拿到舊的）")
    check(cs.sheet_dir(v, tmp) in p.parents, "放在該影片自己的目錄下")
    other = cs.cache_path(v, 3, 60, tmp, 640)
    check(other != p, "換了每格寬度就是另一個檔，不會蓋掉舊尺寸")


def test_gpu_command_shape():
    print("\nGPU 指令的形狀")
    src, out = Path("in.mp4"), Path("out.jpg")
    cpu = cs._grab_cmd(src, 10, out, 480, gpu=False)
    gpu = cs._grab_cmd(src, 10, out, 480, gpu=True)
    check("-hwaccel" not in cpu, "CPU 版本沒有 hwaccel")
    j = " ".join(gpu)
    check("cuda" in j, "GPU 版本有 cuda")
    # the point of the GPU path: shrink in video memory, then bring back the
    # small frame. Downloading the 4K frame and scaling on the CPU costs
    # nearly twice the CPU for the same wall time (6.4 vs 3.4 CPU-seconds).
    check("hwaccel_output_format cuda" in j, "解碼後留在顯示記憶體")
    check("scale_cuda" in j and "hwdownload" in j, "在 GPU 上縮放後才搬回來")
    check(j.index("scale_cuda") < j.index("hwdownload"),
          "順序不能顛倒 —— 先縮小再搬回來才有意義")


def test_gpu_falls_back(tmp: Path):
    print("\nGPU 失敗時要退回 CPU，不能無聲什麼都不做")
    src = tmp / "clip.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=10:duration=6",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    str(src)], capture_output=True)
    out = tmp / "tile.jpg"
    # probe first: deciding GPU-or-not reads the codec, and that ffprobe would
    # otherwise go through the fake below and be counted as a "cpu" grab
    cs.video_duration(src)

    calls = []
    real = cs.subprocess.run

    def fake_run(cmd, **kw):
        gpu = "-hwaccel" in cmd
        calls.append("gpu" if gpu else "cpu")
        if gpu:
            return real(["ffmpeg", "-hide_banner", "-v", "error", "-y",
                         "-f", "lavfi", "-i", "nullsrc", "-frames:v", "0",
                         "-f", "null", "-"], **kw)      # writes no file
        return real(cmd, **kw)

    cs.subprocess.run = fake_run
    try:
        ok = cs._grab(src, 2, out, 480, gpu=True)
    finally:
        cs.subprocess.run = real
    check(calls[:2] == ["gpu", "cpu"], f"先試 GPU，失敗後退回 CPU：{calls}")
    check(ok and out.exists() and out.stat().st_size > 0,
          "而且真的產出了圖 —— NVDEC 不支援的編碼不能讓縮圖表整個消失")


def _record_grabs(fail_gpu: bool):
    """Patch subprocess.run to log 'gpu'/'cpu' per ffmpeg call; returns
    (calls, restore). With `fail_gpu` the GPU attempt writes no file."""
    calls = []
    real = cs.subprocess.run

    def fake_run(cmd, **kw):
        gpu = "-hwaccel" in cmd
        calls.append("gpu" if gpu else "cpu")
        if gpu and fail_gpu:
            return real(["ffmpeg", "-hide_banner", "-v", "error", "-y",
                         "-f", "lavfi", "-i", "nullsrc", "-frames:v", "0",
                         "-f", "null", "-"], **kw)
        return real(cmd, **kw)

    cs.subprocess.run = fake_run

    def restore():
        cs.subprocess.run = real
    return calls, restore


def test_av1_never_tries_gpu(tmp: Path):
    """RTX 2070 has no AV1 decoder, and a refused GPU grab does not fail fast:
    it times out at 60 s. `林小雨 10.ts` (AV1) took 182 s per sheet that way."""
    print("\nAV1 直接用 CPU，不去試 GPU")
    src = tmp / "av1.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=10:duration=4",
                    "-c:v", "libaom-av1", "-cpu-used", "8", "-crf", "50",
                    "-pix_fmt", "yuv420p", str(src)], capture_output=True)
    check(cs.video_codec(src) == "av1", f"探測到的編碼是 {cs.video_codec(src)!r}")
    out = tmp / "tile.jpg"
    calls, restore = _record_grabs(fail_gpu=True)
    try:
        ok = cs._grab(src, 1, out, 480, gpu=True)
    finally:
        restore()
    check(calls == ["cpu"], f"只跑了一次 CPU，沒有試 GPU：{calls}")
    check(ok and out.stat().st_size > 0, "而且產得出圖")


def test_gpu_refusal_remembered(tmp: Path):
    """Codecs nobody listed: after the first refusal, the rest of the video
    goes straight to the CPU instead of waiting out the timeout per cell."""
    print("\nGPU 拒絕過一次，同一支影片之後都直接用 CPU")
    src = _clip(tmp / "refused.mp4", 6)
    cs.video_duration(src)
    calls, restore = _record_grabs(fail_gpu=True)
    try:
        cs._grab(src, 1, tmp / "a.jpg", 480, gpu=True)
        first = list(calls)
        calls.clear()
        ok = cs._grab(src, 3, tmp / "b.jpg", 480, gpu=True)
    finally:
        restore()
    check(first[:2] == ["gpu", "cpu"], f"第一格先試 GPU 再退回 CPU：{first}")
    check(calls == ["cpu"], f"第二格不再試 GPU：{calls}")
    check(ok, "第二格照樣產出")


def test_stop_abandons_sheet(tmp: Path):
    """Closing the viewer, or opening another video, must stop the sheet in
    flight — and must not leave a partial sheet behind as if it were whole."""
    print("\n中途喊停：不再抓新的格子，也不存下不完整的縮圖表")
    src = _clip(tmp / "stop.mp4", 30)
    cache = tmp / "c"
    for workers in (1, 4):
        grabs = [0]
        real = cs._grab

        def counting(*a, **kw):
            grabs[0] += 1
            return real(*a, **kw)

        cs._grab = counting
        try:
            out = cs.render(src, 0, 1, cache, workers=workers,
                            should_stop=lambda: grabs[0] >= 2)
        finally:
            cs._grab = real
        dst = cs.cache_path(src, 0, 1, cache)
        check(out is None, f"{workers} 條：喊停後回傳 None")
        check(not dst.exists(), f"{workers} 條：沒有存下縮圖表")
        check(grabs[0] <= 2 + workers,
              f"{workers} 條：12 格只抓了 {grabs[0]} 格（最多是喊停時已在跑的）")
    whole = cs.render(src, 0, 1, cache, workers=4)
    check(whole is not None and cs._usable(whole),
          "之後正常要求，整張照樣做得出來")


def test_cancel_stops_requested_sheet(tmp: Path):
    """Closing the viewer must stop the sheet being *waited on*, not only the
    prefetch — otherwise a slow video holds the renderer and the next video
    the user opens queues behind it (25 s for an MP4 that takes 1.6)."""
    print("\n關掉檢視器：連正在等的那一張也停，下一次照常產生")
    src = _clip(tmp / "asked.mp4", 30)
    other = tmp / "other.mp4"
    cache = tmp / "c"
    real = cs._grab
    grabs = [0]

    def slow(*a, **kw):
        grabs[0] += 1
        if grabs[0] == 1:
            cs.cancel_prefetch(other)       # someone else's video: no effect
            cs.cancel_prefetch(src)         # the viewer is closed mid-sheet
        return real(*a, **kw)

    cs._grab = slow
    try:
        t = cs.ticket(src)
        out = cs.render(src, 0, 1, cache, workers=1,
                        should_stop=lambda: cs.cancelled_since(src, t))
    finally:
        cs._grab = real
    check(out is None, "取消後回傳 None")
    check(not cs.cache_path(src, 0, 1, cache).exists(), "沒有存下半張")
    check(grabs[0] == 1, f"取消後沒有再抓新的格子（抓了 {grabs[0]} 格）")
    check(cs.cancelled_since(src, t), "取消前拿的號碼已經過期")
    t2 = cs.ticket(src)
    again = cs.render(src, 0, 1, cache, workers=4,
                      should_stop=lambda: cs.cancelled_since(src, t2))
    check(again is not None and cs._usable(again), "重新開啟時用新號碼，整張照常產生")


def test_yield_to_other_video():
    """Opening another video sends no cancel for the previous one; the request
    itself must make the old prefetch give way, or it queues behind it."""
    print("\n要求另一支影片的縮圖表時，舊的預跑要讓路")
    with cs._wlock:
        saved = dict(cs._want)
        cs._want["video"] = str(Path("C:/x/old.ts"))
    try:
        cs.yield_to(Path("C:/x/old.ts"))
        check(cs._want["video"] == str(Path("C:/x/old.ts")),
              "同一支影片：預跑照舊")
        cs.yield_to(Path("C:/x/new.mp4"))
        check(cs._want["video"] is None, "另一支影片：舊的預跑被放棄")
    finally:
        with cs._wlock:
            cs._want.update(saved)


def _clip(dst: Path, seconds: int) -> Path:
    subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y",
                    "-f", "lavfi",
                    "-i", f"testsrc2=size=320x180:rate=10:duration={seconds}",
                    "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", str(dst)], capture_output=True)
    return dst


def test_duration_memoised(tmp: Path):
    """The status endpoint is polled while sheets build; it must not re-probe.

    ffprobe costs 107 ms median / 216 ms worst locally. At roughly one poll a
    second that is a fifth of a core spent answering "how far along are you",
    competing with the four ffmpeg grabs doing the real work.
    """
    print("\n時長要記住，不能每次都重新探測")
    src = _clip(tmp / "memo.mp4", 6)
    cs._dur_cache.clear()

    calls = [0]
    real = cs.probe_video_codec

    def counting(p):
        calls[0] += 1
        return real(p)

    cs.probe_video_codec = counting
    try:
        first = cs.video_duration(src)
        for _ in range(20):
            cs.video_duration(src)
        check(first > 0, f"讀到時長 {first:.1f} 秒")
        check(calls[0] == 1, f"21 次呼叫只探測了 {calls[0]} 次")

        # a replaced file must not keep serving the old length
        _clip(src, 12)
        again = cs.video_duration(src)
        check(calls[0] == 2, "檔案換掉後會重新探測")
        check(again > first + 3, f"讀到新的時長 {again:.1f} 秒（原本 {first:.1f}）")
    finally:
        cs.probe_video_codec = real


def test_status_counts_cached_sheets(tmp: Path):
    print("\nstatus 要回報總張數與已完成張數")
    # 30 s, not 24: at 24 s the old count reached 3 sheets only through a
    # third sheet holding one cell at t=24 — past the last frame, the exact
    # bug END_GUARD fixes. 30 s is 30 real cells, 3 real sheets.
    src = _clip(tmp / "count.mp4", 30)
    cache = tmp / "cache"
    cs._dur_cache.clear()
    interval = 1
    total = cs.sheet_count(cs.video_duration(src), interval)
    check(total >= 3, f"這支 30 秒的片在 1 秒間隔下需要 {total} 張")

    s = cs.status(src, interval, cache)
    check(s["total"] == total and s["ready"] == 0, "還沒開始時 ready 是 0")
    check(s["building"] is None, "沒有正在產生的張次")

    # fabricate two finished sheets
    for i in (0, 1):
        p = cs.cache_path(src, i, interval, cache)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * 100)
    s = cs.status(src, interval, cache)
    check(s["ready"] == 2, f"看得到兩張已完成（ready={s['ready']}）")

    # an empty file is not a finished sheet — that was the broken-image bug
    p = cs.cache_path(src, 2, interval, cache)
    p.write_bytes(b"")
    check(cs.status(src, interval, cache)["ready"] == 2,
          "0 位元組的檔案不算完成")

    # eviction: the bar must survive ready going DOWN
    cs.cache_path(src, 0, interval, cache).unlink()
    check(cs.status(src, interval, cache)["ready"] == 1,
          "快取被清掉後 ready 會變少（進度條不能假設只增不減）")


def test_progress_published_during_render(tmp: Path):
    """The `progress` callback existed from the start and nothing used it."""
    print("\n產生過程中要能查到進度")
    src = _clip(tmp / "prog.mp4", 24)
    cache = tmp / "cache2"
    cs._dur_cache.clear()
    interval = 1

    seen = []
    stop = threading.Event()

    def watch():
        while not stop.is_set():
            b = cs.status(src, interval, cache)["building"]
            if b:
                seen.append((b["index"], b["done"], b["of"]))

    w = threading.Thread(target=watch)
    w.start()
    try:
        # one worker, so the twelve grabs are serial and the watcher has time
        out = cs.render(src, 0, interval, cache, cs.TILE_W, workers=1)
    finally:
        stop.set()
        w.join()

    check(out is not None and out.exists(), "縮圖表產生成功")
    check(bool(seen), f"產生期間查到了進度（{len(seen)} 次取樣）")
    if seen:
        check(all(i == 0 for i, _d, _o in seen), "回報的是第 0 張")
        check(all(o == cs.PER_SHEET for _i, _d, o in seen),
              f"每張 {cs.PER_SHEET} 格")
        check(max(d for _i, d, _o in seen) > min(d for _i, d, _o in seen),
              "格數有在往前走，不是卡住的固定值")

    after = cs.status(src, interval, cache)
    check(after["building"] is None, "產生結束後不再回報正在產生")
    check(after["ready"] == 1, f"完成的那張算進 ready（{after['ready']}）")

    # progress is per-video: another file must not inherit this one's state
    other = _clip(tmp / "other.mp4", 6)
    check(cs.status(other, interval, cache)["building"] is None,
          "別的影片查不到這部的進度")


def test_status_contract(tmp: Path):
    """The shape the progress bar reads, and the route it reads it from.

    Written because of an earlier bug in this project where the server read a
    field the client never sent: a mismatch between the two sides is silent at
    every layer, so it gets an explicit test rather than a hope.
    """
    print("\n進度端點的欄位契約")
    src = _clip(tmp / "shape.mp4", 6)
    cs._dur_cache.clear()
    s = cs.status(src, 1, tmp / "c3")
    check(set(s) == {"total", "ready", "building", "prefetching"},
          f"回傳欄位就是這四個：{sorted(s)}")

    # force a building state to inspect its shape without racing a render
    cs._note_progress(src, 2, 5, 12)
    try:
        b = cs.status(src, 1, tmp / "c3")["building"]
        check(set(b) == {"index", "done", "of"}, f"building 的欄位：{sorted(b)}")
        check((b["index"], b["done"], b["of"]) == (2, 5, 12), "數值原樣傳出")
    finally:
        cs._drop_progress(src)

    from app.main import app
    routes = {getattr(r, "path", "") for r in app.routes}
    check("/api/sheet/status" in routes, "端點有註冊在 app 上")

    # every field sheetprogress.js reads must be one the server actually sends
    js = (Path(__file__).resolve().parent.parent.parent
          / "frontend" / "src" / "sheetprogress.js").read_text(encoding="utf-8")
    for field in ("total", "ready", "building"):
        check(f".{field}" in js or f"s.{field}" in js,
              f"前端有讀 {field}")
    for used in ("b.done", "b.of", "b.index"):
        check(used in js, f"前端有讀 building 的 {used.split('.')[1]}")


def test_status_survives_unreadable_file(tmp: Path):
    print("\n讀不到的檔案不能讓 status 爆掉")
    s = cs.status(tmp / "does-not-exist.mp4", 30, tmp / "c")
    check(s["total"] == 0 and s["ready"] == 0, "回報 0 張而不是丟例外")
    check(s["building"] is None and s["prefetching"] is False, "其餘欄位也乾淨")


def test_frame_count():
    print("\n格數：最後一格要落在真的有畫面的地方")
    # the real file: container 2160.004 s, last video frame 2159.971
    check(cs.frame_count(2160.004, 30) == 72,
          "36:00 整的錄影 → 72 格（不能排一格在 2160 秒）")
    check(cs.sheet_count(2160.004, 30) == 6, "→ 6 張，不是只剩一格的第 7 張")
    check(cs.frame_count(3700, 30) == 124, "一般長度不受影響（124 格）")
    check(cs.frame_count(0.4, 30) == 1, "比 END_GUARD 還短的片仍有第 0 秒那一格")
    check(cs.frame_count(0, 30) == 0 and cs.sheet_count(0, 30) == 0, "讀不到 → 0")


def test_exact_multiple_renders_every_sheet(tmp: Path):
    """The bug as it happened: length a whole multiple of the interval.

    12 s at a 1 s interval is the 2160 s / 30 s case scaled down. The old count
    gave 13 cells, so sheet 1 held one cell at t=12 — past the last frame — and
    render() returned None for the whole sheet.
    """
    print("\n長度剛好是間隔整數倍：每一張都要產得出來")
    v = tmp / "exact.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc=size=320x180:rate=30:duration=12",
                    "-f", "lavfi", "-i", "sine=duration=12",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(v)], check=True)
    dur = cs.video_duration(v)
    total = cs.sheet_count(dur, 1)
    check(total == 1, f"長度 {dur:.3f} 秒、每 1 秒一格 → 1 張（得到 {total}）")
    cache = tmp / "c"
    ok = [cs.render(v, i, 1, cache, workers=1) is not None for i in range(total)]
    check(all(ok), f"全部 {total} 張都產得出來（{ok}）")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    test_worker_count()
    test_gpu_worker_cap()
    test_gpu_command_shape()
    test_frame_count()
    test_yield_to_other_video()
    for fn in (test_av1_never_tries_gpu, test_gpu_refusal_remembered,
               test_stop_abandons_sheet, test_cancel_stops_requested_sheet,
               test_never_partial, test_failed_save_leaves_nothing,
               test_parallel_matches_sequential, test_cache_path_shape,
               test_gpu_falls_back, test_duration_memoised,
               test_status_counts_cached_sheets,
               test_progress_published_during_render,
               test_status_contract,
               test_status_survives_unreadable_file,
               test_exact_multiple_renders_every_sheet):
        d = Path(tempfile.mkdtemp())
        try:
            fn(d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
