"""DMM covers saved as <番號>-poster.jpg, shown by the card's shape.

The parts that would fail quietly:
  * `<番號>-poster.jpg` is found for every part of the film, and a same-named
    picture still wins over it
  * the listing reports the cover's mtime, so a cover downloaded later gets
    a new card URL instead of waiting out the browser's month
  * a portrait card gets the sleeve's front only, under its own cache key; a
    picture that is already portrait is not cut
  * a placeholder, a tiny picture or a cut-off download is never saved, and
    an existing cover is never overwritten
  * the scan asks DMM as little as it can, and stops asking once DMM is down
  * nothing real is touched: folders, cache and DMM are all throwaway stubs

    python tests/test_dmmcover.py
"""
import io
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image                                  # noqa: E402

from app.core import dmm, dmmcover, media, photothumb  # noqa: E402
from app import main                                   # noqa: E402

FAIL = []


def check(ok, msg):
    print(("  ok   " if ok else "  FAIL ") + msg)
    if not ok:
        FAIL.append(msg)


def sleeve(size=(2184, 1468)) -> bytes:
    """Shaped like DMM's: back | spine | front, the front a lighter block on
    the right whose width is 0.703 of the height."""
    w, h = size
    im = Image.new("RGB", size, (40, 40, 60))
    front = round(h * photothumb.FRONT_ASPECT)
    im.paste((230, 200, 180), (w - front, 0, w, h))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def test_candidates(tmp: Path):
    print("\n封面檔名：同名圖片優先，再來 <stem>-poster、<番號>-poster")
    d = tmp / "lib"
    d.mkdir()
    part = d / "MIDE-844-1.mp4"
    part.write_bytes(b"x")
    (d / "MIDE-844-poster.jpg").write_bytes(sleeve((800, 538)))
    check(media.poster_for(part) == d / "MIDE-844-poster.jpg",
          "分段的 MIDE-844-1.mp4 找得到 MIDE-844-poster.jpg")
    (d / "MIDE-844-1-poster.jpg").write_bytes(b"y")
    check(media.poster_for(part) == d / "MIDE-844-1-poster.jpg",
          "自己的 <stem>-poster.jpg 優先於共用的番號封面")
    (d / "MIDE-844-1.jpg").write_bytes(b"z")
    check(media.poster_for(part) == d / "MIDE-844-1.jpg", "同名圖片照舊最優先")
    other = d / "隨便的影片.mp4"
    other.write_bytes(b"x")
    check(media.poster_for(other) is None, "沒有番號、沒有圖片：沒有封面")

    print("  —— 依卡片形狀：橫式先找 -fanart，直式先找 -poster")
    check(media.poster_for(part, landscape=True) == d / "MIDE-844-1.jpg",
          "橫式卡片沒有 fanart 時退回直式那幾種")
    (d / "MIDE-844-fanart.jpg").write_bytes(sleeve((800, 538)))
    check(media.poster_for(part, landscape=True) == d / "MIDE-844-fanart.jpg",
          "橫式卡片：番號-fanart.jpg 優先")
    check(media.poster_for(part) == d / "MIDE-844-1.jpg", "直式卡片不受 fanart 影響")
    lone = d / "SOLO-001.mp4"
    lone.write_bytes(b"x")
    (d / "SOLO-001-fanart.jpg").write_bytes(sleeve((800, 538)))
    check(media.poster_for(lone) == d / "SOLO-001-fanart.jpg",
          "直式卡片只有 fanart 時退回它（伺服器再切出正面）")

    posters = {}
    found = media.scan_videos_with_stat(d, posters)
    check(len(found) == 3, "影片照樣列出來")
    check(media.poster_stamp(part, posters) > 0
          and media.poster_stamp(other, posters) == 0,
          "同一趟走訪就知道誰有封面、封面的時間")
    check(media.scan_videos_with_stat(d) == found, "不傳 posters 時行為不變")


def test_front_crop(tmp: Path):
    print("\n直式卡片只取封套的正面")
    src = tmp / "ABC-123-poster.jpg"
    src.write_bytes(sleeve())
    cache = tmp / "cache"
    whole = photothumb.get(src, cache)
    front = photothumb.get(src, cache, front=True)
    with Image.open(whole) as a, Image.open(front) as b:
        wa, wb = a.size[0] / a.size[1], b.size[0] / b.size[1]
        light = b.convert("L").crop((0, 0, 4, b.size[1])).getextrema()[0]
    check(abs(wa - 2184 / 1468) < 0.02, f"橫式卡片：整張封套（{wa:.2f}）")
    check(abs(wb - photothumb.FRONT_ASPECT) < 0.02, f"直式卡片：正面的比例（{wb:.3f}）")
    check(light > 150, "切下來的是右邊的正面，不是封底")
    check(whole != front, "兩種各有自己的快取")
    # the formula before this change, written out: 48,320 cached thumbnails
    # are named by it and must stay hits
    import hashlib
    st = src.stat()
    key = hashlib.md5(f"{src}|{st.st_size}|{int(st.st_mtime)}|{photothumb.SHORT_EDGE}"
                      .encode()).hexdigest()
    check(whole.name == f"{key}.jpg", "沒裁切的那種鍵跟以前一樣，舊快取照樣命中")

    portrait = tmp / "p.jpg"
    Image.new("RGB", (1032, 1468), (200, 0, 0)).save(portrait)
    with Image.open(photothumb.get(portrait, cache, front=True)) as im:
        check(abs(im.size[0] / im.size[1] - 1032 / 1468) < 0.01,
              "本來就是直式的圖不裁")
    photothumb.purge(src, cache)
    check(not whole.exists() and not front.exists(), "刪檔前清快取：兩種都清掉")


class Resp:
    def __init__(self, body, url):
        self.body, self.url = body, url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def geturl(self):
        return self.url

    def read(self, n=-1):
        return self.body if n < 0 else self.body[:n]


def test_fetch_and_save(tmp: Path):
    print("\n下載：佔位圖、太小、截斷的都不存；已有的不覆蓋")
    real = dmmcover.urlopen
    try:
        dmmcover.urlopen = lambda req, timeout=0: Resp(sleeve(), req.full_url)
        check(len(dmmcover.fetch("https://x/abc00123pl.jpg")) > 1000, "正常的封套")
        dmmcover.urlopen = lambda req, timeout=0: Resp(
            sleeve(), "https://x/now_printing.jpg")
        bad = []
        for label, op in (
                ("佔位圖", lambda req, timeout=0: Resp(sleeve(), "https://x/now_printing.jpg")),
                ("太小", lambda req, timeout=0: Resp(sleeve((147, 200)), req.full_url)),
                ("截斷", lambda req, timeout=0: Resp(sleeve()[:5000], req.full_url))):
            dmmcover.urlopen = op
            try:
                dmmcover.fetch("https://x/abc00123pl.jpg")
                bad.append(label)
            except Exception:
                pass
        check(not bad, f"佔位圖、太小、截斷都被拒絕（漏掉：{bad}）")
    finally:
        dmmcover.urlopen = real

    print("  —— 正面：DMM 的正面圖夠大就用，太小就從封套切")
    real = dmmcover.urlopen
    try:
        big = Image.new("RGB", (1032, 1468), (9, 9, 9))
        buf = io.BytesIO()
        big.save(buf, "JPEG")
        dmmcover.urlopen = lambda req, timeout=0: Resp(buf.getvalue(), req.full_url)
        f = dmmcover.front(sleeve(), "https://x/ps.jpg")
        check(dmmcover._size(f) == (1032, 1468), "新片：直接用 DMM 的 1032x1468 正面")
        dmmcover.urlopen = lambda req, timeout=0: Resp(sleeve((147, 200)), req.full_url)
        w, h = dmmcover._size(dmmcover.front(sleeve((800, 538)), "https://x/ps.jpg"))
        check(abs(w / h - photothumb.FRONT_ASPECT) < 0.01,
              f"舊片：正面只有 147x200，改從封套切（{w}x{h}）")
    finally:
        dmmcover.urlopen = real

    print("  —— 寫入：缺什麼補什麼，已有的不覆蓋")
    a, b = tmp / "a", tmp / "b"
    a.mkdir()
    b.mkdir()
    va, vb = a / "ABC-123.mp4", b / "ABC-123 片名.mp4"
    va.write_bytes(b"x")
    vb.write_bytes(b"x")
    (b / "ABC-123-poster.jpg").write_bytes(dmmcover.front_from(sleeve()))
    todo = dmmcover.plan([va, vb], "ABC-123")
    check(todo[a] == {"fanart": True, "poster": True}
          and todo[b]["fanart"] and not todo[b]["poster"],
          "每個資料夾各自判斷缺哪一種")
    mine = (b / "ABC-123-poster.jpg").read_bytes()
    sl = sleeve()
    done = dmmcover.save(todo, "abc-123", sl, dmmcover.front_from(sl))
    check(sorted(p.name for p in done) ==
          ["ABC-123-fanart.jpg", "ABC-123-fanart.jpg", "ABC-123-poster.jpg"],
          f"番號大寫、兩種都存：{[str(p) for p in done]}")
    check((b / "ABC-123-poster.jpg").read_bytes() == mine, "已經有的 poster 不覆蓋")
    check(not any(tmp.rglob("*.part")), "沒有留下半成品")
    check(dmmcover.plan([va, vb], "ABC-123") == {}, "兩種都有了：不再列入")


def test_scan_cover(tmp: Path):
    print("\n片名查詢裡的封面：能不問 DMM 就不問，DMM 連不上就停")
    calls = []
    saved = (dmm.askable, dmm.remembered_cid, dmm.recently_missed, dmm.lookup,
             dmm.remember, dmm.package_images, dmmcover.fetch)
    try:
        dmm.askable = lambda v: True
        dmm.remembered_cid = lambda v: calls.append(("remembered", v)) or (
            "abc00123" if v == "ABC-123" else "")
        dmm.recently_missed = lambda v: False
        dmm.lookup = lambda v: calls.append(("lookup", v)) or {"cid": "new00001"}
        dmm.remember = lambda v, hit: calls.append(("remember", v))
        dmm.package_images = lambda cid: calls.append(("image", cid)) or (
            f"https://x/{cid}pl.jpg", "")
        dmmcover.fetch = lambda url: sleeve((800, 538))

        d = tmp / "lib"
        d.mkdir()
        v1 = d / "ABC-123.mp4"
        v1.write_bytes(b"x")
        tally = {"new": [], "had": 0, "none": [], "failed": []}
        err = main._fetch_cover("ABC-123", [v1], None, tally)
        check(err is None and tally["new"] == ["ABC-123"]
              and (d / "ABC-123-poster.jpg").is_file()
              and (d / "ABC-123-fanart.jpg").is_file(), "下載並存好兩種")
        check(("lookup", "ABC-123") not in calls,
              "之前查片名時存過 DMM 編號：不再查一次")

        calls.clear()
        main._fetch_cover("ABC-123", [v1], None, tally)
        check(tally["had"] == 1 and not calls, "兩種都有了：完全不問 DMM")

        v2 = d / "NEW-001.mp4"
        v2.write_bytes(b"x")
        main._fetch_cover("NEW-001", [v2], {"cid": "new00001"}, tally)
        check(("lookup", "NEW-001") not in calls and ("image", "new00001") in calls,
              "這次查片名剛拿到的編號直接用")

        def down(cid):
            raise RuntimeError("DMM unreachable")
        dmm.package_images = down
        v3 = d / "OFF-001.mp4"
        v3.write_bytes(b"x")
        err = main._fetch_cover("OFF-001", [v3], {"cid": "off00001"}, tally)
        check(err == "DMM unreachable" and tally["failed"][-1]["id"] == "OFF-001",
              "DMM 連不上：回報錯誤，讓這一輪不再問")
    finally:
        (dmm.askable, dmm.remembered_cid, dmm.recently_missed, dmm.lookup,
         dmm.remember, dmm.package_images, dmmcover.fetch) = saved


def portrait(size=(422, 600)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 120, 90)).save(buf, "JPEG")
    return buf.getvalue()


# Shaped like javdatabase's movie pages on 2026-10-28: the DMM sleeve under
# the film's real content id, its sample frames, and for Prestige films MGS's
# sample captures — whose folder names the MGS sleeve and front.
PAGE_DMM = ('<img src="https://pics.dmm.co.jp/digital/video/118onez00103/118onez00103pl.jpg">'
            '<a href="https://pics.dmm.co.jp/digital/video/118onez00103/118onez00103jp-1.jpg">'
            '<img src="https://www.javdatabase.com/covers/thumb/11/118onez00103ps.webp">')
PAGE_MGS = ('<img src="https://www.javdatabase.com/covers/thumb/pb/pb_e_abw-085.webp">'
            '<a href="https://image.mgstage.com/images/prestige/abw/085/cap_e_0_abw-085.jpg">'
            '<a href="https://image.mgstage.com/images/prestige/abw/085/cap_e_1_abw-085.jpg">')


def test_javdatabase_route(tmp: Path):
    print("\nDMM 目錄查不到的片：改用 javdatabase 頁面上的連結")
    u = dmmcover.javdatabase_urls(PAGE_DMM)
    check(u["sleeves"] == [
        "https://awsimgsrc.dmm.co.jp/pics_dig/digital/video/118onez00103/118onez00103pl.jpg",
        "https://pics.dmm.co.jp/digital/video/118onez00103/118onez00103pl.jpg"],
        "DMM 的真正編號（118onez00103）：先高清、再舊路徑")
    check("jp-1" not in str(u), "劇照不算封面")
    u = dmmcover.javdatabase_urls(PAGE_MGS)
    check(u["sleeves"] == ["https://image.mgstage.com/images/prestige/abw/085/pb_e_abw-085.jpg"]
          and u["fronts"] == ["https://image.mgstage.com/images/prestige/abw/085/pf_e_abw-085.jpg"],
          "MGS 的資料夾（prestige/abw/085）→ pb_e 封套、pf_e 正面")
    check(dmmcover.javdatabase_urls("") == {"sleeves": [], "fronts": []}, "沒有頁面就沒有候選")

    saved = (dmmcover.javdb._fetch, dmmcover.fetch)
    try:
        served = {}
        dmmcover.fetch = lambda url: served[url] if url in served else (_ for _ in ()).throw(
            ValueError("404"))
        dmmcover.javdb._fetch = lambda url: PAGE_DMM + PAGE_MGS

        aws = "https://awsimgsrc.dmm.co.jp/pics_dig/digital/video/118onez00103/118onez00103pl.jpg"
        old = "https://pics.dmm.co.jp/digital/video/118onez00103/118onez00103pl.jpg"
        pb = "https://image.mgstage.com/images/prestige/abw/085/pb_e_abw-085.jpg"
        pf = "https://image.mgstage.com/images/prestige/abw/085/pf_e_abw-085.jpg"
        served.update({aws: sleeve(), old: sleeve((800, 534)), pb: sleeve((840, 566)),
                       pf: portrait()})
        s, f = dmmcover.from_javdatabase("ONEZ-103")
        check(dmmcover._size(s) == (2184, 1468), "封套取最寬的（DMM 高清 2184）")
        check(dmmcover._size(f)[0] > 422, "正面：從 2184 封套裁出來的比 MGS 的 422 寬，用裁的")

        served.clear()
        served.update({pb: sleeve((840, 566)), pf: portrait()})
        s, f = dmmcover.from_javdatabase("ABW-085")
        check(dmmcover._size(s) == (840, 566) and dmmcover._size(f) == (422, 600),
              "只有 MGS：封套 840、正面用 MGS 自己的 422x600（比裁的 398 寬）")

        served.clear()
        served.update({aws: portrait((423, 600)), old: portrait((564, 800))})
        s, f = dmmcover.from_javdatabase("COM-001")
        check(s == b"" and dmmcover._size(f) == (564, 800),
              "COM-001 的「封套」其實是直的：只當正面，不當 fanart")

        d = tmp / "com"
        d.mkdir()
        v = d / "COM-001.mp4"
        v.write_bytes(b"x")
        wrote = dmmcover.save(dmmcover.plan([v], "COM-001"), "COM-001", s, f)
        check([p.name for p in wrote] == ["COM-001-poster.jpg"], "只有正面時只寫 poster")
    finally:
        dmmcover.javdb._fetch, dmmcover.fetch = saved

    print("  —— 片名查詢裡的接法")
    saved = (dmm.askable, dmm.remembered_cid, dmm.recently_missed, dmm.lookup,
             dmm.remember, dmm.package_images, dmmcover.from_javdatabase)
    try:
        asked = []
        dmm.askable = lambda v: True
        dmm.remembered_cid = lambda v: ""
        dmm.recently_missed = lambda v: False
        dmm.lookup = lambda v: asked.append(v) or None          # not in the catalogue
        dmm.remember = lambda v, hit: None
        dmm.package_images = lambda cid: ("", "")
        dmmcover.from_javdatabase = lambda vid: (sleeve((800, 534)), portrait())
        d = tmp / "lib"
        d.mkdir()
        v1 = d / "ONEZ-103.mp4"
        v1.write_bytes(b"x")
        tally = {"new": [], "had": 0, "none": [], "failed": []}
        main._fetch_cover("ONEZ-103", [v1], None, tally)
        check(tally["new"] == ["ONEZ-103"] and tally.get("javdatabase") == ["ONEZ-103"]
              and (d / "ONEZ-103-fanart.jpg").is_file(), "DMM 目錄沒有 → 經 javdatabase 存好兩種")

        v2 = d / "ONEZ-104.mp4"
        v2.write_bytes(b"x")
        asked.clear()
        main._fetch_cover("ONEZ-104", [v2], None, tally, ask_dmm=False)
        check(not asked and "ONEZ-104" in tally["new"],
              "DMM 這一輪連不上：不再問 DMM，但 javdatabase 這條照走")

        dmmcover.from_javdatabase = lambda vid: (b"", b"")
        v3 = d / "ARA-348.mp4"
        v3.write_bytes(b"x")
        main._fetch_cover("ARA-348", [v3], None, tally)
        check(tally["none"] == ["ARA-348"], "兩邊都沒有：列在「找不到」")
    finally:
        (dmm.askable, dmm.remembered_cid, dmm.recently_missed, dmm.lookup,
         dmm.remember, dmm.package_images, dmmcover.from_javdatabase) = saved


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for fn in (test_candidates, test_front_crop, test_fetch_and_save, test_scan_cover,
               test_javdatabase_route):
        d = Path(tempfile.mkdtemp())
        try:
            fn(d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
