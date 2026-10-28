"""Full-decode check: find videos whose picture data is damaged.

Everything else in the converter looks at the *wrapper* — container, codec
name, timestamps — and a file with a corrupt bitstream passes all of it. The
only way to see the damage is to decode every frame. Found in practice:
`yaokaixin77\\yaokaixin77\\0K4KREIq4KD1kijH.mp4` probes as clean h264 at
4:25, yet from 0:18 to 4:09 the picture data is garbage (no decodable
keyframe for 231 s); seeking into it freezes the browser until it gives up
and restarts from 0. A full pass over that folder (207 files, 12.9 GB)
found 25 such files.

What counts: lines from the *decoder* only. The null muxer we decode into
also complains — "non monotonically increasing dts" — about files with
duplicated timestamps (29 in that folder, X downloads), and those play fine.
Counting them would have flagged 54 files instead of 25. Measured gap: clean
files 0 decoder lines, the least damaged 122.

Cost is the whole point of keeping this opt-in: median 4.3 s per file,
longest 95 s, CPU-bound — about a day for the full library. Hence the cache:
a file whose size and mtime are unchanged is not decoded again.

GPU (follows the contact-sheet CPU/GPU setting — same trade, "who pays"):
measured on four real files, NVDEC finds the same damage (34,415 vs 34,415
lines; 96,317 vs 96,315; 142 vs 140; clean 4K 0 vs 0) while CPU use drops
from 5–9 cores to 0.1–0.5. Wall time is a wash: small files 1–3 s slower
(fixed GPU start-up), a 4K file 19.0 → 13.5 s. VRAM +145–477 MiB per file.
Only an allowlist goes to the GPU: a codec NVDEC refuses does not fail fast
(see contactsheet's AV1 note — it reads to the timeout), and its complaints
would be counted as damage. Anything else, or a GPU run that does not end
cleanly, is decoded on the CPU.

Audio is decoded in the same pass and counted separately (2026-10-17).
`MTALL-120-Uncensored-Leaked.mp4` — remuxed from a leaked .ts with 65
dropouts — decodes with *zero* video errors, but 79 AAC packets around the
dropouts are corrupt ("Number of bands exceeds limit", "Input buffer
exhausted"). ffmpeg and VLC skip such a packet; Chrome's audio decoder
fails the whole media element on the first one (PIPELINE_ERROR_DECODE), so
every seek after it does nothing. The picture is fine, so unlike video
damage this is fixable: re-encode the audio, copy the video (see
`convert`'s "audiofix"). Decoding the audio costs next to nothing — the
whole 2-hour track took 4.8 s.
"""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from .media import SUBPROCESS_FLAGS

# Severity, by decoder error lines (the user's chosen cut-offs, set from the
# spread of the 25 damaged files: 122–540 / 3,762–8,590 / 10,167–96,317).
MODERATE = 1_000
SEVERE = 10_000
TIMEOUT = 1800            # a 2-hour 4K file can take minutes; never hang a scan

# What the RTX 2070's NVDEC takes. Pixel formats matter as much as codecs:
# 10-bit or 4:2:2 h264 is not decodable on it.
GPU_OK = {"h264": {"yuv420p", "yuvj420p"},
          "hevc": {"yuv420p", "yuvj420p", "yuv420p10le"}}
# A GPU run whose stderr mentions these was refused, not damaged.
# Input options for any run that decodes audio. A corrupt AAC packet can
# decode into a frame claiming another sample rate (its program config element
# is garbage); ffmpeg then rebuilds the audio filter chain for it, fails
# ("Error reinitializing filters!", -22) and stops the *whole* run there.
# OK4yHUw8VFxpDXDD.mp4 stopped at 10:47 of 13:10 — its concealing re-encode
# failed outright, and every check of it had only seen 10:47. Dropping such
# frames instead lets the run go to the end: that file then re-encodes in
# 47 s, full length, 0 errors.
SKIP_CHANGED_AUDIO = ["-reinit_filter:a", "0", "-drop_changed:a", "1"]

_GPU_REFUSED = ("hwaccel", "Function not implemented", "Failed setup",
                "No decoder surfaces", "cuda", "CUDA")


def level(errors: Optional[int]) -> str:
    """'' for clean or unknown, else 輕微 / 中等 / 嚴重."""
    if not errors:
        return ""
    if errors > SEVERE:
        return "嚴重"
    if errors >= MODERATE:
        return "中等"
    return "輕微"


def count_errors(stderr: str) -> int:
    """Decoder complaints only; the null muxer's timestamp warnings are not
    damage (see the module docstring)."""
    v, a = split_errors(stderr)
    return v + a


# ffmpeg tags each line with the component that wrote it: `[aac @ 0x..]`,
# `[aist#0:1/aac @ 0x..] [dec:aac @ 0x..]` for audio, `[h264 @ ..]` or
# `[vist#0:0/h264 @ ..]` for video.
_TAG = re.compile(r"\[([^\]@ ]+)")
_PROGRESS = re.compile(r"^[a-z_0-9]+=")
_OUT_TIME = re.compile(r"^out_time_us=(\d+)")
_AUDIO_DECODERS = {"aac", "aac_fixed", "mp3", "mp3float", "mp2", "mp2float",
                   "ac3", "ac3_fixed", "eac3", "opus", "libopus", "vorbis",
                   "libvorbis", "flac", "alac", "dca", "truehd", "wmav2"}


def _is_audio(line: str) -> bool:
    m = _TAG.match(line)
    tag = m.group(1) if m else ""
    return (tag.startswith("aist#") or tag in _AUDIO_DECODERS
            or tag.startswith("pcm_"))


def split_errors(stderr: str) -> tuple:
    """(video, audio) decoder complaint counts, muxer warnings excluded.

    ffmpeg collapses a repeated line into "    Last message repeated N
    times", with no component tag. It belongs to whatever line came before
    it, so it is counted (once, as before) with that line — and skipped
    after a muxer warning. It used to be counted as a video error every
    time: all 57 files of `zenmeimei` were reported damaged with 3–10
    "errors" that were exactly these lines after the null muxer's dts
    warnings, and not one decoder complaint. The old test fed thirty
    identical warnings, which a real ffmpeg never prints.
    """
    v = a = 0
    prev = None                 # "v", "a", or None after a skipped line
    for line in stderr.splitlines():
        line = line.strip()
        # `-progress` reports (key=value) are not log lines at all, and do
        # not break a "Last message repeated" from the line it refers to
        if not line or _PROGRESS.match(line):
            continue
        if line.startswith("Last message repeated"):
            if prev == "a":
                a += 1
            elif prev == "v":
                v += 1
            continue
        if line.startswith("[null @"):
            prev = None
            continue
        if _is_audio(line):
            a += 1
            prev = "a"
        else:
            v += 1
            prev = "v"
    return v, a


# A decoder complaint within this many seconds of the previous one belongs to
# the same damaged stretch.
REGION_GAP = 2.0


def locate(path: Path, limit: int = 50) -> list:
    """Where the damage is: [{"start", "end", "errors"}] in seconds.

    For a file the scan could not place (checked before it recorded places,
    or too short for the progress reports — see `regions_of`): one more CPU
    decode with the progress interleave, and when that is too coarse too,
    with `showinfo`, which prints every frame's time — exact, and verbose,
    which only matters on long files, where the first way already works.
    Measured on three damaged files in yaokaixin77: each one stretch about a
    second long (1:25-1:26 on KM60iwqikKHwjfWy.mp4).
    """
    try:
        got = regions_of(_run(path, False).stderr, limit, duration=_length(path))
    except (subprocess.TimeoutExpired, OSError):
        return []
    return got if got is not None else _locate_frames(path, limit)


_SHOWINFO = re.compile(r"Parsed_showinfo.*?pts_time:\s*(-?[\d.]+)")


def _locate_frames(path: Path, limit: int) -> list:
    """Each error placed at the frame `showinfo` printed just before it."""
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-v", "info",
             *SKIP_CHANGED_AUDIO, "-i", str(path),
             "-map", "0:v:0", "-map", "0:a:0?", "-filter:v", "showinfo",
             "-f", "null", "-"],
            capture_output=True, timeout=TIMEOUT, creationflags=SUBPROCESS_FLAGS)
    except Exception:
        return []
    # rewritten as progress-style lines so one parser counts both
    out = []
    for line in r.stderr.decode("utf-8", "replace").splitlines():
        m = _SHOWINFO.search(line)
        if m:
            out.append(f"out_time_us={int(float(m.group(1)) * 1e6)}")
        elif line.startswith("[") and "showinfo" not in line and _decoder_line(line):
            out.append(line)
        elif line.strip().startswith("Last message repeated"):
            out.append(line)
    return regions_of("\n".join(out), limit, exact=True,
                      duration=_length(path)) or []


def _length(path: Path) -> float:
    """Container length in seconds, 0 if unknown: where a stretch whose
    picture never comes back is taken to end."""
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "format=duration", "-of", "csv=p=0", str(path)],
                           capture_output=True, text=True, timeout=30,
                           creationflags=SUBPROCESS_FLAGS)
        return float(r.stdout.strip() or 0)
    except Exception:
        return 0.0


def _decoder_line(line: str) -> bool:
    """`-v info` also lets the input, the filter graph and the muxer speak;
    only decoder lines are damage."""
    m = _TAG.match(line)
    tag = m.group(1) if m else ""
    return (tag in ("h264", "hevc", "mpeg4", "vp9", "av1", "mpeg2video")
            or tag.startswith("vist#") or _is_audio(line))


def locate_cached(path: Path, cache_path: Path) -> list:
    """`locate`, remembered in the check cache. Several seconds for a short
    clip and about as long as a full decode for a film, so worth keeping."""
    try:
        st = path.stat()
    except OSError:
        return []
    cache = Cache(cache_path)
    try:
        hit = cache.get_regions(path, st.st_size, st.st_mtime)
        if hit is not None:
            return hit
        regions = locate(path)
        cache.put_regions(path, st.st_size, st.st_mtime, regions)
        return regions
    finally:
        cache.close()


# Progress reports further apart than this (in film time) cannot place an
# error closely enough to jump to.
COARSEST = 3.0


def regions_of(stderr: str, limit: int = 50, exact: bool = False,
               duration: float = 0.0) -> Optional[list]:
    """Where the errors are, from a decode run with `-progress pipe:2`:
    [{"start", "end", "errors"}] in seconds, stretches merged when less than
    `REGION_GAP` apart, the biggest `limit` of them when there are more.
    Counted by the same rule as `split_errors` (muxer warnings out, a repeat
    line with what it repeats).

    A stretch ends where the picture comes back: the first report *after*
    its last error. Where nothing decodes at all the reported time stands
    still while the errors pour in — dHUrRjIiXZ9ezqMO.mp4 reported 0:28 for
    24,000 lines and then jumped to 2:09 — so ending at the last error's own
    time made a 1:41 hole look like a one-second glitch.

    None when the reports were too sparse to say: they come every 0.01 s of
    *wall* time, and a 12 s clip decodes in a tenth of a second, so all its
    errors landed on 0:00 (test_conceal caught it). A jump between reports
    only counts as sparse when the time before it was reported once; a time
    reported over and over is the picture stalling, which is the finding.
    `exact` (frame-by-frame input from `showinfo`) skips that test: there a
    jump is always frames missing. `locate` falls back to it."""
    last, hits, prev = None, [], None   # None: no report yet
    times = []                  # every reported time, in order
    seen = 0                    # how many reports at the current time
    coarse = False
    resume = {}                 # error index → first report after it
    for line in stderr.splitlines():
        line = line.strip()
        m = _OUT_TIME.match(line)
        if m:
            t = int(m.group(1)) / 1e6
            times.append(t)
            if last is None:
                # the first report: errors before it can only be placed
                # somewhere in 0..t, which is no place at all when t is far
                if hits and t > COARSEST:
                    coarse = True
                last, seen = t, 1
                continue
            if t > last:
                if t - last > COARSEST and seen <= 1:
                    coarse = True
                for i in range(len(hits) - 1, -1, -1):
                    if i in resume:
                        break
                    resume[i] = t
                seen = 1
            else:
                seen += 1
            last = max(last, t)
            continue
        if not line or _PROGRESS.match(line):
            continue
        if line.startswith("Last message repeated"):
            if prev:
                hits.append(last or 0.0)
            continue
        if line.startswith("[null @"):
            prev = None
            continue
        hits.append(last or 0.0)
        prev = True
    if hits and (coarse or last is None) and not exact:
        return None
    regions = []
    for i, t in enumerate(hits):
        stop = resume.get(i, t)
        if regions and t - regions[-1]["end"] <= REGION_GAP:
            regions[-1]["end"] = max(regions[-1]["end"], stop)
            regions[-1]["errors"] += 1
        else:
            regions.append({"start": t, "end": stop, "errors": 1})
    if exact and times:
        # Frame by frame, the picture can also come back for one frame and
        # then vanish again: dHUrRjIiXZ9ezqMO.mp4 shows a frame at 0:28 after
        # 11,676 errors, and the next one around 2:08. A hole in the frames
        # right after a damaged stretch is part of it. (Not with the progress
        # reports: there a long gap can just be a fast decode.)
        import bisect
        # (A few frames can come out first — there, 28.03 and 28.07 — so the
        # hole is looked for within REGION_GAP after the stretch, not only
        # right at its edge.)
        for g in regions:
            j = bisect.bisect_left(times, g["end"])
            while j + 1 < len(times) and times[j] - g["end"] <= REGION_GAP:
                if times[j + 1] - times[j] > REGION_GAP:
                    g["end"] = times[j + 1]
                j += 1
    if regions and times and duration:
        # Nothing at all after the last stretch: the picture never came back
        # (0K4KREIq4KD1kijH.mp4 stops at 0:18 of 4:25), so it runs to the end.
        g = regions[-1]
        tail = max(times[-1], g["end"])
        if tail - g["end"] <= REGION_GAP and duration - tail > REGION_GAP:
            g["end"] = duration
    if len(regions) > limit:
        regions = sorted(sorted(regions, key=lambda g: -g["errors"])[:limit],
                         key=lambda g: g["start"])
    return [{"start": round(g["start"], 2), "end": round(g["end"], 2),
             "errors": g["errors"]} for g in regions]


class Counts(int):
    """The video error count, carrying the audio count beside it.

    An int on purpose: everything that already reads "how damaged is the
    picture" — the severity levels, the damaged rows, the GPU fallback —
    keeps working unchanged, and `.audio` is there for what is new.
    """
    audio: int = 0
    regions: Optional[list] = None      # where, when the run could say

    def __new__(cls, video: int, audio: int = 0, regions: Optional[list] = None):
        obj = super().__new__(cls, video)
        obj.audio = audio
        obj.regions = regions
        return obj


def gpu_ok(path: Path) -> bool:
    """True when the first video stream is one NVDEC is known to take."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name,pix_fmt", "-of", "csv=p=0",
             str(path)], capture_output=True, text=True, timeout=30,
            creationflags=SUBPROCESS_FLAGS)
        codec, pix = (r.stdout.strip().split(",") + ["", ""])[:2]
    except Exception:
        return False
    return pix in GPU_OK.get(codec, ())


class Stopped(Exception):
    """The scan was stopped while this file was being decoded."""


def _run(path: Path, gpu: bool, stop: Optional[Callable[[], bool]] = None):
    cmd = ["ffmpeg", "-hide_banner", "-v", "error", *SKIP_CHANGED_AUDIO]
    if gpu:
        cmd += ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"]
    # the first audio track rides along (`?`: a silent file has none); it is
    # decoded on the CPU either way and costs next to nothing
    # `-progress pipe:2`: ffmpeg's error lines carry no time, but its progress
    # report does, and on stderr it lands in order between them — so each
    # error can be placed at the last reported time. Every 0.01 s of wall
    # time because a CPU decode runs ~130x real time: at the default 0.5 s a
    # 4-minute clip got two reports and every error landed on 0:00.
    # Measured on KM60iwqikKHwjfWy.mp4 (damage at 85.6-86.4 s): 85.3-86.4 on
    # the CPU, 85.7-86.4 on the GPU, 1.9 s against 1.7 s without it.
    cmd += ["-i", str(path), "-map", "0:v:0", "-map", "0:a:0?",
            "-progress", "pipe:2", "-stats_period", "0.01",
            "-f", "null", "-"]
    # communicate() drains stderr while the process runs, so a damaged file's
    # tens of thousands of lines cannot fill a pipe and stall it. In short
    # slices rather than one call, to look at `stop` in between: one file
    # can take many minutes (a 4-hour film shared the GPU for 8+ minutes), and
    # the stop button used to wait for every file in hand to finish —
    # 「停止中」 for a quarter of an hour. The answer is thrown away, which is
    # all killing costs: decoding only reads.
    p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.PIPE, text=True, encoding="utf-8",
                         errors="replace", creationflags=SUBPROCESS_FLAGS)
    t0 = time.monotonic()
    while True:
        try:
            _out, err = p.communicate(timeout=0.5)
            return subprocess.CompletedProcess(cmd, p.returncode, "", err)
        except subprocess.TimeoutExpired:
            stopped = bool(stop and stop())
            if stopped or time.monotonic() - t0 > TIMEOUT:
                p.kill()
                p.communicate()
                if stopped:
                    raise Stopped(str(path))
                raise subprocess.TimeoutExpired(cmd, TIMEOUT)


def decode(path: Path, gpu: bool = False,
           stop: Optional[Callable[[], bool]] = None) -> Optional[Counts]:
    """Decode the first video and audio streams end to end; the error-line
    counts (video, with `.audio` beside it), or None if ffmpeg could not be
    run to completion (timeout, missing file).

    With `gpu`, files NVDEC takes are decoded there; a GPU run that reports
    a hardware refusal is redone on the CPU, so a refusal is never reported
    as damage.

    `stop` is asked every half second; when it says so, ffmpeg is ended and
    `Stopped` raised — never taken for a GPU refusal and redone on the CPU."""
    if gpu and gpu_ok(path):
        try:
            r = _run(path, True, stop)
            v, a = split_errors(r.stderr)
            # A non-zero exit alone is not a refusal: a badly damaged file
            # exits 69 on the CPU too (0K4KREIq4KD1kijH.mp4, both paths).
            # Refused = a hardware complaint, or a failure that reported no
            # decode error at all.
            refused = (any(m in r.stderr for m in _GPU_REFUSED)
                       or (r.returncode != 0 and v + a == 0))
            if not refused:
                return Counts(v, a, regions_of(r.stderr, duration=_length(path))
                              if v + a else [])
        except (subprocess.TimeoutExpired, OSError):
            pass
    try:
        err = _run(path, False, stop).stderr
        v, a = split_errors(err)
        return Counts(v, a, regions_of(err, duration=_length(path))
                      if v + a else [])
    except (subprocess.TimeoutExpired, OSError):
        return None


def audio_errors(path: Path) -> Optional[int]:
    """Decode only the first audio track; its error-line count (0 when the
    file has no audio), or None if ffmpeg could not run. Seconds even for a
    2-hour file — used before a remux and to verify an audio fix."""
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-v", "error", *SKIP_CHANGED_AUDIO,
             "-i", str(path), "-map", "0:a:0?", "-f", "null", "-"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=TIMEOUT, creationflags=SUBPROCESS_FLAGS)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if "does not contain any stream" in r.stderr:
        return 0                            # no audio track: nothing to break
    # only one stream is decoded, so every decoder line is about it
    return count_errors(r.stderr)


class Cache:
    """Results keyed on (path, size, mtime) in `data/decode_check.sqlite3`.

    A plain SQLite file — Python ships SQLite, nothing to install. Losing it
    only means the next check decodes again, so it is not backed up.
    """

    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.execute("""CREATE TABLE IF NOT EXISTS checks (
            path TEXT PRIMARY KEY, size INTEGER, mtime REAL,
            errors INTEGER, at REAL, audio INTEGER)""")
        # Tables made before audio was counted lack the column — CREATE IF
        # NOT EXISTS leaves an existing table as it was. Their rows read as
        # misses (audio NULL), so those files are decoded once more.
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(checks)")}
        if "audio" not in cols:
            self.conn.execute("ALTER TABLE checks ADD COLUMN audio INTEGER")
        # Where the damage is (`locate`), as JSON; filled on demand when a
        # damaged file is played or concealed, NULL until then.
        if "regions" not in cols:
            self.conn.execute("ALTER TABLE checks ADD COLUMN regions TEXT")
        # Version 2: "Last message repeated" after a muxer warning was counted
        # as damage (see `split_errors`). That only ever *added* errors, so
        # clean rows stand; every row that reported damage is checked again.
        if self.conn.execute("PRAGMA user_version").fetchone()[0] < 2:
            self.conn.execute("DELETE FROM checks WHERE errors > 0 OR audio > 0")
            self.conn.execute("PRAGMA user_version = 2")
        # Version 4: runs used to stop at the first corrupt audio frame that
        # claimed new parameters (see SKIP_CHANGED_AUDIO), so a damaged file
        # could have been checked only up to there. Damaged rows go again.
        if self.conn.execute("PRAGMA user_version").fetchone()[0] < 4:
            self.conn.execute("DELETE FROM checks WHERE errors > 0 OR audio > 0")
            self.conn.execute("PRAGMA user_version = 4")
        # Version 3: a stretch used to end at its last error, which made a
        # minute and a half with no picture look like one second
        # (`regions_of`). Places are recomputed on demand; counts stand.
        if self.conn.execute("PRAGMA user_version").fetchone()[0] < 3:
            self.conn.execute("UPDATE checks SET regions = NULL")
            self.conn.execute("PRAGMA user_version = 3")
        self.conn.commit()

    def get(self, path: Path, size: int, mtime: float) -> Optional[Counts]:
        with self._lock:
            row = self.conn.execute(
                "SELECT size, mtime, errors, audio, regions FROM checks WHERE path = ?",
                (str(path),)).fetchone()
        if (row and row[0] == size and abs(row[1] - mtime) < 1e-6
                and row[3] is not None):
            return Counts(row[2], row[3], json.loads(row[4]) if row[4] else None)
        return None

    def put(self, path: Path, size: int, mtime: float, errors) -> None:
        regions = getattr(errors, "regions", None)
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO checks "
                "(path, size, mtime, errors, at, audio, regions) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (str(path), size, mtime, int(errors), time.time(),
                 int(getattr(errors, "audio", 0)),
                 json.dumps(regions) if regions is not None else None))
            self.conn.commit()

    def get_regions(self, path: Path, size: int, mtime: float) -> Optional[list]:
        """The located damage, or None when not located for this exact file."""
        with self._lock:
            row = self.conn.execute(
                "SELECT size, mtime, regions FROM checks WHERE path = ?",
                (str(path),)).fetchone()
        if (row and row[0] == size and abs(row[1] - mtime) < 1e-6
                and row[2] is not None):
            return json.loads(row[2])
        return None

    def put_regions(self, path: Path, size: int, mtime: float, regions: list) -> None:
        """Remembered beside the check result. Only on a row for this exact
        file: a changed file has a new check coming, and its own damage."""
        with self._lock:
            self.conn.execute(
                "UPDATE checks SET regions = ? WHERE path = ? AND size = ? "
                "AND abs(mtime - ?) < 1e-6",
                (json.dumps(regions), str(path), size, mtime))
            self.conn.commit()

    def close(self) -> None:
        self.conn.close()


def check(path: Path, cache: Optional[Cache], gpu: bool = False,
          force: bool = False,
          stop: Optional[Callable[[], bool]] = None) -> Optional[Counts]:
    """Error counts for `path`, from the cache when the file is unchanged.
    `force` decodes anyway (the scan's "check everything again") and
    remembers the new answer. None when it could not be decoded — or the
    scan was stopped in the middle of it, which is remembered as nothing."""
    try:
        st = path.stat()
    except OSError:
        return None
    if cache and not force:
        hit = cache.get(path, st.st_size, st.st_mtime)
        if hit is not None:            # 0 is an answer, not a miss
            return hit
    try:
        n = decode(path, gpu, stop)
    except Stopped:
        return None
    if n is not None and (n or n.audio) and n.regions is None:
        # Damaged, but the progress reports were too sparse to say where (a
        # short file decodes in a blink): place it frame by frame now, while
        # the scan is here anyway, so ▶ in the dialog can show it at once.
        # Only damaged files pay for this, and they are few.
        n.regions = locate(path)
    if n is not None and cache:
        cache.put(path, st.st_size, st.st_mtime, n)
    return n
