"""Gfriends headshots as avatar choices in 角色查詢 → 個資.

The parts that would fail quietly:
  * every name the person goes by is looked up and merged, a two-person
    folder is taken apart, and a weak name (`MIRAI`) never preselects
  * the index is fetched at most daily, and a failed refresh keeps the old one
  * only pictures in the index are fetched, and a truncated one is refused
  * the copy lands in `.vam/`, which the photo grid, the duplicate scan and
    the person scan all skip
  * an avatar set since the scan is never replaced
  * no real path is touched: index, cache and folders are all throwaway, and
    the network is a stub

    python tests/test_gfriends.py
"""
import io
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image                                 # noqa: E402

from app.core import gfriends, idolprofile, imgdedup, media  # noqa: E402
# Imported here, before any test redirects a path: importing main sets
# `gfriends.INDEX_PATH` to the real data directory. Imported inside a test it
# did exactly that after setup() had pointed it away — and the stub index
# landed in the real data/gfriends-filetree.json.
from app import main                                  # noqa: E402

FAIL = []


def check(ok, msg):
    print(("  ok   " if ok else "  FAIL ") + msg)
    if not ok:
        FAIL.append(msg)


# shaped like the real Filetree.json: display name → stored name (with a
# cache-busting query), folders prefixed by their source priority
TREE = {"Information": {}, "Content": {
    "9-Javrave": {"三浦真央.jpg": "三浦真央.jpg?t=1"},
    "0-Hand-Storage": {"三浦真央.jpg": "三浦真央.jpg?t=2"},
    "3-MUTEKI": {"三浦真央.jpg": "AI-Fix-三浦真央.jpg?t=3",
                 "Mao Miura.jpg": "Mao Miura.jpg"},
    "7-Moodyz": {"夏川くるみ.jpg": "夏川くるみ.jpg", "心愛りな.jpg": "心愛りな.jpg",
                 "奏みらい.jpg": "奏みらい.jpg"},
    "z-DMM(骑)": {"MIRAI.jpg": "MIRAI.jpg"},
}}


def jpeg(size=(400, 600)) -> bytes:
    g = Image.linear_gradient("L").resize(size)
    buf = io.BytesIO()
    Image.merge("RGB", (g, g.rotate(90).resize(size), g)).save(buf, "JPEG")
    return buf.getvalue()


class Net:
    """Stands in for GitHub: counts requests, can be made to fail."""
    def __init__(self):
        self.calls, self.down = [], False

    def __call__(self, url, limit):
        self.calls.append(url)
        if self.down:
            raise OSError("network is down")
        if url.endswith("Filetree.json"):
            return json.dumps(TREE, ensure_ascii=False).encode()
        return jpeg()


REAL_DATA = Path(__file__).resolve().parents[2] / "data"


def setup(tmp: Path):
    net = Net()
    gfriends._get = net
    gfriends.INDEX_PATH = tmp / "data" / "gfriends-filetree.json"
    assert REAL_DATA not in gfriends.INDEX_PATH.parents
    gfriends._mem.update({"mtime": None, "index": {}, "pairs": set()})
    return net


def test_lookup(tmp: Path):
    print("\n查名字：整串名字都查、合併、拆開雙人資料夾")
    setup(tmp)
    idx = gfriends.index()
    names = gfriends.name_chain(["三浦真央"], ["Mao Miura"])
    c = gfriends.candidates(names, idx)
    check([x["cat"] for x in c] == ["0-Hand-Storage", "3-MUTEKI", "3-MUTEKI", "9-Javrave"],
          f"依來源優先順序排，別名查到的也併進來：{[x['cat'] for x in c]}")
    check(c[0]["file"] == "三浦真央.jpg" and c[0]["source"] == "Hand-Storage",
          "檔名去掉 ?t= 尾巴，來源名去掉排序前綴")
    check({x["by"] for x in c} == {"三浦真央", "Mao Miura"}, "每張記得是哪個名字查到的")

    pair = gfriends.name_chain(["夏川くるみ／心愛りな"])
    check("夏川くるみ" in pair and "心愛りな" in pair, f"雙人資料夾拆開查：{pair}")
    check(len(gfriends.candidates(pair, idx)) == 2, "兩個人都查得到")
    check(gfriends.name_chain(["Reina Kiritani (桐谷れいな)"])[-2:] == ["Reina Kiritani", "桐谷れいな"],
          "「名字 (另一個名字)」也拆開")


def test_weak_name(tmp: Path):
    print("\n太短或太通用的名字不預選（MIRAI 查到的是別人）")
    setup(tmp)
    idx = gfriends.index()
    c = gfriends.candidates(gfriends.name_chain(["奏みらい"], ["MIRAI"]), idx)
    check([x["by"] for x in c] == ["奏みらい", "MIRAI"], "兩個名字都列出來")
    check(c[1]["weak"] and not c[0]["weak"], "MIRAI 標成 weak")
    check(gfriends.preselect(c) == 0, "預選的是本名查到的那張")
    only_weak = gfriends.candidates(["MIRAI"], idx)
    check(gfriends.preselect(only_weak) is None, "只有 weak 的時候不預選任何一張")
    check(not gfriends.weak("Mao Miura") and not gfriends.weak("森田花梨")
          and gfriends.weak("あい"), "有空格的英文名、三個字以上的日文名不算 weak")


def test_index_refresh(tmp: Path):
    print("\n索引一天最多抓一次，抓不到就沿用舊的")
    net = setup(tmp)
    gfriends.index()
    gfriends.index()
    check(sum(u.endswith("Filetree.json") for u in net.calls) == 1, "一天內第二次不再下載")
    old = time.time() - gfriends.MAX_AGE - 10
    os.utime(gfriends.INDEX_PATH, (old, old))
    net.down = True
    idx = gfriends.index()
    check(len(net.calls) == 2 and "三浦真央" in idx, "過期了會再抓；失敗時舊索引照用")
    check(gfriends.index(refresh=False) is idx, "只為了驗證名字時不碰網路")


def test_image_and_install(tmp: Path):
    print("\n下載、快取、存進角色資料夾的 .vam/")
    net = setup(tmp)
    cache = tmp / "cache"
    p = gfriends.image("3-MUTEKI", "Mao Miura.jpg", cache)
    gfriends.image("3-MUTEKI", "Mao Miura.jpg", cache)
    check(sum("Content/" in u for u in net.calls) == 1, "同一張只下載一次")
    check("Content/3-MUTEKI/Mao%20Miura.jpg" in net.calls[-1], "網址有正確編碼")
    check(p.stat().st_size > 0, "快取裡有檔案")

    gfriends.index()
    check(gfriends.known("3-MUTEKI", "Mao Miura.jpg"), "索引裡的認得")
    check(not gfriends.known("3-MUTEKI", "../../etc/passwd"), "索引裡沒有的一律不抓")

    gfriends._get = lambda url, limit: jpeg()[:2000]
    try:
        gfriends.image("7-Moodyz", "心愛りな.jpg", cache)
        check(False, "截斷的圖要被拒絕")
    except Exception:
        check(not any(cache.rglob("*.part")), "截斷的圖被拒絕，沒有留下半成品")
    gfriends._get = net

    folder = tmp / "lib" / "三浦真央"
    folder.mkdir(parents=True)
    rel = gfriends.install(folder, "3-MUTEKI", "Mao Miura.jpg", cache)
    check(rel.startswith(".vam/avatar-gfriends-") and rel.endswith(".jpg"),
          f"存成資料夾內的相對路徑：{rel}")
    check((folder / rel).read_bytes() == p.read_bytes(), "內容就是快取那張")
    mine = folder / ".vam" / "notes.txt"
    mine.write_text("x")
    rel2 = gfriends.install(folder, "0-Hand-Storage", "三浦真央.jpg", cache)
    # the avatar URL carries only the path, and the browser keeps images for
    # a month — a new picture under the old name would keep the old face
    check(rel2 != rel, "換一張圖就換一個檔名，瀏覽器快取不會沿用舊的")
    check(not (folder / rel).exists() and (folder / rel2).exists(),
          "之前從圖庫放的那張刪掉")
    check(mine.exists(), ".vam 裡不是我們放的檔案不動")


def test_vam_dir_is_skipped(tmp: Path):
    print("\n.vam/ 不出現在照片格子、圖片去重、相似排序")
    folder = tmp / "lib" / "某人"
    (folder / ".vam").mkdir(parents=True)
    (folder / "set1").mkdir()
    for p in (folder / "a.jpg", folder / "set1" / "b.jpg",
              folder / ".vam" / "avatar-gfriends.jpg"):
        p.write_bytes(jpeg((50, 50)))
    names = lambda ps: sorted(Path(p).name for p in ps)
    check(names(media.scan_photos(folder)) == ["a.jpg", "b.jpg"], "scan_photos 不含 .vam")
    check(names(r["path"] for r in media.scan_profile_albums(folder)) == ["a.jpg", "b.jpg"],
          "個人資料頁（含相簿檢視、相似排序）不含 .vam")
    check(names(p for p, _s, _m in imgdedup.scan_images(folder)) == ["a.jpg", "b.jpg"],
          "圖片去重不含 .vam")
    check(media.in_vam_dir(folder / ".vam" / "x.jpg", folder)
          and not media.in_vam_dir(folder / "a.jpg", folder)
          and not media.in_vam_dir(folder / ".vam.jpg", folder),
          "in_vam_dir 只認子資料夾，不認檔名")


def test_proposal(tmp: Path):
    print("\n個資提案：只給沒有大頭照的；未判定時不用投票第一名的名字查")
    setup(tmp)
    saved = (idolprofile.vote_performer, idolprofile.fetch_profile,
             idolprofile.javdb.idol_romaji)
    try:
        idolprofile.fetch_profile = lambda slug: {"height": "160"}
        idolprofile.javdb.idol_romaji = lambda slug: "Mao Miura"

        a = tmp / "lib" / "三浦真央"
        a.mkdir(parents=True)
        idolprofile.vote_performer = lambda f, n, al: {
            "ok": True, "slug": "mao-miura", "name": "三浦真央"}
        row = idolprofile.build_proposal("三浦真央", [a])
        check(row.get("avatar") and row["avatar"]["pick"] == 0,
              "沒有大頭照：附上候選，預選第一張")
        check(any(c["by"] == "Mao Miura" for c in row["avatar"]["choices"]),
              "羅馬拼音（網站給的）也拿去查")

        media.save_person_info(a, {"profile_photo": "a.jpg",
                                   "profile": {"height": "158"}})
        row = idolprofile.build_proposal("三浦真央", [a])
        check("avatar" not in row, "已經有大頭照：不列候選")
        h = [f for f in row["fields"] if f["key"] == "height"][0]
        check(h["skip"] and not h["replace"], "已有的欄位跳過")

        print("  —— 勾選「也查詢已有的個資與大頭照」")
        row = idolprofile.build_proposal("三浦真央", [a], recheck=True)
        av = row.get("avatar") or {}
        check(av.get("choices") and av["pick"] is None,
              "已有大頭照：列出候選但不預選")
        check(av.get("current") == "a.jpg" and av["current_path"] == str(a / "a.jpg"),
              "附上目前那張，畫面拿來對照")
        h = [f for f in row["fields"] if f["key"] == "height"][0]
        check(h["replace"] and not h["skip"] and h["current"] == "158",
              "值不同的欄位列成「目前 → 新值」")
        media.save_person_info(a, {"profile_photo": "a.jpg",
                                   "profile": {"height": "160"}})
        row = idolprofile.build_proposal("三浦真央", [a], recheck=True)
        h = [f for f in row["fields"] if f["key"] == "height"][0]
        check(h["skip"] and not h["replace"], "值一樣的照樣跳過，不列成取代")

        b = tmp / "lib" / "未知的人"
        b.mkdir(parents=True)
        idolprofile.vote_performer = lambda f, n, al: {
            "ok": False, "name": "三浦真央", "reason": "共演太多"}
        row = idolprofile.build_proposal("未知的人", [b])
        check("avatar" not in row and not row["has_changes"],
              "未判定的投票第一名可能是共演者，不拿它的名字查")
        media.save_person_info(b, {"aliases": ["心愛りな"]})
        row = idolprofile.build_proposal("未知的人", [b])
        check(row.get("avatar") and row["has_changes"],
              "但資料夾自己的別名照查，查到就可以寫入")
    finally:
        (idolprofile.vote_performer, idolprofile.fetch_profile,
         idolprofile.javdb.idol_romaji) = saved


def test_field_replacement(tmp: Path):
    print("\n已有的欄位只有勾了才取代")
    folder = tmp / "lib" / "某人"
    folder.mkdir(parents=True)
    media.save_person_info(folder, {"profile": {"height": "158", "cup": "C"}})
    row = {"name": "某人", "paths": [str(folder)], "vote": {"slug": "x"},
           "fields": [
               {"key": "height", "value": "160", "current": "158",
                "skip": False, "replace": True},             # not ticked
               {"key": "cup", "value": "D", "current": "C",
                "skip": False, "replace": True, "approved": True},
               {"key": "dob", "value": "1993-08-16", "current": None,
                "skip": False, "replace": False}]}
    done = idolprofile.apply_proposal(row)
    prof = media.load_person_info(folder)["profile"]
    check(prof["height"] == "158", "沒勾的取代不寫（保持 158）")
    check(prof["cup"] == "D", "勾了的取代寫入（C → D）")
    check(prof["dob"] == "1993-08-16", "原本空的欄位照常補上")
    check(sorted(done["fields"]) == ["cup", "dob"], f"回報寫了哪些：{done['fields']}")


def test_apply(tmp: Path):
    print("\n寫入：設定大頭照；這段時間裡已經有了就不覆蓋")
    setup(tmp)
    saved = (main.CACHE_DIR, main._files_changed)
    changed = []
    try:
        main.CACHE_DIR = tmp / "cache"
        main._files_changed = lambda: changed.append(1)
        gfriends.index()
        folder = tmp / "lib" / "七瀬"
        folder.mkdir(parents=True)
        media.save_person_info(folder, {"aliases": ["x"], "profile": {"height": "150"}})
        rel = main._install_gfriends_avatar(folder, {"cat": "7-Moodyz", "file": "心愛りな.jpg"})
        info = media.load_person_info(folder)
        check(rel and info["profile_photo"] == rel and (folder / rel).is_file(),
              "大頭照指向 .vam 裡那張")
        check(info["aliases"] == ["x"] and info["profile"] == {"height": "150"},
              "person_info 其他欄位原封不動")
        check(changed, "通知衍生快取（角色統計、搜尋索引）")

        again = main._install_gfriends_avatar(folder, {"cat": "3-MUTEKI",
                                                       "file": "Mao Miura.jpg"})
        check(again is None and media.load_person_info(folder)["profile_photo"] == rel,
              "已經有大頭照就不換")

        # a recheck: the user chose to replace the avatar the scan showed
        (folder / "mine.jpg").write_bytes(jpeg((50, 50)))
        info = media.load_person_info(folder)
        info["profile_photo"] = "mine.jpg"
        media.save_person_info(folder, info)
        stale = main._install_gfriends_avatar(
            folder, {"cat": "3-MUTEKI", "file": "Mao Miura.jpg", "replaces": rel})
        check(stale is None and media.load_person_info(folder)["profile_photo"] == "mine.jpg",
              "查詢時看到的大頭照之後被改過：不取代")
        new = main._install_gfriends_avatar(
            folder, {"cat": "3-MUTEKI", "file": "Mao Miura.jpg", "replaces": "mine.jpg"})
        check(new and media.load_person_info(folder)["profile_photo"] == new,
              "還是查詢時那張：取代成圖庫的")
        check((folder / "mine.jpg").exists(), "原本的大頭照檔案不刪，只是改指向新的")
        try:
            main._install_gfriends_avatar(tmp / "lib" / "別人",
                                          {"cat": "x", "file": "../../a.jpg"})
            check(False, "索引裡沒有的要拒絕")
        except ValueError:
            check(True, "索引裡沒有的要拒絕")
    finally:
        main.CACHE_DIR, main._files_changed = saved


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    real = (gfriends._get, gfriends.INDEX_PATH)
    for fn in (test_lookup, test_weak_name, test_index_refresh, test_image_and_install,
               test_vam_dir_is_skipped, test_proposal, test_field_replacement,
               test_apply):
        d = Path(tempfile.mkdtemp())
        try:
            fn(d)
        finally:
            gfriends._get, gfriends.INDEX_PATH = real
            shutil.rmtree(d, ignore_errors=True)
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
