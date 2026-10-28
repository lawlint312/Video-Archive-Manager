"""Files whose colour metadata swscale refuses.

Reported as "縮圖表跟卡片都解不出圖片，轉檔卻說它是可播放的 MP4" — and the
convert tool was right. The file is h264 High / yuv420p / avc1, genuinely
playable. What breaks is the *picture extraction*: its H.264 SPS declares
colour primaries and transfer characteristics as "reserved", and swscale
refuses that input outright:

    Unsupported input (Error number -129 occurred):
    fmt:yuv420p csp:bt709 prim:reserved trc:reserved -> fmt:yuv420p ...

Measured on the real file: card thumbnail, contact-sheet grab and the
duplicate scanner's phash all fail; shrink and clip do not. The dividing line
is whether swscale must *convert colour* (JPEG is full-range, phash asks for
gray) or merely resize.

Prevalence, measured: 1 file in a 202-file sample. Rare — but the failure is
total and silent, so the fix is a retry, never a default. Forcing bt709 on
every file would flatten the colour of anything correctly tagged as something
else (HDR) to rescue one file in two hundred.

### Why these tests fake the failure

The trigger could not be synthesised. Writing "reserved" into the container's
`colr` box does make ffprobe report it, but the grab still succeeds — the real
file has no `colr` box at all and the values come from the bitstream, and
`h264_metadata` normalises 0 and 3 to "unspecified" on the way out. So these
tests drive the retry ladder with a stubbed `subprocess.run`, the same
technique `test_contactsheet.test_gpu_falls_back` uses.

**The fix itself was verified against the user's actual file**, all four
paths: card thumbnail 33,724 B, sheet grab 18,934 B, phash
0x213dfcfc7c30080c, whole contact sheet 229,190 B — every one of them failing
before the change.

Run:  python tests/test_colour_metadata.py        (from backend/)
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import contactsheet as cs, dedup, media       # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


class Refuser:
    """Stands in for ffmpeg: refuses unless the colour override is present.

    `writes` is what a successful run produces, so "did it retry" and "did it
    produce a file" stay separate questions.
    """

    def __init__(self, real, writes=b"\xff\xd8jpeg", stdout=None):
        self.real, self.writes, self.stdout = real, writes, stdout
        self.calls = []                 # one entry per attempt: 'plain'/'fixed'

    def __call__(self, cmd, **kw):
        joined = " ".join(cmd)
        fixed = media.COLOUR_FIX in joined
        gpu = "-hwaccel" in cmd
        self.calls.append(("gpu" if gpu else "cpu") + ("+fix" if fixed else ""))
        if not fixed:
            # what ffmpeg really does: exits non-zero, writes nothing usable
            return subprocess.CompletedProcess(cmd, 1, b"", b"Unsupported input")
        if self.stdout is not None:
            return subprocess.CompletedProcess(cmd, 0, self.stdout, b"")
        out = Path(cmd[-4] if cmd[-3] == "-y" else cmd[-1])
        for a in cmd:
            if a.endswith(".jpg"):
                out = Path(a)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(self.writes)
        return subprocess.CompletedProcess(cmd, 0, b"", b"")


def test_command_shape():
    print("\n色彩改寫的指令形狀")
    src, out = Path("in.mp4"), Path("out.jpg")

    plain = " ".join(cs._grab_cmd(src, 10, out, 480, gpu=False))
    check(media.COLOUR_FIX not in plain, "第一次嘗試不帶色彩改寫（絕不當預設）")

    cpu = " ".join(cs._grab_cmd(src, 10, out, 480, gpu=False, colour_fix=True))
    check(media.COLOUR_FIX in cpu, "重試時帶上 setparams")
    check(cpu.index(media.COLOUR_FIX) < cpu.index("scale="),
          "CPU 路徑要先改寫再縮放 —— 反過來的話 swscale 已經拒絕了")

    gpu = " ".join(cs._grab_cmd(src, 10, out, 480, gpu=True, colour_fix=True))
    check(gpu.index("hwdownload") < gpu.index(media.COLOUR_FIX),
          "GPU 路徑要等畫格搬回主記憶體之後才改寫")

    check("bt709" in media.COLOUR_FIX,
          f"改寫成 bt709（{media.COLOUR_FIX}）")


def test_sheet_grab_retries(tmp: Path):
    print("\n縮圖表：被拒絕之後要用色彩改寫重試")
    real = cs.subprocess.run
    stub = Refuser(real)
    cs.subprocess.run = stub
    try:
        ok = cs._grab(tmp / "x.mp4", 5, tmp / "tile.jpg", 480, gpu=False)
    finally:
        cs.subprocess.run = real
    check(ok, "最後有抓到格")
    check(stub.calls == ["cpu", "cpu+fix"], f"嘗試順序：{stub.calls}")


def test_sheet_grab_retries_after_gpu(tmp: Path):
    """The two ladders are independent and must not shadow each other."""
    print("\n縮圖表：GPU 模式下兩層退避要接得起來")
    real = cs.subprocess.run
    stub = Refuser(real)
    cs.subprocess.run = stub
    try:
        ok = cs._grab(tmp / "x.mp4", 5, tmp / "tile2.jpg", 480, gpu=True)
    finally:
        cs.subprocess.run = real
    check(ok, "最後有抓到格")
    check(stub.calls == ["gpu", "cpu", "cpu+fix"], f"嘗試順序：{stub.calls}")
    check(stub.calls[-1].startswith("cpu"),
          "色彩改寫一律在 CPU 上做，不再回頭試 GPU")


def test_no_retry_when_it_works(tmp: Path):
    """The retry must cost nothing for the 201 files in 202 that are fine."""
    print("\n正常檔案不能因此多跑一次 ffmpeg")
    calls = []
    real = cs.subprocess.run

    def happy(cmd, **kw):
        calls.append(cmd)
        for a in cmd:
            if a.endswith(".jpg"):
                Path(a).parent.mkdir(parents=True, exist_ok=True)
                Path(a).write_bytes(b"\xff\xd8jpeg")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    cs.subprocess.run = happy
    try:
        ok = cs._grab(tmp / "x.mp4", 5, tmp / "fine.jpg", 480, gpu=False)
    finally:
        cs.subprocess.run = real
    check(ok and len(calls) == 1, f"只呼叫了 {len(calls)} 次 ffmpeg")


def test_card_thumbnail_retries(tmp: Path):
    print("\n卡片縮圖：同樣要重試")
    real = media.subprocess.run
    stub = Refuser(real)
    media.subprocess.run = stub
    try:
        ok = media.make_thumbnail(tmp / "x.mp4", tmp / "card.jpg", 5, 640)
    finally:
        media.subprocess.run = real
    check(ok, "最後有產出縮圖")
    check(stub.calls == ["cpu", "cpu+fix"], f"嘗試順序：{stub.calls}")


def test_card_thumbnail_checks_size(tmp: Path):
    """A refused conversion still creates the file, at zero bytes."""
    print("\n卡片縮圖：0 位元組不算成功")
    real = media.subprocess.run
    dst = tmp / "empty.jpg"

    def creates_empty(cmd, **kw):
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"")
        return subprocess.CompletedProcess(cmd, 1, b"", b"")

    media.subprocess.run = creates_empty
    try:
        ok = media.make_thumbnail(tmp / "x.mp4", dst, 5, 640)
    finally:
        media.subprocess.run = real
    check(not ok, "回報失敗，而不是把空檔當成成功")


def test_phash_retries(tmp: Path):
    """Quietest of the three: the file simply never matches anything."""
    print("\n重複掃描 phash：同樣要重試")
    real = dedup.subprocess.run
    stub = Refuser(real, stdout=bytes(range(64)))
    dedup.subprocess.run = stub
    try:
        h = dedup._frame_ahash(tmp / "x.mp4", 5)
    finally:
        dedup.subprocess.run = real
    check(h is not None, f"算出了雜湊：{hex(h) if h is not None else None}")
    check(stub.calls == ["cpu", "cpu+fix"], f"嘗試順序：{stub.calls}")


def test_phash_gives_up_cleanly(tmp: Path):
    print("\n重複掃描 phash：兩次都失敗要回 None，不是丟例外")
    real = dedup.subprocess.run
    dedup.subprocess.run = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, b"", b"")
    try:
        h = dedup._frame_ahash(tmp / "x.mp4", 5)
    finally:
        dedup.subprocess.run = real
    check(h is None, "回傳 None")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    test_command_shape()
    for fn in (test_sheet_grab_retries, test_sheet_grab_retries_after_gpu,
               test_no_retry_when_it_works, test_card_thumbnail_retries,
               test_card_thumbnail_checks_size, test_phash_retries,
               test_phash_gives_up_cleanly):
        d = Path(tempfile.mkdtemp())
        try:
            fn(d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
