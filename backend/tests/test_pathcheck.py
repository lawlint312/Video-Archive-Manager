"""Paths a client sends that used to be trusted further than they should be
(found by CodeQL, 2026-10-28).

  * the merge plan is sent back by the browser and executed as written; only
    its sources were checked, so a forged plan could move files anywhere,
    write person_info.json over any path, and delete without the delete
    permission
  * a character name of `..` passed the separator check, and `root / ".."`
    filed into the folder above the archive root

Run:  python tests/test_pathcheck.py        (from backend/)
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# before any app module is imported: every path derives from these
_TMP = Path(tempfile.mkdtemp(prefix="pathcheck_"))
os.environ["VAM_DATA_DIR"] = str(_TMP / "data")
os.environ["VAM_CACHE_DIR"] = str(_TMP / "cache")

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def test_merge_plan():
    print("合併：送回來的計畫只能在兩個資料夾之間動")
    from app import main
    from app.core import dedup
    root = _TMP / "lib"
    src, dst, other = root / "A", root / "B", root / "C"
    for d in (src / "IG", dst, other):
        d.mkdir(parents=True, exist_ok=True)
    (src / "v.mp4").write_bytes(b"a" * 100)
    (src / "IG" / "p.jpg").write_bytes(b"b" * 10)
    (src / "person_info.json").write_text("{}", encoding="utf-8")
    (dst / "person_info.json").write_text("{}", encoding="utf-8")
    (other / "keep.mp4").write_bytes(b"c" * 100)

    plan = dedup.build_merge_plan(src, dst)
    check(main._merge_plan_problem(plan, src, dst) is None,
          f"真正的計畫照常通過（{len(plan)} 項）")

    def forged(**kw):
        return main._merge_plan_problem([{"src": str(src / "v.mp4"),
                                          "dst": str(dst / "v.mp4"),
                                          "action": "move", **kw}], src, dst)

    check(forged(dst=str(_TMP / "outside.mp4")), "目的地在目標資料夾外：擋")
    check(forged(dst=str(dst / ".." / "C" / "v.mp4")), "用 .. 繞出目標資料夾：擋")
    check(forged(dst=str(dst)), "目的地就是目標資料夾本身：擋")
    check(forged(src=str(other / "keep.mp4")), "來源不在來源資料夾（別的角色的檔）：擋")
    check(forged(src=str(other / "keep.mp4"), action="delete", dst=None),
          "刪除別的資料夾的檔：擋（合併權限不能拿來刪檔）")
    check(forged(action="copy"), "不認得的動作：擋")
    check(forged(action="merge_info"), "merge_info 只能是 person_info.json 對 person_info.json")
    check(main._merge_plan_problem(["x"], src, dst), "不是物件的項目：擋")
    check(forged(dst=None), "缺目的地：擋")


def test_merge_endpoint():
    print("\nHTTP：偽造的計畫整份拒絕，檔案沒被動")
    from fastapi.testclient import TestClient
    from app import auth, main
    root = _TMP / "lib"
    src, dst, other = root / "A", root / "B", root / "C"
    saved = main.is_allowed
    main.is_allowed = lambda p, u=None: True
    main.app.dependency_overrides[auth.current_user] = lambda: {
        "id": 1, "role": "admin", "username": "a", "can_edit": True}
    try:
        c = TestClient(main.app)
        r = c.post("/api/character/merge", json={
            "src": str(src), "dst": str(dst),
            "plan": [{"src": str(src / "v.mp4"), "dst": str(_TMP / "escaped.mp4"),
                      "action": "move", "name": "v.mp4"}]})
        check(r.status_code == 403, f"回 403（{r.status_code}）")
        check((src / "v.mp4").exists() and not (_TMP / "escaped.mp4").exists(),
              "檔案還在原位")
        r = c.post("/api/character/merge", json={
            "src": str(src), "dst": str(dst),
            "plan": [{"src": str(other / "keep.mp4"), "dst": None,
                      "action": "delete", "name": "keep.mp4"}]})
        check(r.status_code == 403 and (other / "keep.mp4").exists(),
              f"別的資料夾的檔沒被刪（{r.status_code}）")
    finally:
        main.is_allowed = saved
        main.app.dependency_overrides.clear()


def test_folder_names():
    print("\n角色名稱：只能是根目錄正下方的一層")
    from app import main
    for bad in ("..", ".", "...", " .. ", ". .", "a/b", "a\\b", "x:y", ""):
        check(main._bad_folder_name(bad), f"擋 {bad!r}")
    for ok in ("山田花子", "Ann.", ".hidden", "a..b", "S.H.E", "名字 (別名)"):
        check(not main._bad_folder_name(ok), f"照過 {ok!r}")


def test_archive_endpoints():
    print("\nHTTP：歸檔不能用 .. 跳到根目錄上一層")
    from fastapi.testclient import TestClient
    from app import auth, main, settings as app_settings
    root = _TMP / "arch" / "root"
    inbox = _TMP / "arch" / "inbox"
    (inbox / "pack").mkdir(parents=True)
    root.mkdir(parents=True)
    clip = inbox / "clip.mp4"
    clip.write_bytes(b"x" * 10)
    saved = (main.is_allowed, app_settings.archive_roots_for)
    main.is_allowed = lambda p, u=None: True
    app_settings.archive_roots_for = lambda u: [root]
    main.app.dependency_overrides[auth.current_user] = lambda: {
        "id": 1, "role": "admin", "username": "a", "can_edit": True}
    try:
        c = TestClient(main.app)
        r = c.post("/api/archive/check", json={"path": str(clip), "person": "..",
                                                "root": str(root)})
        check(r.status_code == 400, f"單檔歸檔檢查：{r.status_code}")
        r = c.post("/api/archive", json={"path": str(clip), "person": "..",
                                          "root": str(root)})
        check(r.status_code == 400 and clip.exists(), f"單檔歸檔：{r.status_code}，檔案沒動")
        r = c.post("/api/archive/folder", json={"folder": str(inbox / "pack"),
                                                 "person": "..", "root": str(root)})
        check(r.status_code == 400 and (inbox / "pack").exists(),
              f"整包歸檔：{r.status_code}，資料夾沒動")
        r = c.post("/api/archive/check", json={"path": str(clip), "person": "新角色",
                                                "root": str(root)})
        check(r.status_code == 200 and Path(r.json()["dest_dir"]) == root / "新角色",
              "一般名字照常")
        ch = root / "Old"
        ch.mkdir()
        r = c.post("/api/character/rename", json={"path": str(ch), "new_name": ".."})
        check(r.status_code == 400 and ch.exists(), f"改名成 ..：{r.status_code}")
    finally:
        main.is_allowed, app_settings.archive_roots_for = saved
        main.app.dependency_overrides.clear()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for fn in (test_merge_plan, test_merge_endpoint, test_folder_names,
               test_archive_endpoints):
        fn()
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
