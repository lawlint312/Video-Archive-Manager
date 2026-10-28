"""Contact sheets: one image holding N frames sampled at a fixed interval.

Built to answer "what is in this video?" without opening it, which is the slow
part of tidying a library.

Measured on a 5 GB / 123 min / 1080p file, 12 frames per sheet:

    decode the 4-minute window and tile it     6.8 s
    12 separate seeks with -ss before -i       2.5 s   <- this one

Putting `-ss` before `-i` jumps to a keyframe and reads only a sliver of the
file; putting it after decodes from the start (measured at 183 s for a whole
film). Even for a contiguous window, twelve keyframe seeks beat decoding four
minutes of 1080p.

Sheets are composed with PIL rather than ffmpeg's tile filter, because the
timestamps have to be drawn on each frame and ffmpeg's drawtext needs a font
path escaped in a way that is its own small nightmare on Windows.
"""
from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional

from .media import (COLOUR_FIX, SUBPROCESS_FLAGS, drop_stale, file_stamp,
                    probe_video_codec)

log = logging.getLogger("vam")

COLS, ROWS = 4, 3
PER_SHEET = COLS * ROWS
TILE_W = 480          # default; the caller passes the configured value
PAD = 4
DEFAULT_INTERVAL = 60

# How many frame grabs run at once. Each grab is its own ffmpeg: it seeks to a
# keyframe, decodes one picture, and exits — 226 ms measured on a 1080p film,
# 49 ms of which is just starting the process. Twelve of those in a row is the
# 2.4 s a sheet used to take, on a machine with twelve idle cores.
#
# End to end, per sheet (probe + 12 grabs + compose), best of three:
#
#     1  2.78 s      2  1.94 s      4  1.57 s
#     6  1.67 s      8  1.54 s     12  1.54 s
#
# Four is the knee and everything past it is flat — the serial parts (one
# ffprobe, the PIL compose) put a floor under it, so more processes buy
# nothing. Measured on the NAS too: 3.40 s -> 2.07 s at four, same shape.
#
# Note the grab phase *alone* keeps improving to 12 workers (2.43 -> 0.95 s);
# quoting that number would promise a speedup the user never sees.
#
# A GPU does not help and was measured, not assumed: `-hwaccel cuda` took
# **466 ms** for a single grab against 226 ms on the CPU. One frame per
# process means NVDEC spends more on session setup than it saves on decode.
DEFAULT_WORKERS = 4

_FONTS = ("C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/arial.ttf",
          "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")

# One sheet at a time. Twelve ffmpeg processes is already a burst; letting
# several sheets run at once would flatten the CPU and the NAS at the same
# time, and the whole point is that the first sheet arrives quickly.
_render_lock = threading.Lock()


# ── duration, memoised ───────────────────────────────────────────────────────
# Every render() re-probed the same file, and the progress endpoint is polled
# roughly once a second while a set builds. ffprobe costs **107 ms median /
# 216 ms worst** on a local drive (8 files sampled), so the polling alone would
# spend a fifth of a core answering "how far along are you" — while competing
# with the four ffmpeg grabs doing the actual work. A full 20-sheet set also
# probed 20 times for an answer that cannot change.
#
# Keyed on size and mtime, so replacing the file re-probes rather than serving
# the old length.
_dur_cache: dict = {}
_DUR_CACHE_MAX = 512
_dlock = threading.Lock()


def video_duration(video: Path) -> float:
    """Length in seconds, remembered per (path, size, mtime). 0 if unreadable.

    Not called `duration` because `sheet_count` already takes a parameter by
    that name, and a module function shadowed by a parameter is a trap.
    """
    return _probe(video)[0]


def video_codec(video: Path) -> str:
    """Video codec name (`h264`, `av1`, …), from the same memoised probe."""
    return _probe(video)[1]


def _probe(video: Path) -> tuple:
    try:
        st = video.stat()
    except OSError:
        return 0.0, ""
    key = (str(video), st.st_size, int(st.st_mtime))
    with _dlock:
        hit = _dur_cache.get(key)
    if hit is not None:
        return hit
    dur, _w, _h, codec = probe_video_codec(video)
    with _dlock:
        if len(_dur_cache) >= _DUR_CACHE_MAX:
            # a hint, not a store — dropping the lot is cheaper than tracking
            # an eviction order, and the cost of a miss is one ffprobe
            _dur_cache.clear()
        _dur_cache[key] = (dur, codec)
    return dur, codec


# ── which videos the GPU cannot take ─────────────────────────────────────────
# A GPU grab NVDEC cannot decode does not fail fast: ffmpeg logs
# "Function not implemented" for every packet and keeps reading until the 60 s
# timeout. Measured on an AV1 `.ts` (`林小雨 10.ts`, RTX 2070 has no AV1
# decoder): every cell waited ~60 s before falling back, so one 12-cell sheet
# took **182 s** — and since sheets render one at a time, the next video the
# user opened (MIAA-371, normally ~2 s) waited **114 s** behind it.
#
# Two guards. AV1 is known up front (the codec comes free with the duration
# probe) so it never tries the GPU. Anything else the GPU refuses is
# remembered by path after the first failure, so the rest of that video goes
# straight to the CPU — this is what catches codecs nobody listed. In memory
# only: a GPU that was merely busy or out of memory should get another chance
# after a restart.
NO_GPU_CODECS = {"av1"}
_gpu_refused: set = set()
_glock = threading.Lock()


def _gpu_ok(video: Path) -> bool:
    with _glock:
        if str(video) in _gpu_refused:
            return False
    return video_codec(video) not in NO_GPU_CODECS


def _gpu_failed(video: Path) -> None:
    with _glock:
        _gpu_refused.add(str(video))


# The last grab must sit this far before the end. The container's duration is
# not the time of the last picture: `LaurelLeier-20260920-222030.mp4` reports
# 2160.004 s (the audio runs to 2160.000) while its last video frame is at
# 2159.971. At a 30 s interval the old rule put a cell at exactly 2160 — after
# the last frame, where ffmpeg "succeeds" and writes nothing — and since that
# was the only cell of sheet 7, the whole sheet 404'd and the viewer showed a
# broken image with "00:36:00 – 00:36:00". It happens whenever the length is
# a whole multiple of the interval, which recordings cut at round minutes are.
END_GUARD = 1.0


def frame_count(duration: float, interval: int) -> int:
    """How many cells the whole set has: one every `interval` s from 0.

    The single source of the count — sheet_count, render and /api/sheet/info
    all use it, and the viewer takes it from the info response instead of
    working it out again (it used `ceil(d / i)`, which disagreed with the old
    `floor + 1` here on every exact multiple).
    """
    if duration <= 0 or interval <= 0:
        return 0
    return int(max(0.0, duration - END_GUARD) // interval) + 1


def sheet_count(duration: float, interval: int) -> int:
    frames = frame_count(duration, interval)
    if not frames:
        return 0
    return -(-frames // PER_SHEET)                  # ceil


def sheet_dir(video: Path, cache: Path) -> Path:
    """One directory per video, so deleting the video can drop the lot.

    A flat name-per-sheet layout could not be cleaned up: the key includes the
    sampling interval and the sheet index, and neither is knowable after the
    file is gone.
    """
    key = hashlib.md5(str(video).encode()).hexdigest()
    return cache / "sheets" / key[:2] / key


def cache_path(video: Path, index: int, interval: int, cache: Path,
               tile_w: int = TILE_W, stamp: Optional[str] = None) -> Path:
    # the width is part of the name, so changing the setting produces new
    # files instead of serving the old size; the file's size and mtime are
    # too (`media.file_stamp`), so a video replaced outside VAM gets new sheets
    if stamp is None:
        stamp = file_stamp(video)
    return (sheet_dir(video, cache) /
            f"{stamp}_{interval}_{index}_{COLS}x{ROWS}_{tile_w}.jpg")


def _grab_cmd(src: Path, t: float, dst: Path, tile_w: int, gpu: bool,
              colour_fix: bool = False) -> list:
    """One frame, scaled to a tile.

    The GPU form keeps the picture in video memory to shrink it, and only then
    brings back the small version — `hwaccel_output_format cuda` plus
    `scale_cuda`. Downloading a 4K frame and scaling it on the CPU (the
    halfway version) costs nearly twice the CPU for the same wall time.

    `colour_fix` overrides "reserved" colour metadata that swscale refuses;
    see `media.COLOUR_FIX`. It is a retry, never the first attempt.
    """
    if gpu:
        vf = f"scale_cuda={tile_w}:-2:format=nv12,hwdownload,format=nv12"
        if colour_fix:
            vf = f"{vf},{COLOUR_FIX}"
        return ["ffmpeg", "-hwaccel", "cuda", "-hwaccel_output_format", "cuda",
                "-ss", f"{max(0, t):.2f}", "-i", str(src),
                "-frames:v", "1", "-q:v", "4", "-vf", vf,
                str(dst), "-y", "-loglevel", "error"]
    vf = f"scale={tile_w}:-1"
    if colour_fix:
        vf = f"{COLOUR_FIX},{vf}"
    return ["ffmpeg", "-ss", f"{max(0, t):.2f}", "-i", str(src),
            "-frames:v", "1", "-q:v", "4", "-vf", vf,
            str(dst), "-y", "-loglevel", "error"]


def _grab(src: Path, t: float, dst: Path, tile_w: int = TILE_W,
          gpu: bool = False) -> bool:
    """Grab one tile, retrying along two independent fallbacks.

    GPU → CPU is not optional. NVDEC handles h264/hevc/vp9 but not every codec
    or pixel format, and it can also simply be busy or out of memory — without
    a retry those videos would silently produce no sheet at all, which looks
    like the feature is broken rather than like the GPU declined.

    Then plain → colour-fixed, for the files swscale refuses outright (see
    `media.COLOUR_FIX`). Same shape of failure, different cause, and the same
    reason to retry rather than to give up quietly.
    """
    # decided per grab, not per sheet: the first cell the GPU refuses switches
    # the remaining cells of the same sheet to the CPU too
    gpu = gpu and _gpu_ok(src)
    attempts = [(True, False), (False, False)] if gpu else [(False, False)]
    attempts.append((False, True))          # last resort, always on the CPU
    for use_gpu, fix in attempts:
        try:
            subprocess.run(_grab_cmd(src, t, dst, tile_w, use_gpu, fix),
                           timeout=60, capture_output=True,
                           creationflags=SUBPROCESS_FLAGS)
            if dst.exists() and dst.stat().st_size > 0:
                if fix:
                    log.info("色彩描述被拒，改寫成 bt709 後成功：%s", src.name)
                return True
        except Exception:
            pass
        if use_gpu:
            log.info("GPU 抓格失敗，改用 CPU：%s @ %.0fs", src.name, t)
            _gpu_failed(src)
    return False


def _hms(t: float) -> str:
    t = int(max(0, t))
    return f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}"


def _font(size: int):
    from PIL import ImageFont
    for p in _FONTS:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _compose(frames: List[tuple], dst: Path) -> bool:
    """frames: [(Path, seconds)] -> one tiled JPEG with a timestamp per cell."""
    from PIL import Image, ImageDraw
    if not frames:
        return False
    try:
        tiles = []
        for p, t in frames:
            try:
                im = Image.open(p).convert("RGB")
            except Exception:
                continue
            tiles.append((im, t))
        if not tiles:
            return False

        tw = max(im.width for im, _ in tiles)
        th = max(im.height for im, _ in tiles)
        sheet = Image.new("RGB", (COLS * tw + (COLS + 1) * PAD,
                                  ROWS * th + (ROWS + 1) * PAD), (18, 18, 26))
        font = _font(max(12, tw // 22))
        draw = ImageDraw.Draw(sheet)

        for i, (im, t) in enumerate(tiles):
            x = PAD + (i % COLS) * (tw + PAD)
            y = PAD + (i // COLS) * (th + PAD)
            sheet.paste(im, (x, y))
            label = _hms(t)
            # a plain overlay disappears on a bright frame, so the text gets a
            # box behind it — these frames are not chosen for legibility
            tb = draw.textbbox((0, 0), label, font=font)
            w, h = tb[2] - tb[0], tb[3] - tb[1]
            bx, by = x + 4, y + im.height - h - 8
            draw.rectangle([bx - 3, by - 2, bx + w + 3, by + h + 4],
                           fill=(0, 0, 0))
            draw.text((bx, by), label, font=font, fill=(255, 255, 255))

        dst.parent.mkdir(parents=True, exist_ok=True)
        # Write beside the target and rename into place. Saving straight to
        # `dst` publishes the name before the bytes: PIL creates the file, then
        # buffers, then flushes at the end — measured on a 1.5 MB sheet, the
        # file existed at **0 bytes for 959 of 961 samples** across the 64 ms
        # write. Meanwhile `render()` starts with `if dst.exists(): return dst`
        # and the prefetch worker is writing sheets continuously, so paging to
        # the sheet being built handed back an empty JPEG — the broken image
        # people kept hitting. `os.replace` is atomic, so the name only ever
        # appears once the content is complete.
        part = dst.with_name(f"{dst.name}.{os.getpid()}.part")
        try:
            sheet.save(part, "JPEG", quality=82, optimize=True)
            os.replace(part, dst)
        except Exception:
            try:
                part.unlink()
            except OSError:
                pass
            raise
        return True
    except Exception:
        log.exception("could not compose a contact sheet")
        return False


def _usable(p: Path) -> bool:
    try:
        return p.stat().st_size > 0
    except OSError:
        return False


# Concurrent grabs are capped harder on the GPU than on the CPU, because the
# limit being protected is different. A CPU decoder that needs more memory
# gets it; video memory simply runs out, and running out of it took the whole
# machine down once. Measured on this card: one 4K NVDEC context costs about
# 400 MiB, so four is ~1.6 GB of 8 GB — leaving room for whatever else is
# using the GPU, which on this machine includes ComfyUI.
GPU_MAX_WORKERS = 4


# ── progress ─────────────────────────────────────────────────────────────────
# Measured on this library: the sampling interval is 30 s, so one sheet covers
# 6 minutes and the median film needs **20 sheets** (max 30) — 30-50 s of
# building, during which the only feedback was a spinner.
#
# `render()` has taken a `progress` callback since it was written and nothing
# ever passed one. This is what it was for.
_progress = {"video": None, "index": -1, "done": 0, "of": 0}
_plock = threading.Lock()


def _note_progress(video: Path, index: int, done: int, of: int) -> None:
    with _plock:
        _progress.update(video=str(video), index=index, done=done, of=of)


def _drop_progress(video: Path) -> None:
    with _plock:
        if _progress["video"] == str(video):
            _progress.update(video=None, index=-1, done=0, of=0)


def status(video: Path, interval: int, cache: Path,
           tile_w: int = TILE_W) -> dict:
    """How far along this video's whole set is.

    `ready` can go *down* between two calls. The sheet cache has a size limit
    (1 GB here) and eviction takes finished sheets back, so nothing downstream
    may assume the number only grows.

    `building` is None when nothing is being built *for this video* — which
    includes the case where the renderer is busy with someone else's video,
    since `_render_lock` allows only one at a time across the whole server.
    """
    total = sheet_count(video_duration(video), interval)
    stamp = file_stamp(video)               # once, not a stat per sheet
    ready = sum(1 for i in range(total)
                if _usable(cache_path(video, i, interval, cache, tile_w, stamp)))

    with _plock:
        building = None
        if _progress["video"] == str(video) and _progress["index"] >= 0:
            building = {"index": _progress["index"], "done": _progress["done"],
                        "of": _progress["of"]}
    # taken separately, never nested inside _plock: _prefetch_loop holds _wlock
    # and then renders, so nesting the two in the other order here would be a
    # deadlock waiting for a slow night
    with _wlock:
        target = _want["video"]
    prefetching = bool(target) and Path(target) == video

    return {"total": total, "ready": ready, "building": building,
            "prefetching": prefetching}


def _worker_count(workers: Optional[int], gpu: bool = False) -> int:
    """Clamp to something sane; 1 means "no parallelism at all"."""
    if not workers or workers < 1:
        n = DEFAULT_WORKERS
    else:
        n = min(int(workers), max(1, os.cpu_count() or 1))
    return min(n, GPU_MAX_WORKERS) if gpu else n


def render(video: Path, index: int, interval: int, cache: Path,
           tile_w: int = TILE_W, workers: Optional[int] = None,
           progress=None, gpu: bool = False,
           should_stop=None) -> Optional[Path]:
    """Build sheet `index`, or return it straight from cache.

    `should_stop`, when given, is asked before each cell; once it says yes no
    further cell is started and the sheet is abandoned — returns None and
    writes nothing. The prefetch passes it, and so does the endpoint (see
    `ticket`): a sheet whose viewer has been closed is nobody's any more.
    """
    stamp = file_stamp(video)
    dst = cache_path(video, index, interval, cache, tile_w, stamp)
    # size, not just existence: the write is atomic now, but an empty file
    # here is exactly the failure that produced broken images, and treating
    # it as a cache hit would make it permanent instead of transient
    if _usable(dst):
        return dst

    dur = video_duration(video)
    if dur <= 0:
        return None
    total = sheet_count(dur, interval)
    if index < 0 or index >= total:
        return None

    with _render_lock:
        if _usable(dst):                    # someone else got there first
            return dst
        tmp = Path(tempfile.mkdtemp(prefix="vamsheet"))
        try:
            wanted = []
            last = frame_count(dur, interval)
            for i in range(PER_SHEET):
                n = index * PER_SHEET + i
                if n >= last:
                    break
                wanted.append((i, n * interval, tmp / f"{i:02d}.jpg"))
            if not wanted:
                return None

            done = [0]
            stopped = [False]
            lock = threading.Lock()
            # published before the first grab, so a poll that lands in the gap
            # says "building sheet N, 0 of 12" instead of "nothing happening"
            _note_progress(video, index, 0, len(wanted))

            def one(job):
                i, t, f = job
                if stopped[0] or (should_stop and should_stop()):
                    stopped[0] = True
                    return None
                ok = _grab(video, t, f, tile_w, gpu)
                with lock:
                    done[0] += 1
                    n = done[0]
                _note_progress(video, index, n, len(wanted))
                if progress:
                    progress(n, len(wanted))
                return (i, f, t) if ok else None

            n = _worker_count(workers, gpu)
            if n <= 1:
                got = [one(j) for j in wanted]
            else:
                with ThreadPoolExecutor(max_workers=n) as ex:
                    got = list(ex.map(one, wanted))
            # Abandoned: compose nothing. A sheet built from the cells that
            # happened to finish would be cached as complete and show a gap
            # forever — it looks like a normal image, so nobody would know.
            if stopped[0]:
                return None
            # back into time order — the tile layout depends on it, and a
            # thread pool finishes in whatever order it pleases
            frames = [(f, t) for i, f, t in sorted(x for x in got if x)]
            if not frames:
                return None
            if not _compose(frames, dst):
                return None
            # sheets of what the file used to be (it was replaced in place)
            drop_stale(dst.parent, stamp)
            return dst
        finally:
            _drop_progress(video)
            for f in tmp.glob("*"):
                try:
                    f.unlink()
                except OSError:
                    pass
            try:
                tmp.rmdir()
            except OSError:
                pass


# ── prefetch ─────────────────────────────────────────────────────────────────
# The point of the feature is speed, so once a sheet is asked for the rest of
# that video is built in the background. Only ever one video at a time: asking
# for a different one abandons the old queue rather than adding to it, so a
# few clicks around the grid cannot pile up hundreds of sheets.

_want: dict = {"video": None, "interval": DEFAULT_INTERVAL, "cache": None,
               "tile_w": TILE_W, "workers": DEFAULT_WORKERS,
               "gpu": False}
_worker: Optional[threading.Thread] = None
_wlock = threading.Lock()


def prefetch(video: Path, interval: int, cache: Path,
             tile_w: int = TILE_W, workers: Optional[int] = None,
             gpu: bool = False):
    global _worker
    with _wlock:
        _want["video"] = str(video)
        _want["interval"] = interval
        _want["cache"] = cache
        _want["tile_w"] = tile_w
        _want["workers"] = workers
        _want["gpu"] = gpu
        if _worker and _worker.is_alive():
            return                          # it will pick up the new target
        _worker = threading.Thread(target=_prefetch_loop, daemon=True)
        _worker.start()


_gen: dict = {}
_glock = threading.Lock()


def ticket(video: Path) -> int:
    """The cancel count for `video`, taken when a sheet is asked for.

    The sheet being *waited on* has to be stoppable too, not only the
    prefetch behind it. A recording still being written (`.tmp.ts`, 5.3 GB on
    a disk the recorder is busy with) took 25 s for one sheet; the user closed
    the viewer after a few seconds and opened an ordinary MP4, which then sat
    25 s behind `_render_lock` instead of ~1.6 s. The request keeps its ticket
    and gives up once `cancel_prefetch` has moved the count on.
    """
    with _glock:
        return _gen.get(str(video), 0)


def cancelled_since(video: Path, t: int) -> bool:
    with _glock:
        return _gen.get(str(video), 0) != t


def cancel_prefetch(video: Path) -> bool:
    """Stop building sheets for `video`. True if it was in fact the target.

    Also stops a sheet of `video` someone asked for and is still waiting on
    (see `ticket`) — closing the viewer or deleting the file both mean nobody
    wants it.

    Prefetching is speculative — nobody asked for those sheets. When the user
    deletes the file they have said plainly that they do not want it, so the
    speculative work should get out of the way rather than explain itself.

    This does not kill anything: the grabs already running finish, and no
    further cell is started — the half-built sheet is abandoned, not saved.
    It used to let the whole sheet in flight finish, which was ~1.6 s on a
    normal video but **182 s** on one whose every cell timed out on the GPU.
    Without cancelling at all a single video can stay intermittently
    undeletable for over a minute (measured: 13 sheets, 67 s, a child process
    on the file for ~14 s of that).
    """
    with _glock:
        _gen[str(video)] = _gen.get(str(video), 0) + 1
    with _wlock:
        if _want["video"] and Path(_want["video"]) == video:
            _want["video"] = None
            log.info("cancelled contact-sheet prefetch for %s", video.name)
            return True
    return False


def yield_to(video: Path) -> None:
    """A sheet of `video` was asked for: drop a prefetch of any other video.

    `prefetch()` would retarget anyway, but only *after* the requested sheet
    is rendered — and that render waits on `_render_lock`, which the old
    prefetch is holding. Opening another video straight from the grid sends
    no cancel for the previous one, so without this the request queued behind
    the old video's sheet (MIAA-371 waited 114 s).
    """
    with _wlock:
        old = _want["video"]
        if old and Path(old) != video:
            _want["video"] = None
            log.info("contact-sheet prefetch for %s gave way to %s",
                     Path(old).name, video.name)


def _prefetch_loop():
    while True:
        with _wlock:
            target = _want["video"]
            interval = _want["interval"]
            cache = _want["cache"]
            tile_w = _want["tile_w"]
            workers = _want["workers"]
            gpu = _want.get("gpu", False)
        if not target or not cache:
            return
        video = Path(target)
        try:
            total = sheet_count(video_duration(video), interval)
        except Exception:
            return

        made = False
        stamp = file_stamp(video)
        for i in range(total):
            with _wlock:
                if _want["video"] != target:
                    break                   # a different video was asked for
            if _usable(cache_path(video, i, interval, cache, tile_w, stamp)):
                continue
            render(video, i, interval, cache, tile_w, workers, gpu=gpu,
                   should_stop=lambda: _want["video"] != target)
            made = True
            # let an on-demand request in between sheets
            time.sleep(0.05)
        else:
            with _wlock:
                if _want["video"] == target:
                    _want["video"] = None   # this video is fully built
                    return
        if not made:
            with _wlock:
                if _want["video"] == target:
                    _want["video"] = None
                    return
