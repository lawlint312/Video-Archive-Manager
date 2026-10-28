"""Convert non-MP4 videos into browser-playable MP4.

Most files only need a *remux*: the video and audio bitstreams are already
what MP4 holds, they are just wrapped in the wrong container. Copying the
streams takes seconds and loses nothing, so re-encoding is reserved for the
files that genuinely cannot be copied.

The other half of the problem is seeking: MPEG-TS has no index, so a browser
cannot map a timestamp to a byte offset and has to stream from the beginning.
`-movflags +faststart` puts MP4's index at the front and fixes that.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional

from . import checkcache, decodecheck, repair, retime
from .media import SUBPROCESS_FLAGS, VIDEO_EXT, ffmpeg_error

log = logging.getLogger("vam.convert")

# Codecs MP4 can hold *and* a browser will actually decode.
_OK_VIDEO = {"h264"}
_OK_AUDIO = {"aac", "mp3"}
# Held by MP4 but browser support is patchy — copied, then flagged.
_RISKY_VIDEO = {"hevc", "av1"}

# `yuvj420p` is `yuv420p` with a full-range flag (`color_range=pc`) — same
# chroma subsampling, same decoder path, and browsers play it. It used to be
# rejected here, which meant offering a slow *lossy* re-encode of a file that
# already worked. The formats that genuinely do not play are the ones with
# different subsampling (yuv422p, yuv444p).
_OK_PIX = {"yuv420p", "yuvj420p"}

# "damaged" is a finding, not something `run` can do: the picture data is
# gone, so the only remedies are the delete mode and the clip tool.
# "audiofix" is the fixable sibling: the picture is fine but some audio
# packets are corrupt, and Chrome fails the whole player on the first one
# (see decodecheck). The video is copied, the audio re-encoded.
# "gapfix" is the other one: the picture errors are only the truncated last
# frame before each recording dropout, and every dropout resumes on a
# keyframe — so those frames can be dropped without re-encoding anything.
# "gapcut" is a recording that simply stopped receiving for a while — the
# streamer offline or in a private show — while the recorder kept the clock
# running: minutes with no picture and no sound, shown as a frozen frame.
# Nothing is damaged, so the decode check passes it. The empty stretches are
# taken out by rewriting timestamps (`setts`), every packet copied.
# "conceal" is what can be done for "damaged": re-encode with the decoder's
# own concealment, so the new file has no broken packets left and the browser
# plays and seeks it. The damaged moment still looks damaged. Lossy, so the
# result is saved beside the original and goes to the pending list.
ACTIONS = ("remux", "remux_audio", "reencode", "repair", "retime", "audiofix",
           "gapfix", "gapcut", "conceal", "skip", "damaged")
RUNNABLE = ("remux", "remux_audio", "reencode", "repair", "retime", "audiofix",
            "gapfix", "gapcut", "conceal")
# The user's choice: about the size of the originals measured (crf 20 made
# them 1.3-1.8x bigger), the picture outside the damage near-identical.
CONCEAL_CRF = 23
# A jump in video DTS bigger than this is a dropout in the recording.
GAP_SECONDS = 0.5
# Beyond this many the drop expression gets unwieldy, and a recording that
# broken is not what this fix was measured on.
MAX_GAPS = 2000
# How many full decodes at once depends on the length (the user's rule,
# measured 2026-10-25 with decodecheck.decode on synthetic 720p H.264):
#   4 × 10-minute clips   GPU 47.2 / 46.8 / 46.2 s at 1 / 2 / 4 at once,
#                          CPU 21.5 / 22.2 s at 1 / 4 — no gain at all, each
#                          file just takes 4x as long (and a 4-hour 1080p film
#                          would cross decodecheck.TIMEOUT and be decoded again)
#   16 × 30-second clips  GPU 14.0 / 11.8 / 10.8 s, CPU 4.8 / 4.6 / 4.2 s —
#                          the start-up cost of each file overlaps
# So short files go DECODE_WORKERS at a time, long ones one at a time.
DECODE_WORKERS = 4
LONG_DECODE_SECONDS = 600   # at 10 minutes the start-up is under a tenth of it
# A stretch with no video *and* no audio at least this long is a break in the
# recording worth taking out. Short hiccups (MTALL-120's 1-4 s dropouts) stay:
# they are a second of freeze, and that file's problem was corrupt packets.
BLANK_SECONDS = 5.0
# One term per blank in the setts expression.
MAX_BLANKS = 200
# Remembered answers per check (core/checkcache.py). Bump a kind's number when
# its rules change, so answers worked out under the old rules are not reused.
# The full decode check keeps its own cache (decodecheck.Cache) and versions.
# probe 2: the remux reason no longer promises "數秒完成" — the reason text is
# part of the remembered answer, so old answers would keep showing it
CHECK_VERSIONS = {"probe": 2, "timing": 1, "blanks": 1, "gaps": 1}
# A check that could not read the file at all. Not an answer, so never
# remembered: on a NAS that is often a passing hiccup, and remembering it as
# "nothing found" would hide the file until it changed.
_UNREAD = object()


def probe(path: Path) -> dict:
    """Codec summary for one file; empty dict when ffprobe cannot read it."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format",
             "-of", "json", str(path)],
            capture_output=True, timeout=60, creationflags=SUBPROCESS_FLAGS)
        d = json.loads(r.stdout or b"{}")
    except Exception:
        return {}
    v = a = None
    for s in d.get("streams", []):
        if s.get("codec_type") == "video" and v is None:
            v = s
        elif s.get("codec_type") == "audio" and a is None:
            a = s
    if not v:
        return {}
    try:
        dur = float(d.get("format", {}).get("duration") or 0)
    except (TypeError, ValueError):
        dur = 0.0
    return {
        "vcodec": v.get("codec_name", ""),
        "acodec": (a or {}).get("codec_name", ""),
        "pix_fmt": v.get("pix_fmt", ""),
        "width": int(v.get("width") or 0),
        "height": int(v.get("height") or 0),
        "duration": dur,
        # `retime` refuses a file with more than one audio track rather than
        # silently keeping the first, so the count has to survive this summary.
        "nb_audio": sum(1 for x in d.get("streams", [])
                        if x.get("codec_type") == "audio"),
    }


def plan_for(path: Path) -> dict:
    """Decide how (and whether) this file needs converting."""
    info = probe(path)
    if not info:
        # "damaged or not a video" was wrong on both counts for the one real
        # case: a fragmented MP4 named .ts, structurally intact, with a single
        # bad fragment out of 3,676. Both conditions are required — 19 other
        # files in the library have an `ftyp` header under a non-mp4 name and
        # read perfectly, so the header alone must not offer a repair.
        if repair.looks_like_mp4(path):
            return {"action": "repair", "reason": "片段損壞，可嘗試修復",
                    **_empty()}
        return {"action": "skip", "reason": "無法讀取（檔案損毀或不是影片）", **_empty()}

    v, a, pix = info["vcodec"], info["acodec"], info["pix_fmt"]
    note = ""

    if v in _OK_VIDEO and pix in _OK_PIX:
        if not a or a in _OK_AUDIO:
            action, reason = "remux", "換殼（無損，不重新編碼）"
        else:
            action, reason = "remux_audio", f"換殼＋音訊轉 AAC（視訊無損，來源音訊 {a}）"
    elif v in _RISKY_VIDEO:
        action, reason = "reencode", f"需重編碼（{v} 在瀏覽器支援不穩定）"
    elif v in _OK_VIDEO:
        action, reason = "reencode", f"需重編碼（像素格式 {pix}，瀏覽器不支援）"
    else:
        action, reason = "reencode", f"需重編碼（視訊為 {v}）"

    return {"action": action, "reason": reason, "note": note, **info}


def _empty() -> dict:
    return {"vcodec": "", "acodec": "", "pix_fmt": "",
            "width": 0, "height": 0, "duration": 0.0, "nb_audio": 0}


def target_for(src: Path) -> Path:
    """Where the converted file goes.

    For a non-MP4 source this is the same name with an `.mp4` suffix. For an
    MP4 source it is *the source itself*, because a `.mp4` that a browser
    cannot play still wants to end up as that exact filename — so the caller
    writes to `staging_for()` first and only then replaces the original.
    """
    return src.with_suffix(".mp4")


def is_mp4(path: Path) -> bool:
    return path.suffix.lower() == ".mp4"


def staging_for(src: Path) -> Path:
    """Temporary output beside the source, for the replace-in-place case.

    Beside it rather than in a temp directory: the replace has to be an
    `os.replace` on the same volume to be atomic, and these files are gigabytes
    on NAS shares where a cross-volume copy would be a second full transfer.
    """
    return src.with_name(src.stem + ".converting.mp4")


# ── running ffmpeg ───────────────────────────────────────────────────────────

def _build_cmd(src: Path, dst: Path, action: str, info: dict,
               crf: int = 20, gpu: bool = False) -> list:
    # +genpts repairs the missing/rolling timestamps that broadcast-style TS
    # captures often carry
    cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-y",
           "-fflags", "+genpts", "-i", str(src),
           "-map", "0:v:0", "-map", "0:a:0?"]
    # Anything that decodes the audio must not stop at a corrupt frame that
    # claims new parameters (decodecheck.SKIP_CHANGED_AUDIO) — that is how
    # the concealing re-encode of OK4yHUw8VFxpDXDD.mp4 failed at 10:47.
    if action in ("remux_audio", "reencode", "audiofix", "conceal") or (
            action == "gapfix" and info.get("fix_audio")):
        cmd[cmd.index("-i"):cmd.index("-i")] = decodecheck.SKIP_CHANGED_AUDIO

    if action == "remux":
        cmd += ["-c", "copy"]
        # AAC inside MPEG-TS is ADTS-framed; MP4 needs the raw ASC form
        if info.get("acodec") == "aac":
            cmd += ["-bsf:a", "aac_adtstoasc"]
    elif action == "remux_audio":
        cmd += ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k"]
    elif action == "audiofix":
        # The decoder drops the corrupt packets; `aresample=async=1` then
        # fills the holes they — and the recording's own dropouts — leave
        # with silence, so the new track stays on the timestamps and in sync
        # with the copied video. Measured on the MTALL-120 file: a seek into
        # its first dropout failed the browser's player, the fixed copy
        # seeked there, back, and played straight through.
        cmd += ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                "-af", "aresample=async=1:first_pts=0"]
    elif action == "conceal":
        cmd += ["-c:v", "libx264", "-crf", str(CONCEAL_CRF), "-preset", "medium",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                "-af", "aresample=async=1:first_pts=0"]
    elif action == "gapcut":
        # Each blank's length is subtracted from every packet after it, so
        # the recording plays straight on; every packet is copied. Decided on
        # DTS for both pts and dts so a reordered B-frame moves with the
        # packets around it, not on its own.
        expr = _setts_expr(info.get("blanks") or [])
        abf = expr
        if info.get("acodec") == "aac" and not is_mp4(src):
            abf += ",aac_adtstoasc"
        cmd += ["-c", "copy", "-bsf:v", expr, "-bsf:a", abf]
        # -copyts: the cut points were read from the file's own timestamps,
        # so setts must see those, unshifted. Without it ffmpeg moves the
        # start to 0 and, for MPEG-TS, quietly closes any jump over 10 s
        # itself (its discontinuity handling) — the blank would then be
        # taken out twice.
        cmd.insert(cmd.index("-i"), "-copyts")
    elif action == "gapfix":
        # `noise=drop=` drops packets whose expression is non-zero; `n` is the
        # packet's index in the stream, the same order `gap_plan` counted in.
        drop = info.get("drop") or []
        expr = "+".join(f"eq(n\\,{i})" for i in drop) or "0"
        cmd += ["-c:v", "copy", "-bsf:v", f"noise=drop={expr}"]
        if info.get("fix_audio"):
            cmd += ["-c:a", "aac", "-b:a", "192k",
                    "-af", "aresample=async=1:first_pts=0"]
        else:
            cmd += ["-c:a", "copy"]
            if info.get("acodec") == "aac" and not is_mp4(src):
                cmd += ["-bsf:a", "aac_adtstoasc"]
    else:
        if gpu:
            cmd += ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr",
                    "-cq", str(crf)]
        else:
            cmd += ["-c:v", "libx264", "-crf", str(crf), "-preset", "medium"]
        cmd += ["-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k"]

    cmd += ["-movflags", "+faststart", "-progress", "pipe:1", "-nostats",
            str(dst)]
    return cmd


_TIME_RE = re.compile(rb"out_time_ms=(\d+)")


def run_one(src: Path, dst: Path, action: str, info: dict,
            crf: int = 20, gpu: bool = False,
            progress: Optional[Callable[[float], None]] = None) -> None:
    """Convert one file. Raises RuntimeError with ffmpeg's message on failure.

    stderr is drained by its own thread, as in `clip._run_cmd`. Reading it
    only after stdout ends deadlocks once ffmpeg has more to say than the
    pipe holds — and on Windows that is a few KB. Clean files say nothing,
    so this hid until the audio fix of MTALL-120: 79 corrupt AAC packets,
    a decoder line each, and ffmpeg sat blocked at 62% with 0 CPU.
    """
    cmd = _build_cmd(src, dst, action, info, crf, gpu)
    total = max(info.get("duration") or 0, 0.001)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=SUBPROCESS_FLAGS)
    err_parts: list = []

    def _drain():
        try:
            for chunk in iter(lambda: proc.stderr.read(65536), b""):
                err_parts.append(chunk)
                # bounded: the log keeps the tail, ffmpeg_error summarises it
                if len(err_parts) > 16:
                    del err_parts[0]
        except Exception:
            pass

    pump = threading.Thread(target=_drain, daemon=True)
    pump.start()
    try:
        for line in proc.stdout:
            m = _TIME_RE.search(line)
            if m and progress:
                progress(min(1.0, int(m.group(1)) / 1e6 / total))
    finally:
        proc.stdout.close()
        proc.wait()
        pump.join(timeout=5)
        proc.stderr.close()
    err = b"".join(err_parts).decode("utf-8", "replace").strip()
    if proc.returncode != 0:
        # The whole thing goes to the log even though only a summary reaches
        # the UI: a failure that happens once, on a 2 GB file, over a scan the
        # user has since closed, is not something anyone can reproduce on
        # demand. `err` is bounded by ffmpeg's own `-v error`.
        log.error("ffmpeg failed (%d) on %s\n%s",
                  proc.returncode, src, err)
        raise RuntimeError(ffmpeg_error(err, proc.returncode))


def verify(src_info: dict, dst: Path, action: str) -> Optional[str]:
    """Return None when the output looks right, otherwise why it does not.

    The original is only deleted when this passes.
    """
    if not dst.exists() or dst.stat().st_size == 0:
        return "輸出檔不存在或為空"
    out = probe(dst)
    if not out:
        return "輸出檔無法讀取"
    if not out["vcodec"]:
        return "輸出檔沒有視訊軌"
    src_dur = src_info.get("duration") or 0
    if (action == "conceal" and src_dur > 0
            and abs(out["duration"] - src_dur) > 1.0):
        # 0K4KREIq4KD1kijH.mp4: four minutes with no decodable keyframe, and
        # the re-encode came out 18 s long. That is not a repair.
        return (f"損壞太嚴重：修完只剩 {out['duration']:.0f} 秒"
                f"（原檔 {src_dur:.0f} 秒）")
    if src_dur > 0 and abs(out["duration"] - src_dur) > 1.0:
        return (f"時長不符（原檔 {src_dur:.1f}s，輸出 {out['duration']:.1f}s）")
    if (action in ("remux", "remux_audio", "audiofix", "gapfix", "gapcut")
            and out["vcodec"] != src_info["vcodec"]):
        return f"視訊編碼改變了（{src_info['vcodec']} → {out['vcodec']}）"
    if action == "audiofix":
        # the one thing this action exists to change, so check it did
        n = decodecheck.audio_errors(dst)
        if n:
            return f"聲音仍有 {n} 個壞掉的封包"
    if action == "gapfix":
        # Dropping frames is only right if the frames dropped were the whole
        # problem. The scan's call that they were is a heuristic; this is the
        # proof, and the original is only replaced when it holds.
        n = decodecheck.decode(dst)
        if n is None:
            return "無法完整解碼驗證輸出檔"
        if n or n.audio:
            return f"修復後仍有畫面錯誤 {int(n)} 行、聲音錯誤 {n.audio} 行"
    if action == "conceal":
        n = decodecheck.decode(dst)
        if n is None:
            return "無法完整解碼驗證輸出檔"
        if n or n.audio:
            return f"重新編碼後仍有畫面錯誤 {int(n)} 行、聲音錯誤 {n.audio} 行"
    if action == "gapcut":
        # Only timestamps were meant to change: every frame must still be
        # there, and the blanks must actually be gone.
        frames = src_info.get("frames")
        got = _video_packets(dst)
        if frames and got != frames:
            return f"畫格數不符（原檔 {frames}，輸出 {got}）"
        after = blank_plan(dst)
        if after and after["blanks"]:
            return f"輸出仍有 {len(after['blanks'])} 段空白"
    if action == "reencode":
        # The whole point of a re-encode is that the codec changed, so the
        # check above is inverted here: what would be a failure for a remux is
        # the success condition for this. Without it a re-encode that silently
        # copied the stream would pass.
        if out["vcodec"] not in _OK_VIDEO:
            return f"輸出仍然不是瀏覽器能播的編碼（{out['vcodec']}）"
        if out["pix_fmt"] and out["pix_fmt"] not in _OK_PIX:
            return f"輸出的像素格式仍不支援（{out['pix_fmt']}）"
    return None


def replace_original(src: Path, staged: Path) -> None:
    """Swap a verified re-encode in for the source it came from.

    `os.replace` is atomic on the same volume, so there is no moment where the
    name exists with nothing behind it — which matters because this path is
    used on files the user cannot currently play but has nothing else of.

    The caller is responsible for dropping the derived data afterwards: the
    path is unchanged, so every cached thumbnail and contact sheet still keys
    to it while now showing the *old* content. Watched/favourite marks are also
    keyed by path and must NOT be dropped — it is the same film.
    """
    import os
    os.replace(str(staged), str(src))


# Probing is one ffmpeg process per file and the wait is almost all I/O, so
# it parallelises nearly linearly. Measured over the real library: 80 files at
# a time, 8 workers — local disk 140 → 13 ms/file, NAS 91 → 10 ms/file. That is
# what makes checking all 19,503 MP4s a 3.5-minute job instead of a 33-minute
# one. Eight rather than the core count because the limit here is the NAS, not
# the CPU, and because a convert may be running at the same time.
PROBE_WORKERS = 8


def scan(roots, progress: Optional[Callable[[str], None]] = None,
         include_mp4: bool = False, check_timing: bool = False,
         check_decode: bool = False, decode_cache: Optional[Path] = None,
         decode_gpu: bool = False, check_blanks: bool = False,
         check_cache: Optional[Path] = None, recheck: bool = False,
         stats: Optional[dict] = None) -> list:
    """Videos under `roots` that need converting, with the plan for each.

    With `check_cache`, every check remembers its answer per file (see
    core/checkcache.py) and a file whose size and mtime have not changed is
    not checked again — scanning the same character twice used to pay for
    every check twice. `recheck` ignores what is remembered (and writes the
    new answers). `stats`, when given, is filled with how many files each
    check reused and how many it actually checked.

    `include_mp4` exists because "the extension is .mp4" was being treated as
    "a browser can play it", and that is simply not true — the container says
    nothing about the codec inside. The library holds an `EVDV-54319.mp4` whose
    video stream is `mpeg4` (MPEG-4 Part 2, the Xvid era), which no browser
    decodes. It was invisible to this scan, so the one tool that could fix it
    could not see it.

    It is opt-in rather than the default because it costs an ffprobe on every
    MP4 in the library — a sampled survey put 95.7% of them at plain h264, so
    the overwhelming majority of that work finds nothing.
    """
    files = []
    every = []                  # all videos in scope, for the decode check
    seen = set()
    for root in roots:
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            ext = path.suffix.lower()
            if ext not in VIDEO_EXT:
                continue
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            every.append(path)
            if ext == ".mp4" and not include_mp4:
                continue
            files.append(path)

    store = checkcache.Store(check_cache) if check_cache else None
    run = lambda paths, fn, kind, label, **kw: _run_checks(
        paths, fn, kind, label, store=store, recheck=recheck,
        progress=progress, stats=stats, **kw)
    try:
        # an unreadable file may be a passing NAS hiccup, and a repair is
        # cheap to find again: neither is remembered
        plans = run(files, plan_for, "probe", "檢查格式",
                    keep=lambda p: p["action"] != "repair"
                    and not p["reason"].startswith("無法讀取"))
        timing = {}
        if check_timing:
            # only an MP4 the probe passed can have the timing defect
            late = [path for path, p in zip(files, plans)
                    if is_mp4(path) and p["action"] in ("remux", "skip")]
            timing = dict(zip(late, run(late, retime.needs_retime, "timing",
                                        "檢查影格時序")))
        out = [_row(path, p, timing.get(path)) for path, p in zip(files, plans)]
        if check_blanks:
            _blank_pass(every, out, progress, run)
        if check_decode:
            _decode_pass(every, out, progress, decode_cache, decode_gpu,
                         recheck=recheck, stats=stats,
                         gaps=lambda paths: run(paths, _gap_check, "gaps",
                                                "檢查錄影斷訊",
                                                keep=lambda v: v is not _UNREAD))
    finally:
        if store:
            store.close()
    return out


def _stat(path: Path):
    try:
        st = path.stat()
        return st.st_size, st.st_mtime
    except OSError:
        return None


def _run_checks(paths, fn, kind: str, label: str, *, store=None,
                recheck: bool = False, progress=None, stats=None,
                keep=None, stoppable: bool = False,
                workers: int = PROBE_WORKERS) -> list:
    """`fn(path)` for every path, in order, reusing remembered answers.

    A file is looked up by its size and mtime as they are *before* the check
    runs, so one that changes while being checked is checked again next time.
    `keep(answer)` says whether an answer may be remembered. With `stoppable`
    the stop button skips the files not started yet; their slots hold
    `checkcache.MISS`.
    """
    version = CHECK_VERSIONS[kind]
    out = [checkcache.MISS] * len(paths)
    todo = []
    for i, path in enumerate(paths):
        st = _stat(path)
        if store and st and not recheck:
            hit = store.get(kind, version, path, *st)
            if hit is not checkcache.MISS:
                out[i] = hit
                continue
        todo.append((i, path, st))
    reused = len(paths) - len(todo)
    stop = lambda: stoppable and bool(getattr(progress, "cancelled", False))
    done = [0]
    lock = threading.Lock()
    tail = f"，另 {reused} 支沿用上次結果" if reused else ""

    def one(item):
        i, path, st = item
        if stop():
            return
        v = fn(path)
        if store and st and (keep is None or keep(v)):
            store.put(kind, version, path, *st, v)
        out[i] = v
        with lock:
            done[0] += 1
            k = done[0]
        if progress:
            progress(f"{label}：{path.name}（{k}/{len(todo)}{tail}）")

    if todo:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(one, todo))
    if stats is not None and kind != "gaps":    # a step of the decode check
        stats[kind] = {"reused": reused, "checked": done[0]}
    return out


def _decode_pass(every, out: list, progress, cache_path: Optional[Path],
                 gpu: bool = False, recheck: bool = False,
                 stats: Optional[dict] = None, gaps=None) -> None:
    """Fully decode every video in scope; mark the damaged ones in `out`.

    Covers every video, not just the ones the probe pass listed: a damaged
    file is usually a perfectly ordinary `.mp4` that the other checks pass.
    A damaged file that also needed converting is reported as damaged —
    converting it would only carry the damage into a new file.

    Stoppable: `progress.cancelled` (set by the stop button) ends the pass at
    once — the files being decoded are abandoned, not finished; what was
    checked before is still reported.
    """
    by_path = {r["path"]: i for i, r in enumerate(out)}
    cache = decodecheck.Cache(cache_path) if cache_path else None
    stop = lambda: bool(getattr(progress, "cancelled", False))
    done = [0]
    lock = threading.Lock()
    # Remembered answers are looked up first, so the progress counts only the
    # files that are actually decoded — the ones that cost time.
    results, todo = [], []
    for path in every:
        st = _stat(path) if cache and not recheck else None
        hit = cache.get(path, *st) if st else None
        if hit is not None:            # 0 is an answer, not a miss
            results.append((path, hit))
        else:
            todo.append(path)
    tail = f"，另 {len(results)} 支沿用上次結果" if results else ""

    def one(path: Path):
        if stop():
            return path, None
        # stop is also asked *during* the file: one can take many minutes
        n = decodecheck.check(path, cache, gpu, force=recheck, stop=stop)
        with lock:
            done[0] += 1
            k = done[0]
        if progress:
            progress(f"完整解碼檢查：{path.name}（{k}/{len(todo)}{tail}）")
        return path, n

    # The lengths decide how many at once (see DECODE_WORKERS); a length that
    # cannot be read counts as long — one at a time is never the wrong call.
    with ThreadPoolExecutor(max_workers=PROBE_WORKERS) as ex:
        lengths = list(ex.map(_duration, todo))
    short = [p for p, d in zip(todo, lengths) if 0 < d < LONG_DECODE_SECONDS]
    long_ = [p for p, d in zip(todo, lengths) if not 0 < d < LONG_DECODE_SECONDS]
    try:
        with ThreadPoolExecutor(max_workers=DECODE_WORKERS) as ex:
            results += ex.map(one, short)
        results += [one(p) for p in long_]
    finally:
        if cache:
            cache.close()
    if stats is not None:
        stats["decode"] = {"reused": len(every) - len(todo), "checked": done[0]}
    # Whether each damaged file's errors are only the cut-off frame before a
    # recording dropout (`gap_plan`); asked for the damaged ones only.
    hurt = [p for p, n in results if n]
    plans = dict(zip(hurt, gaps(hurt) if gaps else map(_gap_check, hurt)))
    for path, n in results:
        # n is an int (video errors) carrying `.audio` — 0 video errors with
        # damaged audio is falsy, so test both
        if n is None or (not n and not getattr(n, "audio", 0)):
            continue
        key = str(path)
        before = out[by_path[key]] if key in by_path else None
        audio = getattr(n, "audio", 0)
        if not n:
            # Picture intact, audio damaged: fixable. A non-MP4 that is
            # being remuxed anyway gets the fix as part of that (see
            # `audio_needs_fix`), so it only needs saying.
            act = before.get("action") if before else None
            if act in ("remux", "remux_audio"):
                out[by_path[key]] = {**before, "audio_errors": audio,
                                     "reason": before["reason"] +
                                     f"；聲音有 {audio} 個壞封包，換殼時會一併重新編碼聲音"}
                continue
            if act and act != "skip":
                # needs something bigger (a re-encode, a repair): that action
                # stays, the finding is only noted
                out[by_path[key]] = {**before, "audio_errors": audio,
                                     "reason": before["reason"] +
                                     f"；聲音另有 {audio} 個壞封包"}
                continue
            if before and not is_mp4(path):
                continue                # a skipped non-MP4 keeps its reason
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            row = dict(before) if before else {
                "path": key, "name": path.name, "size": size,
                "dst": str(path), "in_place": True, **_empty(),
                "duration": _duration(path)}
            row.update(action="audiofix", audio_errors=audio,
                       reason=f"聲音有 {audio} 個壞封包（瀏覽器拖進度條會卡死）；"
                              "影像不動、只重新編碼聲音，驗證後取代原檔")
            if key in by_path:
                out[by_path[key]] = row
            else:
                out.append(row)
            continue
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        row = dict(before) if before else {
            "path": key, "name": path.name, "size": size, "dst": "",
            "in_place": False, **_empty(),
            "duration": _duration(path)}
        # Picture errors that are only the truncated frame before each
        # dropout are fixable (see `gap_plan`). A cheap test here — a few
        # errors per dropout, every dropout on a keyframe — and the full
        # decode after the fix decides for real.
        plan = plans.get(path)
        plan = plan if isinstance(plan, dict) else None     # MISS, _UNREAD
        if plan and int(n) <= 3 * plan["gaps"]:
            row.update(action="gapfix", decode_errors=int(n), audio_errors=audio,
                       gaps=plan["gaps"],
                       dst=str(path) if is_mp4(path) else str(target_for(path)),
                       in_place=is_mp4(path),
                       reason=f"錄影斷訊 {plan['gaps']} 處，斷訊前殘缺的畫格 {int(n)} 行錯誤"
                              + (f"、聲音 {audio} 個壞封包" if audio else "")
                              + "；丟掉殘缺的格子（不重新編碼），完整解碼驗證後"
                              + ("取代原檔" if is_mp4(path) else "轉成 .mp4"))
            if key in by_path:
                out[by_path[key]] = row
            else:
                out.append(row)
            continue
        lv = decodecheck.level(n)
        row.update(action="damaged", decode_errors=int(n), damage=lv,
                   audio_errors=audio,
                   # where, when the check knew: the row and ▶ show it at once
                   regions=getattr(n, "regions", None),
                   reason=f"畫面損壞（{lv}，解碼錯誤 {int(n):,} 行）")
        if key in by_path:
            out[by_path[key]] = row
        else:
            out.append(row)


def gap_plan(path: Path) -> Optional[dict]:
    """The packets to drop for a dropout fix, or None when it does not apply.

    `MTALL-120-Uncensored-Leaked.mp4`: 65 dropouts (video DTS jumps 1–4 s),
    every one resuming on a keyframe, and at 63 of them the last frame
    before the cut is truncated — 1–2 decoder errors each. ffmpeg conceals
    them; Chrome fails the player on the first ("video decode error!"), so a
    seek past 1486 s did nothing even after the audio was fixed. Dropping
    that one packet before each dropout cannot hurt anything after it — the
    next packet is a keyframe — and measured: 65 of 225,699 frames dropped,
    full decode 0 video / 0 audio errors, all 63 dropouts tested seek and
    play through in the browser. Applies only when *every* dropout resumes
    on a keyframe; the full decode after the fix is the real check.
    """
    plan = _gap_check(path)
    return None if plan is _UNREAD else plan


def _gap_check(path: Path):
    """`gap_plan`, except a file whose packets could not be read at all is
    `_UNREAD` rather than None ("no dropouts") — the scan remembers None."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "packet=dts_time,flags", "-of", "csv=p=0",
             str(path)], capture_output=True, text=True, timeout=600,
            creationflags=SUBPROCESS_FLAGS)
    except Exception:
        return _UNREAD
    if not r.stdout.strip():
        return _UNREAD
    dts, key = [], []
    for line in r.stdout.splitlines():
        parts = line.split(",")
        try:
            dts.append(float(parts[0]))
        except (ValueError, IndexError):
            dts.append(None)        # keep the index: it is what gets dropped
        # by position: MPEG-TS adds an empty trailing column (see `_packets`)
        key.append(len(parts) > 1 and "K" in parts[1])
    gaps = [i for i in range(len(dts) - 1)
            if dts[i] is not None and dts[i + 1] is not None
            and dts[i + 1] - dts[i] > GAP_SECONDS]
    if not gaps or len(gaps) > MAX_GAPS:
        return None
    if not all(key[i + 1] for i in gaps):
        return None
    return {"gaps": len(gaps), "drop": gaps}


def _packets(path: Path, stream: str) -> list:
    """[(time, is_keyframe)] for one stream in file order; [] if unreadable.
    The time is the DTS where there is one, else the PTS."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", stream,
             "-show_entries", "packet=pts_time,dts_time,flags",
             "-of", "csv=p=0", str(path)], capture_output=True, text=True,
            timeout=600, creationflags=SUBPROCESS_FLAGS)
    except Exception:
        return []
    out = []
    for line in r.stdout.splitlines():
        parts = line.split(",")
        t = None
        for v in parts[:2]:             # pts_time, then dts_time wins
            try:
                t = float(v)
            except ValueError:
                pass
        if t is not None:
            # the flags column by position: MPEG-TS adds a trailing empty
            # side-data column, so `parts[-1]` would read that and never K
            out.append((t, len(parts) > 2 and "K" in parts[2]))
    return out


def _video_packets(path: Path) -> int:
    return len(_packets(path, "v:0"))


def blank_plan(path: Path) -> Optional[dict]:
    """The empty stretches in a recording, or None when there are none.

    `MMMMMooooooooooo-20261022-220946.mp4`: 61 min 20 s long, but no video
    and no audio packets at all from 1:08 to 21:06, 37:18 to 47:16 and for
    2 s at 51:28 — the recorder kept its clock while the stream sent nothing.
    Players hold the last frame, so it looks like "many identical frames".
    The decode check passes it: nothing in it is damaged.

    A blank is a jump of at least `BLANK_SECONDS` between two video packets
    with no audio packet inside it either (sound over a frozen camera is
    content, not a blank), resuming on a keyframe so it can come out without
    re-encoding. Ones that fail either test are counted in `skipped` and left
    alone. Reads only the packet index: about 0.5 s a file on a local disk.
    """
    plan = _blank_check(path)
    return None if plan is _UNREAD else plan


def _blank_check(path: Path):
    """`blank_plan`, except an unreadable file is `_UNREAD` (see `_gap_check`)."""
    import bisect
    v = sorted(_packets(path, "v:0"))
    if len(v) < 2:
        return _UNREAD
    a = sorted(t for t, _k in _packets(path, "a:0"))
    deltas = sorted(y[0] - x[0] for x, y in zip(v, v[1:]) if y[0] > x[0])
    if not deltas:
        return None
    fd = deltas[len(deltas) // 2]       # one frame
    blanks, skipped = [], 0
    for x, y in zip(v, v[1:]):
        if y[0] - x[0] < BLANK_SECONDS:
            continue
        i = bisect.bisect_right(a, x[0] + 1.0)
        sound = i < len(a) and a[i] < y[0] - 1.0
        if sound or not y[1]:
            skipped += 1
            continue
        blanks.append({"start": round(x[0], 3), "end": round(y[0], 3),
                       "cut": round(y[0] - x[0] - fd, 6)})
    if not blanks and not skipped:
        return None
    if len(blanks) > MAX_BLANKS:
        skipped += len(blanks)
        blanks = []
    return {"blanks": blanks, "skipped": skipped, "frames": len(v),
            "removed": round(sum(bl["cut"] for bl in blanks), 3)}


def _setts_expr(blanks: list) -> str:
    """`setts` text that takes every blank out of the timeline.

    Each threshold is the blank's midpoint, so a packet exactly on an edge
    cannot land on the wrong side. Commas are escaped because `-bsf` takes a
    comma-separated list of filters.
    """
    terms = "+".join(
        f"gte(DTS*TB\\,{(bl['start'] + bl['end']) / 2:.6f})*{bl['cut']:.6f}"
        for bl in blanks) or "0"
    off = f"({terms})/TB"
    return f"setts=pts=PTS-{off}:dts=DTS-{off}"


def _mmss(sec: float) -> str:
    sec = int(round(max(sec, 0)))
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _blank_pass(every, out: list, progress, run=None) -> None:
    """Look for empty stretches in every video in scope (`check_blanks`).

    A file with nothing else to do becomes a `gapcut` row (a non-MP4 is
    remuxed to .mp4 in the same pass). One that needs something bigger keeps
    that action and the blanks are only mentioned: do that first, then scan
    again. Stoppable like the decode pass.
    """
    by_path = {r["path"]: i for i, r in enumerate(out)}
    run = run or (lambda paths, fn, kind, label, **kw:
                  _run_checks(paths, fn, kind, label, progress=progress, **kw))
    plans = run(every, _blank_check, "blanks", "檢查錄影中斷",
                keep=lambda v: v is not _UNREAD, stoppable=True)
    for path, plan in zip(every, plans):
        if not isinstance(plan, dict):      # none, unreadable, or stopped
            continue
        key = str(path)
        before = out[by_path[key]] if key in by_path else None
        n, removed = len(plan["blanks"]), plan["removed"]
        if not n:
            if before:
                out[by_path[key]] = {**before, "reason": before["reason"] +
                                     f"；有 {plan['skipped']} 段空白無法無損去除"
                                     "（空白中仍有聲音，或空白後不是關鍵影格）"}
            continue
        if before and before.get("reason") == "同名的 .mp4 已存在":
            continue
        act = before.get("action") if before else "skip"
        if act not in ("skip", "remux", "remux_audio"):
            out[by_path[key]] = {**before, "reason": before["reason"] +
                                 f"；另有錄影中斷 {n} 處（共 {_mmss(removed)}），"
                                 "先處理這個，之後再掃一次"}
            continue
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        dur = (before or {}).get("duration") or _duration(path)
        row = dict(before) if before else {
            "path": key, "name": path.name, "size": size, **_empty(),
            "duration": dur}
        row.update(action="gapcut", blanks=n, removed=removed,
                   kept=round(max(dur - removed, 0), 1),
                   skipped_blanks=plan["skipped"],
                   dst=str(path) if is_mp4(path) else str(target_for(path)),
                   in_place=is_mp4(path),
                   reason=f"錄影中斷 {n} 處，共 {_mmss(removed)} 沒有畫面也沒有聲音；"
                          f"無損去除後長度 {_mmss(dur - removed)}，驗證後"
                          + ("取代原檔" if is_mp4(path) else "轉成 .mp4")
                          + (f"（另有 {plan['skipped']} 段無法無損去除）"
                             if plan["skipped"] else ""))
        if key in by_path:
            out[by_path[key]] = row
        else:
            out.append(row)


def audio_needs_fix(path: Path) -> int:
    """Corrupt audio packets in `path`'s first audio track (0 if none).

    Asked before a remux: copying the streams carries a corrupt packet into
    the new MP4 untouched, and the browser then fails on it exactly as it
    did on the source — so a remux of such a file has to re-encode the audio
    (`audiofix`). A few seconds even for a 2-hour file. A file ffmpeg cannot
    decode at all counts as 0 here; the remux itself will report that.
    """
    return decodecheck.audio_errors(path) or 0


def _duration(path: Path) -> float:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "format=duration", "-of", "csv=p=0", str(path)],
                           capture_output=True, text=True, timeout=30,
                           creationflags=SUBPROCESS_FLAGS)
        return float(r.stdout.strip() or 0)
    except Exception:
        return 0.0


def _timing_row(path: Path, p: dict, found=None) -> Optional[dict]:
    """The retime plan for an otherwise-fine MP4, or None when it has none.

    A file that has the defect but cannot be rebuilt (not H.264, more than one
    audio track) is still reported — as a skip that says why. Staying silent
    would leave the user with a file that stutters and no explanation.
    `found` is `needs_retime`'s answer when the scan already has it.
    """
    bad, why = found if found is not None else retime.needs_retime(path)
    if not bad:
        return None
    if not retime.available():
        return {**p, "action": "skip",
                "reason": why + "；需要安裝 GPAC 才能修復"}
    unsupported = retime.supported(p)
    if unsupported:
        return {**p, "action": "skip", "reason": f"{why}；{unsupported}"}
    return {**p, "action": "retime",
            "reason": "修正影格時序（無損，驗證後取代原檔）"}


def _row(path: Path, p: dict, timing=None) -> dict:
    """`timing`: `needs_retime`'s answer for this file, None when not asked."""
    dst = target_for(path)
    if is_mp4(path):
        # The source is already the destination, so the two checks that make
        # sense for a .ts do not apply here. What matters instead is whether
        # anything is actually wrong with it.
        if p["action"] in ("remux", "skip"):
            # "已經是可播放的 MP4" was answering the wrong question for a whole
            # class of file: the codec is fine and the browser still drops half
            # the frames, because the container has no composition times for a
            # stream that uses B-frames. See core/retime.py. Only asked when
            # the caller opted in — it costs two more probes per file.
            late = (_timing_row(path, p, timing)
                    if timing not in (None, checkcache.MISS) else None)
            p = late or {**p, "action": "skip", "reason": "已經是可播放的 MP4"}
        elif p["action"] == "reencode":
            p = {**p, "reason": p["reason"] + "；驗證後會取代原檔"}
    elif dst.exists():
        p = {**p, "action": "skip", "reason": "同名的 .mp4 已存在"}
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return {"path": str(path), "name": path.name, "size": size,
            "dst": str(dst), "in_place": is_mp4(path), **p}
