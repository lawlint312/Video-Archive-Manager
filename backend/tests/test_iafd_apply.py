"""The IAFD write path, driven through the real endpoint.

`test_iafd.py` covers parsing and matching; neither would have caught what
actually shipped broken. The apply branch passed the character *folder* to
`_checked_path`, which insists on `is_file()` — so every import failed with a
bare `not found`, and the preview looked perfect right up until the button.

That is the shape of bug this file exists for: the parts work, the wiring does
not, and only a request finds out.
"""
import os
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ok = fail = 0


def check(cond, what):
    global ok, fail
    if cond:
        ok += 1
        print("  ok   " + what)
    else:
        fail += 1
        print("  FAIL " + what)


def run():
    # `mkdtemp` + an ignoring rmtree rather than the context manager: importing
    # the app opens a log file inside DATA_DIR, and Windows will not delete a
    # file another handle is holding. The cleanup failing is not a test result.
    td = tempfile.mkdtemp(prefix="iafd_apply_")
    try:
        tmp = Path(td)
        os.environ["VAM_DATA_DIR"] = str(tmp / "data")
        os.environ["VAM_CACHE_DIR"] = str(tmp / "cache")
        for m in [k for k in list(sys.modules) if k.startswith("app")]:
            del sys.modules[m]

        from fastapi.testclient import TestClient
        from app import auth, db, main
        from app.core import filetitle, media

        db.init_db()

        folder = tmp / "Elena Varga"
        folder.mkdir()
        vid = folder / "18OnlyGirls - Rosalie - Applauding Myself.mp4"
        vid.write_bytes(b"x")

        real_allowed = main.is_allowed
        main.is_allowed = lambda p, u: True
        editor = {"id": 1, "role": "editor", "username": "e", "can_edit": True}
        main.app.dependency_overrides[auth.current_user] = lambda: editor
        main.app.dependency_overrides[auth.require_editor] = lambda: editor
        try:
            c = TestClient(main.app)
            body = {
                "site": "iafd.com",
                "folder": str(folder),
                "profile": {"dob": "1993-05-27", "height": "162 cm"},
                "file_released": {str(vid): "2014"},
            }
            r = c.post("/api/wiki/apply", json=body)
            check(r.status_code == 200,
                  f"寫入回 200（實際 {r.status_code}：{r.text[:80]}）")
            if r.status_code != 200:
                return
            w = r.json().get("written", {})
            check(sorted(w.get("profile") or []) == ["dob", "height"],
                  f"兩個欄位都寫進去（{w.get('profile')}）")
            check(w.get("file_released") == 1,
                  f"一筆發行日（{w.get('file_released')}）")

            info = media.load_person_info(folder)
            check((info.get("profile") or {}).get("dob") == "1993-05-27",
                  "person_info.json 真的有那個生日")
            check((info.get("profile_source") or {}).get("site") == "iafd.com",
                  "來源記下來了")

            got = filetitle.all_released()
            check(got.get(str(vid).lower() if os.name == "nt" else str(vid))
                  == "2014", f"發行日讀得回來（{got}）")

            # Re-running must not overwrite a value the user has since edited —
            # the same "hand-typed always wins" rule the catalogue lookup uses.
            info["profile"]["dob"] = "1990-01-01"
            media.save_person_info(folder, info)
            r2 = c.post("/api/wiki/apply", json=body)
            again = media.load_person_info(folder)
            check(again["profile"]["dob"] == "1990-01-01",
                  "手改過的欄位不會被再次匯入覆蓋")
            check("dob" not in (r2.json()["written"].get("profile") or []),
                  "而且回報裡不會謊稱寫了")

            # A folder that is not a folder must be refused clearly, not 404.
            r3 = c.post("/api/wiki/apply",
                        json={**body, "folder": str(vid), "file_released": {}})
            check(r3.status_code == 403,
                  f"傳檔案當資料夾要回 403（實際 {r3.status_code}）")
        finally:
            main.is_allowed = real_allowed
            main.app.dependency_overrides.clear()
    finally:
        import shutil
        shutil.rmtree(td, ignore_errors=True)


if __name__ == "__main__":
    print("IAFD 寫入端點")
    run()
    print("\n%d 通過 / %d 失敗" % (ok, fail))
    sys.exit(1 if fail else 0)
