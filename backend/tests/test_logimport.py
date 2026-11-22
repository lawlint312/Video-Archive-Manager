"""Importing the app must not open or prune a log file; starting it must.

Every backend test imports app.main. When logging was set up at import, each
test process created <stamp>_LOG.txt in the real data/logs and pruned that
directory to the newest LOG_KEEP_RUNS runs — a full suite deleted every real
server log (2026-11-22). Logging now starts in `_startup()`, which only
uvicorn runs.

DATA_DIR points at a temp dir before the import, so this never reads the real
data/logs either: if import wrote a log, it would show up in the temp one.

Run:  python tests/test_logimport.py      (from backend/)
"""
import _tmpdata  # noqa: F401  (first: never the real data/ or cache/)
import ast
import inspect
import logging
import os
import re
import shutil
import sys
import tempfile
import textwrap
from logging.handlers import RotatingFileHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_TMP = Path(tempfile.mkdtemp(prefix="vam_logimport_"))
os.environ["VAM_DATA_DIR"] = str(_TMP / "data")
os.environ["VAM_CACHE_DIR"] = str(_TMP / "cache")

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def file_handlers():
    return [h for h in logging.getLogger().handlers
            if isinstance(h, RotatingFileHandler)]


def run():
    before = list(logging.getLogger().handlers)
    from app import main

    print("\n匯入 app.main 不開日誌檔")
    check(main.DATA_DIR == (_TMP / "data").resolve(),
          f"DATA_DIR 指到暫存目錄（實得 {main.DATA_DIR}）")
    logs = main.DATA_DIR / "logs"
    found = sorted(f.name for f in logs.iterdir()) if logs.exists() else []
    check(found == [], f"data/logs 沒有新檔（實得 {found}）")
    check(file_handlers() == [], "root logger 沒有檔案 handler")
    check(logging.getLogger().handlers == before, "root logger 的 handler 沒變")

    print("\n啟動時第一件事是開日誌")
    tree = ast.parse(textwrap.dedent(inspect.getsource(main._startup)))
    first = tree.body[0].body[0]
    check(isinstance(first, ast.Expr) and isinstance(first.value, ast.Call)
          and getattr(first.value.func, "id", None) == "_setup_logging",
          "_startup() 的第一行是 _setup_logging()")
    # It used to be logged at import, which now comes before the file is
    # open: the first line of every run's log went missing (2026-11-22).
    src = inspect.getsource(main._startup)
    check("serving the built frontend" in src and "no built frontend" in src,
          "用哪一份前端，在開日誌之後才記（匯入時記的寫不進檔案）")

    print("\n開日誌：一次啟動一個檔、名字格式不變、照樣修剪")
    logs.mkdir(parents=True, exist_ok=True)
    for i in range(main.LOG_KEEP_RUNS + 5):          # older runs than today's
        (logs / f"2020-01-01_{i:04d}_LOG.txt").write_text("x")
    (logs / "筆記.txt").write_text("x")
    main._setup_logging()
    try:
        fh = file_handlers()
        check(len(fh) == 1, f"一個檔案 handler（實得 {len(fh)}）")
        new = [f.name for f in logs.iterdir() if not f.name.startswith("2020-")
               and f.name != "筆記.txt"]
        check(len(new) == 1 and re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{4}_LOG\.txt",
                                              new[0] if new else ""),
              f"新檔名是 YYYY-MM-DD_HHMM_LOG.txt（實得 {new}）")
        if fh:
            check(Path(fh[0].baseFilename).parent == logs,
                  "寫在 DATA_DIR/logs 底下")
        runs = [f for f in logs.iterdir() if "_LOG.txt" in f.name]
        # pruned before this run's file opens: N earlier runs plus this one
        check(len(runs) == main.LOG_KEEP_RUNS + 1,
              f"修剪到之前 {main.LOG_KEEP_RUNS} 次＋這一次（實得 {len(runs)}）")
        check((logs / "筆記.txt").exists(), "不是日誌的檔案留著")
        main._setup_logging()                          # --reload runs it twice
        check(len(file_handlers()) == 1, "再呼叫一次不會多開一個檔")
    finally:
        root = logging.getLogger()
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h)
                h.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        run()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
