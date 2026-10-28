"""The profile tab's album view: what `scan_profile_albums` tags each file with.

Pinned here:

  * `album` is the *first-level* subfolder, however deep the file sits — a set
    with its own sub-subfolders is still one set.
  * Videos inside a set come back flagged `in_set`, and `scan_profile_media_stat`
    (the flat grid, and the 相似 scan) must not see them. The flat grid is
    supposed to be exactly what it was before albums existed.
  * IG videos are profile media as before and are NOT `in_set`.
  * Loose videos at the top level are never profile media — they are the
    videos tab's.

Run:  python tests/test_profilealbums.py        (from backend/)
"""
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import media                            # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def _f(p: Path):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(bytes(8))
    return p


def build(tmp: Path):
    _f(tmp / "loose.jpg")
    _f(tmp / "loose.mp4")                       # videos tab only
    _f(tmp / "Vol.005" / "001.jpg")
    _f(tmp / "Vol.005" / "clip.mp4")            # set video
    _f(tmp / "Vol.005" / "extra" / "002.jpg")   # deeper, same album
    _f(tmp / "PPV" / "a.mp4")                   # a set that is only videos
    _f(tmp / "IG" / "p.jpg")
    _f(tmp / "IG" / "reels" / "r.mp4")          # IG video, as before
    _f(tmp / "Vol.005" / "notes.txt")           # neither


def test_tags(tmp: Path):
    print("\n每個檔案帶著它的相簿")
    build(tmp)
    rows = {str(r["path"].relative_to(tmp)).replace("\\", "/"): r
            for r in media.scan_profile_albums(tmp)}
    check("loose.mp4" not in rows, "根目錄的影片不是個人資料")
    check("Vol.005/notes.txt" not in rows, "不是圖片也不是影片")
    check(rows["loose.jpg"]["album"] == "", "根目錄的照片 → 空字串")
    check(rows["Vol.005/extra/002.jpg"]["album"] == "Vol.005",
          "更深一層仍算第一層那一組")
    check(rows["Vol.005/clip.mp4"]["in_set"] is True, "組裡的影片標 in_set")
    check(rows["PPV/a.mp4"]["album"] == "PPV", "只有影片的組也在")
    check(rows["IG/reels/r.mp4"]["in_set"] is False, "IG 影片照舊，不算 in_set")
    check(rows["IG/p.jpg"]["album"] == "IG", "IG 也是一個相簿")
    check(all(r["in_set"] is False for k, r in rows.items() if not r["is_video"]),
          "照片永遠不是 in_set")


def test_flat_unchanged(tmp: Path):
    print("\n平鋪檢視跟以前一模一樣")
    build(tmp)
    got = sorted(str(p.relative_to(tmp)).replace("\\", "/")
                 for p, _v, _m, _s in media.scan_profile_media_stat(tmp))
    want = sorted(["loose.jpg", "Vol.005/001.jpg", "Vol.005/extra/002.jpg",
                   "IG/p.jpg", "IG/reels/r.mp4"])
    check(got == want, f"只有照片 + IG 影片（{got}）")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for fn in (test_tags, test_flat_unchanged):
        d = Path(tempfile.mkdtemp())
        try:
            fn(d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
