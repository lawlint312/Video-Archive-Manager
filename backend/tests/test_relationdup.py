"""同內容不同番號: the same footage held twice, read off AVBase's relations.

No scan — the stored relations against the library index. The parts that
would fail quietly:
  * only `original` / `uncut` make a film redundant — a digest holds less
    than the film it is cut from, and an unknown has no dates
  * both numbers must be here
  * a cut is offered in one group only; Y ⊃ X ⊃ Z is one group under Y
  * the endpoint reads the index, not the disk walk, and every data path is
    a throwaway one

    python tests/test_relationdup.py
"""
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import avbase, dedup, javdb     # noqa: E402

FAIL = []


def check(ok, msg):
    print(("  ok   " if ok else "  FAIL ") + msg)
    if not ok:
        FAIL.append(msg)


def rel(parent, child, kind, pd="2016-01-01", cd="2026-01-01"):
    return {"parent": parent, "child": child, "kind": kind,
            "parent_date": pd, "child_date": cd}


RELS = [rel("UFD-061", "AKO-599", "original"),
        rel("UFD-061", "AKO-385", "original"),
        rel("MAAN-239", "JDG-112", "unknown"),
        rel("BEST-001", "SOLO-001", "digest", "2026-01-01", "2016-01-01"),
        rel("NOT-HERE", "AKO-599", "uncut"),
        rel("ABP-100", "ABP-200", "original"),
        rel("ABP-200", "ABP-300", "original")]
HERE = ["UFD-061", "AKO-599", "AKO-385", "MAAN-239", "JDG-112", "BEST-001",
        "SOLO-001", "ABP-100", "ABP-200", "ABP-300"]


def test_groups():
    print("分組")
    gs = avbase.redundant_groups({v: [1] for v in HERE}, RELS)
    by = {g["parent"]: g for g in gs}
    check(set(by) == {"UFD-061", "ABP-100"},
          f"只有「原片」「完整收錄」成組，缺一邊的不算（{sorted(by)}）")
    check([c["id"] for c in by["UFD-061"]["children"]] == ["AKO-385", "AKO-599"],
          "一部原片底下的擷取版合成一組")
    a = {c["id"]: c["via"] for c in by["ABP-100"]["children"]}
    check(a == {"ABP-200": "ABP-100", "ABP-300": "ABP-200"},
          "ABP-100 ⊃ 200 ⊃ 300：一組，在 100 底下，300 註明經由 200")
    kids = [c["id"] for g in gs for c in g["children"]]
    check(len(kids) == len(set(kids)), "同一部不會在兩組裡被建議刪除")
    check(avbase.redundant_groups({"ABP-100": [1], "ABP-200": [1]},
                                  [rel("ABP-100", "ABP-200", "uncut"),
                                   rel("ABP-200", "ABP-100", "uncut")]) == [],
          "互相包含（資料錯了）：不成組")


def test_endpoint(tmp: Path):
    print("\n端點：讀索引，不掃描")
    from fastapi.testclient import TestClient
    from app import auth, main, settings as app_settings
    from app.core import searchindex

    lib = tmp / "lib" / "森田花梨"
    lib.mkdir(parents=True)
    for v in ("UFD-061", "AKO-599", "JDG-112"):
        (lib / f"{v}.mp4").write_bytes(b"x" * 100)
    from app import db
    saved = (javdb.DB_PATH, main.VIDEO_HASH_DB, searchindex.get, dedup.probe_video,
             app_settings.archive_roots_for, app_settings.inbox_paths_for, db.DB_PATH,
             main.is_allowed)
    walked = []
    try:
        main.is_allowed = lambda p, u=None: True
        # the index reads vam.db too (typed titles, co-star names)
        db.DB_PATH = tmp / "vam.db"
        db.init_db()
        javdb.DB_PATH = tmp / "javdb.sqlite3"
        main.VIDEO_HASH_DB = tmp / "video_hash.sqlite3"
        app_settings.archive_roots_for = lambda u=None: [tmp / "lib"]
        app_settings.inbox_paths_for = lambda u=None: []
        searchindex.get = lambda roots, inbox, scoped=False: (
            walked.append(1), searchindex._compute(roots, inbox))[1]
        dedup.probe_video = lambda p: (100.0, 1920, 1080)
        main.app.dependency_overrides[auth.current_user] = lambda: {"id": 1, "role": "admin"}
        c = TestClient(main.app)

        r = c.get("/api/dedup/relations").json()
        check(r == {"groups": [], "relations": 0}, "沒有收錄關係：空的，連索引都不讀")
        check(not walked, "（真的沒讀）")

        avbase.save_relations(RELS[:3])
        r = c.get("/api/dedup/relations").json()
        check(r["relations"] == 3 and len(r["groups"]) == 1, f"一組（{len(r['groups'])}）")
        g = r["groups"][0]
        check(g["relation"]["parent"] == "UFD-061"
              and [c["id"] for c in g["relation"]["children"]] == ["AKO-599"],
              "AKO-385 不在庫裡就不列；JDG-112 沒日期不列")
        check(sorted((f["vid"], f["role"], f["label"]) for f in g["files"])
              == [("AKO-599", "child", "森田花梨"), ("UFD-061", "parent", "森田花梨")],
              "檔案標出原片／被包含、在哪個角色底下")
        check(g["files"][0]["width"] == 1920, "帶著解析度可以比較")

        print("\n播放器的「收錄」與卡片標記")
        rs = c.get(f"/api/relations?path={lib / 'AKO-599.mp4'}").json()["relations"]
        check([(r["other"], r["this_is"], r["kind"]) for r in rs]
              == [("UFD-061", "child", "original")], f"從自己這端讀（{rs}）")
        check(rs[0]["have"] and rs[0]["have"]["path"] == str(lib / "UFD-061.mp4")
              and rs[0]["have"]["char"] == "森田花梨", "你有的那部帶著路徑，可以直接播")
        rs = c.get(f"/api/relations?path={lib / 'UFD-061.mp4'}").json()["relations"]
        check({r["other"]: r["this_is"] for r in rs} == {"AKO-599": "parent", "AKO-385": "parent"}
              and not {r["other"]: r["have"] for r in rs}["AKO-385"],
              "原片那端：兩部都列，沒有的 have 是空的（全部的關係，不只能刪的）")
        rs = c.get(f"/api/relations?path={lib / 'JDG-112.mp4'}").json()["relations"]
        check([r["other"] for r in rs] == ["MAAN-239"] and rs[0]["have"] is None,
              "對方不在庫裡的關係照樣列（資訊，不是為了刪）")
        vids = {v["video_id"]: v for v in
                c.get(f"/api/videos?root={lib}").json()["videos"]}
        check(vids["AKO-599"]["rel"] == [{"o": "UFD-061", "k": "original", "p": False}]
              and "rel" in vids["UFD-061"],
              "影片清單的每張卡帶著精簡的關係")

        (lib / "AKO-599.mp4").unlink()
        r = c.get("/api/dedup/relations").json()
        check(r["groups"] == [], "擷取版刪掉了（索引還沒更新）：不再列")
    finally:
        (javdb.DB_PATH, main.VIDEO_HASH_DB, searchindex.get, dedup.probe_video,
         app_settings.archive_roots_for, app_settings.inbox_paths_for, db.DB_PATH,
         main.is_allowed) = saved
        main.app.dependency_overrides.clear()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    test_groups()
    d = Path(tempfile.mkdtemp())
    try:
        test_endpoint(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
