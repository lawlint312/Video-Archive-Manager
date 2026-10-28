"""Splitting `person_info.json`'s tag list into links and real tags.

Why this exists at all: a survey of the whole library found the field was not
holding tags. 627 characters, 27 of them tagged, **44 distinct tags used 44
times** — not one ever reused — and 39 of those 44 were URLs. It was a list of
social-media profiles wearing the wrong name.

The user chose to split on read rather than migrate, which is the safe call:
`person_info.json` lives on the NAS where git cannot protect it. That decision
is exactly what makes these tests necessary — the stored shape never changes,
so the split and the join have to be exact inverses or a save quietly loses
something the user typed.

Run:  python tests/test_tags.py        (from backend/)
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import media                              # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


# Real entries from the library, not invented ones.
REAL = [
    "https://www.instagram.com/sample_wifey/",
    "OnlyFans",
    "https://x.com/lina_sample1",
    "中出",
    "https://zt.stripchat.com/example_model",
    "Urabukkake",
]


def test_endpoints_agree():
    """GET and POST must return person_info in the same shape.

    This is the bug that shipped: the GET split `tags`/`links`, the POST
    returned the list as stored. The editor assigns a save response straight
    onto its state, so a link you had just saved reappeared as a tag and the
    links row went empty — while the file on disk was correct the whole time.
    A reload "fixed" it, which is what made it look like an intermittent bug
    rather than a wrong reply.

    So the assertion is not "the split works" (the unit tests above cover
    that). It is "the two endpoints agree", because that is the property that
    was actually violated.
    """
    print("\nHTTP：讀和寫回傳的形狀必須一致")
    import tempfile as tf
    from fastapi.testclient import TestClient        # noqa: E402
    from app import auth, main                       # noqa: E402

    with tf.TemporaryDirectory() as td:
        folder = Path(td) / "高田さち子"
        folder.mkdir()
        real = main.is_allowed
        main.is_allowed = lambda p, u: True
        editor = {"id": 1, "role": "editor", "username": "e", "can_edit": True}
        main.app.dependency_overrides[auth.current_user] = lambda: editor
        main.app.dependency_overrides[auth.require_editor] = lambda: editor
        try:
            c = TestClient(main.app)
            url = f"/api/profile?folder={folder}"
            link = "https://www.instagram.com/ts_takasachi/"

            saved = c.post(url, json={"aliases": [], "tags": ["JAV"],
                                      "links": [link], "notes": ""}).json()["info"]
            loaded = c.get(url).json()["info"]

            check(saved.get("links") == [link],
                  f"存檔的回應把連結放在 links（拿到 {saved.get('links')}）")
            check(saved.get("tags") == ["JAV"],
                  f"而且沒有把它留在 tags（拿到 {saved.get('tags')}）")
            check(saved["tags"] == loaded["tags"] and
                  saved["links"] == loaded["links"],
                  "存檔的回應和重新讀取完全一致 —— 這是原本壞掉的那條性質")
            check(json.loads((folder / "person_info.json").read_text(
                      encoding="utf-8"))["tags"] == ["JAV", link],
                  "磁碟上仍然是一個合併的清單，沒有多出 links 欄位")
        finally:
            main.is_allowed = real
            main.app.dependency_overrides.clear()


def main():
    print("拆開")
    tags, links = media.split_tags(REAL)
    check(links == ["https://www.instagram.com/sample_wifey/",
                    "https://x.com/lina_sample1",
                    "https://zt.stripchat.com/example_model"],
          "網址進連結，順序不變")
    check(tags == ["OnlyFans", "中出", "Urabukkake"],
          "其餘留在標籤 —— 包含 OnlyFans：它是站名但不是網址，"
          "程式沒有立場替使用者猜")

    print("\n來回一趟不能掉東西")
    check(sorted(media.join_tags(tags, links)) == sorted(REAL),
          "拆開再合回來，內容完全相同")
    again = media.split_tags(media.join_tags(tags, links))
    check(again == (tags, links), "第二次拆的結果跟第一次一樣（穩定）")

    print("\n認得的網址形式")
    for u in ["http://example.com/x", "https://example.com",
              "HTTPS://EXAMPLE.COM/x", "  https://example.com/x  "]:
        t, l = media.split_tags([u])
        check(len(l) == 1, f"{u.strip()[:40]} → 連結")
    for t in ["中出", "OnlyFans", "IG", "M", "httpsomething", "www.example.com"]:
        plain, l = media.split_tags([t])
        check(len(plain) == 1 and not l,
              f"「{t}」→ 標籤（www.example.com 沒有協定，貼上去也開不了）")

    print("\n空白與雜訊")
    tags, links = media.split_tags(["", "  ", None, "a", "  b  "])
    # `str(None)` is "None" — without an explicit check, a null in a
    # hand-edited person_info.json becomes a tag called "None", and then it
    # shows up in the autocomplete for every other character.
    check(tags == ["a", "b"] and not links,
          f"空值丟掉、前後空白修掉（拿到 {tags}）")
    check(media.split_tags(None) == ([], []), "None 不會爆")
    check(media.join_tags(None, None) == [], "合併也一樣")

    print("\n順序：標籤在前、連結在後")
    check(media.join_tags(["b", "a"], ["https://z", "https://y"]) ==
          ["b", "a", "https://z", "https://y"],
          "各自的順序保留，只是分成兩段 —— 這是儲存格式唯一的改變，"
          "而且只在使用者本來就要存檔時發生")

    print("\n真的寫進檔案再讀回來")
    with tempfile.TemporaryDirectory() as td:
        folder = Path(td)
        media.save_person_info(folder, {"aliases": [], "tags": REAL,
                                        "notes": ""})
        info = media.load_person_info(folder)
        check(info["tags"] == REAL,
              "load_person_info 不做拆分 —— 拆分只發生在端點，"
              "所以整合與 JAV個資查詢 看到的形狀沒有改變")
        t2, l2 = media.split_tags(info["tags"])
        check((t2, l2) == (tags if False else media.split_tags(REAL)),
              "端點拆出來的跟直接拆一樣")

    test_endpoints_agree()

    print()
    if FAIL:
        print(f"{len(FAIL)} FAILED")
        for m in FAIL:
            print("  - " + m)
        sys.exit(1)
    print("all good")


if __name__ == "__main__":
    main()
