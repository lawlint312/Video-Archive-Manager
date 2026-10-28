"""Taking the empty stretches out of a recording (convert action `gapcut`).

A recorder that keeps its clock while the stream sends nothing leaves minutes
with no video and no audio packets — players hold the last frame, so it looks
like a long run of identical frames, and the decode check passes it because
nothing is damaged (MMMMMooooooooooo-20261022-220946.mp4: 61 min long, 30 of
them empty). The fix rewrites timestamps; every packet is copied.

The clips here are really built that way: two encodes, the second written
with a timestamp offset, concatenated as MPEG-TS — so the blank is in the
packet timestamps exactly as in the real file.

  * a 30 s blank in both streams is found, resumes on a keyframe, and comes
    out: same packet count, length 12 s, no blank left, codec unchanged
  * a frozen picture with sound still running is NOT a blank
  * the scan turns an .mp4 into an in-place `gapcut` row and a .ts into one
    that converts to .mp4; without the option nothing is looked at

Run:  python tests/test_gapcut.py      (from backend/)
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import convert                            # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def ff(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", *args],
                   check=True)


def segment(out: Path, start: float, audio=True):
    """6 s of 640x360 h264 (+ aac) whose timestamps begin at `start`."""
    args = ["-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=6"]
    if audio:
        args += ["-f", "lavfi", "-i", "sine=frequency=440:duration=6"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "30"]
    if audio:
        args += ["-c:a", "aac", "-b:a", "96k"]
    args += ["-output_ts_offset", str(start), "-f", "mpegts", str(out)]
    ff(*args)


def blank_clip(tmp: Path, name: str, gap: float = 30, audio_over_gap=False,
               keep_ts=False) -> Path:
    """Video 0-6 s, nothing until 6+gap, video again for 6 s.

    `audio_over_gap`: the sound keeps going through the gap (a frozen
    camera, not a dropped stream) — that must not be cut.
    """
    a, b = tmp / f"{name}_a.ts", tmp / f"{name}_b.ts"
    segment(a, 0, audio=not audio_over_gap)
    segment(b, 6 + gap, audio=not audio_over_gap)
    joined = tmp / f"{name}_j.ts"
    joined.write_bytes(a.read_bytes() + b.read_bytes())
    out = tmp / f"{name}.mp4"
    if audio_over_gap:
        total = 12 + gap
        # -copyts on every remux here: without it ffmpeg closes a jump of
        # more than 10 s in an MPEG-TS input on its own, and the clip would
        # have no blank to find
        ff("-copyts", "-i", str(joined), "-f", "lavfi",
           "-i", f"sine=frequency=330:duration={total}",
           "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac",
           str(out))
    else:
        ff("-copyts", "-i", str(joined), "-c", "copy", "-bsf:a", "aac_adtstoasc",
           str(out))
    for f in (a, b):
        f.unlink()
    if keep_ts:
        return joined           # the raw concatenation: a .ts with the blank
    joined.unlink()
    return out


def test_cut(tmp: Path):
    print("\n中間 30 秒沒畫面也沒聲音：找得到、去得掉、一格都沒少")
    src = blank_clip(tmp, "blank")
    plan = convert.blank_plan(src)
    check(plan and len(plan["blanks"]) == 1 and plan["skipped"] == 0,
          f"找到 1 段空白：{plan}")
    check(29 < plan["removed"] < 31, f"要去掉約 30 秒：{plan['removed']}")
    info = convert.probe(src)
    check(info["duration"] > 40, f"原檔長度約 42 秒：{info['duration']:.1f}")
    info = {**info, "blanks": plan["blanks"], "frames": plan["frames"],
            "duration": info["duration"] - plan["removed"]}
    dst = tmp / "blank.converting.mp4"
    convert.run_one(src, dst, "gapcut", info)
    problem = convert.verify(info, dst, "gapcut")
    check(problem is None, f"驗證通過：{problem}")
    out = convert.probe(dst)
    check(11 < out["duration"] < 13, f"輸出長度約 12 秒：{out['duration']:.1f}")
    check(convert._video_packets(dst) == plan["frames"], "畫格數完全相同")
    check(convert.blank_plan(dst) is None, "輸出沒有空白了")
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(dst), "-f", "null", "-"],
                       capture_output=True, text=True)
    check(r.returncode == 0 and not r.stderr.strip(), "輸出整支解碼 0 個錯誤")


def test_sound_is_not_blank(tmp: Path):
    print("\n畫面停了但聲音還在：不是空白，不能剪")
    src = blank_clip(tmp, "frozen", audio_over_gap=True)
    plan = convert.blank_plan(src)
    check(plan is not None and plan["blanks"] == [] and plan["skipped"] == 1,
          f"列為無法去除，不剪：{plan}")


def test_scan(tmp: Path):
    print("\n掃描：.mp4 就地處理、.ts 轉成 .mp4；不勾選就不看")
    lib = tmp / "lib"
    lib.mkdir()
    shutil.move(str(blank_clip(tmp, "rec")), lib / "rec.mp4")
    shutil.move(str(blank_clip(tmp, "rec2", keep_ts=True)), lib / "rec2.ts")
    rows = {Path(r["path"]).name: r for r in
            convert.scan([lib], include_mp4=False, check_blanks=True)}
    r = rows.get("rec.mp4")
    check(r and r["action"] == "gapcut" and r["in_place"] and r["blanks"] == 1,
          f".mp4 → 就地去除空白：{r and (r['action'], r['in_place'])}")
    r = rows.get("rec2.ts")
    check(r and r["action"] == "gapcut" and not r["in_place"]
          and r["dst"].endswith("rec2.mp4"), f".ts → 去除空白並轉 .mp4：{r and r['dst']}")
    check(r and "錄影中斷 1 處" in r["reason"], f"說明寫出幾處：{r and r['reason']}")
    # and actually run it on the .ts: -copyts, the ADTS bitstream filter and
    # the MP4 muxer all in one pass
    src = lib / "rec2.ts"
    plan = convert.blank_plan(src)
    info = {**convert.probe(src), "blanks": plan["blanks"], "frames": plan["frames"]}
    info["duration"] -= plan["removed"]
    dst = lib / "rec2.mp4"
    convert.run_one(src, dst, "gapcut", info)
    problem = convert.verify(info, dst, "gapcut")
    check(problem is None, f".ts 實際轉成 .mp4 並去除空白，驗證通過：{problem}")
    dst.unlink(missing_ok=True)
    rows = {Path(r["path"]).name: r for r in convert.scan([lib])}
    check("rec.mp4" not in rows and rows["rec2.ts"]["action"] == "remux",
          "沒勾選：.mp4 不看、.ts 照舊換殼")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for fn in (test_cut, test_sound_is_not_blank, test_scan):
        d = Path(tempfile.mkdtemp())
        try:
            fn(d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
