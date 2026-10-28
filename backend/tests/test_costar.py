"""Co-stars (共演): the parts that fail silently.

  * a numbered film is marked by its number, so every copy carries the mark
    and a rename cannot lose it; anything else is marked by its path
  * a path mark follows the file when it moves, and goes when it is deleted
  * a folder rename or merge rewrites both the videos under it and the
    co-star marks pointing at it — and only that folder, not `Ann` → `Annette`
  * a turned-down suggestion is not offered again
  * over HTTP: the co-star's 「共演」 list really contains the lead's video,
    suggestions come from a stored wiki cast, and the lead is never suggested

Everything runs on a throwaway database and a throwaway library.

Run:  python tests/test_costar.py      (from backend/)
"""
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db                                      # noqa: E402
from app.core import costar, javdb                      # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def fresh(tmp: Path):
    # module globals looked up at call time, so these really redirect
    db.DB_PATH = tmp / "test.db"
    db.init_db()
    javdb.DB_PATH = tmp / "javdb.sqlite3"


def test_old_database(tmp: Path):
    print("\n舊的資料庫（只有 costar 表）啟動後就有放名字的表")
    import sqlite3
    db.DB_PATH = tmp / "old.db"
    conn = sqlite3.connect(str(db.DB_PATH))
    conn.execute("CREATE TABLE costar (video TEXT NOT NULL, folder TEXT NOT NULL, "
                 "state INTEGER NOT NULL DEFAULT 1, PRIMARY KEY (video, folder))")
    conn.execute("INSERT INTO costar VALUES ('id:ABC-123', 'x', 1)")
    conn.commit()
    conn.close()
    db.init_db()
    costar.set_names(tmp / "ABC-123.mp4", ["名字"])
    check(costar.names_for(tmp / "ABC-123.mp4") == ["名字"]
          and costar.for_video(tmp / "ABC-123.mp4") == ["x"],
          "名字寫得進去，原本的標記還在")


def test_keys_and_moves(tmp: Path):
    print("\n標記的鍵：有番號用番號、沒有用路徑；搬移、刪除、資料夾改名都跟著走")
    fresh(tmp)
    lead = tmp / "lib" / "Lead"
    other = tmp / "lib" / "Other"
    ann, annette = tmp / "lib" / "Ann", tmp / "lib" / "Annette"
    num = lead / "ABC-123.mp4"
    live = lead / "live-20260101-120000.mp4"

    check(costar.video_key(num) == "id:ABC-123", f"有番號：{costar.video_key(num)}")
    check(costar.video_key(live).startswith("path:"), "沒番號：用路徑")

    costar.set_for_video(num, [other, ann])
    costar.set_for_video(live, [other])
    check(costar.for_video(num) == [costar.folder_key(other), costar.folder_key(ann)],
          "設定後讀得回來，順序照輸入")
    check(costar.for_video(tmp / "elsewhere" / "ABC-123.mkv")
          == costar.for_video(num), "同番號的另一份檔案也帶著同樣的共演")
    check(set(costar.videos_for([other])) == {"id:ABC-123", costar.video_key(live)},
          "從共演者那邊查得到兩部")

    moved = tmp / "lib" / "Lead" / "sub" / "live-20260101-120000.mp4"
    costar.move(live, moved)
    check(costar.for_video(moved) == [costar.folder_key(other)]
          and costar.for_video(live) == [], "沒番號的檔案搬走，標記跟著走")
    costar.forget(moved)
    check(costar.for_video(moved) == [], "刪檔就清掉（路徑鍵）")
    costar.forget(num)
    check(costar.for_video(num) != [], "有番號的刪掉一份不清：別的副本還在，演員是片子的事")

    costar.set_for_video(lead / "x-live.mp4", [annette])
    costar.move_tree(ann, tmp / "lib" / "Ann2")
    check(costar.folder_key(tmp / "lib" / "Ann2") in costar.for_video(num),
          "共演者資料夾改名：指向它的標記跟著改")
    check(costar.for_video(lead / "x-live.mp4") == [costar.folder_key(annette)],
          "改 Ann 不會動到 Annette")
    costar.move_tree(lead, tmp / "lib" / "Lead_2")
    check(costar.for_video(tmp / "lib" / "Lead_2" / "x-live.mp4")
          == [costar.folder_key(annette)], "主角資料夾改名：底下沒番號影片的標記跟著改")

    under = tmp / "lib" / "Social Media_亞洲" / "A"
    costar.set_for_video(tmp / "lib" / "Social MediaX亞洲" / "A" / "y.mp4", [other])
    costar.move_tree(under.parent, tmp / "lib" / "SM")
    check(costar.for_video(tmp / "lib" / "Social MediaX亞洲" / "A" / "y.mp4")
          == [costar.folder_key(other)], "路徑裡的 _ 不會被當萬用字元")


def test_reject(tmp: Path):
    print("\n否決的建議會記住，不再出現；手動拿掉不算否決")
    fresh(tmp)
    v, a, b = tmp / "ABC-001.mp4", tmp / "A", tmp / "B"
    costar.set_for_video(v, [a])
    costar.reject(v, b)
    check(costar.for_video(v, costar.REJECTED) == [costar.folder_key(b)], "否決記下來了")
    check(costar.for_video(v) == [costar.folder_key(a)], "否決不會變成共演")
    costar.set_for_video(v, [])
    check(costar.for_video(v) == [] and costar.for_video(v, costar.REJECTED)
          == [costar.folder_key(b)], "手動清空：共演沒了、否決還在")


def test_over_http(tmp: Path):
    print("\nHTTP：建議來自已存的共演名單、主角不會被建議、共演者頁看得到那部片")
    fresh(tmp)
    from fastapi.testclient import TestClient            # noqa: E402
    from app import auth, main, settings as app_settings  # noqa: E402
    from app.core import searchindex                     # noqa: E402

    root = tmp / "root"
    lead, co, third = root / "LeadName", root / "CoStar", root / "Third"
    for d in (lead, co, third):
        d.mkdir(parents=True)
    film = lead / "ABC-123.mp4"
    film.write_bytes(b"x" * 1000)
    (third / "ABC-123.mkv").write_bytes(b"y" * 10)      # another copy, elsewhere
    javdb.set_wiki_cast({"ABC-123": ["LeadName", "CoStar", "誰都不是"]}, source="t")

    saved = (main.is_allowed, app_settings.archive_roots_for,
             app_settings.inbox_paths_for, searchindex.get)
    main.is_allowed = lambda p, u: True
    app_settings.archive_roots_for = lambda u: [root]
    app_settings.inbox_paths_for = lambda u: []
    # computed fresh, never written to the real data/ cache
    searchindex.get = lambda roots, inbox, scoped=False: searchindex._compute(roots, inbox)
    main.app.dependency_overrides[auth.current_user] = lambda: {
        "id": 1, "role": "admin", "username": "a", "can_edit": True}
    try:
        c = TestClient(main.app)
        d = c.get(f"/api/costar?path={film}").json()
        cast = {x["name"]: x for x in d["cast"]}
        check(list(cast) == ["LeadName", "CoStar", "誰都不是"],
              f"演出：整份名單照順序，有沒有資料夾都列（{list(cast)}）")
        check(cast["LeadName"]["lead"] and not cast["CoStar"]["lead"]
              and cast["CoStar"]["folder"] == str(co) and cast["誰都不是"]["folder"] == "",
              "主角標出來、對得到的帶資料夾、對不到的只有名字")
        check(d["costars"] == [], "還沒有確認的共演")

        r = c.post("/api/costar/set", json={"path": str(film), "folders": [str(co)]})
        check(r.status_code == 200 and [x["name"] for x in r.json()["costars"]]
              == ["CoStar"], f"確認共演：{r.status_code}")
        d = c.get(f"/api/costar?path={film}").json()
        cast = {x["name"]: x for x in d["cast"]}
        check(cast["CoStar"]["marked"] and not cast["誰都不是"]["marked"],
              "標過的在演出那行打勾")

        v = c.get(f"/api/costar/videos?root={co}").json()
        got = sorted(Path(x["path"]).name for x in v["videos"])
        check(got == ["ABC-123.mkv", "ABC-123.mp4"],
              f"共演者頁列出這部片的所有副本：{got}")
        check({x["lead"] for x in v["videos"]} == {"LeadName", "Third"},
              "每一部都標出它原本在哪位角色底下")
        v = c.get(f"/api/costar/videos?root={third}").json()
        check(v["count"] == 0, "自己資料夾裡的那份不會重複列成共演")

        r = c.post("/api/costar/set", json={"path": str(film),
                                             "folders": [str(root / "nope")]})
        check(r.status_code == 403, f"不存在的資料夾擋下：{r.status_code}")

        print("\n演出只用一份名單：同一個人不重複（MDTM-511）")
        # javdatabase's cache as the real one had it: her former name with a
        # trailing " -", and the romaji as a second spelling of the same person
        (lead / "person_info.json").write_text('{"aliases": ["白石美里"]}', encoding="utf-8")
        main._perf_cache.clear()
        conn = javdb._connect()
        conn.execute("INSERT INTO movie VALUES ('MDTM-511', 'ok', 'karin-morita,runacha', 1)")
        conn.execute("INSERT INTO idol VALUES ('karin-morita', 'Karin Morita', '白石美里  -', 1)")
        conn.execute("INSERT INTO idol VALUES ('runacha', 'Runacha', 'るなちゃ', 1)")
        conn.commit()
        conn.close()
        m511 = lead / "MDTM-511.mp4"
        m511.write_bytes(b"x")
        d = c.get(f"/api/costar?path={m511}").json()
        check([(x["name"], x["lead"]) for x in d["cast"]]
              == [("白石美里", True), ("るなちゃ", False)] and d["cast_source"] == "javdatabase",
              f"沒有 wiki 名單時用 javdatabase：日文名與羅馬拼音算一個人、尾巴的「 -」清掉（{d['cast']}）")
        javdb.set_wiki_cast({"MDTM-511": ["LeadName", "有坂るな", "一色みどり"]}, source="t")
        d = c.get(f"/api/costar?path={m511}").json()
        check([x["name"] for x in d["cast"]] == ["LeadName", "有坂るな", "一色みどり"]
              and d["cast_source"] == "wiki / AVBase",
              f"有 wiki 名單就只列它，不混 javdatabase 的 Karin Morita／るなちゃ（{d['cast']}）")
        m511.unlink()

        print("\n只有名字的共演：存得進去、搜得到、之後有資料夾會自動接上")
        r = c.post("/api/costar/set", json={"path": str(film), "folders": [str(co)],
                                             "names": ["横田夏美"]}).json()
        check([(x["name"], x["kind"], x["folder"]) for x in r["costars"]]
              == [("CoStar", "folder", str(co)), ("横田夏美", "name", "")],
              f"名字跟角色並列，沒有資料夾（{r['costars']}）")
        check(costar.names_for(third / "ABC-123.mkv") == ["横田夏美"],
              "跟角色一樣以番號記：同番號的另一份也有")
        c.post("/api/costar/set", json={"path": str(film), "folders": [str(co)]})
        check(costar.names_for(film) == ["横田夏美"], "舊頁面不送 names：名字不被清掉")
        rows = searchindex._compute([root], [])["v"]
        check(any(r[0] == "ABC-123.mp4" and "横田夏美" in r[5] for r in rows),
              "搜尋索引帶著名字")
        check(c.post("/api/costar/resolve", json={"name": "横田夏美"}).json()["folder"] == "",
              "還沒有她的資料夾")

        yoko = root / "Yoko"
        yoko.mkdir()
        (yoko / "person_info.json").write_text('{"aliases": ["横田夏美"]}', encoding="utf-8")
        main._perf_cache.clear()
        check(c.post("/api/costar/resolve", json={"name": "横田 夏美"}).json()["folder"]
              == str(yoko), "打別名也找得到角色（空白不算）")
        d = c.get(f"/api/costar?path={film}").json()
        check([(x["name"], x["kind"]) for x in d["costars"]]
              == [("CoStar", "folder"), ("Yoko", "name")],
              f"建了資料夾、名字成了她的別名：自動顯示成那位角色（{d['costars']}）")
        v = c.get(f"/api/costar/videos?root={yoko}").json()
        check(sorted(Path(x["path"]).name for x in v["videos"]) == ["ABC-123.mkv", "ABC-123.mp4"],
              "而且她的共演清單裡有這部片")

        moved = lead / "sub" / "live.mp4"
        live = lead / "live.mp4"
        costar.set_names(live, ["某人"])
        costar.move(live, moved)
        check(costar.names_for(moved) == ["某人"] and costar.names_for(live) == [],
              "沒番號的檔案搬走，名字跟著走")
        costar.forget(moved)
        check(costar.names_for(moved) == [], "刪檔就清掉")

        print("\n匯入後批次帶入共演")
        c.post("/api/costar/set", json={"path": str(film), "folders": [], "names": []})
        javdb.set_wiki_cast({"ABC-123": ["LeadName", "CoStar", "横田夏美", "誰都不是"]},
                            source="t")
        p = c.post("/api/costar/fill/preview", json={"ids": ["ABC-123", "ZZZ-1"]}).json()
        check(p["checked"] == 1, f"只看庫裡有的番號，同番號兩份算一部（{p['checked']}）")
        add = [a["name"] for r in p["rows"] for a in r["add"]]
        check(add == ["CoStar", "Yoko"], f"對得到資料夾（含別名）的才加，主角不加（{add}）")
        check([u["name"] for u in p["unmatched"]] == ["誰都不是"],
              "對不到的列出來、不寫")
        items = [{"path": r["path"], "folders": [a["folder"] for a in r["add"]]}
                 for r in p["rows"]]
        c.post("/api/costar/set", json={"path": str(film), "folders": [str(third)]})
        w = c.post("/api/costar/fill", json={"items": items}).json()
        check(w["marks"] == 2 and costar.for_video(film) == [costar.folder_key(f)
                                                            for f in (third, co, yoko)],
              f"只新增，原本的 Third 還在（{w}）")
        c.post("/api/costar/fill", json={"items": items, "undo": True})
        check(costar.for_video(film) == [costar.folder_key(third)], "復原只拿掉剛加的")
        c.post("/api/costar/reject", json={"path": str(film), "folder": str(co)})
        p = c.post("/api/costar/fill/preview", json={"ids": ["ABC-123"]}).json()
        check([a["name"] for r in p["rows"] for a in r["add"]] == ["Yoko"],
              "否決過的不再帶入")
    finally:
        (main.is_allowed, app_settings.archive_roots_for,
         app_settings.inbox_paths_for, searchindex.get) = saved
        main.app.dependency_overrides.clear()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for fn in (test_keys_and_moves, test_reject, test_over_http, test_old_database):
        d = Path(tempfile.mkdtemp())
        try:
            fn(d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
