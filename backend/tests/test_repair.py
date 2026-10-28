"""Rescuing fragmented MP4s that ffmpeg refuses to open.

The test builds a real fragmented MP4 with ffmpeg, corrupts one fragment, and
checks that the repair finds it, drops only that one, and produces a playable
file. That makes it a genuine guard rather than a restatement of the code:
reverting the fix leaves `plan_for` calling it 損毀 and no repair to run.

The other half is the detection rule, which has to be *both* "ffprobe failed"
and "header is ftyp". A library survey found 20 files whose extension is not
.mp4 but whose header is ftyp, and 19 of them read perfectly — keying on the
header alone would offer to repair files with nothing wrong.

Run:  python tests/test_repair.py        (from backend/)
"""
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import repair, convert                  # noqa: E402
from app.core.media import SUBPROCESS_FLAGS           # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def make_fragmented(path: Path, seconds: int = 20) -> bool:
    """A real fragmented MP4 — the shape the capture tools produce."""
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=25:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:v", "libx264", "-g", "25", "-keyint_min", "25",
         "-sc_threshold", "0", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-movflags", "frag_keyframe+empty_moov+default_base_moof",
         "-shortest", str(path)],
        capture_output=True, creationflags=SUBPROCESS_FLAGS)
    return path.exists() and path.stat().st_size > 0


def readable(p: Path) -> bool:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_streams",
                        "-of", "csv=p=0:nk=1", str(p)],
                       capture_output=True, creationflags=SUBPROCESS_FLAGS)
    return bool(r.stdout.strip())


def test_detection(tmp: Path):
    print("\n偵測條件")
    plain = tmp / "notvideo.ts"
    plain.write_bytes(b"this is not a video at all" * 100)
    check(not repair.looks_like_mp4(plain), "隨機資料不會被當成 MP4")
    check(convert.plan_for(plain)["action"] == "skip",
          "讀不到又沒有 ftyp 檔頭 → 略過，不提供修復")

    mp4 = tmp / "healthy.ts"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "error", "-y", "-f", "lavfi",
         "-i", "testsrc=size=160x120:rate=25:duration=2", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", "-f", "mp4", str(mp4)],
        capture_output=True, creationflags=SUBPROCESS_FLAGS)
    check(repair.looks_like_mp4(mp4), "副檔名是 .ts 但檔頭是 ftyp → 認得出來")
    # the point of the two-condition rule: this one is fine, leave it alone
    check(convert.plan_for(mp4)["action"] != "repair",
          "但它讀得到，所以不該被標成需要修復（庫裡有 19 個這種檔案）")


def test_pix_fmt(tmp: Path):
    """yuvj420p is yuv420p with a full-range flag; browsers play it."""
    print("\n像素格式")
    p = tmp / "fullrange.mkv"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "error", "-y", "-f", "lavfi",
         "-i", "testsrc=size=160x120:rate=25:duration=2", "-c:v", "libx264",
         "-pix_fmt", "yuvj420p", "-c:a", "aac", "-f", "matroska", str(p)],
        capture_output=True, creationflags=SUBPROCESS_FLAGS)
    if not p.exists():
        check(False, "無法產生 yuvj420p 測試檔（跳過）")
        return
    info = convert.probe(p)
    plan = convert.plan_for(p)
    check(info.get("pix_fmt") in ("yuvj420p", "yuv420p"),
          f"測試檔的像素格式是 {info.get('pix_fmt')}")
    if info.get("pix_fmt") == "yuvj420p":
        check(plan["action"].startswith("remux"),
              f"yuvj420p 要走無損換殼，不是失真重編碼（得到 {plan['action']}）")


def test_repair(tmp: Path):
    print("\n修復一個真的壞掉的分段 MP4")
    good = tmp / "good.mp4"
    if not make_fragmented(good):
        check(False, "無法產生分段 MP4（跳過）")
        return
    boxes = repair.top_level_boxes(good)
    moofs = [i for i, b in enumerate(boxes) if b[2] == b"moof"]
    check(len(moofs) > 4, f"產生了 {len(moofs)} 個片段")
    check(readable(good), "未損壞前 ffprobe 讀得到")

    # Corrupt one fragment the way the real file was broken: box structure
    # completely intact, contents not. An absurd `trun` sample count does it —
    # picked by measurement. Four other corruptions were tried and ffprobe
    # shrugged them all off (zeroing the whole moof payload, a wild
    # data_offset, renaming the mdat, filling the payload with 0xff), which is
    # exactly why this test needed a real one rather than an assumed one.
    broken = tmp / "broken.ts"
    data = bytearray(good.read_bytes())
    victim = moofs[len(moofs) // 2]
    off, bsz, _t = boxes[victim]
    i = data.find(b"trun", off, off + bsz)
    if i < 0:
        check(False, "測試檔的 moof 裡找不到 trun（跳過）")
        return
    data[i + 8:i + 12] = struct.pack(">I", 0x00FFFFFF)
    broken.write_bytes(bytes(data))

    check(not readable(broken), "弄壞一個片段後，整個檔案就讀不到了")
    check(repair.looks_like_mp4(broken), "但檔頭仍然是 ftyp")
    plan = convert.plan_for(broken)
    check(plan["action"] == "repair",
          f"轉檔掃描把它標成可修復（得到 {plan['action']}：{plan['reason']}）")

    out = repair.target_for(broken)
    check(out.name == "broken_fix.mp4",
          f"輸出檔名帶 _fix：{out.name}（原檔留著，只差副檔名會分不出誰是誰）")
    steps = []
    res = repair.repair(broken, out, progress=lambda d: steps.append(d))
    check(out.exists() and readable(out), "修好的檔案讀得到")
    check(res["dropped"] >= 1, f"丟掉了 {res['dropped']} 段")
    check(res["fragments"] == len(moofs),
          f"認出 {res['fragments']} 個片段（原本 {len(moofs)}）")
    check(res["duration"] > 0, f"成品時長 {res['duration']:.1f}s")
    check(broken.exists() and broken.stat().st_size == len(data),
          "原始檔案完全沒有被更動")
    # What the user reported: every stage said 0%, then the run jumped to
    # done. So the test is about the number, not about a stage being named.
    pcts = [s.get("pct") for s in steps if s.get("pct") is not None]
    check(len(set(pcts)) > 3, f"百分比真的有在動（{len(set(pcts))} 種值）")
    check(all(b >= a for a, b in zip(pcts, pcts[1:])), "百分比從不倒退")
    check(pcts and pcts[-1] >= 0.99, f"最後到 100%（{pcts[-1] if pcts else None}）")
    check({"複製到本機", "找出壞掉的片段", "重新封裝"}
          <= {s.get("stage") for s in steps}, "三個階段都有報到")
    # not fragmented any more, so thumbnails/sheets/range streaming behave
    kinds = {b[2] for b in repair.top_level_boxes(out)}
    check(b"moof" not in kinds,
          "輸出是一般 MP4，不再是分段格式")

    print("\n修不了的檔案要老實說")
    junk = tmp / "junk.ts"
    junk.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 4000)
    try:
        repair.repair(junk, tmp / "nope.mp4")
        check(False, "應該要丟出例外")
    except RuntimeError as e:
        check(True, f"丟出 RuntimeError：{e}")
    check(not (tmp / "nope.mp4").exists(), "失敗時不留半成品")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        test_detection(tmp)
        test_pix_fmt(tmp)
        test_repair(tmp)
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
