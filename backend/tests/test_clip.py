"""Cutting segments out of a video, and joining several into one file.

A short H.264 clip is generated with ffmpeg, so nothing in the library is
touched and the test needs no fixtures.

The check that earns its keep is `test_join`. Copy mode cannot re-encode the
leading partial GOP away, so those frames are physically in every part; MP4
merely *hides* them behind an edit list, which is invisible for a single clip
and wrong the moment parts are joined, because the concat demuxer ignores edit
lists and the hidden frames come back. Measured on a real film, a 3-segment
join came out 6 s long with the same packet count. Cutting to MPEG-TS instead
(no edit lists) makes what the file contains equal what it plays.

Unlike the earlier attempt at guarding the opening-frame bug, this one does
discriminate: swapping the TS parts back to MP4 makes `test_join` fail.

Run:  python tests/test_clip.py        (from backend/)
"""
import json
import subprocess
import sys
import time
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import clip                            # noqa: E402
from app.core.media import SUBPROCESS_FLAGS          # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


# ── pure logic ───────────────────────────────────────────────────────────────

def test_stamp():
    print("\n檔名時間戳")
    check(clip._stamp(602) == "1002", "10:02 → 1002")
    check(clip._stamp(0) == "0000", "0:00 → 0000")
    check(clip._stamp(3903) == "10503", "1:05:03 → 10503（有小時就多一位）")
    check(clip._stamp(59.6) == "0100", "59.6 秒四捨五入成 1 分整")


def test_merge():
    print("\n片段合併與排序")
    check(clip.merge_ranges([(30, 40), (10, 20)]) == [(10, 20), (30, 40)],
          "亂序加入會依時間排好")
    check(clip.merge_ranges([(10, 20), (15, 25)]) == [(10, 25)],
          "重疊的合併成一段（否則重疊處會輸出兩次）")
    check(clip.merge_ranges([(10, 20), (20, 30)]) == [(10, 30)],
          "首尾相接的也合併")
    check(clip.merge_ranges([(10, 60), (20, 30)]) == [(10, 60)],
          "被完全包住的被吸收")
    check(clip.merge_ranges([(10, 20), (30, 40)]) == [(10, 20), (30, 40)],
          "沒碰到的維持兩段")


def test_command():
    """Pin the flags whose absence fails silently rather than loudly."""
    print("\nffmpeg 指令")
    src, dst = Path("a.mp4"), Path("b.mp4")

    ts = clip._build_ts_cmd(src, dst, 602.0, 700.0, {"vcodec": "h264"})
    check("-noaccurate_seek" in ts,
          "複製切割要 -noaccurate_seek（直接指到關鍵幀的時間戳會時好時壞）")
    check(ts[ts.index("-ss") + 1].startswith("602"),
          "-ss 用使用者要求的時間，不是吸附後的關鍵幀")
    check(ts.index("-ss") < ts.index("-i"),
          "-ss 要在 -i 前面（放後面會從頭解碼，實測整片 183 秒）")
    check(ts[ts.index("-t") + 1].startswith("98"),
          "-t 從要求的時間起算，實際涵蓋 end - 關鍵幀")
    check(ts[ts.index("-f") + 1] == "mpegts",
          "中繼檔一定是 TS —— MP4 的 edit list 會把前置 GOP 藏起來，"
          "串接時又冒出來")
    check("h264_mp4toannexb" in ts, "H.264 進 TS 要 Annex-B")
    check("hevc_mp4toannexb" in
          clip._build_ts_cmd(src, dst, 1.0, 2.0, {"vcodec": "hevc"}),
          "HEVC 用對應的 bsf")

    mux = clip._build_mux_cmd(Path("l.txt"), dst, {"acodec": "aac"})
    check("aac_adtstoasc" in mux, "TS 的 aac 是 ADTS，進 MP4 要轉 ASC")
    check("+faststart" in mux, "要有 faststart，否則瀏覽器得下載完才能拖")
    check("copy" in mux, "串接是純複製")

    enc = clip._build_encode_cmd(src, dst, 602.0, 98.0, 20, False)
    check("libx264" in enc, "無 GPU 時用 libx264")
    check("h264_nvenc" in clip._build_encode_cmd(src, dst, 602.0, 98.0, 20, True),
          "勾了 GPU 就用 nvenc")
    join = clip._build_join_cmd(src, dst, [(1.0, 2.0), (5.0, 6.0)], 20, False)
    check("concat=n=2:v=1:a=1[v][a]" in " ".join(join),
          "精準模式多段用一次 filter_complex 接完")


def test_first_gop(tmp: Path):
    """A segment starting inside the first GOP must be cut with `-ss 0`.

    A non-zero `-ss` that resolves to the file's own first keyframe produces a
    TS with no SPS/PPS at all: measured on a real 2h13m file, `-ss 0.97` gave
    a stream ffprobe reads as `h264 0x0`, and muxing it died with 50 KB of
    "non-existing PPS 0 referenced". `-ss 0` and `-ss 60` on the same file are
    both fine.

    ⚠ Not reproducible synthetically. Tried: a plain generated clip (first
    keyframe lands exactly on 0, so there is nothing to normalise) and one
    with the video stream shifted by `-itsoffset 0.04` (first keyframe 0.04 —
    still cut fine under the old behaviour). Only real files trigger it, and
    those cannot live in the repo. So this pins the command instead.
    """
    print("\n第一個 GOP 內的起點")
    cmd = clip._build_ts_cmd(Path("a.mp4"), Path("b.ts"), 0.97, 600.0,
                             {"vcodec": "h264"}, actual_start=0.0)
    check(cmd[cmd.index("-ss") + 1].startswith("0"),
          f"吸附到檔頭時 -ss 要用 0，不是 0.97（得到 {cmd[cmd.index('-ss') + 1]}）")
    check(cmd[cmd.index("-t") + 1].startswith("600"),
          "-t 跟著從 0 起算，涵蓋到原本要求的終點")
    normal = clip._build_ts_cmd(Path("a.mp4"), Path("b.ts"), 60.0, 90.0,
                                {"vcodec": "h264"}, actual_start=58.4)
    check(normal[normal.index("-ss") + 1].startswith("60"),
          "一般情況照舊用要求的時間，不受影響")


def test_stderr_flood(tmp: Path):
    """ffmpeg that floods stderr must not deadlock the runner.

    A pipe holds about 64 KB. Reading stderr only after stdout is exhausted
    means a chatty process blocks on write, stops producing stdout, and the
    reader waits forever — measured in production as a job stuck at "100%"
    for nine minutes with zero I/O.

    This synthetic case does discriminate, though less dramatically than the
    real one: 300 KB of stderr takes the old sequential code ~25 s and the
    threaded drain 0.1 s, so the bound below separates them by a wide margin.
    """
    print("\nstderr 灌爆管線時不能卡死")
    flood = ("import sys;"
             "sys.stderr.write('x' * 300000);"
             "sys.stderr.flush();"
             "sys.exit(3)")
    t0 = time.time()
    try:
        clip._run_cmd([sys.executable, "-c", flood], 1.0, None)
        check(False, "應該要因為 exit code 3 而丟出例外")
    except RuntimeError:
        check(True, "正常丟出例外，沒有卡住")
    except Exception as e:
        check(False, f"丟出非預期的例外：{e}")
    dt = time.time() - t0
    check(dt < 20, f"{dt:.1f} 秒內結束（300 KB 遠超過 64 KB 的管線緩衝）")


def test_output_path(tmp: Path):
    print("\n輸出檔名")
    src = tmp / "OPKT-033.mp4"
    src.write_bytes(b"x")
    out = clip.output_path(src, [(602, 750)])
    check(out.name == "OPKT-033_clip_1002-1230.mp4", f"單段：{out.name}")
    check(out.parent == src.parent, "與原檔同一個資料夾")

    many = clip.output_path(src, [(602, 750), (3000, 3045), (5000, 5030)])
    check(many.name == "OPKT-033_clip_1002-1230.mp4",
          f"多段只用第一段命名，不加段數：{many.name}")

    out.write_bytes(b"x")
    again = clip.output_path(src, [(602, 750)])
    check(again.name == "OPKT-033_clip_1002-1230_2.mp4",
          f"撞名時加序號：{again.name}")
    # a joined clip and a single cut from the same start now share a name,
    # so this is the only thing keeping them apart
    again.write_bytes(b"x")
    third = clip.output_path(src, [(602, 750), (3000, 3045)])
    check(third.name == "OPKT-033_clip_1002-1230_3.mp4",
          f"同名的多段輸出也照樣讓開：{third.name}")


# ── against a real encode ────────────────────────────────────────────────────

def make_video(path: Path, seconds: int = 40, gop: int = 50):
    """`seconds` of test pattern with a keyframe every `gop` frames (25 fps)."""
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=25:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:v", "libx264", "-g", str(gop), "-keyint_min", str(gop),
         "-sc_threshold", "0", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(path)],
        capture_output=True, creationflags=SUBPROCESS_FLAGS)
    return path.exists() and path.stat().st_size > 0


def duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-of", "json", str(path)],
        capture_output=True, creationflags=SUBPROCESS_FLAGS)
    d = json.loads(r.stdout or b"{}")
    return float(d.get("format", {}).get("duration") or 0)


def first_keyframe(path: Path):
    """(pts of the first keyframe, pts of the earliest frame of any kind)."""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-read_intervals", "%+3", "-show_entries", "frame=pts_time,pict_type",
         "-of", "json", str(path)],
        capture_output=True, creationflags=SUBPROCESS_FLAGS)
    frames = json.loads(r.stdout or b"{}").get("frames", [])
    stamps = [float(f["pts_time"]) for f in frames if "pts_time" in f]
    keys = [float(f["pts_time"]) for f in frames
            if f.get("pict_type") == "I" and "pts_time" in f]
    return (min(keys) if keys else None), (min(stamps) if stamps else None)


def test_single(tmp: Path, src: Path):
    print("\n單段切割（2 秒 GOP）")
    kf = clip.keyframe_before(src, 5.0)
    check(kf is not None and abs(kf - 4.0) < 0.1,
          f"5.0 秒之前的關鍵幀應該是 4.0，得到 {kf}")
    check(clip.keyframe_before(src, 0.0) == 0.0, "0 秒回傳 0（檔頭必是關鍵幀）")

    p = clip.plan(src, [(5.0, 9.0)], precise=False,
                  total_duration=40.0, total_size=src.stat().st_size)
    check(p["ok"] and len(p["parts"]) == 1, "計畫成立")
    check(p["parts"][0]["snapped"] and abs(p["parts"][0]["drift"] - 1.0) < 0.1,
          f"起點吸附到關鍵幀，誤差 {p['parts'][0]['drift']}")

    dst = tmp / p["out_name"]
    clip.run(src, dst, p["parts"], False, {"vcodec": "h264", "acodec": "aac"})
    check(clip.verify(dst, p["duration"]) is None,
          f"驗證通過（{clip.verify(dst, p['duration'])}）")
    key, earliest = first_keyframe(dst)
    check(key is not None and earliest is not None and key <= earliest + 0.001,
          f"開頭就是關鍵幀（首個 I 幀 {key}，最早的畫格 {earliest}）")


def test_join(tmp: Path, src: Path):
    """Three segments into one file — the length must be the sum, not more.

    This is the check that would have caught the edit-list bug: with MP4 parts
    each join dragged in the hidden leading GOP, so the total ran a whole GOP
    per segment long.
    """
    print("\n多段合併成一個檔")
    segs = [(5.0, 9.0), (15.0, 19.0), (25.0, 30.0)]
    p = clip.plan(src, segs, precise=False,
                  total_duration=40.0, total_size=src.stat().st_size)
    check(len(p["parts"]) == 3, "三段都保留")
    dst = tmp / p["out_name"]
    clip.run(src, dst, p["parts"], False, {"vcodec": "h264", "acodec": "aac"})

    got = duration(dst)
    tol = clip.tolerance_for(len(p["parts"]))
    check(abs(got - p["duration"]) < tol,
          f"合併後長度 {got:.3f}，預期 {p['duration']:.3f}"
          f"（差 {got - p['duration']:+.3f}，容許 {tol:.1f}）")
    check(clip.verify(dst, p["duration"], tol) is None, "驗證通過")
    key, earliest = first_keyframe(dst)
    check(key is not None and earliest is not None and key <= earliest + 0.001,
          f"開頭就是關鍵幀（{key}）")
    check(not [x for x in dst.parent.iterdir()
               if x.name.startswith(".vamclip-")],
          "中繼檔清乾淨了")

    print("\n多段精準切割")
    p2 = clip.plan(src, segs, precise=True,
                   total_duration=40.0, total_size=src.stat().st_size)
    check(abs(p2["duration"] - 13.0) < 0.01,
          f"重編碼不吸附，總長就是選取的 13 秒（{p2['duration']}）")
    dst2 = tmp / ("p_" + p2["out_name"])
    clip.run(src, dst2, p2["parts"], True, {})
    tol2 = clip.tolerance_for(len(p2["parts"]))
    check(clip.verify(dst2, p2["duration"], tol2) is None,
          f"驗證通過（{clip.verify(dst2, p2['duration'], tol2)}）")


def test_guards(tmp: Path):
    print("\n拒絕無效的片段")
    src = tmp / "src.mp4"
    check(not clip.plan(src, [(5.0, 5.5)], False, 40.0, 1000)["ok"],
          "太短的片段要拒絕")
    check(not clip.plan(src, [(5.0, 4.0)], False, 40.0, 1000)["ok"],
          "終點早於起點要拒絕")
    check(not clip.plan(src, [], False, 40.0, 1000)["ok"], "空清單要拒絕")

    print("\n驗證會抓到長度不符")
    bad = tmp / "bad.mp4"
    bad.write_bytes(b"not a video")
    check(clip.verify(bad, 4.0) is not None, "不是影片 → 回報問題")
    check(clip.verify(tmp / "nope.mp4", 4.0) is not None, "檔案不存在 → 回報問題")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        test_stamp()
        test_merge()
        test_command()
        test_first_gop(tmp)
        test_stderr_flood(tmp)
        test_output_path(tmp)
        test_guards(tmp)
        src = tmp / "src.mp4"
        if make_video(src):
            test_single(tmp, src)
            test_join(tmp, src)
        else:
            check(False, "無法用 ffmpeg 產生測試影片（跳過實際切割）")
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
