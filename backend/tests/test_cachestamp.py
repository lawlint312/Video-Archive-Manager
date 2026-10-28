"""Thumbnails and contact sheets follow a file replaced outside VAM.

The user re-downloads damaged videos and copies them over the old ones in
Explorer: same path, new content. The cache was keyed on the path alone, and
`purge_derived` only runs for what VAM itself does, so the server kept serving
pictures of the old file. Now the file's size and mtime are in the cache names
(`media.file_stamp`):

  * a replaced video gets a new thumbnail, and the old one is removed
  * the same for contact sheets; the sheet count starts over
  * deleting a video still drops all of it without knowing its stamp
  * the old-layout files are cleared at startup; memos are never touched

Run:  python tests/test_cachestamp.py      (from backend/)
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import contactsheet as cs, media      # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def clip(dst: Path, src="testsrc", seconds=30):
    subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y",
                    "-f", "lavfi", "-i", f"{src}=size=320x180:rate=10:duration={seconds}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(dst)], check=True)


def replace(video: Path, tmp: Path, src: str):
    """As copying a re-downloaded file over it in Explorer would."""
    new = tmp / "download.mp4"
    clip(new, src)
    shutil.copyfile(new, video)
    later = time.time() + 5
    os.utime(video, (later, later))


def test_thumb(tmp: Path):
    print("\n影片縮圖：外部覆蓋後換新圖，舊圖刪掉")
    cache = tmp / "cache"
    v = tmp / "a.mp4"
    clip(v, "testsrc")
    first = media.get_or_make_thumb(v, cache)
    check(first and first.exists(), f"產生縮圖：{first and first.name}")
    again = media.get_or_make_thumb(v, cache)
    check(again == first, "沒變的檔：同一張（快取命中）")
    old_bytes = first.read_bytes()
    replace(v, tmp, "smptebars")
    after = media.get_or_make_thumb(v, cache)
    check(after and after != first and after.exists(), f"覆蓋後是新的一張：{after and after.name}")
    check(not first.exists(), "舊的那張已刪掉")
    check(after.read_bytes() != old_bytes, "畫面是新檔的，不是舊檔的")


def test_sheet(tmp: Path):
    print("\n縮圖表：外部覆蓋後重做，舊的刪掉、張數從 0 算起")
    cache = tmp / "cache"
    v = tmp / "b.mp4"
    clip(v, "testsrc", seconds=40)
    s0 = cs.render(v, 0, 10, cache)
    check(s0 and s0.exists(), f"產生第 1 張：{s0 and s0.name}")
    check(cs.status(v, 10, cache)["ready"] >= 1, "狀態說有一張好了")
    replace(v, tmp, "smptebars")
    check(cs.status(v, 10, cache)["ready"] == 0, "覆蓋後：一張都還沒有（不拿舊的充數）")
    s1 = cs.render(v, 0, 10, cache)
    check(s1 and s1 != s0 and s1.exists(), f"重做出新的一張：{s1 and s1.name}")
    check(not s0.exists(), "舊的那張已刪掉")


def test_purge_after_delete(tmp: Path):
    print("\n刪檔之後清衍生資料：不需要知道檔案的大小與時間")
    cache = tmp / "cache"
    v = tmp / "c.mp4"
    clip(v, "testsrc", seconds=20)
    t = media.get_or_make_thumb(v, cache)
    s = cs.render(v, 0, 10, cache)
    v.unlink()
    media.purge_derived(v, cache)
    check(t and not t.exists() and not media.thumb_dir(v, cache).exists(), "縮圖整個資料夾清掉")
    check(s and not s.exists() and not cs.sheet_dir(v, cache).exists(), "縮圖表整個資料夾清掉")


def test_legacy_cleanup(tmp: Path):
    print("\n啟動時清掉舊格式的快取，備忘一律不碰")
    cache = tmp / "cache"
    old_thumb = cache / "thumbs" / "ab" / ("ab" + "0" * 30 + ".jpg")
    old_sheet = cache / "sheets" / "cd" / ("cd" + "1" * 30) / "60_0_4x3_480.jpg"
    new_thumb = cache / "thumbs" / "ef" / ("ef" + "2" * 30) / "1a2b-6543_0.1_640.jpg"
    new_sheet = cache / "sheets" / "cd" / ("cd" + "1" * 30) / "1a2b-6543_60_0_4x3_480.jpg"
    note = cache / "notes" / ("9" * 32 + ".txt")
    for p in (old_thumb, old_sheet, new_thumb, new_sheet, note):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    got = media.drop_legacy_cache(cache)
    check(got == {"thumbs": 1, "sheets": 1}, f"各清掉一個：{got}")
    check(not old_thumb.exists() and not old_sheet.exists(), "舊格式的不見了")
    check(new_thumb.exists() and new_sheet.exists(), "新格式的還在")
    check(note.exists(), "備忘沒被碰")
    check(media.drop_legacy_cache(cache) == {"thumbs": 0, "sheets": 0}, "再跑一次什麼都不做")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for fn in (test_thumb, test_sheet, test_purge_after_delete, test_legacy_cleanup):
        d = Path(tempfile.mkdtemp())
        try:
            fn(d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
