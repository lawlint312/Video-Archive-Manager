"""The recording-dropout check over the NAS, and the scan's progress.

On 2026-11-16 a convert scan with 「檢查錄影中斷」 sat on 27 webcam recordings
(23.8 GB, on the NAS) for most of an hour: ffprobe reads every packet to list
their times, which means the whole file, and the status line kept naming the
previous check because a new check said nothing until its first file was
done. The stop button then waited minutes for the files in hand.

  * an MP4's own index (`mp4index`) screens files: no gap between video
    samples means no blank, without reading the file — and the screen is
    only trusted in that direction: a file with a gap, or one it cannot read
    (MPEG-TS named .mp4, fragmented, garbage), goes to ffprobe as before
  * `_packets` tells "could not read" (None) from "no packets" ([]): an
    audio pass cut short is not a file without sound
  * the stop button ends an ffprobe in hand
  * the progress is a phase per check with a size-weighted bar

Builds its own clips in a temporary folder.

Run:  python tests/test_mp4screen.py      (from backend/)
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import convert, mp4index                  # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def ff(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", *args],
                   check=True)


def segment(out: Path, start: float, audio=True, fmt="mpegts", extra=()):
    args = ["-f", "lavfi", "-i", "testsrc=size=320x180:rate=30:duration=6"]
    if audio:
        args += ["-f", "lavfi", "-i", "sine=frequency=440:duration=6"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "30"]
    if audio:
        args += ["-c:a", "aac", "-b:a", "64k"]
    args += [*extra, "-output_ts_offset", str(start), "-f", fmt, str(out)]
    ff(*args)


def blank_clip(tmp: Path, name: str, gap: float = 30, sound=False) -> Path:
    """Video 0-6 s, nothing until 6+gap, video again — as the recorder
    leaves it (the same construction as test_gapcut). `sound`: audio keeps
    going through the gap, a frozen camera rather than a dropped stream."""
    a, b = tmp / f"{name}_a.ts", tmp / f"{name}_b.ts"
    segment(a, 0, audio=not sound)
    segment(b, 6 + gap, audio=not sound)
    joined = tmp / f"{name}_j.ts"
    joined.write_bytes(a.read_bytes() + b.read_bytes())
    out = tmp / f"{name}.mp4"
    if sound:
        ff("-copyts", "-i", str(joined), "-f", "lavfi",
           "-i", f"sine=frequency=330:duration={12 + gap}",
           "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", str(out))
    else:
        ff("-copyts", "-i", str(joined), "-c", "copy", "-bsf:a", "aac_adtstoasc",
           str(out))
    return out


class Counting:
    """Counts the ffprobe packet runs, still running them."""

    def __init__(self):
        self.calls = []
        self.real = convert._packets

    def __enter__(self):
        convert._packets = lambda p, s, on_time=None: self.calls.append(s) or self.real(p, s)
        return self

    def __exit__(self, *a):
        convert._packets = self.real


def ffprobe_only(path):
    """`_blank_check` as it was: no screen."""
    real = mp4index.video_times
    mp4index.video_times = lambda p: None
    try:
        return convert._blank_check(path)
    finally:
        mp4index.video_times = real


def test_screen(tmp: Path):
    print("索引篩選：沒有空檔就不讀整個檔案，有空檔才交給 ffprobe")
    plain = tmp / "plain.mp4"
    segment(plain, 0, fmt="mp4")
    times = mp4index.video_times(plain)
    pk = sorted(convert._packets(plain, "v:0"))
    check(times is not None and len(times) == len(pk), "索引的畫面數與 ffprobe 相同")
    if times and pk:
        steps = lambda xs: [round(b - a, 4) for a, b in zip(xs, xs[1:])]
        check(steps(sorted(times)) == steps([t for t, _k in pk]), "每一格的間隔都相同")
    with Counting() as c:
        got = convert._blank_check(plain)
    check(got is None and c.calls == [], f"沒有空檔：直接判定、沒有呼叫 ffprobe（{c.calls}）")

    gapped = blank_clip(tmp, "gapped")
    # 30 s between the segments, less the one frame that is there
    check(mp4index.longest_gap(mp4index.video_times(gapped) or []) > 29,
          "有 30 秒空檔的錄影，索引看得到那段空檔")
    with Counting() as c:
        got = convert._blank_check(gapped)
    check(c.calls == ["v:0", "a:0"], f"有空檔：交給 ffprobe 完整檢查（{c.calls}）")
    want = ffprobe_only(gapped)
    check(got == want and isinstance(got, dict) and len(got["blanks"]) == 1,
          "結果與只用 ffprobe 完全相同，找到 1 段")

    frozen = blank_clip(tmp, "frozen", sound=True)
    got = convert._blank_check(frozen)
    check(got == ffprobe_only(frozen) and got and not got["blanks"] and got["skipped"] == 1,
          "畫面停住但聲音還在：一樣交給 ffprobe，不算空白")


def test_not_understood(tmp: Path):
    print("\n讀不懂的交給 ffprobe（篩選回 None）")
    ts_named_mp4 = tmp / "really_ts.mp4"
    segment(ts_named_mp4, 0)                  # MPEG-TS bytes, .mp4 name
    check(mp4index.video_times(ts_named_mp4) is None, "MPEG-TS 取名成 .mp4")
    frag = tmp / "frag.mp4"
    segment(frag, 0, fmt="mp4", extra=("-movflags", "frag_keyframe+empty_moov"))
    check(mp4index.video_times(frag) is None, "分段式 MP4（moof）")
    junk = tmp / "junk.mp4"
    junk.write_bytes(b"\x00\x00\x00\x08free" + b"not a box at all" * 100)
    check(mp4index.video_times(junk) is None, "不是合法的 box")
    audio_only = tmp / "audio.mp4"
    ff("-f", "lavfi", "-i", "sine=duration=3", "-c:a", "aac", str(audio_only))
    check(mp4index.video_times(audio_only) is None, "沒有畫面軌")
    check(mp4index.video_times(tmp / "missing.mp4") is None, "檔案不存在")
    # and the TS still gets its full check
    with Counting() as c:
        convert._blank_check(ts_named_mp4)
    check(c.calls == ["v:0", "a:0"], "MPEG-TS 照舊完整檢查")


def test_unread(tmp: Path):
    print("\n讀不完 ≠ 沒有封包")
    silent = tmp / "silent.mp4"
    segment(silent, 0, audio=False, fmt="mp4")
    check(convert._packets(silent, "a:0") == [], "沒有聲音軌：[]")
    check(convert._packets(tmp / "missing.mp4", "v:0") is None, "檔案不存在：None")
    convert._ctx.stop = lambda: True
    try:
        check(convert._packets(silent, "v:0") is None, "按了停止：None（不是讀到一半的清單）")
    finally:
        convert._ctx.stop = None

    gapped = tmp / "gapped.mp4"
    real = convert._packets
    convert._packets = lambda p, s, on_time=None: None if s == "a:0" else real(p, s)
    try:
        got = convert._blank_check(gapped)
    finally:
        convert._packets = real
    check(got is convert._UNREAD,
          "聲音那一次沒讀完：當作讀不到（不會把有聲音的段落當成空白去剪）")


def test_read_limit(tmp: Path):
    print("\n整檔讀取：不論哪顆磁碟，同時最多 FULL_READS 支")
    clip = tmp / "plain.mp4"
    import threading
    import time as _t
    live, peak, lock = [0], [0], threading.Lock()
    real_run = convert._packets_run

    def run(path, stream, on_time=None):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        try:
            _t.sleep(0.3)               # long enough for the others to pile up
            return real_run(path, stream)
        finally:
            with lock:
                live[0] -= 1

    convert._packets_run = run
    try:
        out = []
        ts = [threading.Thread(target=lambda: out.append(convert._packets(clip, "v:0")))
              for _ in range(6)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        check(peak[0] == convert.FULL_READS and len(out) == 6 and all(out),
              f"本機 6 支同時要讀：最多 {peak[0]} 支一起讀，全部讀完")
        # stop while waiting for a slot: no read at all
        held = [convert._full_reads.acquire() for _ in range(convert.FULL_READS)]
        convert._ctx.stop = lambda: True
        try:
            check(convert._packets(clip, "v:0") is None, "排隊時按停止：不讀、回 None")
        finally:
            convert._ctx.stop = None
            for _ in held:
                convert._full_reads.release()
    finally:
        convert._packets_run = real_run


def test_progress():
    print("\n進度：每項檢查一個階段，進度條依檔案大小")
    seen = []
    rp = convert.ScanProgress(seen.append, phases=2)
    rp.begin("檢查格式", [100, 300], reused=26)
    s = seen[-1]
    check((s["phase"], s["phases"], s["label"], s["done"], s["total"]) == (1, 2, "檢查格式", 0, 2),
          f"一開始就報：階段 1/2、0/2 支（{s}）")
    check(s["hint"] == "另 26 支沿用上次結果", "沿用的支數在 hint")
    rp.started("a.mp4")
    rp.file_done(300)
    rp.finish()
    s = seen[-1]
    check(s["done"] == 1 and abs(s["frac"] - 0.75) < 1e-9,
          f"做完 300 / 400 的那支：1 支、75%（{s['frac']}）")
    rp.begin("完整解碼檢查", [50])
    check(seen[-1]["phase"] == 2 and seen[-1]["hint"] == "", "下一項檢查是階段 2")
    rp.begin("檢查錄影斷訊", [10], sub=True)
    check(seen[-1]["phase"] == 2, "附屬的步驟不另算一個階段")
    rp.begin("沒有要查的", [])
    check(seen[-1]["frac"] is None, "沒有檔案：沒有比例（不是 0% 也不是除以零）")


def test_part_progress(tmp: Path):
    print("\n進度條在檔案讀到一半時也會動（不是整支做完才一次跳）")
    seen = []
    rp = convert.ScanProgress(seen.append, phases=1)
    rp.begin("完整解碼檢查", [100, 300])
    rp.file_part("b", 300, 0.5)
    rp.finish()
    check(abs(seen[-1]["frac"] - 150 / 400) < 1e-9 and seen[-1]["done"] == 0,
          f"大的那支讀到一半：比例 150/400，完成的支數還是 0（{seen[-1]}）")
    rp.file_part("b", 300, 0.2)
    rp.finish()
    check(abs(seen[-1]["frac"] - 150 / 400) < 1e-9,
          "GPU 不收、改用 CPU 從頭解：進度條不往回退")
    rp.file_part("a", 100, 5.0)
    rp.finish()
    check(abs(seen[-1]["frac"] - 250 / 400) < 1e-9, "超過片長的時間不算超過一支")
    rp.file_done(300, "b")
    rp.finish()
    check(abs(seen[-1]["frac"] - 400 / 400) < 1e-9 and seen[-1]["done"] == 1,
          f"做完那支：換成整支的大小，不重複計算（{seen[-1]['frac']}）")

    # the real readers: they report the time they are at, and the answer is
    # the same as without anyone listening
    from app.core import decodecheck
    clip = tmp / "plain.mp4"
    heard = []
    with_cb = convert._packets_run(clip, "v:0", heard.append)
    # the first packet's DTS sits a frame or two before 0 (B-frames);
    # file_part clamps it
    check(heard and all(-1 <= t <= 7 for t in heard),
          f"讀封包時回報讀到的時間（{heard[:3]}）")
    check(with_cb == convert._packets_run(clip, "v:0"),
          "有沒有人在聽，讀到的封包一樣")
    heard = []
    r = decodecheck._run(clip, False, None, heard.append)
    check(heard and all(0 <= t <= 7 for t in heard),
          f"解碼時回報解到的時間（{heard[:3]}）")
    plain = decodecheck._run(clip, False)
    check(decodecheck.split_errors(r.stderr) == decodecheck.split_errors(plain.stderr)
          and r.returncode == plain.returncode == 0,
          "有沒有人在聽，錯誤計數一樣")


tmp = Path(tempfile.mkdtemp(prefix="vam-mp4screen-"))
try:
    test_screen(tmp)
    test_not_understood(tmp)
    test_unread(tmp)
    test_read_limit(tmp)
    test_progress()
    test_part_progress(tmp)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\nFAIL: %d" % len(FAIL) if FAIL else "\nPASS")
sys.exit(1 if FAIL else 0)
