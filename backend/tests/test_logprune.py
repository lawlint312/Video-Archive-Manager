"""One log file per run, and the pruning that keeps them from piling up.

The naming is the easy half. The half worth a test is the deletion: this runs
on every start, the server starts on every login, and a bug here either lets
the directory grow without bound or throws away the log of the run you were
about to read.

Run:  python tests/test_logprune.py        (from backend/)
"""
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import _prune_logs, LOG_KEEP_RUNS      # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def names(d: Path):
    return sorted(f.name for f in d.iterdir())


def test_keeps_the_newest(tmp: Path):
    print("\n只留最近 N 次啟動")
    for i in range(40):
        (tmp / f"2026-09-{i % 28 + 1:02d}_{i:04d}_LOG.txt").write_text("x")
    _prune_logs(tmp, keep=15)
    left = names(tmp)
    check(len(left) == 15, f"剩下 15 個（實得 {len(left)}）")
    # sorted by name, and the name starts with the date — newest survive
    check(left == sorted(left)[-15:] if len(left) == 15 else False,
          "留下來的是名字最大的那 15 個，也就是最新的")


def test_rotated_parts_go_with_their_run(tmp: Path):
    print("\n同一次啟動輪替出來的 .1 .2 要一起處理")
    # A long run rotates into <stamp>_LOG.txt.1 — keeping half a run's history
    # would be worse than keeping none of it.
    for i in range(5):
        stamp = f"2026-09-01_{i:04d}"
        for suffix in ("", ".1", ".2"):
            (tmp / f"{stamp}_LOG.txt{suffix}").write_text("x")
    _prune_logs(tmp, keep=2)
    left = names(tmp)
    check(len(left) == 6, f"2 次啟動 x 3 個檔 = 6（實得 {len(left)}）")
    stamps = {f.split("_LOG.txt")[0] for f in left}
    check(len(stamps) == 2, "剩下的是完整的兩次啟動，不是六個零散的檔")
    for s in stamps:
        check(sum(1 for f in left if f.startswith(s)) == 3,
              f"{s} 的三個檔都在")


def test_console_files_belong_to_their_run(tmp: Path):
    print("\n啟動器側錄的 CONSOLE 檔要跟同一次啟動一起處理")
    # The vbs writes <stamp>_CONSOLE.txt; the app writes <stamp>_LOG.txt. They
    # are two halves of one run — uvicorn's own output (a failed bind, a
    # traceback) only exists in the console file, so keeping one without the
    # other loses exactly the half that explains a failed start.
    for i in range(6):
        stamp = f"2026-09-01_{i:04d}"
        (tmp / f"{stamp}_LOG.txt").write_text("x")
        (tmp / f"{stamp}_CONSOLE.txt").write_text("x")
    _prune_logs(tmp, keep=2)
    left = names(tmp)
    check(len(left) == 4, f"2 次啟動 x 2 個檔 = 4（實得 {len(left)}）")
    for f in left:
        stamp = f.split("_LOG.txt")[0].split("_CONSOLE.txt")[0]
        check(f"{stamp}_LOG.txt" in left and f"{stamp}_CONSOLE.txt" in left,
              f"{stamp} 的兩半都在")


def test_under_the_limit_does_nothing(tmp: Path):
    print("\n沒超過就一個都不刪")
    for i in range(3):
        (tmp / f"2026-09-01_{i:04d}_LOG.txt").write_text("x")
    _prune_logs(tmp, keep=15)
    check(len(names(tmp)) == 3, "三個還是三個")


def test_ignores_other_files(tmp: Path):
    print("\n不碰不是自己產的檔案")
    # backend.log is what this replaced; deleting a user's old evidence
    # because the naming scheme changed would be rude.
    (tmp / "backend.log").write_text("x")
    (tmp / "backend.log.1").write_text("x")
    (tmp / "筆記.txt").write_text("x")
    for i in range(20):
        (tmp / f"2026-09-01_{i:04d}_LOG.txt").write_text("x")
    _prune_logs(tmp, keep=2)
    left = names(tmp)
    check("backend.log" in left and "backend.log.1" in left, "舊的 backend.log 留著")
    check("筆記.txt" in left, "無關的檔案留著")
    check(sum(1 for f in left if "_LOG.txt" in f) == 2, "只刪自己的")


def test_empty_and_missing(tmp: Path):
    print("\n空目錄不會爆")
    _prune_logs(tmp, keep=5)
    check(True, "空的目錄")
    _prune_logs(tmp / "不存在", keep=5)
    check(True, "不存在的目錄")


def test_default_keep_is_sane(_tmp):
    print("\n預設保留數")
    check(5 <= LOG_KEEP_RUNS <= 50,
          f"LOG_KEEP_RUNS = {LOG_KEEP_RUNS}，夠回頭查又不會無限長")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for fn in (test_keeps_the_newest, test_rotated_parts_go_with_their_run,
               test_console_files_belong_to_their_run,
               test_under_the_limit_does_nothing, test_ignores_other_files,
               test_empty_and_missing, test_default_keep_is_sane):
        d = Path(tempfile.mkdtemp())
        try:
            fn(d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
