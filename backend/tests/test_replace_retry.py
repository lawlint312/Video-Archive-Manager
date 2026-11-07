"""Swapping a finished convert in for its source must ride out a brief lock.

On the NAS, renaming over a file something else has open fails with WinError 5
(access denied) rather than 32. One file failed that way at the end of a
33-minute batch and was free again a minute later, so the swap now backs off
the way deleting does, and says what the system said when it gives up.

Temp files only; the waits are patched out.

Run:  python tests/test_replace_retry.py        (from backend/)
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import main                                    # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def locked(times, winerror=5):
    """A replace_original that is refused `times` times, then really renames."""
    real = main.convert.replace_original
    calls = []

    def fake(src, staged):
        calls.append(1)
        if len(calls) <= times:
            e = PermissionError(13, "存取被拒。")
            e.winerror = winerror
            raise e
        real(src, staged)
    return fake, calls


def files():
    d = Path(tempfile.mkdtemp())
    src, staged = d / "a.mp4", d / "a.converting.mp4"
    src.write_bytes(b"old")
    staged.write_bytes(b"new")
    return src, staged


def run():
    real_replace, real_sleep = main.convert.replace_original, main.time.sleep
    main.time.sleep = lambda s: None
    try:
        src, staged = files()
        main.convert.replace_original, calls = locked(2)
        main._replace_original(src, staged)
        check(src.read_bytes() == b"new" and not staged.exists(),
              "WinError 5 twice, then free: the new file is in place")
        check(len(calls) == 3, f"three tries ({len(calls)})")

        src, staged = files()
        main.convert.replace_original, calls = locked(99)
        try:
            main._replace_original(src, staged)
            check(False, "a lock that never clears is reported")
        except RuntimeError as e:
            msg = str(e)
            check("存取被拒" in msg and "WinError 5" in msg and "重試 4 次" in msg,
                  f"gives up with the system's words: {msg}")
        check(len(calls) == 5 and src.read_bytes() == b"old",
              "five tries, and the original is untouched")

        src, staged = files()
        main.convert.replace_original, calls = locked(1, winerror=3)
        try:
            main._replace_original(src, staged)
            check(False, "an unrelated error is not retried")
        except PermissionError:
            check(len(calls) == 1, "an unrelated error is not retried")
    finally:
        main.convert.replace_original, main.time.sleep = real_replace, real_sleep


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    run()
    print("全部通過" if not FAIL else f"{len(FAIL)} 項失敗")
    sys.exit(1 if FAIL else 0)
