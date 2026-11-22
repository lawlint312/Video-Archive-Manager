"""Settings → folders: browsing the server's folders and checking the lists.

The inbox and archive lists used to be a textarea each; now every row says
whether the server can reach it, and folders can be picked by browsing.

  * browsing lists folders only, without hidden/system ones, sorted the way
    Explorer does (case-insensitive), and says where "up" goes
  * an unreadable folder answers with the OS's own words, as a 400
  * empty path lists the drives (no I/O — an offline mapped drive must not
    stall it)
  * the check counts each folder's subfolders (an archive root's characters)
    and reports an unreachable one instead of failing the whole list

Only ever looks at a temporary directory (and the drive list).

Run:  python tests/test_settingsfolders.py      (from backend/)
"""
import _tmpdata  # noqa: F401  (first: never the real data/ or cache/)
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def main_test(tmp: Path):
    from fastapi import HTTPException
    from app import main

    root = tmp / "Lib"
    for name in ("beta", "Alpha", "gamma", ".hidden", "$RECYCLE.BIN", "#recycle"):
        (root / name).mkdir(parents=True)
    (root / "file.mp4").write_bytes(b"\0")

    browse = lambda p: main.browse_folders(main.FoldersBody(path=p), admin={})
    r = browse(str(root))
    check(r["dirs"] == ["Alpha", "beta", "gamma"],
          f"只列資料夾、不列隱藏與資源回收筒、不分大小寫排序（{r['dirs']}）")
    check(Path(r["parent"]) == tmp, "上一層是父資料夾")
    check(r["more"] == 0, "沒有被截掉")

    try:
        browse(str(tmp / "nope"))
        check(False, "不存在的資料夾要報錯")
    except HTTPException as e:
        check(e.status_code == 400 and e.detail, f"不存在：400 加上作業系統的原話（{e.detail}）")

    drives = browse("")
    check(drives["parent"] is None and drives["dirs"]
          and all(d.endswith(("\\", "/")) for d in drives["dirs"]),
          f"空路徑列出磁碟機（{drives['dirs'][:4]}）")
    if drives["dirs"][0].endswith(":\\"):
        top = browse(drives["dirs"][0][:2])
        check(top["path"].endswith(":\\") and top["parent"] == "",
              "磁碟機根目錄：「D:」當成根目錄，上一層回到磁碟機清單")

    res = main.check_paths(main.PathsBody(paths=[str(root), str(tmp / "nope"), " "]),
                           admin={})["paths"]
    check(res[str(root)] == {"ok": True, "dirs": 3}, f"數子資料夾（{res[str(root)]}）")
    check(res[str(tmp / "nope")]["ok"] is False and res[str(tmp / "nope")]["error"],
          "找不到的資料夾單獨回報，不影響其他列")
    check(len(res) == 2, "空白列忽略")

    print("\nHTTP：只收 POST（GET 會被別的網站的連結觸發）")
    from fastapi.testclient import TestClient
    from app import auth
    main.app.dependency_overrides[auth.require_admin] = lambda: {"id": 1, "role": "admin"}
    main.app.dependency_overrides[auth.current_user] = lambda: {"id": 1, "role": "admin"}
    seen = []
    real_scandir = main.os.scandir

    def spy(p):
        seen.append(str(p))
        return real_scandir(p)
    main.os.scandir = spy
    try:
        c = TestClient(main.app)
        r = c.post("/api/settings/folders", json={"path": str(root)})
        check(r.status_code == 200 and r.json()["dirs"] == ["Alpha", "beta", "gamma"],
              f"POST 加 JSON 照常列出（{r.status_code}）")
        seen.clear()
        r = c.get("/api/settings/folders", params={"path": r"\\attacker.invalid\x"})
        check(r.status_code in (404, 405) and not seen,
              f"GET 不再處理、也沒有去讀那個路徑（{r.status_code}，{seen}）")
        # what a form on another site can send: no JSON, so nothing is read
        r = c.post("/api/settings/folders", data={"path": r"\\attacker.invalid\x"})
        check(r.status_code == 422 and not seen,
              f"表單送來的（跨網站做得到的形式）不處理（{r.status_code}，{seen}）")
    finally:
        main.os.scandir = real_scandir
        main.app.dependency_overrides.clear()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    d = Path(tempfile.mkdtemp(prefix="vam-setfolders-"))
    try:
        main_test(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
