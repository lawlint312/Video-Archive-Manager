"""Downscaled photos for the grids.

The point of the feature is a number: a screenful of phone-camera originals
was 43.9 MB and 333 megapixels of decoding, against a 280px grid cell. So the
tests are mostly about *not* quietly losing that — a thumbnail that comes back
at full size still renders correctly and would never be noticed.

Run:  python tests/test_photothumb.py        (from backend/)
"""
import io
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageFilter, ImageStat         # noqa: E402

from app.core import photothumb                       # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def photo(p: Path, size=(3024, 4032), mode="RGB", fmt="JPEG"):
    p.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new(mode, size)
    # noise, so the encoder cannot compress it to nothing and the size
    # comparisons below stay meaningful
    px = im.load()
    for y in range(0, size[1], 7):
        for x in range(0, size[0], 7):
            px[x, y] = ((x * 7) % 256, (y * 5) % 256, (x + y) % 256) \
                if mode == "RGB" else ((x % 256), (y % 256), 0, 255)
    im.save(p, fmt)
    return p


def test_downscales(tmp: Path):
    print("\n縮圖要真的變小（這功能的全部意義）")
    src = photo(tmp / "big.jpg")
    cache = tmp / "cache"
    t = photothumb.get(src, cache)
    check(t is not None and t.is_file(), "產生得出來")
    with Image.open(t) as im:
        w, h = im.size
    check(min(w, h) == photothumb.SHORT_EDGE,
          f"短邊縮到 {photothumb.SHORT_EDGE}：{w}x{h}")
    # the grid uses object-fit: cover on a 280x350 cell, so it is the SHORT
    # edge that has to cover it — sizing by the long edge leaves a portrait
    # photo too narrow and the browser upscales it again
    check(abs(w / h - 3024 / 4032) < 0.01, f"長寬比不變：{w}x{h}")
    check(t.stat().st_size < src.stat().st_size / 4,
          f"檔案小很多：{src.stat().st_size // 1024} KB → {t.stat().st_size // 1024} KB")
    check(t.parent.parent.name == photothumb.DIR,
          f"住在 cache/{photothumb.DIR}/ 底下：{t.parent.parent.name}")


def test_cached_not_redone(tmp: Path):
    print("\n第二次要直接命中，不重做")
    src = photo(tmp / "a.jpg", size=(2000, 1500))
    cache = tmp / "c"
    t0 = time.time()
    first = photothumb.get(src, cache)
    made = time.time() - t0
    stamp = first.stat().st_mtime_ns
    t0 = time.time()
    again = photothumb.get(src, cache)
    hit = time.time() - t0
    check(again == first and again.stat().st_mtime_ns == stamp,
          "回同一個檔案而且沒有重寫")
    check(hit < made / 2 or hit < 0.01, f"命中快得多：{made*1000:.0f}ms → {hit*1000:.0f}ms")


def test_key_follows_the_file(tmp: Path):
    print("\n照片被換掉要重做，不能拿舊圖出來")
    src = photo(tmp / "b.jpg", size=(1200, 1200))
    cache = tmp / "c"
    first = photothumb.get(src, cache)
    time.sleep(0.01)
    photo(src, size=(900, 1600))            # same path, different picture
    second = photothumb.get(src, cache)
    check(second != first, "換了檔案就換一把鑰匙")
    with Image.open(second) as im:
        check(im.size[0] < im.size[1], f"內容是新的那張：{im.size}")


def test_empty_file_is_not_a_hit(tmp: Path):
    print("\n0 位元組的殘骸不算命中")
    src = photo(tmp / "c.jpg", size=(1000, 1000))
    cache = tmp / "c"
    t = photothumb.get(src, cache)
    t.write_bytes(b"")                       # a write that died half way
    again = photothumb.get(src, cache)
    check(again is not None and again.stat().st_size > 0,
          "看大小不是看存在，否則那格會永遠是破圖")


def test_alpha_and_gif(tmp: Path):
    print("\n透明與動圖")
    cache = tmp / "c"
    png = photo(tmp / "t.png", size=(1400, 1400), mode="RGBA", fmt="PNG")
    t = photothumb.get(png, cache)
    check(t is not None, "帶透明度的 PNG 也做得出來（壓在深色底上，不是黑框）")

    gif = tmp / "anim.gif"
    Image.new("P", (1200, 1200)).save(gif)
    check(not photothumb.wanted(gif),
          "GIF 不做縮圖 —— 會失去動畫，而全庫只有 36 個，不值得第二條程式路徑")


def test_small_source_still_cached(tmp: Path):
    print("\n本來就小的照片也要進快取")
    src = photo(tmp / "small.jpg", size=(400, 500))
    cache = tmp / "c"
    t = photothumb.get(src, cache)
    check(t is not None and t.is_file(), "一樣有快取項目")
    with Image.open(t) as im:
        check(im.size == (400, 500), f"不放大：{im.size}")
    # one code path, always a cache hit afterwards — the alternative was
    # re-reading the header from the NAS on every single request to decide
    check(photothumb.get(src, cache) == t, "之後就是純命中")


def test_purge(tmp: Path):
    print("\n刪照片前要清掉縮圖")
    src = photo(tmp / "d.jpg", size=(1000, 1000))
    cache = tmp / "c"
    t = photothumb.get(src, cache)
    check(t.is_file(), "先有")
    photothumb.purge(src, cache)
    check(not t.exists(), "清掉了")

    # and the ordering constraint that makes it work at all
    src2 = photo(tmp / "e.jpg", size=(1000, 1000))
    t2 = photothumb.get(src2, cache)
    src2.unlink()
    photothumb.purge(src2, cache)
    check(t2.exists(),
          "檔案先被刪掉就清不掉了（鑰匙含大小與時間）—— 所以呼叫順序必須是先清再刪")


def test_truncated_is_salvaged(tmp: Path):
    print("\n被截斷的照片要救回來，不是退回原檔")
    src = photo(tmp / "whole.jpg", size=(1600, 1200))
    data = src.read_bytes()
    cut = tmp / "cut.jpg"
    cut.write_bytes(data[:int(len(data) * 0.75)])      # a real truncated JPEG

    t = photothumb.get(cut, tmp / "c")
    check(t is not None, "產得出來")
    # This is the case that matters most: the browser draws a truncated JPEG
    # anyway, so failing here made the grid serve the *full original* for
    # precisely the files least worth downloading whole.
    if t:
        check(t.stat().st_size < cut.stat().st_size / 4,
              f"而且真的小很多：{cut.stat().st_size//1024} KB → {t.stat().st_size//1024} KB")


def damaged_progressive(p: Path) -> Path:
    """The shape of the four black photos in `2km2km a.k.a DJ MIU`: the first
    scans of a progressive JPEG intact, then an undefined `FF 99` marker,
    foreign bytes and no end marker. libjpeg abandons the whole picture."""
    # filled and smooth like a photo; the sparse dots `photo()` draws are
    # nearly all black, which is the one thing this test must not resemble
    g = Image.linear_gradient("L")
    im = Image.merge("RGB", (g.resize((1080, 1350)),
                             g.rotate(90).resize((1080, 1350)),
                             g.rotate(45).resize((1080, 1350)))).filter(
        ImageFilter.GaussianBlur(3))
    buf = io.BytesIO()
    im.save(buf, "JPEG", progressive=True, quality=90)
    data = buf.getvalue()
    sos = [i for i in range(len(data) - 1) if data[i:i + 2] == b"\xff\xda"]
    mid = (sos[3] + sos[4]) // 2
    p.write_bytes(data[:mid] + b"\xff\x99" + bytes(range(256)) * 40)
    p.with_name("intact_" + p.name).write_bytes(data)
    return p


def test_damaged_progressive_is_not_black(tmp: Path):
    print("\nprogressive 後段壞掉的照片：縮圖要有畫面，不是一片黑")
    src = damaged_progressive(tmp / "broken_scan.jpg")
    try:
        with Image.open(src) as im:
            im.load()
        check(False, "素材要真的壞（嚴格解碼會失敗）")
    except OSError:
        pass
    t = photothumb.get(src, tmp / "c")
    check(t is not None, "產得出來")
    if t:
        whole = photothumb.get(src.with_name("intact_" + src.name), tmp / "c")
        with Image.open(t) as a, Image.open(whole) as b:
            got, want = (ImageStat.Stat(x.convert("L")).mean[0] for x in (a, b))
        # under LOAD_TRUNCATED_IMAGES this was 0.0 — solid black
        check(abs(got - want) < want * 0.2,
              f"跟完好的那張一樣亮，不是全黑：{got:.1f} vs {want:.1f}")


def test_does_not_flip_pillow_globally(tmp: Path):
    print("\n縮圖不能改 Pillow 的全域設定（會讓圖片去重把壞圖當成全黑）")
    from PIL import ImageFile
    from app.core import imgdedup
    src = damaged_progressive(tmp / "broken_scan.jpg")
    photothumb.get(photo(tmp / "whole.jpg", size=(800, 600)), tmp / "c")
    photothumb.get(src, tmp / "c")
    check(not ImageFile.LOAD_TRUNCATED_IMAGES, "LOAD_TRUNCATED_IMAGES 仍然是關的")
    # with the switch on this was 32 zero bytes, and every damaged photo in a
    # folder became a "duplicate" of every other one
    check(imgdedup.image_hash(src) is None,
          "做過縮圖之後，去重照樣認得出這是壞檔（不是全 0 的雜湊）")


def test_unreadable(tmp: Path):
    print("\n壞檔不要讓整格變成錯誤")
    cache = tmp / "c"
    bad = tmp / "broken.jpg"
    bad.write_bytes(b"not an image at all")
    check(photothumb.get(bad, cache) is None,
          "回 None 讓端點退回原檔，而不是丟例外")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for fn in (test_downscales, test_cached_not_redone, test_key_follows_the_file,
               test_empty_file_is_not_a_hit, test_alpha_and_gif,
               test_small_source_still_cached, test_purge,
               test_truncated_is_salvaged, test_damaged_progressive_is_not_black,
               test_does_not_flip_pillow_globally, test_unreadable):
        d = Path(tempfile.mkdtemp())
        try:
            fn(d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
