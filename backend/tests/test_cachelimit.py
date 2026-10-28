"""Cache eviction must not delete what is being served right now.

`enforce()` snapshots the whole cache, sorts by mtime and then deletes. That
list can take a while to work through — a real sweep removed 345 files — and a
request arriving in the meantime calls `touch()` on the entry it is about to
send. Without a re-check that entry is still in the snapshot with its old
mtime, gets deleted mid-flight, and the browser shows a broken image.

Run:  python tests/test_cachelimit.py        (from backend/)
"""
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import cachelimit                       # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def seed(d: Path, n=50, size=100_000):
    """`n` files, oldest first — 000 is the least recently used."""
    for i in range(n):
        f = d / f"{i:03d}.bin"
        f.write_bytes(b"x" * size)
        os.utime(f, (1000 + i, 1000 + i))


def test_basic(d: Path):
    print("\n基本淘汰")
    seed(d)
    res = cachelimit.enforce(d, 2_000_000)
    check(res["before"] == 5_000_000, f"掃描前 {res['before']:,}")
    check(res["after"] <= 2_000_000, f"掃描後 {res['after']:,} 在上限內")
    check(not (d / "000.bin").exists(), "最舊的被刪掉")
    check((d / "049.bin").exists(), "最新的留著")


def test_touched_during_sweep(d: Path):
    print("\n掃描途中被使用的檔案")
    seed(d)
    victim = d / "010.bin"          # old enough to be on the chopping list
    orig = os.remove
    fired = [False]

    def hook(p):
        r = orig(p)
        # someone requests `victim` just after the sweep started
        if not fired[0] and Path(p).name == "000.bin":
            now = time.time()
            os.utime(victim, (now, now))
            fired[0] = True
        return r

    os.remove = hook
    try:
        cachelimit.enforce(d, 2_000_000)
    finally:
        os.remove = orig

    check(fired[0], "測試確實在掃描途中動了那個檔案")
    check(victim.exists(),
          "掃描途中被 touch 的檔案沒有被刪 —— 它正在被送給瀏覽器")
    check(not (d / "000.bin").exists(), "其他沒被使用的舊檔照樣刪掉")


def test_notes_are_never_evicted(d: Path):
    """The sweep must not be able to reach the user's own memos.

    Inbox videos keep their note in `cache/notes/`, and the old sweep walked
    the whole cache root — so a memo could be deleted to make room for a
    thumbnail. Separate subtrees make that structurally impossible.
    """
    print("\n備忘不在任何上限的管轄範圍內")
    (d / "thumbs").mkdir()
    (d / "notes").mkdir()
    seed(d / "thumbs")
    note = d / "notes" / "abc.txt"
    note.write_text("使用者自己寫的備忘", encoding="utf-8")
    os.utime(note, (1, 1))          # as old as anything can be

    cachelimit.enforce(cachelimit.kind_dir(d, "thumbs"), 1_000_000)
    check(note.exists(), "掃描縮圖不會碰到 notes/，即使它是全場最舊的檔案")
    check(note.read_text(encoding="utf-8") == "使用者自己寫的備忘", "內容完好")
    check(len(list((d / "thumbs").iterdir())) < 50, "縮圖確實有被淘汰")


def test_under_limit(d: Path):
    print("\n沒超過上限時不刪東西")
    seed(d, n=5)
    res = cachelimit.enforce(d, 10_000_000)
    check(res["removed"] == 0, "一個都沒刪")
    check(len(list(d.iterdir())) == 5, "檔案全在")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for fn in (test_basic, test_touched_during_sweep,
               test_notes_are_never_evicted, test_under_limit):
        d = Path(tempfile.mkdtemp())
        try:
            fn(d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
