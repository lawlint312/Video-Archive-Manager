"""Re-encoding videos to reclaim space.

The dangerous parts here are quiet ones: a scale that produces an odd
dimension fails only after the encode has run, an output that came out
*larger* than the source is a pure loss, and a filename that does not say
which settings produced it makes two runs indistinguishable.

Run:  python tests/test_shrink.py        (from backend/)
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import shrink                            # noqa: E402
from app.core.media import SUBPROCESS_FLAGS            # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def make(path: Path, w: int, h: int, seconds: int = 6, crf: int = 5) -> bool:
    """A deliberately over-encoded clip, so there is something to reclaim."""
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "error", "-y", "-f", "lavfi",
         "-i", f"testsrc=size={w}x{h}:rate=30:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:v", "libx264", "-crf", str(crf), "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(path)],
        capture_output=True, creationflags=SUBPROCESS_FLAGS)
    return path.exists() and path.stat().st_size > 0


def test_target_size():
    print("\n解析度上限的計算")
    check(shrink.target_size(3840, 2160, 0) is None, "0 = 不縮")
    check(shrink.target_size(1280, 720, 1920) is None, "比上限小就不動（不放大）")
    check(shrink.target_size(3840, 2160, 1920) == (1920, 1080), "橫式 4K → 1080")
    check(shrink.target_size(2160, 3840, 1920) == (1080, 1920), "直式 4K → 1080")
    # odd dimensions are the trap: h264+yuv420p needs even, and ffmpeg only
    # complains once the encode is already under way
    for w, h in ((1919, 1079), (1001, 667), (999, 1777)):
        t = shrink.target_size(w, h, 720)
        check(t is None or (t[0] % 2 == 0 and t[1] % 2 == 0),
              f"{w}x{h} → {t} 兩邊都是偶數")


def test_names(tmp: Path):
    print("\n輸出檔名要說出用了什麼設定")
    src = tmp / "clip.mp4"
    src.write_bytes(b"x")
    info = {"width": 3840, "height": 2160}
    a = shrink.output_path(src, 26, 0, info)
    check(a.name == "clip_crf26.mp4", f"沒縮解析度：{a.name}")
    b = shrink.output_path(src, 26, 1920, info)
    check(b.name == "clip_1080p_crf26.mp4", f"有縮解析度：{b.name}")
    c = shrink.output_path(src, 23, 1920, info)
    check(c.name != b.name, "不同設定不會撞名（否則分不出哪個是哪個）")
    b.write_bytes(b"x")
    d = shrink.output_path(src, 26, 1920, info)
    check(d.name == "clip_1080p_crf26_2.mp4", f"真的撞名才加序號：{d.name}")


def test_estimate_is_monotonic():
    print("\n粗估曲線")
    vals = [shrink.expected_saving(m) for m in (1, 3, 6, 10, 20, 50, 200)]
    check(all(a <= b for a, b in zip(vals, vals[1:])),
          f"位元率越高、估計省得越多：{[round(v,2) for v in vals]}")
    check(0 < vals[0] < 1 and 0 < vals[-1] < 1, "永遠落在 0~1 之間")


def test_encode_and_verify(tmp: Path):
    print("\n實際壓縮一個檔")
    src = tmp / "big.mp4"
    if not make(src, 640, 480):
        check(False, "無法產生測試影片（跳過）")
        return
    info = shrink.probe(src)
    check(info and info["width"] == 640 and info["duration"] > 0,
          f"探測到 {info.get('width')}x{info.get('height')}, "
          f"{info.get('mbps')} Mbps")

    dst = shrink.output_path(src, 28, 0, info)
    shrink.run(src, dst, 28, 0, info)
    check(dst.exists(), f"產生了 {dst.name}")
    check(shrink.verify(info, dst) is None,
          f"驗證通過（{shrink.verify(info, dst)}）")
    check(dst.stat().st_size < info["size"], "確實變小了")
    check(src.exists(), "原始檔案完全沒被動到")

    out = shrink.probe(dst)
    check(abs(out["duration"] - info["duration"]) < 1.0, "時長一致")
    check(out["width"] == info["width"], "沒縮解析度時尺寸不變")

    print("\n縮解析度")
    d2 = shrink.output_path(src, 28, 320, info)
    shrink.run(src, d2, 28, 320, info)
    o2 = shrink.probe(d2)
    check((o2["width"], o2["height"]) == (320, 240),
          f"縮到 320 長邊：{o2['width']}x{o2['height']}")

    print("\n壓不小的檔案要被擋下來")
    # verify() refuses an output that is not smaller — shipping one would be a
    # pure quality loss for no space at all
    check(shrink.verify({"duration": info["duration"], "size": 1}, dst)
          is not None, "輸出比原檔大 → 回報問題")


def test_sample(tmp: Path):
    print("\n試看要同時給出對照")
    src = tmp / "s.mp4"
    if not make(src, 640, 480, seconds=30):
        check(False, "無法產生測試影片（跳過）")
        return
    cache = tmp / "cache"
    s = shrink.sample(src, 28, 0, cache, seconds=4)
    b, a = Path(s["before"]), Path(s["after"])
    check(b.exists() and a.exists(), "產生了原始與壓縮兩段")
    check(b.stat().st_size > 0 and a.stat().st_size > 0, "兩段都不是空的")
    check(s["at"] > 0, f"從中段取樣（{s['at']}s），不是從開頭的黑畫面")
    check(0 < s["saved"] < 1, f"回報實際省下的比例 {s['saved']}")
    check(s["projected_size"] > 0, "換算出整支片的預估大小")
    # both halves must cover the *same* footage, or the comparison is
    # meaningless. The copy can only start on a keyframe, so the sample start
    # is snapped to one and both halves use it.
    durs = []
    for p in (b, a):
        d = json.loads(subprocess.run(
            ["ffprobe", "-v", "error", "-show_format", "-of", "json", str(p)],
            capture_output=True, creationflags=SUBPROCESS_FLAGS).stdout)
        durs.append(float(d["format"]["duration"]))
        check(abs(durs[-1] - 4) < 1.5, f"{p.name} 長度接近 4 秒（{durs[-1]:.2f}）")
    check(abs(durs[0] - durs[1]) < 0.5,
          f"兩段長度一致（{durs[0]:.2f} vs {durs[1]:.2f}）—— 不然是在比不同的畫面")

    again = shrink.sample(src, 28, 0, cache, seconds=4)
    check(again["before"] == s["before"], "同樣參數會重用已產生的樣本")

    # Reuse is why these are not wiped on some event — see cachelimit.KINDS,
    # which bounds the directory instead. `clear_samples()` used to live here
    # and was never called by anything; 136 MB had accumulated.
    from app.core import cachelimit
    check("shrink_samples" in cachelimit.KINDS,
          "樣本目錄有容量上限管著（否則會無限成長）")
    limit = cachelimit.DEFAULT_LIMIT_GB["shrink_samples"] * (1 << 30)
    # 1 byte, not 0 — `enforce` treats 0 as "no limit" on purpose, so that a
    # missing or zeroed setting can never wipe a whole cache tree
    res = cachelimit.enforce(cache / "shrink_samples", 1)
    check(res["removed"] >= 2 and not any((cache / "shrink_samples").glob("*.mp4")),
          f"超過上限時沿用既有的 LRU 掃除，沒有第二套清理邏輯（刪了 {res['removed']} 個）")
    check(limit < cachelimit.DEFAULT_LIMIT_GB["thumbs"] * (1 << 30),
          "預算比縮圖小：一次試看約 45 MB，而且看完就沒用了")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    test_target_size()
    test_estimate_is_monotonic()
    for fn in (test_names, test_encode_and_verify, test_sample):
        d = Path(tempfile.mkdtemp())
        try:
            fn(d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
