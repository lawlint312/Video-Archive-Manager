"""Watched / favourite marks: the parts that fail silently.

This feature is easy to write and easy to get subtly wrong, because every way
it breaks is quiet. Nothing throws, nothing is logged, and you only notice
weeks later that a mark you were relying on is gone -- or, worse, that a mark
you never made is there.

So the assertions here are about the four silent failures:

1. **A moved file forgets its marks.** The key is a path (most of this library
   has no catalogue number to key on), and the single most likely moment for a
   video to already carry marks is archiving it out of the inbox -- you watched
   it, which is how you decided where it belonged.
2. **A new file inherits a deleted one's marks.** Same path, different film.
3. **One user sees another's marks.** They are per-account, and the whole point
   of that is that they do not leak.
4. **Un-marking leaves a row behind.** `flags_for` loads the entire table on
   every listing, so rows that say nothing are pure growth.

Run:  python tests/test_videoflags.py      (from backend/)
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db                                      # noqa: E402
from app.core import videoflags                         # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


ALICE, BOB = 1, 2


def fresh(tmp: Path):
    """Point the whole module at a throwaway database.

    `db.connect()` looks `DB_PATH` up as a module global at call time, so
    rebinding it here really does redirect every query. (That is not automatic:
    a default argument would have been bound at import and this would silently
    have written to the user's real database -- which is exactly how the dedup
    report got overwritten once.)
    """
    db.DB_PATH = tmp / "test.db"
    db.init_db()


def test_over_http(tmp: Path):
    """The endpoints as the browser actually calls them.

    Worth its own pass because two of the decisions here only exist at the HTTP
    layer and neither shows up in the module: that `/api/videos` carries the
    marks with every row (so a listing costs one query, not one per card), and
    that setting a mark is *not* gated on the editor role (a viewer keeps their
    own record, and the phone hides the editing surface entirely).
    """
    print("\nHTTP：清單帶著標記，設定不需要編輯權限")
    from fastapi.testclient import TestClient        # noqa: E402
    from app import auth, main                       # noqa: E402

    lib = tmp / "http_lib"
    lib.mkdir()
    for i in range(3):
        (lib / f"v{i}.mp4").write_bytes(b"x" * (1000 + i))

    real_allowed = main.is_allowed
    main.is_allowed = lambda p, u: True
    # A viewer, deliberately: `require_editor` would reject this account, so if
    # anyone re-gates these endpoints the two POSTs below turn into 403s.
    viewer = {"id": 7, "role": "viewer", "username": "v", "can_edit": False}
    # Deleting needs the "delete" permission, which require_perm checks off
    # current_user -- so the override has to answer per endpoint: the reader
    # sets marks, the deleter deletes. `state` is what the steps below switch.
    from fastapi import Request
    state = {"reader": viewer, "deleter": None}
    _DELETING = ("/api/media/delete", "/api/pairs/resolve")

    def _who(request: Request):
        if request.url.path in _DELETING and state["deleter"]:
            return state["deleter"]
        return state["reader"]
    main.app.dependency_overrides[auth.current_user] = _who
    # Deleting needs an editor, so the two roles are separate here on purpose:
    # the favourite below is made by the viewer and the delete attempted by the
    # editor, which is exactly the cross-account case the guard has to cover.
    # A file one account cares about is gone for everyone once it is unlinked.
    editor = {"id": 8, "role": "editor", "username": "e", "can_edit": True}
    state["deleter"] = editor
    try:
        c = TestClient(main.app)
        target = str(lib / "v1.mp4")

        r = c.post("/api/flags/fav", json={"path": target, "on": True})
        check(r.status_code == 200, f"viewer 也能加最愛：{r.status_code}")
        r = c.post("/api/flags/watched", json={"path": target, "on": True})
        check(r.status_code == 200, f"viewer 也能標看過：{r.status_code}")

        d = c.get(f"/api/videos?root={lib}").json()
        rows = {Path(v["path"]).name: v for v in d["videos"]}
        check(len(rows) == 3, f"清單回得來（{len(rows)} 部）")
        check(all("watched" in v and "fav" in v for v in rows.values()),
              "每一列都帶著 watched 與 fav —— 前端要靠它篩選")
        check(rows["v1.mp4"]["fav"] and rows["v1.mp4"]["watched"],
              "剛標的那一部標記正確")
        check(not rows["v0.mp4"]["fav"] and not rows["v0.mp4"]["watched"],
              "沒標的那些是 false，不是缺欄位")

        check(c.post("/api/flags/fav",
                     json={"path": target, "on": False}).json()["fav"] is False,
              "取消最愛回傳的是新狀態")
        check(c.get(f"/api/flags?path={target}").json()["watched"] is True,
              "單筆查詢拿得到「看過」還在")

        print("\n  防呆：最愛的檔案刪不掉（而且是別的帳號標的）")
        # The endpoint has two shapes and they are not interchangeable: one
        # `path` raises, a list of `paths` returns a per-file error list. Both
        # are exercised, because the ten call sites use both.
        c.post("/api/flags/fav", json={"path": target, "on": True})
        r = c.post("/api/media/delete", json={"path": target})
        check(Path(target).is_file(), "檔案還在磁碟上 —— 這才是重點")
        check(r.status_code == 409,
              f"單筆是 409（伺服器聽懂了而且拒絕），不是 500：{r.status_code}")
        check("最愛" in r.json()["detail"],
              f"理由講得出是最愛：{r.json()['detail']}")
        # The favourite is the *viewer's*, so "取消最愛" would be the wrong
        # instruction for this editor: their own heart is off. What they can
        # do is ask an admin, and the message has to say so.
        check("管理員" in r.json()["detail"] and "1 位" in r.json()["detail"],
              f"說出是幾個人、該找誰 —— 不是叫你取消一顆你根本沒點亮的愛心："
              f"{r.json()['detail']}")
        # Listed as the editor: for the viewer who made the mark, it is their
        # own favourite and `fav_others` is rightly 0.
        state["reader"] = editor
        rows = {Path(v["path"]).name: v
                for v in c.get(f"/api/videos?root={lib}").json()["videos"]}
        state["reader"] = viewer
        check(rows["v1.mp4"]["fav_others"] == 1 and not rows["v1.mp4"]["fav"],
              "別人的清單帶著「別人標了幾個」，卡片在按下去之前就能顯示鎖")

        print("\n  管理員：確認後可以刪，但只限一次一部")
        admin = {"id": 9, "role": "admin", "username": "a", "can_edit": True}
        state["deleter"] = admin
        r = c.post("/api/media/delete", json={"path": target})
        check(r.status_code == 409 and "確認" in r.json()["detail"],
              f"沒帶確認照樣擋：{r.json()['detail']}")
        r = c.post("/api/media/delete",
                   json={"paths": [target], "force_fav": True}).json()
        check(Path(target).is_file() and r["errors"],
              "批次刪除就算帶了確認也不刪 —— 強制只給單筆")
        state["deleter"] = editor
        r = c.post("/api/media/delete", json={"path": target, "force_fav": True})
        check(r.status_code == 409 and Path(target).is_file(),
              "可編輯帳號自己帶確認旗標也沒用 —— 判斷在伺服器端")

        print("\n  批次時是「跳過」不是「整批中止」")
        others = [str(lib / "v0.mp4"), target, str(lib / "v2.mp4")]
        r = c.post("/api/media/delete", json={"paths": others}).json()
        check(len(r["deleted"]) == 2,
              f"另外兩個照樣刪掉了（{len(r['deleted'])} 個）—— "
              "一個受保護不該讓整批四十個都停下來")
        check(Path(target).is_file(), "受保護的那個還在")
        check(len(r["errors"]) == 1, "剛好一筆錯誤")

        print("\n  管理員確認後刪除：刪得掉，而且留下紀錄")
        state["deleter"] = admin
        r = c.post("/api/media/delete", json={"path": target, "force_fav": True})
        check(r.status_code == 200, f"這次過了：{r.status_code} {r.text[:80]}")
        check(not Path(target).is_file(), "檔案真的不見了")
        conn = db.connect()
        row = conn.execute("SELECT detail FROM security_alerts "
                           "WHERE kind = 'fav_forced' ORDER BY id DESC LIMIT 1"
                           ).fetchone()
        conn.close()
        check(row and "1 位" in row["detail"],
              "異常通知裡看得到是哪部、幾個人的最愛")
        check(not videoflags.any_fav(target), "那些最愛的紀錄也一起清掉了")

        print("\n  自己的最愛：連管理員都要先自己取消")
        own = str(lib / "v2b.mp4")
        Path(own).write_bytes(b"q" * 300)
        state["reader"] = admin; state["deleter"] = admin
        c.post("/api/flags/fav", json={"path": own, "on": True})
        r = c.post("/api/media/delete", json={"path": own, "force_fav": True})
        check(r.status_code == 409 and "取消最愛" in r.json()["detail"],
              "自己標的就叫自己取消 —— 強制旗標只跳過別人的")
        state["reader"] = viewer
        state["deleter"] = editor

        print("\n  待決配對也擋（它在 /api/media/delete 之外自己刪）")
        keep, doomed = lib / "v0b.mp4", lib / "v0.mp4"
        doomed.write_bytes(b"y" * 500)
        keep.write_bytes(b"z" * 400)
        c.post("/api/flags/fav", json={"path": str(doomed), "on": True})
        r = c.post("/api/pairs/resolve",
                   json={"original": str(doomed), "produced": str(keep),
                         "action": "keep_produced"})
        check(r.status_code == 409, f"擋下來了：{r.status_code}")
        check(doomed.is_file(), "原檔還在 —— 標記不會自動轉移給產出檔，"
                                "使用者選的是「任何地方都不刪最愛」")
    finally:
        main.is_allowed = real_allowed
        main.app.dependency_overrides.clear()


def main():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        fresh(tmp)

        inbox = tmp / "inbox" / "LaurelLeier-20260825-194508.mp4"
        archived = tmp / "chars" / "Laurel" / "LaurelLeier-20260825-194508.mp4"

        print("marking")
        videoflags.mark_watched(ALICE, inbox)
        videoflags.set_fav(ALICE, inbox, True)
        f = videoflags.get(ALICE, inbox)
        check(f["watched"] and f["fav"], "a mark can be set and read back")

        print("\nper-account isolation")
        check(videoflags.get(BOB, inbox) == {"watched": False, "fav": False},
              "another account sees none of it")
        videoflags.set_fav(BOB, inbox, True)
        check(not videoflags.get(BOB, inbox)["watched"],
              "and its own marks stay independent")

        print("\nthe first view is the one that is kept")
        first = videoflags.flags_for(ALICE)
        videoflags.mark_watched(ALICE, inbox)   # watched again later
        conn = db.connect()
        rows = conn.execute("SELECT watched FROM video_flags WHERE user_id = 1"
                            " AND path = ?", (videoflags.key(inbox),)).fetchall()
        conn.close()
        check(len(rows) == 1, "re-watching does not add a second row")
        check(first is not None, "flags_for returned the listing map")

        print("\nmoving the file carries the marks (this is the one that")
        print("would otherwise lose them at the worst possible moment)")
        videoflags.move(inbox, archived)
        check(videoflags.get(ALICE, archived)["fav"],
              "the favourite followed the file into the archive")
        check(not videoflags.get(ALICE, inbox)["fav"],
              "and is no longer at the old path")
        check(videoflags.get(BOB, archived)["fav"],
              "every account's marks moved, not just the caller's")

        print("\nlistings")
        m = videoflags.flags_for(ALICE)
        check(videoflags.key(archived) in m, "flags_for is keyed the same way")
        check(videoflags.key(str(archived).upper()) in m
              or videoflags.key(archived) == videoflags.key(str(archived).upper())
              or sys.platform != "win32",
              "the key is case-folded the way this file system is")

        print("\ndeleting")
        videoflags.forget(archived)
        check(not videoflags.get(ALICE, archived)["fav"],
              "the mark is gone for the account that made it")
        check(not videoflags.get(BOB, archived)["fav"],
              "and for every other account too -- the file is gone for all")

        print("\na new file on a reused path starts clean")
        videoflags.mark_watched(ALICE, archived)
        videoflags.forget(archived)
        check(not videoflags.get(ALICE, archived)["watched"],
              "no inherited 'watched' from the file that used to live here")

        print("\nfolder-level moves (rename / merge)")
        old = tmp / "chars" / "cncmeng8"
        new = tmp / "chars" / "Cncmeng8_merged"
        for i in range(3):
            videoflags.set_fav(ALICE, old / "IG" / f"v{i}.mp4", True)
        # a sibling whose name merely starts with the same text must not move
        outsider = tmp / "chars" / "cncmeng8_other" / "v.mp4"
        videoflags.set_fav(ALICE, outsider, True)
        moved = videoflags.move_tree(old, new)
        check(moved == 3, f"exactly the three files under the folder moved (got {moved})")
        check(videoflags.get(ALICE, new / "IG" / "v1.mp4")["fav"],
              "nested paths keep their position under the new folder")
        check(videoflags.get(ALICE, outsider)["fav"],
              "a sibling folder sharing the name prefix was left alone")

        print("\nunderscores and percent signs in folder names")
        # LIKE treats _ and % as wildcards; a folder called `Social Media_亞洲`
        # is not hypothetical here, it is what the archive roots are called.
        a = tmp / "Social Media_A" / "x.mp4"
        b = tmp / "Social MediaXA" / "x.mp4"     # would match `Media_A` unescaped
        videoflags.set_fav(ALICE, a, True)
        videoflags.set_fav(ALICE, b, True)
        videoflags.move_tree(tmp / "Social Media_A", tmp / "Renamed")
        check(videoflags.get(ALICE, tmp / "Renamed" / "x.mp4")["fav"],
              "the folder with the underscore moved")
        check(videoflags.get(ALICE, b)["fav"],
              "the folder that only matches it as a wildcard did not")

        print("\nrows that say nothing are not kept")
        p = tmp / "never" / "v.mp4"
        videoflags.set_fav(ALICE, p, True)
        videoflags.set_fav(ALICE, p, False)
        conn = db.connect()
        left = conn.execute("SELECT COUNT(*) c FROM video_flags WHERE path = ?",
                            (videoflags.key(p),)).fetchone()["c"]
        conn.close()
        check(left == 0, "un-favouriting an unwatched video removes the row")

        videoflags.mark_watched(ALICE, p)
        videoflags.set_fav(ALICE, p, True)
        videoflags.set_fav(ALICE, p, False)
        check(videoflags.get(ALICE, p)["watched"],
              "but a row still carrying 'watched' is kept")

        print("\nclearing a mis-tapped 'watched'")
        videoflags.set_watched(ALICE, p, False)
        check(not videoflags.get(ALICE, p)["watched"], "it can be undone")

        test_over_http(tmp)

    print()
    if FAIL:
        print(f"{len(FAIL)} FAILED")
        for m in FAIL:
            print("  - " + m)
        sys.exit(1)
    print("all good")


if __name__ == "__main__":
    main()
