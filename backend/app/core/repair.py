"""Rescue fragmented-MP4 files that ffmpeg refuses to open.

Some capture tools (X/Twitter downloads, livestream recorders) write a
*fragmented* MP4 and name it `.ts`. That alone is harmless — ffmpeg reads the
header, sees `ftyp`, and uses the MP4 demuxer regardless of the extension. But
if a single fragment inside is damaged, ffmpeg gives up on the **whole file**:
`error reading header` / `Invalid data found`, and the convert scan reported
that as "檔案損毀或不是影片", which is wrong on both counts. Measured on the
one real case: 3,676 fragments, structurally intact all the way to the last
byte, exactly one of them bad — 0.17 seconds of a 31-minute recording. VLC
plays it because VLC is forgiving; ffmpeg is not.

Detection has to be *both* conditions. A library survey found 20 files whose
extension is not `.mp4` but whose header is `ftyp` — and 19 of them read
perfectly. Keying on the header alone would offer to "repair" files with
nothing wrong.

Finding the bad fragment costs a bisection, and that is not for want of
trying: two cheaper structural tests were measured and both failed. Comparing
each `trun`'s declared sample sizes against the following `mdat` flagged 21
fragments including good ones (audio `trun`s carry no sizes — they come from
`trex` defaults), and `tfdt` was monotonic across all 3,676. The bisection
costs about 4 seconds on a 480 MB file, so it is simply not worth more
cleverness.
"""
from __future__ import annotations

import logging
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional

import math
import re

from .media import SUBPROCESS_FLAGS, ffmpeg_error

log = logging.getLogger("vam.repair")

_MAX_BOXES = 500_000        # a sane ceiling; the real file had 14,706


def looks_like_mp4(path: Path) -> bool:
    """True when the file carries an MP4 `ftyp` header, whatever it is named.

    Only meaningful together with "ffprobe could not read it" — on its own it
    matches plenty of perfectly healthy files.
    """
    try:
        with open(path, "rb") as f:
            head = f.read(8)
    except OSError:
        return False
    return len(head) >= 8 and head[4:8] == b"ftyp"


def target_for(src: Path) -> Path:
    """`foo.ts` → `foo_fix.mp4`, next to the source.

    Not plain `foo.mp4` like the convert path uses, because repair keeps the
    original: the two would then sit side by side differing only in extension,
    and the recovered one is the one with frames missing. The suffix says
    which is which at a glance.
    """
    return src.with_name(src.stem + "_fix.mp4")


def top_level_boxes(path: Path) -> list:
    """`[(offset, size, type)]` for the whole file, stopping at anything odd.

    Reads only the 8-byte headers, so it costs seeks rather than bandwidth.
    """
    size = path.stat().st_size
    out = []
    with open(path, "rb") as f:
        off = 0
        while off < size and len(out) < _MAX_BOXES:
            f.seek(off)
            h = f.read(8)
            if len(h) < 8:
                break
            bsz = struct.unpack(">I", h[:4])[0]
            typ = h[4:8]
            if bsz == 1:                      # 64-bit size follows the header
                ext = f.read(8)
                if len(ext) < 8:
                    break
                bsz = struct.unpack(">Q", ext)[0]
            if bsz < 8 or off + bsz > size:
                break
            out.append((off, bsz, typ))
            off += bsz
    return out


def _readable(path: Path) -> bool:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "csv=p=0:nk=1",
         str(path)],
        capture_output=True, timeout=600, creationflags=SUBPROCESS_FLAGS)
    return bool(r.stdout.strip())


def duration_of(path: Path) -> float:
    import json
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_format", "-of", "json",
             str(path)],
            capture_output=True, timeout=600, creationflags=SUBPROCESS_FLAGS)
        return float(json.loads(r.stdout or b"{}")
                     .get("format", {}).get("duration") or 0)
    except Exception:
        return 0.0


def _write(src_f, dst_f, off: int, length: int):
    src_f.seek(off)
    remain = length
    while remain:
        chunk = src_f.read(min(1 << 20, remain))
        if not chunk:
            break
        dst_f.write(chunk)
        remain -= len(chunk)


def _build(src: Path, dst: Path, boxes: list, skip: set,
           end_box: Optional[int] = None):
    """Copy every box except `skip`, optionally stopping before `end_box`."""
    with open(src, "rb") as f, open(dst, "wb") as o:
        for i, (off, bsz, _t) in enumerate(boxes):
            if end_box is not None and i >= end_box:
                break
            if i in skip:
                continue
            _write(f, o, off, bsz)


_OUT_TIME = re.compile(rb"out_time_ms=(\d+)")


def _copy(src: Path, dst: Path, report: Callable[[float], None],
          chunk: int = 8 << 20) -> None:
    """`shutil.copy2`, but saying how far it has got.

    The copy is the one stage whose length is known exactly in advance, and on
    a multi-GB recording from another drive it is the longest wait of the run.
    """
    total = src.stat().st_size or 1
    done = 0
    report(0.0)
    with open(src, "rb") as f, open(dst, "wb") as o:
        while True:
            b = f.read(chunk)
            if not b:
                break
            o.write(b)
            done += len(b)
            report(done / total)
    shutil.copystat(src, dst)


def _remux(src: Path, dst: Path, duration: float,
           report: Callable[[float], None]) -> tuple:
    """Stream-copy into a plain MP4, reporting `-progress` against `duration`.

    Returns (returncode, stderr text). A duration of 0 means "unknown" and the
    stage simply reports its start; guessing a fraction would be worse.
    """
    report(0.0)
    p = subprocess.Popen(
        ["ffmpeg", "-hide_banner", "-v", "error", "-y", "-i", str(src),
         "-c", "copy", "-movflags", "+faststart",
         "-progress", "pipe:1", "-nostats", str(dst)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=SUBPROCESS_FLAGS)
    try:
        for line in p.stdout:
            m = _OUT_TIME.search(line)
            if m and duration > 0:
                report(int(m.group(1)) / 1e6 / duration)
    finally:
        p.stdout.close()
        err = p.stderr.read().decode("utf-8", "replace").strip()
        p.stderr.close()
        p.wait()
    return p.returncode, err


def repair(src: Path, dst: Path,
           progress: Optional[Callable[[dict], None]] = None) -> dict:
    """Rebuild `src` into `dst` without the fragments ffmpeg chokes on.

    Everything kept is copied byte for byte — nothing is re-encoded, so the
    surviving video is bit-identical to the original. `src` is never modified.

    Raises RuntimeError when the file cannot be made readable at all.
    """
    # Every stage used to report pct 0, so the dialog sat on "remux — 0%" for
    # the whole run and then jumped to done (a real report: 2 min 50 s of
    # that). Weights are from a measured run on a 287 MB recording: copy
    # 1.6 s, finding the bad fragment 1.7 s, remux 0.8 s.
    W_COPY, W_FIND, W_REMUX = (0.00, 0.40), (0.40, 0.80), (0.80, 1.00)
    floor = [0.0]                     # never report going backwards

    def say(slot, f, label, **kw):
        if not progress:
            return
        lo, hi = slot
        v = max(floor[0], lo + (hi - lo) * max(0.0, min(1.0, f)))
        floor[0] = v
        progress({"pct": v, "stage": label, **kw})

    work = Path(tempfile.mkdtemp(prefix="vamrepair-"))
    try:
        # Work on a local copy: the bisection reads the file a dozen times and
        # the originals live on other drives or the NAS.
        local = work / "source.bin"
        _copy(src, local, lambda f: say(W_COPY, f, "複製到本機"))

        say(W_FIND, 0.0, "找出壞掉的片段")
        boxes = top_level_boxes(local)
        moofs = [i for i, b in enumerate(boxes) if b[2] == b"moof"]
        if not moofs:
            raise RuntimeError("不是分段 MP4（找不到 moof），無法用這個方式修復")

        probe_f = work / "probe.mp4"
        rebuilt = work / "rebuilt.mp4"
        skip: set = set()
        dropped: list = []

        # Each round finds the earliest fragment that still breaks the file and
        # drops it. No ceiling on the number of rounds — the user asked for
        # none, and it terminates either way: worst case every fragment goes.
        for round_no in range(1, len(moofs) + 1):
            _build(local, rebuilt, boxes, skip)
            if _readable(rebuilt):
                break
            lo, hi = 0, len(moofs)
            # The number of rounds is unknown up front, so each round gets
            # half of what is left of the slice: monotonic, never overshoots,
            # and the common case (one bad fragment) fills most of it.
            r_lo = 1 - 0.5 ** (round_no - 1)
            r_hi = 1 - 0.5 ** round_no
            steps = max(1, math.ceil(math.log2(max(2, len(moofs)))))
            step = 0

            def ok(n: int) -> bool:
                _build(local, probe_f, boxes, skip,
                       end_box=moofs[n] if n < len(moofs) else len(boxes))
                return _readable(probe_f)

            while hi - lo > 1:
                step += 1
                label = "找出壞掉的片段" + ("（第 %d 輪）" % round_no
                                           if round_no > 1 else "")
                say(W_FIND, r_lo + (r_hi - r_lo) * min(1.0, step / steps), label)
                mid = (lo + hi) // 2
                if ok(mid):
                    lo = mid
                else:
                    hi = mid
            bad = hi - 1                       # 0-based index of the fragment
            if moofs[bad] in skip:
                raise RuntimeError("找不到還能丟的片段，這個檔案修不了")
            skip.add(moofs[bad])
            if moofs[bad] + 1 < len(boxes) and boxes[moofs[bad] + 1][2] == b"mdat":
                skip.add(moofs[bad] + 1)
            dropped.append(bad)
            say(W_FIND, r_hi, "找出壞掉的片段", dropped=len(dropped),
                fragment=bad + 1, fragments=len(moofs))
        else:
            raise RuntimeError("丟掉所有片段後仍然讀不到，這個檔案修不了")

        # Rewrite as an ordinary (non-fragmented) MP4 so the rest of the app —
        # thumbnails, contact sheets, range streaming — behaves normally.
        say(W_FIND, 1.0, "找出壞掉的片段")
        code, err = _remux(rebuilt, dst, duration_of(rebuilt),
                           lambda f: say(W_REMUX, f, "重新封裝"))
        if code != 0 or not dst.exists() or dst.stat().st_size == 0:
            dst.unlink(missing_ok=True)
            raise RuntimeError(ffmpeg_error(err, code) if err else "重新封裝失敗")

        got = duration_of(dst)
        if got <= 0:
            dst.unlink(missing_ok=True)
            raise RuntimeError("修復後的檔案讀不到時長")

        # Rough, and deliberately on the pessimistic side: fragments vary in
        # length, so this averages them. On the real file it reported 0.5 s
        # where the dropped fragment actually held 5 frames (~0.17 s). Over-
        # stating the loss is the safe direction for a number the user reads
        # after the fact.
        lost = len(dropped) / len(moofs) * got if len(moofs) else 0.0
        return {"fragments": len(moofs), "dropped": len(dropped),
                "dropped_at": dropped, "duration": got,
                "lost_seconds": round(lost, 2)}
    finally:
        shutil.rmtree(work, ignore_errors=True)
