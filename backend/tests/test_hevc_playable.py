"""H.265 counts as playable, so the convert check leaves it alone.

It used to flag every H.265 file "needs re-encoding (browser support is
unreliable)". Now the shrink tool writes H.265, and that rule would have
offered to convert each result straight back to H.264, undoing the saving.
The user accepted that devices too old to decode H.265 will not play these
(2026-11-07).

What stays: a pixel format browsers do not take (4:2:2) still needs a
re-encode, and an H.265 stream copied out of MKV is tagged `hvc1` — ffmpeg
would keep `hev1`, which Safari and iOS refuse.

Every fixture is generated with ffmpeg in a temp directory.

Run:  python tests/test_hevc_playable.py      (from backend/)
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import convert                          # noqa: E402
from app.core.media import SUBPROCESS_FLAGS           # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def make(path: Path, pix: str = "yuv420p", extra=()) -> bool:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "error", "-y", "-f", "lavfi",
         "-i", "testsrc=size=320x240:rate=25:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-c:v", "libx265", "-x265-params", "log-level=error", "-pix_fmt", pix,
         *extra, "-c:a", "aac", "-shortest", str(path)],
        capture_output=True, creationflags=SUBPROCESS_FLAGS)
    return path.exists() and path.stat().st_size > 0


def tag(path: Path) -> str:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=codec_name,codec_tag_string",
                        "-of", "json", str(path)],
                       capture_output=True, creationflags=SUBPROCESS_FLAGS)
    s = json.loads(r.stdout)["streams"][0]
    return f"{s['codec_name']}/{s['codec_tag_string']}"


def main(tmp: Path):
    print("\nH.265 的 MP4 不再列入轉檔")
    mp4 = tmp / "a.mp4"
    if not make(mp4, extra=["-tag:v", "hvc1"]):
        check(False, "無法產生 H.265 測試影片（跳過）")
        return
    p = convert.plan_for(mp4)
    # for an .mp4, "remux" is what the scan treats as nothing to do
    check(p["vcodec"] == "hevc" and p["action"] == "remux",
          f"8-bit H.265 → {p['action']}（{p['reason']}）")

    ten = tmp / "b.mp4"
    if make(ten, "yuv420p10le"):
        p = convert.plan_for(ten)
        check(p["action"] == "remux", f"10-bit Main10 也可以播 → {p['action']}")

    yuv422 = tmp / "c.mp4"
    if make(yuv422, "yuv422p"):
        p = convert.plan_for(yuv422)
        check(p["action"] == "reencode",
              f"4:2:2 瀏覽器還是不吃 → {p['action']}（{p['reason']}）")

    print("\nMKV 裡的 H.265 無損換殼，而且標成 hvc1")
    mkv = tmp / "d.mkv"
    make(mkv)
    p = convert.plan_for(mkv)
    check(p["action"] == "remux", f"MKV 的 H.265 → {p['action']}，不重編碼")
    dst = convert.target_for(mkv)
    convert.run_one(mkv, dst, p["action"], p)
    check(tag(dst) == "hevc/hvc1", f"輸出是 {tag(dst)}（Safari／iOS 只認 hvc1）")
    check(convert.verify(p, dst, p["action"]) is None, "通過驗證")

    print("\nH.264 不受影響")
    cmd = convert._build_cmd(mkv, dst, "remux", {"vcodec": "h264"})
    check("-tag:v" not in cmd, "H.264 換殼不加 hvc1 標籤")
    cmd = convert._build_cmd(mkv, dst, "reencode", {"vcodec": "hevc"})
    check("-tag:v" not in cmd, "重編碼成 H.264 時也不加（那不是 H.265 了）")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    d = Path(tempfile.mkdtemp())
    try:
        main(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
