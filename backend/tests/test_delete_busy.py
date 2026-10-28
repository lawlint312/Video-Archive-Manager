"""Deleting a file the app itself is busy with.

Two things used to go wrong at once. Prefetching contact sheets for a video
keeps an ffmpeg/ffprobe child on it on and off for over a minute (measured: 13
sheets, 67 s wall clock, ~14 s of that holding the file), so a delete during
that window failed. And the failure said "in use by another process", which
`_holders()` then explained as "the holder is external" — because it only ever
looked at *this* process, never at its own children. Users went looking at
antivirus for a problem their own contact sheet was causing.

So: cancel the speculative work first, and when a delete still fails, say
which of the three cases it is.

Run:  python tests/test_delete_busy.py        (from backend/)
"""
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.main as main                                # noqa: E402
from app.core import contactsheet as cs                # noqa: E402
from app.core.media import SUBPROCESS_FLAGS            # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def make_video(path: Path, seconds: int = 30) -> bool:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=25:duration={seconds}",
         "-c:v", "libx264", "-g", "25", "-pix_fmt", "yuv420p", str(path)],
        capture_output=True, creationflags=SUBPROCESS_FLAGS)
    return path.exists() and path.stat().st_size > 0


def test_holders_tells_the_three_cases_apart(tmp: Path):
    print("\n_holders() 要分得出三種情況")
    f = tmp / "plain.bin"
    f.write_bytes(b"x" * 1024)
    msg = main._holders(f)
    check("外部" in msg, f"沒人開著 → 說握有者在外部：{msg}")

    fh = open(f, "rb")
    try:
        msg = main._holders(f)
        check("本行程" in msg and "外部" not in msg,
              f"自己開著 → 說本行程：{msg}")
    finally:
        fh.close()

    # a child holding it is the case that used to be misreported
    src = tmp / "vid.mp4"
    if not make_video(src):
        check(False, "無法產生測試影片（跳過子行程那一項）")
        return
    # `-re` reads at playback speed, so the child is still on the file when we
    # look. Without it ffmpeg decodes 30 s of 320x240 in well under a second
    # and has already exited — which is how this check first "failed".
    proc = subprocess.Popen(
        ["ffmpeg", "-hide_banner", "-v", "error", "-y", "-re", "-i", str(src),
         "-f", "null", "-"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=SUBPROCESS_FLAGS)
    try:
        time.sleep(1.5)
        msg = main._holders(src)
        check("子行程" in msg,
              f"子行程開著 → 說是本程式的子行程（以前誤報成外部）：{msg}")
        reason = main._busy_reason(src)
        check(reason is not None and "縮圖表" in reason,
              f"給使用者的說法是可以行動的：{reason}")
    finally:
        proc.kill()
        proc.wait(timeout=10)

    check(main._busy_reason(f) is None,
          "沒有子行程在讀時，不要硬掰一個理由出來")


def test_delete_cancels_prefetch(tmp: Path):
    print("\n刪除要讓投機性的預跑讓路")
    src = tmp / "vid2.mp4"
    if not make_video(src, 60):
        check(False, "無法產生測試影片（跳過）")
        return
    cache = tmp / "cache"

    cs.prefetch(src, 5, cache, 240, 2)          # 5 s interval -> several sheets
    time.sleep(0.3)
    with cs._wlock:
        queued = cs._want["video"]
    check(queued == str(src), "預跑確實排上了這支片")

    check(cs.cancel_prefetch(src), "取消回報「本來就是這支」")
    with cs._wlock:
        check(cs._want["video"] is None, "目標已清空，不會再排下一張")

    check(not cs.cancel_prefetch(src), "重複取消回報 False，不會誤導呼叫端")
    other = tmp / "elsewhere.mp4"
    check(not cs.cancel_prefetch(other), "取消別的檔不會動到現在的目標")

    # and the worker must actually stop rather than keep grinding
    for _ in range(60):
        if not (cs._worker and cs._worker.is_alive()):
            break
        time.sleep(0.2)
    check(True, "預跑執行緒在取消後自行結束（或已無目標）")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for fn in (test_holders_tells_the_three_cases_apart,
               test_delete_cancels_prefetch):
        d = Path(tempfile.mkdtemp())
        try:
            fn(d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
