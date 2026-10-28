"""Per-group display settings, the poster beside a video, and the folder cover.

The rules worth pinning down:

  * `poster_for` must never reach outside the video's own directory. A rule
    that also accepted `cover.jpg` would give every video in a folder the same
    card picture, which is worse than the frame grabs it replaced.
  * `folder_poster` faces the opposite situation — one folder, one card — so it
    *does* accept generic names. Its risk is a different one: with several
    candidates present the choice must not depend on directory order, or the
    home card changes picture between reloads.
  * `_clean_group_view` must keep only deviations from the default. That is
    what lets a settings file written before this feature existed load with no
    migration at all — and "the test ran on a fresh database so it never saw
    the missing migration" is a mistake this project has already made once.

Run:  python tests/test_groupview.py        (from backend/)
"""
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import media                            # noqa: E402
from app.main import (_clean_group_view,              # noqa: E402
                      GROUP_VIEW_DEFAULT, GROUP_RATIOS, GROUP_HOME_SHAPES)

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def _vid(p: Path):
    p.write_bytes(bytes(16))
    return p


def _img(p: Path):
    p.write_bytes(b"\xff\xd8\xff" + bytes(32))
    return p


# ── poster_for (beside a video) ──────────────────────────────────────────────

def test_finds_same_name(tmp: Path):
    print("\n同名、同目錄")
    v = _vid(tmp / "clip.mp4")
    want = _img(tmp / "clip.jpg")
    check(media.poster_for(v) == want, "clip.mp4 → clip.jpg")


def test_no_image(tmp: Path):
    print("\n沒有圖片就回 None（呼叫端退回抽幀）")
    v = _vid(tmp / "clip.mp4")
    check(media.poster_for(v) is None, "旁邊什麼都沒有")


def test_never_leaves_the_directory(tmp: Path):
    print("\n只看同目錄 —— 這是整條規則")
    sub = tmp / "sub"
    sub.mkdir()
    v = _vid(sub / "clip.mp4")
    _img(tmp / "clip.jpg")           # right name, parent directory
    check(media.poster_for(v) is None, "上層的同名圖片不算")
    _img(sub / "cover.jpg")          # same directory, wrong name
    check(media.poster_for(v) is None,
          "同目錄的 cover.jpg 不算 —— 否則整個資料夾的卡片會長一樣")


def test_extension_order_is_stable(tmp: Path):
    print("\n同時有好幾種副檔名時，每次都要挑同一個")
    v = _vid(tmp / "clip.mp4")
    _img(tmp / "clip.png")
    _img(tmp / "clip.webp")
    jpg = _img(tmp / "clip.jpg")
    picked = {media.poster_for(v) for _ in range(5)}
    check(picked == {jpg}, "五次都挑到 clip.jpg，卡片不會每次重整換一張圖")


def test_empty_file_is_not_a_poster(tmp: Path):
    print("\n0 位元組的圖片不算")
    v = _vid(tmp / "clip.mp4")
    (tmp / "clip.jpg").write_bytes(b"")
    (tmp / "clip.png").write_bytes(b"")
    check(media.poster_for(v) is None,
          "空檔會讓 Pillow 失敗，當作沒有比較誠實")


def test_dotted_name(tmp: Path):
    print("\n檔名裡本來就有點的影片")
    v = _vid(tmp / "a.b.c [1080p].mp4")
    want = _img(tmp / "a.b.c [1080p].jpg")
    check(media.poster_for(v) == want, "只換最後一段副檔名")


# ── folder_poster (the character folder's cover) ─────────────────────────────

def test_folder_poster_names(tmp: Path):
    print("\n四種檔名都認")
    # `<資料夾名>_poster` is the convention the user intends; the other three
    # are what the library already contains (folder.* in four folders,
    # poster.* in one). Accepting only the first would ship a feature that
    # matches nothing on day one.
    for i, n in enumerate(["星川優子_poster", "poster", "folder", "cover"]):
        d = tmp / f"c{i}"
        d.mkdir()
        _img(d / f"{n}.jpg")
        got = media.folder_poster(d, "星川優子")
        check(got is not None and got.name == f"{n}.jpg", f"{n}.jpg")


def test_folder_poster_order_is_fixed(tmp: Path):
    print("\n同時有好幾種時，順序由 POSTER_NAMES 決定，不是目錄列舉順序")
    d = tmp / "星川優子"
    d.mkdir()
    # written in the reverse of the wanted priority
    for n in ("cover.jpg", "folder.jpg", "poster.jpg", "星川優子_poster.jpg"):
        _img(d / n)
    picked = {media.folder_poster(d, "星川優子").name for _ in range(5)}
    check(picked == {"星川優子_poster.jpg"},
          "五次都挑到 <資料夾名>_poster —— 使用者的命名優先，而且穩定")
    (d / "星川優子_poster.jpg").unlink()
    check(media.folder_poster(d, "星川優子").name == "poster.jpg", "接著是 poster")
    (d / "poster.jpg").unlink()
    check(media.folder_poster(d, "星川優子").name == "folder.jpg", "再來是 folder")


def test_folder_poster_extensions(tmp: Path):
    print("\n圖片副檔名的順序也固定")
    d = tmp / "角色"
    d.mkdir()
    for ext in (".webp", ".png", ".jpg"):
        _img(d / f"folder{ext}")
    check(media.folder_poster(d, "角色").name == "folder.jpg", "jpg 優先")


def test_folder_poster_first_level_only(tmp: Path):
    print("\n只看第一層")
    d = tmp / "角色"
    (d / "sub").mkdir(parents=True)
    _img(d / "sub" / "folder.jpg")
    check(media.folder_poster(d, "角色") is None, "子資料夾裡的不算")
    _img(tmp / "folder.jpg")
    check(media.folder_poster(d, "角色") is None, "上層的也不算")


def test_folder_poster_none(tmp: Path):
    print("\n沒有就回 None（呼叫端顯示人像佔位圖）")
    d = tmp / "角色"
    d.mkdir()
    _img(d / "隨便一張照片.jpg")
    check(media.folder_poster(d, "角色") is None, "名字不對的圖片不會被當封面")
    check(media.folder_poster(tmp / "不存在", "x") is None, "資料夾不存在也不會爆")


def test_folder_poster_wrong_ext(tmp: Path):
    print("\n副檔名要是圖片")
    d = tmp / "角色"
    d.mkdir()
    (d / "folder.txt").write_text("x", encoding="utf-8")
    (d / "poster.mp4").write_bytes(bytes(8))
    check(media.folder_poster(d, "角色") is None, "folder.txt / poster.mp4 都不算")


# ── _clean_group_view ────────────────────────────────────────────────────────

def test_only_deviations_stored(_tmp):
    print("\n只存偏離預設的部分")
    order = ["JAV", "着エロ"]
    out = _clean_group_view({
        "JAV": dict(GROUP_VIEW_DEFAULT),
        "着エロ": {**GROUP_VIEW_DEFAULT, "ratio": "4:5"},
    }, order)
    check("JAV" not in out, "整組都是預設 → 根本不存")
    check(out["着エロ"] == {"ratio": "4:5"}, "只留改過的那一欄")


def test_unknown_group_dropped(_tmp):
    print("\n不在 order 裡的分類要丟掉")
    out = _clean_group_view({"沒這個": {"ratio": "4:5"}}, ["JAV"])
    check(out == {}, "刪掉的分類不會留下孤兒設定")


def test_home_shape(_tmp):
    print("\n主頁卡片形狀")
    check(GROUP_VIEW_DEFAULT["home"] == "circle", "預設是圓形 —— 現況不變")
    for s in GROUP_HOME_SHAPES:
        got = _clean_group_view({"JAV": {"home": s}}, ["JAV"])
        want = {} if s == "circle" else {"JAV": {"home": s}}
        check(got == want, f"{s} 是合法的")
    check(_clean_group_view({"JAV": {"home": "oval"}}, ["JAV"]) == {},
          "認不得的形狀退回圓形，不是餵給 CSS 的 aspect-ratio")


def test_albums_setting(_tmp):
    print("\n個人資料的相簿檢視")
    check(GROUP_VIEW_DEFAULT["albums"] is False,
          "預設關 —— 上線當天每個分類都維持平鋪")
    check(_clean_group_view({"S": {"albums": True}}, ["S"]) == {"S": {"albums": True}},
          "開了才存")
    check(_clean_group_view({"S": {"albums": False}}, ["S"]) == {},
          "關著等於預設，不存")
    # the sidebar's existing settings must survive the new key being added
    got = _clean_group_view({"裏番": {"profile": False, "ratio": "4:5",
                                     "poster": True}}, ["裏番"])
    check(got == {"裏番": {"profile": False, "ratio": "4:5", "poster": True}},
          "現有的設定原封不動，不會多出 albums")


def test_bad_ratio_refused(_tmp):
    print("\n認不得的比例不能存進去")
    # It ends up in CSS `aspect-ratio`, where a junk value is silently dropped
    # and the card collapses — no error anywhere.
    out = _clean_group_view({"JAV": {"ratio": "3:7"}}, ["JAV"])
    check(out == {}, "3:7 退回 16:9，而 16:9 是預設所以整項消失")
    for r in GROUP_RATIOS:
        got = _clean_group_view({"JAV": {"ratio": r}}, ["JAV"])
        want = {} if r == GROUP_VIEW_DEFAULT["ratio"] else {"JAV": {"ratio": r}}
        check(got == want, f"{r} 是合法的")


def test_both_tabs_off_refused(_tmp):
    print("\n兩個分頁不能同時關掉（端點不經過對話框也叫得到）")
    out = _clean_group_view({"JAV": {"videos": False, "profile": False}},
                            ["JAV"])
    check(out["JAV"].get("videos") is not False,
          "至少留一個，否則點進角色是一片空白且沒有回頭路")


def test_junk_is_ignored(_tmp):
    print("\n亂七八糟的輸入不會爆")
    check(_clean_group_view({"JAV": "not a dict"}, ["JAV"]) == {}, "字串")
    check(_clean_group_view({}, ["JAV"]) == {}, "空的")
    check(_clean_group_view({"JAV": {"nonsense": 1}}, ["JAV"]) == {},
          "沒聽過的欄位直接不理")


def test_absent_key_means_default(_tmp):
    print("\n這功能之前寫的設定檔沒有這個鍵 —— 不需要遷移")
    out = _clean_group_view({}, ["JAV", "WEB CAM"])
    check(out == {}, "空的就是空的，前端讀出來全是預設")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for fn in (test_finds_same_name, test_no_image,
               test_never_leaves_the_directory, test_extension_order_is_stable,
               test_empty_file_is_not_a_poster, test_dotted_name,
               test_folder_poster_names, test_folder_poster_order_is_fixed,
               test_folder_poster_extensions,
               test_folder_poster_first_level_only, test_folder_poster_none,
               test_folder_poster_wrong_ext,
               test_only_deviations_stored, test_unknown_group_dropped,
               test_home_shape, test_albums_setting, test_bad_ratio_refused,
               test_both_tabs_off_refused, test_junk_is_ignored,
               test_absent_key_means_default):
        d = Path(tempfile.mkdtemp())
        try:
            fn(d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
