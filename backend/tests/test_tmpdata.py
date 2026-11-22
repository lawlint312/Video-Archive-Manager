"""Every test runs against a throwaway data/ and cache/, never the repo's own.

Two halves:
  * each tests/test_*.py imports `_tmpdata` as its very first import — before
    `app.config` reads VAM_DATA_DIR / VAM_CACHE_DIR, which is the only moment
    they count. A new test file that forgets fails here, not by quietly
    opening the user's real databases (2026-11-22: test_tags read the real
    vam.db, and a full run left vam.db and javdb.sqlite3 in a clean checkout);
  * after that import, the app's directories really are inside the temp root.

Run:  python tests/test_tmpdata.py      (from backend/)
"""
import _tmpdata  # noqa: F401  (first: never the real data/ or cache/)
import ast
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def first_import(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for n in tree.body:
        if isinstance(n, ast.Import):
            return n.names[0].name
        if isinstance(n, ast.ImportFrom):
            return f"from {n.module}"
    return None


def test_every_file_imports_it_first():
    print("\n每支測試的第一個 import 都是 _tmpdata")
    files = sorted(HERE.glob("test_*.py"))
    check(len(files) > 50, f"找到 {len(files)} 支測試")
    bad = [f"{f.name}（第一個是 {first_import(f)}）" for f in files
           if first_import(f) != "_tmpdata"]
    check(not bad, "全部都是" if not bad else "沒有先匯入 _tmpdata：" + "、".join(bad))


def test_app_paths_are_temp():
    print("\n匯入之後 app 的資料與快取目錄在暫存處")
    from app import config
    root = Path(_tmpdata.ROOT).resolve()
    for name in ("DATA_DIR", "CACHE_DIR"):
        p = getattr(config, name)
        check(root in p.parents, f"{name} 在 {root} 底下（實得 {p}）")
        check(REPO not in p.parents, f"{name} 不在專案目錄裡")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    test_every_file_imports_it_first()
    test_app_paths_are_temp()
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
