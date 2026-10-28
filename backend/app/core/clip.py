"""Cut one segment out of a video into a file of its own.

Stream copy is the default because it is effectively free — measured on a
5 GB / 123-minute 1080p film, cutting 10:02–12:30 took **0.24 s** by copy
against **82 s** for libx264 and **15 s** for nvenc — and it loses nothing.

The catch is that a copy can only *begin* on a keyframe. Asking ffmpeg for an
arbitrary second produces a file whose opening frames reference a keyframe
that was left behind: measured, the first **0.567 s** of such a clip had no
I-frame at all, so it plays as garbage or a freeze — and ffmpeg reports
success. So the start is snapped back to the preceding keyframe here, and the
caller shows the time it actually landed on.

Keyframe spacing sampled across six random films in the library: 1.0, 2.0,
2.0, 2.5, 2.5 and 8.3 seconds. Usually a second or two of extra lead-in,
occasionally more. `precise=True` re-encodes instead and cuts to the frame.

The end is deliberately *not* snapped: every frame kept after the last
keyframe still references frames that are present, so the tail is fine.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Callable, Optional

from .media import SUBPROCESS_FLAGS

log = logging.getLogger("vam.clip")

# How far back to look for a keyframe, in seconds. Most files answer within
# the first window; the widest is for the 8-second-GOP outliers and anything
# worse than that.
_LOOKBACK = (12.0, 40.0, 150.0)

MIN_SECONDS = 1.0
_SPACE_MARGIN = 1.15        # estimates are rough; refuse to fill the disk

# H.264/HEVC need Annex-B framing to go into MPEG-TS. Anything else is left
# alone and ffmpeg will refuse loudly if TS cannot carry it, which is the
# right outcome: the message points at 精準切割.
_TS_BSF = {"h264": "h264_mp4toannexb", "hevc": "hevc_mp4toannexb"}


def keyframe_before(src: Path, t: float) -> Optional[float]:
    """Timestamp of the last keyframe at or before `t`, or None if unknown.

    ffprobe's `-read_intervals` is approximate at its start — asking for
    `590%602` came back with frames from 588.56, because it seeks to the
    keyframe before the interval and reads from there. That is harmless (we
    take the largest stamp that is still `<= t`) but it also means a window
    starting at 0 can come back empty, which is why reaching the head of the
    file answers 0.0 rather than "unknown": a file always opens on a keyframe.
    """
    if t <= 0:
        return 0.0
    for window in _LOOKBACK:
        lo = max(0.0, t - window)
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "error",
                 "-read_intervals", f"{lo}%{t + 0.001}",
                 "-select_streams", "v:0", "-skip_frame", "nokey",
                 "-show_entries", "frame=pts_time", "-of", "json", str(src)],
                capture_output=True, timeout=180, creationflags=SUBPROCESS_FLAGS)
            frames = json.loads(r.stdout or b"{}").get("frames", [])
        except Exception:
            log.exception("keyframe probe failed for %s", src)
            return None
        stamps = [float(f["pts_time"]) for f in frames if "pts_time" in f]
        before = [s for s in stamps if s <= t + 0.001]
        if before:
            return max(before)
        if lo <= 0.0:
            return 0.0          # looked at the whole head; the file opens on one
    return None


def first_keyframe(src: Path) -> Optional[float]:
    """Timestamp of the file's very first keyframe.

    Rarely 0 — measured 0.033 on one file and 0.017 on another. That matters
    because a segment snapping to it must be cut with `-ss 0`; see
    `_build_ts_cmd`.
    """
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-read_intervals", "%+5",
             "-select_streams", "v:0", "-skip_frame", "nokey",
             "-show_entries", "frame=pts_time", "-of", "json", str(src)],
            capture_output=True, timeout=180, creationflags=SUBPROCESS_FLAGS)
        stamps = [float(f["pts_time"])
                  for f in json.loads(r.stdout or b"{}").get("frames", [])
                  if "pts_time" in f]
    except Exception:
        log.exception("first-keyframe probe failed for %s", src)
        return None
    return min(stamps) if stamps else None


def merge_ranges(segments) -> list:
    """Sort by start and fuse anything that overlaps or touches.

    Two segments that overlap would put the shared stretch into the output
    twice, and out of order if the list was built out of order. Merging is
    what the user meant — these are highlights of one film, not a playlist.
    """
    out = []
    for a, b in sorted((float(a), float(b)) for a, b in segments):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def _stamp(seconds: float) -> str:
    """`1002` for 10:02, `10503` for 1:05:03 — compact but still readable."""
    s = max(0, int(round(seconds)))
    h, m, sec = s // 3600, (s // 60) % 60, s % 60
    return (f"{h}{m:02d}{sec:02d}" if h else f"{m:02d}{sec:02d}")


def output_path(src: Path, segments) -> Path:
    """`OPKT-033_clip_1002-1230.mp4`, next to the source.

    Named after the first segment only, however many there are — source stems
    are already long (`Selenaaaaa--20260802-044946`) and spelling out six
    ranges produces a name nobody can read. A joined clip therefore looks the
    same as a single cut starting at the same place; the `_2` suffix below
    keeps them from colliding.

    Always `.mp4` — the clip is meant to be watched in the browser, and the
    catalogue number survives the suffix either way (verified against
    `detect_video_id`, including the amateur `062125_001` form), so the film
    title still attaches to it.
    """
    first = segments[0]
    base = f"{src.stem}_clip_{_stamp(first[0])}-{_stamp(first[1])}"
    cand = src.with_name(base + ".mp4")
    n = 2
    while cand.exists():
        cand = src.with_name(f"{base}_{n}.mp4")
        n += 1
    return cand


def plan(src: Path, segments, precise: bool,
         total_duration: float, total_size: int) -> dict:
    """What this cut would produce, without producing it.

    One segment or many: several are joined into a single file, which is why
    the per-segment detail comes back as a list — each one snaps to its own
    keyframe, so the drift has to be reported per segment, not once.
    """
    clean = []
    for a, b in segments:
        a = max(0.0, float(a))
        b = float(b)
        if total_duration > 0:
            b = min(b, total_duration)
        if b - a < MIN_SECONDS:
            return {"ok": False,
                    "error": f"片段太短（至少 {MIN_SECONDS:.0f} 秒）"}
        clean.append((a, b))
    if not clean:
        return {"ok": False, "error": "沒有選取任何片段"}

    clean = merge_ranges(clean)
    # A segment that snaps to the file's own first keyframe is reported — and
    # cut — as starting at 0. The first keyframe is usually a hair after zero
    # (0.033 and 0.017 on two real files), and treating that as a distinct
    # start position is both meaningless and, in copy mode, actively broken.
    head = first_keyframe(src) if not precise else None
    parts, total_span = [], 0.0
    for a, b in clean:
        actual, snapped = a, False
        if not precise:
            kf = keyframe_before(src, a)
            if kf is not None:
                if head is not None and kf <= head + 0.001:
                    kf = 0.0
                actual, snapped = kf, True
        span = b - actual
        total_span += span
        parts.append({"start": a, "end": b, "actual_start": actual,
                      "drift": round(a - actual, 3), "snapped": snapped,
                      "duration": round(span, 3)})

    est = (int(total_size * (total_span / total_duration))
           if total_duration > 0 else 0)
    try:
        free = shutil.disk_usage(src.parent).free
    except OSError:
        free = -1

    out = output_path(src, clean)
    return {
        "ok": True,
        "parts": parts,
        "merged": len(clean) < len(segments),
        "duration": round(total_span, 3),
        "out_name": out.name,
        "est_size": est,
        "free": free,
        "enough_space": free < 0 or free > est * _SPACE_MARGIN,
    }


def _build_encode_cmd(src: Path, dst: Path, begin: float, span: float,
                      crf: int, gpu: bool) -> list:
    """Precise mode: decode, cut to the frame, re-encode."""
    return ["ffmpeg", "-hide_banner", "-v", "error", "-y",
            "-ss", f"{begin:.6f}", "-i", str(src), "-t", f"{span:.6f}",
            "-map", "0:v:0", "-map", "0:a:0?"] + _encoder(crf, gpu) + [
            "-movflags", "+faststart", "-progress", "pipe:1", "-nostats",
            str(dst)]


def _encoder(crf: int, gpu: bool) -> list:
    if gpu:
        v = ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr",
             "-cq", str(crf)]
    else:
        v = ["-c:v", "libx264", "-crf", str(crf), "-preset", "medium"]
    return v + ["-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k"]


def _build_ts_cmd(src: Path, dst: Path, start: float, end: float,
                  info: dict, actual_start: float = -1.0) -> list:
    """Copy mode: cut one segment losslessly into MPEG-TS.

    Two things here are not obvious and both were measured.

    **Seek at the time the user asked for, not at the keyframe we snapped to.**
    Seeking to a keyframe's own timestamp is a coin toss — ffmpeg's index is by
    DTS, which for a B-frame stream sits earlier than the PTS, so it lands on
    the *previous* keyframe. Sometimes the wanted I-frame survives the trim,
    sometimes it does not: on one file, 600.5726 lost it (two seconds of broken
    picture, exit code 0) while 2998.9926 kept it. `-noaccurate_seek` at the
    requested time lands on the keyframe before it — the one `plan()` already
    reported — every time. `-t` is then measured from the requested time, so
    the segment physically spans `end - keyframe`.

    **MPEG-TS, not MP4.** A copy cannot re-encode the leading partial GOP away,
    so those frames are always physically in the output; MP4 merely *hides*
    them behind an edit list. That is invisible for a single clip and wrong the
    moment segments are joined, because the concat demuxer ignores edit lists
    and the hidden frames come back — measured, a 3-segment join came out 6 s
    long with the same packet count. TS has no edit lists, so what the file
    contains is what it plays, and the join is exact.
    """
    # Special case: the segment starts inside the first GOP, so the keyframe it
    # snaps to *is* the start of the file. A non-zero `-ss` that resolves to
    # position 0 produces a TS with **no SPS/PPS at all** — measured on a real
    # file, `-ss 0.97` gave a stream ffprobe reads as `h264 0x0`, and muxing it
    # died with 50 KB of "non-existing PPS 0 referenced". `-ss 0` on the same
    # file is fine, and so is `-ss 60`; it is specifically a seek that lands on
    # the file's own first keyframe. Since `plan()` already reports the start as
    # 00:00.0 in that case, asking for 0 is also the honest thing to do.
    if 0 <= actual_start <= 0.0001:
        start = 0.0
    cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-y",
           "-noaccurate_seek", "-ss", f"{start:.6f}", "-i", str(src),
           "-t", f"{end - start:.6f}", "-map", "0:v:0", "-map", "0:a:0?",
           "-c", "copy"]
    bsf = _TS_BSF.get(info.get("vcodec", ""))
    if bsf:
        cmd += ["-bsf:v", bsf]
    cmd += ["-f", "mpegts", "-progress", "pipe:1", "-nostats", str(dst)]
    return cmd


def _build_mux_cmd(listfile: Path, dst: Path, info: dict) -> list:
    """Join the TS parts (or wrap the single one) into the final MP4."""
    cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-y",
           "-f", "concat", "-safe", "0", "-i", str(listfile), "-c", "copy"]
    # AAC inside MPEG-TS is ADTS-framed; MP4 needs the raw ASC form
    if info.get("acodec") == "aac":
        cmd += ["-bsf:a", "aac_adtstoasc"]
    # without faststart the index sits at the end and the browser has to
    # download the whole clip before it can seek
    cmd += ["-movflags", "+faststart", "-progress", "pipe:1", "-nostats",
            str(dst)]
    return cmd


_TIME_RE = re.compile(rb"out_time_ms=(\d+)")


def _run_cmd(cmd, total: float,
             progress: Optional[Callable[[float], None]] = None) -> None:
    """Run ffmpeg, following `-progress` on stdout.

    stderr is drained by its own thread. Reading it only after stdout is
    exhausted deadlocks: a pipe holds ~64 KB, and ffmpeg that fills it blocks
    on write — which stops it producing stdout, so the loop below waits for
    output that can never come. Measured: a cut whose intermediate file was
    malformed emitted 50 KB of decoder errors and hung for over nine minutes
    with zero I/O, sitting at "100%" in the UI.
    """
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=SUBPROCESS_FLAGS)
    err_parts: list = []

    def _drain():
        try:
            for chunk in iter(lambda: proc.stderr.read(65536), b""):
                # keep only the tail; 50 KB of repeated decoder noise is not
                # worth holding, and only the last line is ever reported
                err_parts.append(chunk)
                if len(err_parts) > 4:
                    del err_parts[0]
        except Exception:
            pass

    pump = threading.Thread(target=_drain, daemon=True)
    pump.start()
    try:
        for line in proc.stdout:
            m = _TIME_RE.search(line)
            if m and progress:
                progress(min(1.0, int(m.group(1)) / 1e6 / max(total, 0.001)))
    finally:
        proc.stdout.close()
        proc.wait()
        pump.join(timeout=5)
        try:
            proc.stderr.close()
        except Exception:
            pass
    if proc.returncode != 0:
        err = b"".join(err_parts).decode("utf-8", "replace").strip()
        raise RuntimeError(err.splitlines()[-1] if err else
                           f"ffmpeg 失敗（代碼 {proc.returncode}）")


def _build_join_cmd(src: Path, dst: Path, parts, crf: int, gpu: bool) -> list:
    """Precise mode: cut and join every segment in one re-encoding pass.

    Copy mode cannot do this — a filter graph decodes — so it goes through
    temporary TS parts instead.
    """
    cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-y"]
    for a, b in parts:
        cmd += ["-ss", f"{a:.6f}", "-to", f"{b:.6f}", "-i", str(src)]
    n = len(parts)
    chain = "".join(f"[{i}:v:0][{i}:a:0]" for i in range(n))
    cmd += ["-filter_complex", f"{chain}concat=n={n}:v=1:a=1[v][a]",
            "-map", "[v]", "-map", "[a]"] + _encoder(crf, gpu) + [
            "-movflags", "+faststart", "-progress", "pipe:1", "-nostats",
            str(dst)]
    return cmd


def run(src: Path, dst: Path, parts, precise: bool, info: dict,
        crf: int = 20, gpu: bool = False,
        progress: Optional[Callable[[float], None]] = None) -> None:
    """Cut every segment and leave exactly one file behind.

    `parts` are dicts from `plan()` — already merged, sorted and snapped.
    Raises RuntimeError carrying ffmpeg's own last line on failure.
    """
    total = sum(p["duration"] for p in parts)

    if precise:
        cmd = (_build_encode_cmd(src, dst, parts[0]["start"], total, crf, gpu)
               if len(parts) == 1
               else _build_join_cmd(src, dst,
                                    [(p["start"], p["end"]) for p in parts],
                                    crf, gpu))
        _run_cmd(cmd, total, progress)
        return

    # Copy mode always goes via TS parts, even for a single segment: one code
    # path, and the edit-list trap in `_build_ts_cmd` cannot come back through
    # a second one. Measured cost of the extra remux on a 2.5-minute clip:
    # about a third of a second.
    #
    # The parts are written *next to the destination* on purpose. The system
    # temp dir is usually another volume, and the join would then read and
    # rewrite every byte across it — these are full-bitrate video, so that is
    # gigabytes of pointless traffic to and from the NAS.
    work = Path(tempfile.mkdtemp(dir=str(dst.parent), prefix=".vamclip-"))
    # gigabytes of parts beside the user's files: a restart mid-cut must not
    # strand them there (jobs.recover removes it)
    from .. import jobs
    jobs.writing(src, work)
    try:
        files = []
        done = 0.0
        for i, p in enumerate(parts):
            part = work / f"{i:03d}.ts"
            base, span = done, p["duration"]
            _run_cmd(_build_ts_cmd(src, part, p["start"], p["end"], info,
                                   p["actual_start"]),
                     span,
                     (lambda f, b=base, s=span:
                      progress(min(1.0, (b + f * s) / total)))
                     if progress else None)
            files.append(part)
            done += span
        lst = work / "list.txt"
        lst.write_text("".join(f"file '{f.as_posix()}'\n" for f in files),
                       encoding="utf-8")
        _run_cmd(_build_mux_cmd(lst, dst, info), total, None)
    finally:
        shutil.rmtree(work, ignore_errors=True)
        jobs.wrote(work)


def tolerance_for(n_parts: int) -> float:
    """How far off the total may legitimately be, for `n_parts` segments.

    Every cut ends on a frame boundary and every join carries the audio frame
    that straddles it, so a small overshoot is normal and accumulates with the
    segment count. Measured per seam: 0.03 s on a real 1080p film, 0.21 s on a
    short synthetic clip with a different audio rate. Generous enough for the
    worse of those, still nowhere near the failure worth catching — a cut that
    silently produced three seconds instead of three minutes.
    """
    return max(1.0, 0.3 * n_parts)


def verify(dst: Path, expect_seconds: float,
           tolerance: float = 1.0) -> Optional[str]:
    """None when the clip looks right, otherwise why it does not.

    Same idea as `convert.verify`, but the yardstick is the requested span
    rather than the source file: a cut that silently produced three seconds
    of video is the failure worth catching.
    """
    if not dst.exists() or dst.stat().st_size == 0:
        return "輸出檔不存在或為空"
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format",
             "-of", "json", str(dst)],
            capture_output=True, timeout=120, creationflags=SUBPROCESS_FLAGS)
        d = json.loads(r.stdout or b"{}")
    except Exception:
        return "輸出檔無法讀取"
    vs = [s for s in d.get("streams", []) if s.get("codec_type") == "video"]
    if not vs:
        return "輸出檔沒有視訊軌"
    try:
        got = float(d.get("format", {}).get("duration") or 0)
    except (TypeError, ValueError):
        got = 0.0
    if abs(got - expect_seconds) > tolerance:
        return f"時長不符（預期 {expect_seconds:.1f}s，實際 {got:.1f}s）"
    return None
