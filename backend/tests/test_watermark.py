"""Watermark area within a group of similar images.

The detector compares the copies with each other, so the cases are about
pairs, not about recognising any particular logo:

  * a clean copy and the same picture with text laid on top → the text is
    charged to the copy that carries it, and the clean one reads 0
  * two different shots of the same scene → no verdict (None), because the
    difference is the picture, not an overlay
  * two byte-identical watermarked copies plus a clean one → both copies are
    charged; compared with each other they show nothing, and taking the
    smallest over comparisons would call them clean
  * the scan puts the result on each file of a similar group (`wm`)

The thresholds themselves were measured on a real folder (see watermark.py);
these images only have to land on the obvious side of them.

Run:  python tests/test_watermark.py        (from backend/)
"""
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw, ImageFont        # noqa: E402

from app.core import imgdedup, watermark           # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def scene(seed=1, shift=0, size=(900, 1200)):
    """A smooth 'photo': gradients and soft shapes, no sharp text."""
    im = Image.new("RGB", size)
    d = ImageDraw.Draw(im)
    for y in range(size[1]):
        d.line([(0, y), (size[0], y)],
               fill=((seed * 37 + y // 5) % 256, (90 + y // 8) % 256, 140))
    for i in range(6):
        x = (seed * (i + 3) * 97 + shift) % (size[0] - 260)
        y = (seed * (i + 5) * 61) % (size[1] - 260)
        d.ellipse([x, y, x + 250, y + 250],
                  fill=((seed * i * 53) % 256, (seed + i * 91) % 256, 60))
    return im


def font(px):
    for f in ("C:/Windows/Fonts/arial.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(f, px)
        except OSError:
            pass
    return ImageFont.load_default(size=px)


def stamp(im, text="sexbank.tv", at=(260, 760), px=70):
    """Semi-transparent white text, the way site watermarks are laid on."""
    im = im.convert("RGBA")
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).text(at, text, font=font(px),
                               fill=(255, 255, 255, 170))
    return Image.alpha_composite(im, layer).convert("RGB")


def save(im, path, q=90):
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, "JPEG", quality=q)
    return path


def test_clean_vs_marked(tmp: Path):
    print("\n乾淨的一張 vs 加了浮水印的一張")
    base = scene()
    c = save(base, tmp / "clean.jpg", 85)
    m = save(stamp(base), tmp / "marked.jpg", 97)    # bigger file, like Misa_av
    areas = watermark.group_areas([c, m])
    check(areas[0] is not None and areas[0] < watermark.CLEAN_MAX,
          f"乾淨的那張是 0（{areas[0]}）")
    check(areas[1] is not None and areas[1] > watermark.CLEAN_MAX,
          f"有浮水印的那張被算到面積（{areas[1]}）")
    check(m.stat().st_size > c.stat().st_size,
          "而且有浮水印的檔案比較大 —— 舊規則會選錯的那種")


def test_bigger_overlay(tmp: Path):
    print("\n兩張都有浮水印：大的被算成比較大")
    base = scene(3)
    a = save(stamp(stamp(base, "Telegram@sexbanktv", (40, 40), 60)), tmp / "a.jpg")
    # elsewhere, as in the real groups (#43-47): overlapping marks merge into
    # one patch, which then goes wholly to whichever has more strokes in it
    b = save(stamp(base, "sexbank.tv", (420, 1080), 50), tmp / "b.jpg")
    areas = watermark.group_areas([a, b])
    check(None not in areas and areas[0] > areas[1] > watermark.CLEAN_MAX,
          f"兩個浮水印各歸各的、大小分得出來：{areas}")


def test_different_shots(tmp: Path):
    print("\n不同畫面：不下判斷")
    a = save(stamp(scene(5)), tmp / "a.jpg")
    b = save(scene(5, shift=180), tmp / "b.jpg")
    areas = watermark.group_areas([a, b])
    check(areas == [None, None], f"整片不同的就是不同畫面：{areas}")


def test_identical_marked_copies(tmp: Path):
    print("\n兩份一模一樣的有浮水印副本 + 一張乾淨的")
    base = scene(7)
    c = save(base, tmp / "clean.jpg")
    m1 = save(stamp(base), tmp / "m1.jpg")
    m2 = tmp / "m2.jpg"
    shutil.copy(m1, m2)
    areas = watermark.group_areas([c, m1, m2])
    check(areas[0] < watermark.CLEAN_MAX, f"乾淨的仍是 0（{areas[0]}）")
    check(areas[1] > watermark.CLEAN_MAX and areas[2] > watermark.CLEAN_MAX,
          f"兩份副本都被算到浮水印（{areas[1:]}）—— 取最小值會把它們當成乾淨")


def test_aspect_mismatch(tmp: Path):
    print("\n比例不同的照片無法逐點比對")
    a = save(scene(9), tmp / "a.jpg")
    b = save(scene(9).crop((0, 0, 900, 900)), tmp / "b.jpg")
    check(watermark.group_areas([a, b]) == [None, None], "回報判斷不了，不是亂算")


def test_scan_puts_wm_on_files(tmp: Path):
    print("\n重複掃描：相似組的每個檔案都帶 wm，而且多一個階段")
    base = scene(11)
    lib = tmp / "lib" / "Someone"
    save(base, lib / "clean.jpg", 85)
    save(stamp(base), lib / "marked.jpg", 97)
    seen = []
    groups = imgdedup.find_duplicates([tmp / "lib"], tier2=True,
                                      cache_db=tmp / "hash.sqlite3",
                                      progress=lambda s: seen.append(dict(s)))
    sim = [g for g in groups if g["tier"] == 2]
    check(len(sim) == 1, f"兩張被分成同一組相似（{len(groups)} 組）")
    if sim:
        wm = {Path(f["path"]).name: f.get("wm") for f in sim[0]["files"]}
        check(wm.get("clean.jpg") == 0, f"乾淨的 wm = 0：{wm}")
        check((wm.get("marked.jpg") or 0) > watermark.CLEAN_MAX,
              f"有浮水印的 wm > 門檻：{wm}")
    phases = {s.get("phase") for s in seen if isinstance(s, dict)}
    check(4 in phases, f"第 4 階段「比對浮水印」有回報：{sorted(p for p in phases if p)}")
    p4 = [s for s in seen if isinstance(s, dict) and s.get("phase") == 4]
    if p4:
        check(p4[-1]["done"] == p4[-1]["total"] == len(sim),
              f"第 4 階段總數 = 相似組數，而且跑到底：{p4[-1]['done']}/{p4[-1]['total']}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for fn in (test_clean_vs_marked, test_bigger_overlay, test_different_shots,
               test_identical_marked_copies, test_aspect_mismatch,
               test_scan_puts_wm_on_files):
        d = Path(tempfile.mkdtemp())
        try:
            fn(d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
