"""What the user is told when ffmpeg fails.

This exists because of one real report: `gFYwOpwUEVnVcsleBZsN.mp4 — Last
message repeated 11 times`. That names no fault, suggests no action, and is
not searchable. The code took `stderr.splitlines()[-1]`, and ffmpeg prints its
log-deduplication marker *after* the messages it stands in for, so the one
line that carried no information was the one line guaranteed to be picked.

The file behind that report had 51,058 `Invalid NAL unit size` lines. The
count is the diagnosis: one bad frame is a glitch, 51,058 is a broken file.
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.media import ffmpeg_error      # noqa: E402

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
    # The shape of the real failure: many lines differing only in numbers,
    # then ffmpeg's own marker last.
    err = "\n".join(
        ["[h264 @ 00000248d4eea8c0] Invalid NAL unit size (%d > %d)." % (-900000 - i, 60000 + i)
         for i in range(11)]
        + ["    Last message repeated 11 times"])
    msg = ffmpeg_error(err, 1)
    check("Last message repeated" not in msg,
          "不會把 ffmpeg 的去重標記當成錯誤（%s）" % msg[:60])
    check("Invalid NAL unit size" in msg, "說得出真正的錯誤")
    check("11" in msg, "而且講出次數，次數本身就是診斷")

    # A single distinct message must not grow a misleading count.
    one = ffmpeg_error("[aac] Error while opening decoder", 1)
    check(one == "[aac] Error while opening decoder", "只有一行時原樣呈現（%s）" % one)

    # Nothing but the marker, or nothing at all, still has to say something.
    check(ffmpeg_error("  \n Last message repeated 3 times \n", 69)
          == "ffmpeg 失敗（代碼 69）", "只有標記時退回代碼")
    check(ffmpeg_error("", 8) == "ffmpeg 失敗（代碼 8）", "完全沒輸出時退回代碼")

    # Two different faults are both worth seeing.
    two = ffmpeg_error("\n".join(["A failed 1"] * 5 + ["B failed 2"] * 3), 1)
    check("A failed" in two and "B failed" in two, "兩種錯誤都會報（%s）" % two)

    # A pathological single line must not flood the dialog.
    long_msg = ffmpeg_error("x" * 500, 1)
    check(len(long_msg) <= 161, "過長的單行會截斷（%d 字）" % len(long_msg))


if __name__ == "__main__":
    print("ffmpeg 錯誤摘要")
    run()
    print("\n%d 通過 / %d 失敗" % (ok, fail))
    sys.exit(1 if fail else 0)
