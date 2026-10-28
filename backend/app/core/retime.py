"""Rebuild the missing composition times (`ctts`) of an MP4 with B-frames.

**The defect.** Some encoders write an MP4 whose H.264 stream uses B-frames but
whose container gives every frame the same presentation and decode time
(PTS == DTS for 100% of packets, i.e. no `ctts`). With B-frames the decode
order is not the display order, so that is simply wrong. Chrome believes the
container and renders in decode order, so every frame arrives "earlier than the
last rendered frame" and gets dropped — measured on `FC2-PPV-1224187.mp4`,
about half the frames, turning 29.97 fps into roughly 15. VLC and friends
reconstruct the order from the bitstream's POC and never notice, which is why
the same file is smooth locally and stutters in a browser. Reported as
「播放會鈍鈍的」 and diagnosed from `chrome://media-internals`:

    Dropping frame with timestamp 2.5025 s, which is earlier than
    the last rendered frame (2.53587 s).

12 of the 40 files in `K:\\FC2\\FC2_2022前` carry it — 42.5 GB.

**Why MP4Box and not ffmpeg.** Five stream-copy routes were measured and all
five failed, because `-c copy` never parses the bitstream and so just carries
the flat timestamps across: plain remux, `-fflags +genpts`,
`-bsf:v h264_mp4toannexb`, a round trip through MPEG-TS, and a round trip
through raw Annex-B. MP4Box parses the SPS and the picture order counts when it
imports raw H.264, which is the one step that recovers the real display order.

**It is lossless for the picture, and may shrink the file.** Measured per NAL
type on `FC2-PPV-1019007.mp4`: slices, SEI, SPS and PPS all byte-identical;
the only thing dropped was 1.5 MB per 20 s of *filler data* (NAL type 12), the
padding a CBR encoder inserts to waste bits. Decoded frames compared as a
multiset over 120 s: 3,597 frames, none unique to either side. That file lost
24% of its size; two others lost nothing, so treat the saving as incidental.

**Audio sync is preserved on purpose.** Extracting the tracks separately drops
the source's edit lists, which carry a small per-track offset (measured 12–18
ms between video and audio). Left alone that becomes a constant relative shift
— inaudible, but wrong — so the offset is measured up front and handed back to
MP4Box as a track delay. Residual after rounding to whole milliseconds: 0.5 ms
and 1.4 ms on the two files checked.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Tuple

from .media import ffmpeg_error

_OUT_TIME = re.compile(rb"out_time_ms=(\d+)")
# MP4Box colours its output; the escape's trailing `m` otherwise glues onto the
# label and produces stage names like 「mImport」.
_ANSI = re.compile(rb"\x1b\[[0-9;]*[A-Za-z]")
# MP4Box: `Importing AVC-H264: |=====   | (37/100)` on stderr, CR-separated.
_BOX_PCT = re.compile(rb"([A-Za-z][A-Za-z0-9 \-]*?):\s*\|[^|]*\|\s*\((\d+)/100\)")

log = logging.getLogger("vam")

# Where the GPAC installer puts it. `shutil.which` first so a PATH install or a
# container image wins; this list is the Windows fallback.
_CANDIDATES = (
    r"C:\Program Files\GPAC\MP4Box.exe",
    r"C:\Program Files (x86)\GPAC\MP4Box.exe",
)

_SAMPLE_AT = "300%+8"      # 8 seconds of packets from 5 minutes in
_FRAMES_AT = "300%+3"


def mp4box() -> Optional[str]:
    """Path to MP4Box, or None when GPAC is not installed."""
    p = shutil.which("MP4Box") or shutil.which("mp4box")
    if p:
        return p
    for c in _CANDIDATES:
        if os.path.isfile(c):
            return c
    return None


def available() -> bool:
    return mp4box() is not None


def _probe(path: Path, args: list) -> dict:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json"] + args + [str(path)],
        capture_output=True)
    try:
        return json.loads(r.stdout.decode("utf-8", "replace"))
    except Exception:
        return {}


def _first_pts(path: Path, sel: str) -> float:
    d = _probe(path, ["-select_streams", sel, "-show_entries", "packet=pts_time",
                      "-read_intervals", "%+2"])
    v = [float(x["pts_time"]) for x in d.get("packets", [])
         if x.get("pts_time") is not None]
    return min(v) if v else 0.0


def needs_retime(path: Path) -> Tuple[bool, str]:
    """Does this file have flat composition times despite using B-frames?

    **Both conditions are required.** A file with no B-frames is supposed to
    have PTS == DTS, so the timestamps alone would flag a great many perfectly
    good files — the same trap `repair.looks_like_mp4` documents, where 19 of
    20 header matches were fine. The frame-type probe is the expensive half, so
    it only runs when the cheap half has already said "suspicious".
    """
    d = _probe(path, ["-select_streams", "v:0",
                      "-show_entries", "packet=pts,dts",
                      "-read_intervals", _SAMPLE_AT])
    pk = [x for x in d.get("packets", [])
          if x.get("pts") is not None and x.get("dts") is not None]
    if len(pk) < 30:
        return False, ""                    # too short to judge; leave it alone
    if any(x["pts"] != x["dts"] for x in pk):
        return False, ""                    # has ctts, nothing to do

    d = _probe(path, ["-select_streams", "v:0", "-show_frames",
                      "-show_entries", "frame=pict_type",
                      "-read_intervals", _FRAMES_AT])
    fr = [f.get("pict_type") for f in d.get("frames", [])]
    nb = sum(1 for f in fr if f == "B")
    if not nb:
        return False, ""
    return True, "影格時序錯誤（有 B 影格卻沒有顯示時間表，瀏覽器會掉格）"


def supported(info: dict) -> Optional[str]:
    """Why this file cannot be retimed, or None when it can.

    Kept separate from `needs_retime` so the scan can say "this is the defect
    but I cannot fix it here" rather than silently skipping.
    """
    if (info.get("vcodec") or "") != "h264":
        return "只支援 H.264（MP4Box 的 POC 解析是逐編碼器實作的）"
    if info.get("nb_audio", 1) > 1:
        # Silently keeping only the first track would lose the others, and the
        # user would find out much later. Refuse instead.
        return "有多條音軌，目前的流程只會保留第一條"
    a = info.get("acodec") or ""
    if a and a != "aac":
        return f"音訊為 {a}，目前只驗證過 AAC"
    return None


def retime(src: Path, dst: Path, progress=None) -> dict:
    """Write `src` to `dst` with the composition times rebuilt.

    Every step is a stream copy; nothing is re-encoded.
    """
    box = mp4box()
    if not box:
        raise RuntimeError("找不到 MP4Box（需要安裝 GPAC）")

    st = _probe(src, ["-show_streams"])
    streams = st.get("streams", [])
    vs = [s for s in streams if s.get("codec_type") == "video"]
    aus = [s for s in streams if s.get("codec_type") == "audio"]
    if len(vs) != 1:
        raise RuntimeError("只支援單一影像軌，這個檔案有 %d 軌" % len(vs))

    fps = vs[0].get("r_frame_rate") or "30000/1001"
    # The source's own video/audio offset, so the rebuild does not silently
    # shift one against the other by dropping the edit lists.
    delay_ms = 0
    if aus:
        delay_ms = round((_first_pts(src, "a:0") - _first_pts(src, "v:0")) * 1000)

    dur = 0.0
    try:
        dur = float((st.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        pass
    if dur <= 0:
        try:
            dur = float(vs[0].get("duration") or 0)
        except (TypeError, ValueError):
            dur = 0.0

    # Stage weights, from the measured split on a 6.45 GB / 120-minute file:
    # the two passes each move the whole file once, and the audio pass is
    # noise. A run with no progress at all looked frozen for 83 seconds.
    W = ((0.00, 0.45, "抽出影像位元流"),
         (0.45, 0.50, "抽出音訊"),
         (0.50, 1.00, "重建影格時序"))

    def _report(lo, hi, label, f):
        if progress:
            progress({"pct": lo + (hi - lo) * max(0.0, min(1.0, f)),
                      "stage": label})

    tmp = Path(tempfile.mkdtemp(prefix="retime_", dir=str(dst.parent)))
    raw, aac = tmp / "v.h264", tmp / "a.aac"
    try:
        def _ff(args, lo, hi, label):
            """ffmpeg with `-progress`, mapped into this stage's slice."""
            _report(lo, hi, label, 0.0)
            p = subprocess.Popen(
                ["ffmpeg", "-hide_banner", "-v", "error", "-y"] + args
                + ["-progress", "pipe:1", "-nostats"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                for line in p.stdout:
                    m = _OUT_TIME.search(line)
                    if m and dur > 0:
                        _report(lo, hi, label, int(m.group(1)) / 1e6 / dur)
            finally:
                p.stdout.close()
                err = p.stderr.read().decode("utf-8", "replace").strip()
                p.stderr.close()
                p.wait()
            if p.returncode:
                # Same reasoning as convert.run_one: the last 300 characters
                # of a log that repeats one fault 51,058 times say nothing.
                raise RuntimeError("ffmpeg: " + ffmpeg_error(err, p.returncode))

        lo, hi, label = W[0]
        _ff(["-i", str(src), "-map", "0:v:0", "-c", "copy",
             "-bsf:v", "h264_mp4toannexb", "-f", "h264", str(raw)],
            lo, hi, label)

        add = ["-add", "%s:fps=%s" % (raw, fps)]
        if aus:
            lo, hi, label = W[1]
            _ff(["-i", str(src), "-map", "0:a:0", "-c", "copy", "-f", "adts",
                 str(aac)], lo, hi, label)
            add += ["-add", str(aac) + (":delay=%d" % delay_ms if delay_ms else "")]

        # MP4Box writes `Label: |====| (n/100)` to stderr, carriage-return
        # separated. Two phases (import, then write), so the percentage resets
        # — the label is reported with it rather than pretending it is one bar.
        lo, hi, label = W[2]
        _report(lo, hi, label, 0.0)
        p = subprocess.Popen([box] + add + ["-new", str(dst)],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        tail, buf, order, seen_f = b"", b"", [], 0.0
        try:
            for chunk in iter(lambda: p.stdout.read(256), b""):
                tail = (tail + chunk)[-2000:]
                # Carry the unmatched remainder: a fixed-size read cuts labels
                # in half, and the first version reported stages called
                # 「ng」 and 「riting」.
                buf = _ANSI.sub(b"", buf + chunk)
                last = 0
                for m in _BOX_PCT.finditer(buf):
                    what = m.group(1).decode("utf-8", "replace").strip()
                    last = m.end()
                    # MP4Box runs two phases (import, then write) and each
                    # counts 0→100 of its own, so mapping both onto one slice
                    # made the bar jump back to 50%. Each label gets its own
                    # share, in order of first appearance.
                    if what not in order:
                        order.append(what)
                    share = 1.0 / max(2, len(order))
                    f = order.index(what) * share + share * int(m.group(2)) / 100.0
                    seen_f = max(seen_f, f)      # never go backwards
                    _report(lo, hi, "%s（%s）" % (label, what), seen_f)
                buf = buf[last:][-400:]
        finally:
            p.stdout.close()
            p.wait()
        if p.returncode:
            raise RuntimeError(
                "MP4Box: " + tail.decode("utf-8", "replace")[-300:])
        _report(lo, hi, label, 1.0)
        return {"delay_ms": delay_ms}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def verify(src: Path, dst: Path) -> Optional[str]:
    """Return None when the rebuild looks right, otherwise why it does not.

    The frame count is the check that matters: the picture data is copied, so
    the one thing that could go wrong is losing or duplicating frames while the
    timestamps are recomputed. The ctts check confirms the defect is actually
    gone rather than faithfully reproduced.
    """
    def nframes(p):
        d = _probe(p, ["-select_streams", "v:0",
                       "-show_entries", "stream=nb_frames"])
        s = (d.get("streams") or [{}])[0]
        try:
            return int(s.get("nb_frames") or 0)
        except (TypeError, ValueError):
            return 0

    a, b = nframes(src), nframes(dst)
    if a and b and a != b:
        return f"影格數不符（原檔 {a}，輸出 {b}）"

    d = _probe(dst, ["-select_streams", "v:0", "-show_entries", "packet=pts,dts",
                     "-read_intervals", _SAMPLE_AT])
    pk = [x for x in d.get("packets", [])
          if x.get("pts") is not None and x.get("dts") is not None]
    if not pk:
        return "輸出檔讀不到影像封包"
    if all(x["pts"] == x["dts"] for x in pk):
        return "時序沒有被修正（輸出仍然沒有顯示時間表）"
    return None
