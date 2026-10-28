"""Re-encode videos that are larger than they need to be.

Separate from `convert.py` on purpose: that module answers "can a browser play
this?" and reaches for `-c copy` whenever it can. This one answers "is this
file paying for quality nobody asked for?", and its whole job is to lose some
on purpose.

Everything below was measured rather than assumed.

**Bitrate is the candidate filter.** It predicts how much a file will shrink
better than bits-per-pixel does — correlation with the actual saving was
**+0.87** for bitrate against **+0.71** for bpp across eight sample files. The
theoretically-fairer metric lost to the blunt one.

**CRF is a quality target, so one value works library-wide.** The same CRF
lands on the same quality regardless of how wasteful the source was; only the
saving changes:

                     ordinary 1080p (6.1 Mbps)   over-encoded 4K (52 Mbps)
        crf 20       41% saved, SSIM 0.9939      64% saved, SSIM 0.9935
        crf 23       58% saved, SSIM 0.9923      77% saved, SSIM 0.9918
        crf 26       70% saved, SSIM 0.9902      86% saved, SSIM 0.9898
        crf 28       76% saved, SSIM 0.9884      89% saved, SSIM 0.9882

**No GPU option, and that is deliberate.** nvenc encodes two to three times
faster but saves less than half as much: on the same 4K clip, `-cq 23` gave
37% against libx264's 64% at the same resolution, and 84% against 92% when
scaled to 1080. Speed is not what this feature is for.

**No frame-rate option either.** Dropping 60 fps to 30 saved under 1% (9.6 MB
against 9.7 MB) because the encoder already handles near-duplicate frames.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Callable, Optional

from .media import SUBPROCESS_FLAGS, VIDEO_EXT

log = logging.getLogger("vam.shrink")

DEFAULT_MIN_MBPS = 18.0     # see the note in the API layer on why this default
DEFAULT_CRF = 26
CRF_CHOICES = (20, 23, 26, 28)
EDGE_CHOICES = (0, 2560, 1920, 1280)        # 0 = leave the resolution alone
SAMPLE_SECONDS = 10

# Measured (bitrate Mbps -> fraction saved at crf 26, resolution unchanged).
# Only ever used to sort the candidate list and to show a rough figure; the
# exact answer for one file comes from `sample()`, which really encodes it.
_CURVE = ((1.5, .23), (3.3, .45), (6.1, .62), (6.3, .74), (13.0, .80),
          (52.0, .86))


def expected_saving(mbps: float) -> float:
    if mbps <= _CURVE[0][0]:
        return _CURVE[0][1]
    for (a, sa), (b, sb) in zip(_CURVE, _CURVE[1:]):
        if mbps <= b:
            return sa + (sb - sa) * (mbps - a) / (b - a)
    return _CURVE[-1][1]


def probe(path: Path) -> dict:
    """Just enough to judge a candidate: size, duration, bitrate, geometry."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries",
             "stream=width,height,avg_frame_rate,codec_name"
             ":format=duration,size,bit_rate",
             "-of", "json", str(path)],
            capture_output=True, timeout=120, creationflags=SUBPROCESS_FLAGS)
        d = json.loads(r.stdout or b"{}")
    except Exception:
        return {}
    s = (d.get("streams") or [{}])[0]
    fm = d.get("format", {})
    try:
        w, h = int(s.get("width") or 0), int(s.get("height") or 0)
        dur = float(fm.get("duration") or 0)
        size = int(fm.get("size") or 0)
        br = int(fm.get("bit_rate") or 0)
    except (TypeError, ValueError):
        return {}
    if not (w and h and dur > 0 and size):
        return {}
    if not br:
        br = int(size * 8 / dur)            # some containers omit it
    num, _, den = (s.get("avg_frame_rate") or "0/1").partition("/")
    try:
        fps = float(num) / float(den) if float(den) else 0.0
    except (TypeError, ValueError, ZeroDivisionError):
        fps = 0.0
    return {"width": w, "height": h, "fps": round(fps, 2), "duration": dur,
            "size": size, "bitrate": br, "mbps": round(br / 1e6, 2),
            "vcodec": s.get("codec_name", "")}


def target_size(w: int, h: int, max_edge: int):
    """New (w, h) capping the long edge, or None to leave it alone.

    Never upscales, and keeps both sides even — H.264 with yuv420p needs it,
    and an odd dimension makes ffmpeg fail after the encode has already run.
    """
    if not max_edge or max(w, h) <= max_edge:
        return None
    scale = max_edge / max(w, h)
    nw = max(2, int(round(w * scale)) // 2 * 2)
    nh = max(2, int(round(h * scale)) // 2 * 2)
    return nw, nh


def output_path(src: Path, crf: int, max_edge: int, info: dict) -> Path:
    """`clip_crf26.mp4`, or `clip_1080p_crf26.mp4` when it is also scaled.

    The settings go in the name because the same source may well be tried at
    more than one setting, and `_2` alone would not say which is which.
    """
    tag = f"crf{crf}"
    tgt = target_size(info.get("width", 0), info.get("height", 0), max_edge)
    if tgt:
        tag = f"{min(tgt)}p_{tag}"
    cand = src.with_name(f"{src.stem}_{tag}.mp4")
    n = 2
    while cand.exists():
        cand = src.with_name(f"{src.stem}_{tag}_{n}.mp4")
        n += 1
    return cand


def scan(roots, min_mbps: float = DEFAULT_MIN_MBPS,
         progress: Optional[Callable[[str], None]] = None) -> list:
    """Every video at or above `min_mbps`, worst offender first.

    Note the threshold does not make this any faster: a file's bitrate is only
    knowable by probing it, so every video gets probed either way. It controls
    how long the list is, nothing else.
    """
    out, seen = [], set()
    for root in roots:
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in VIDEO_EXT:
                continue
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            if progress:
                progress(f"檢查：{path.name}")
            info = probe(path)
            if not info or info["mbps"] < min_mbps:
                continue
            est = int(info["size"] * expected_saving(info["mbps"]))
            out.append({"path": key, "name": path.name, "est_saving": est,
                        **info})
    out.sort(key=lambda r: -r["est_saving"])
    return out


def build_cmd(src: Path, dst: Path, crf: int, max_edge: int, info: dict,
              start: Optional[float] = None,
              seconds: Optional[float] = None) -> list:
    cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-y"]
    if start is not None:
        cmd += ["-ss", f"{start:.2f}"]
    cmd += ["-i", str(src)]
    if seconds is not None:
        cmd += ["-t", f"{seconds:.2f}"]
    cmd += ["-map", "0:v:0", "-map", "0:a:0?",
            "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
            "-pix_fmt", "yuv420p"]
    tgt = target_size(info.get("width", 0), info.get("height", 0), max_edge)
    if tgt:
        # an explicit size, computed here from the probe rather than left to an
        # ffmpeg expression — the `if(gt(iw,ih),…)` form is easy to get subtly
        # wrong and only fails once the encode is already under way
        cmd += ["-vf", f"scale={tgt[0]}:{tgt[1]}:flags=lanczos"]
    cmd += ["-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
            "-progress", "pipe:1", "-nostats", str(dst)]
    return cmd


_TIME_RE = re.compile(rb"out_time_ms=(\d+)")


def _run(cmd, total: float, progress: Optional[Callable[[float], None]] = None):
    """Run ffmpeg, draining stderr on its own thread.

    The threaded drain is not optional: a pipe holds about 64 KB, and a
    chatty ffmpeg that fills it stops producing stdout, so a reader waiting on
    stdout waits for ever. That deadlock has already cost this project one
    job stuck at "100%" for nine minutes.
    """
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            creationflags=SUBPROCESS_FLAGS)
    tail: list = []

    def drain():
        try:
            for chunk in iter(lambda: proc.stderr.read(65536), b""):
                tail.append(chunk)
                if len(tail) > 4:
                    del tail[0]
        except Exception:
            pass

    pump = threading.Thread(target=drain, daemon=True)
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
        err = b"".join(tail).decode("utf-8", "replace").strip()
        raise RuntimeError(err.splitlines()[-1] if err else
                           f"ffmpeg 失敗（代碼 {proc.returncode}）")


def run(src: Path, dst: Path, crf: int, max_edge: int, info: dict,
        progress: Optional[Callable[[float], None]] = None) -> None:
    _run(build_cmd(src, dst, crf, max_edge, info), info.get("duration") or 1.0,
         progress)


def verify(src_info: dict, dst: Path) -> Optional[str]:
    """None when the result is usable, otherwise why it is not.

    The source is never deleted here — this always writes a new file — so this
    is about not leaving a broken output lying around, not about protecting
    the original.
    """
    if not dst.exists() or dst.stat().st_size == 0:
        return "輸出檔不存在或為空"
    out = probe(dst)
    if not out:
        return "輸出檔無法讀取"
    want = src_info.get("duration") or 0
    if want > 0 and abs(out["duration"] - want) > 1.0:
        return f"時長不符（原檔 {want:.1f}s，輸出 {out['duration']:.1f}s）"
    if out["size"] >= src_info.get("size", 0):
        return "壓縮後反而更大，已捨棄（這個檔案本來就編得很有效率）"
    return None


def sample(src: Path, crf: int, max_edge: int, cache: Path,
           seconds: int = SAMPLE_SECONDS) -> dict:
    """Encode a short slice, and cut the matching slice of the original.

    Both halves are needed: a compressed clip on its own tells you nothing
    without the thing it is being compared against. Taken from a third of the
    way in, because the opening seconds of a video are often titles or black.
    """
    info = probe(src)
    if not info:
        raise RuntimeError("無法讀取影片資訊")
    at = max(0.0, min(info["duration"] / 3, max(0.0, info["duration"] - seconds)))
    # Snap to a keyframe first. The "before" half is a stream copy and can only
    # begin on one, so without this it silently starts earlier than the "after"
    # half and runs longer — measured 5.8 s against 4.0 s — and the two clips
    # then show *different footage*, which makes the comparison worthless.
    from . import clip as _clip
    kf = _clip.keyframe_before(src, at)
    if kf is not None:
        at = kf

    out = cache / "shrink_samples"
    out.mkdir(parents=True, exist_ok=True)
    import hashlib
    key = hashlib.md5(f"{src}|{crf}|{max_edge}|{at}".encode()).hexdigest()[:16]
    before, after = out / f"{key}_before.mp4", out / f"{key}_after.mp4"

    if not (before.exists() and before.stat().st_size):
        _run(["ffmpeg", "-hide_banner", "-v", "error", "-y",
              "-noaccurate_seek", "-ss", f"{at:.3f}", "-i", str(src),
              "-t", str(seconds), "-map", "0:v:0", "-map", "0:a:0?",
              "-c", "copy", "-movflags", "+faststart", str(before)], seconds)
    if not (after.exists() and after.stat().st_size):
        _run(build_cmd(src, after, crf, max_edge, info, start=at,
                       seconds=seconds), seconds)

    b, a = before.stat().st_size, after.stat().st_size
    tgt = target_size(info["width"], info["height"], max_edge)
    return {
        "before": str(before), "after": str(after),
        "before_size": b, "after_size": a,
        "saved": round(1 - a / b, 4) if b else 0.0,
        "at": round(at, 1), "seconds": seconds,
        "width": info["width"], "height": info["height"],
        "out_width": tgt[0] if tgt else info["width"],
        "out_height": tgt[1] if tgt else info["height"],
        # the ratio from a real encode of this file, unlike the curve-based
        # estimate in the candidate list
        "projected_size": int(info["size"] * (a / b)) if b else 0,
    }


# There used to be a `clear_samples()` here that wiped the directory outright.
# Nothing ever called it — the samples grew to 136 MB unnoticed — and it was
# the wrong shape anyway: the clips are keyed on (file, crf, max_edge) and
# reused, so wiping them on some event throws away work you may be mid-way
# through comparing. `shrink_samples` is a cachelimit kind now, which bounds
# the directory without deciding for the user when they are finished looking.
