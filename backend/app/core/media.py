"""
Pure media logic for the web backend — ported from the desktop app's app.py
(the parts with no PyQt dependency): scanning, probing, thumbnails, hashing.
"""
from __future__ import annotations

import os
import json
import shutil
import logging
import hashlib
import platform
import subprocess
import unicodedata
from pathlib import Path
from typing import List, Tuple, Optional

log = logging.getLogger("vam")

VIDEO_EXT = {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.m4v',
             '.ts', '.webm', '.rmvb', '.mpg', '.mpeg', '.3gp'}
IMAGE_EXT = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.tiff', '.tif'}

# ffmpeg/ffprobe must be on PATH (installed in the Docker image, or locally in dev)
SUBPROCESS_FLAGS = (subprocess.CREATE_NO_WINDOW
                    if platform.system() == "Windows" else 0)


# ── Scanning ─────────────────────────────────────────────────────────────────

def scan_videos_with_stat(root: Path, posters: Optional[dict] = None
                          ) -> List[Tuple[Path, int, float]]:
    """Recursively scan for videos via os.scandir → (path, size, mtime).

    `posters`, when given, is filled from the same walk with {lower-case path:
    mtime} of every image that could be a cover (`poster_stamp` reads it).
    The card's URL carries the video's mtime so the browser can keep the
    thumbnail a month — which also means a cover downloaded later would not
    show for a month. With the cover's mtime in the URL it shows at once. On
    Windows `DirEntry.stat()` comes with the listing: no extra request.
    """
    result: List[Tuple[Path, int, float]] = []

    def _walk(dir_path: str):
        try:
            with os.scandir(dir_path) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if not entry.name.startswith('.'):
                                _walk(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            suf = Path(entry.name).suffix.lower()
                            if suf in VIDEO_EXT:
                                st = entry.stat()
                                result.append((Path(entry.path),
                                               st.st_size, st.st_mtime))
                            elif posters is not None and suf in POSTER_EXT:
                                st = entry.stat()
                                if st.st_size > 0:
                                    posters[entry.path.lower()] = st.st_mtime
                    except OSError:
                        continue
        except OSError:
            return

    _walk(str(root))
    return result


# ── Probe / thumbnails (ffmpeg) ──────────────────────────────────────────────

def probe_video(path: Path) -> Tuple[float, int, int]:
    """Return (duration_sec, width, height) via ffprobe."""
    dur, w, h, _codec = probe_video_codec(path)
    return dur, w, h


def probe_video_codec(path: Path) -> Tuple[float, int, int, str]:
    """`probe_video` plus the video codec name, from the same single ffprobe."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", "-show_format", str(path)],
            capture_output=True, text=True, encoding='utf-8',
            errors='replace', timeout=15, creationflags=SUBPROCESS_FLAGS)
        data = json.loads(r.stdout)
        dur = float(data.get("format", {}).get("duration", 0))
        w = h = 0
        codec = ""
        for s in data.get("streams", []):
            if s.get("codec_type") == "video":
                w, h = s.get("width", 0), s.get("height", 0)
                codec = s.get("codec_name", "") or ""
                break
        return dur, w, h, codec
    except Exception:
        return 0.0, 0, 0, ""


# Some files declare colour primaries and transfer characteristics as
# "reserved" (H.264 VUI value 0). An AI upscaler in this library writes them —
# measured 1 file in a 202-file sample. swscale **refuses** such input:
#
#     Unsupported input (Error number -129 occurred):
#     fmt:yuv420p csp:bt709 prim:reserved trc:reserved -> fmt:yuv420p ...
#
# It only bites when swscale must convert colour, not merely resize — which is
# exactly the three paths that extract a picture (JPEG is full-range, and the
# duplicate scanner asks for gray). Measured: card thumbnail, contact sheet and
# phash all fail; shrink and clip, which stay in limited-range yuv420p, do not.
#
# The failure is total and silent: no frame, no thumbnail, no error the user
# can see — just an empty box.
#
# Used only as a RETRY. Forcing bt709 on every file would flatten the colour of
# anything whose metadata is correct and not bt709 (HDR), to fix one file in
# two hundred.
COLOUR_FIX = "setparams=color_primaries=bt709:color_trc=bt709"


def make_thumbnail(src: Path, dst: Path, t: float, size: int = 640) -> bool:
    dst.parent.mkdir(parents=True, exist_ok=True)
    for vf in (f"scale={size}:-1", f"{COLOUR_FIX},scale={size}:-1"):
        try:
            subprocess.run(
                ["ffmpeg", "-ss", str(max(0, t)), "-i", str(src),
                 "-vframes", "1", "-q:v", "3", "-vf", vf,
                 str(dst), "-y", "-loglevel", "error"],
                timeout=30, capture_output=True, creationflags=SUBPROCESS_FLAGS)
            # size, not existence: a refused conversion still creates the file
            if dst.exists() and dst.stat().st_size > 0:
                return True
        except Exception:
            pass
    return False


THUMB_DIR = "thumbs"


def file_stamp(path: Path) -> str:
    """The file's size and mtime as a short tag, for cache names.

    A cache keyed on the path alone kept serving the old picture after a file
    was replaced *outside* VAM — the user re-downloads damaged videos and
    copies them over the old ones in Explorer, same name, new content — since
    `purge_derived` only runs for what VAM itself does. With the stamp in the
    name the replaced file simply has no cache entry yet.
    """
    try:
        st = path.stat()
    except OSError:
        return "0"
    return f"{st.st_size:x}-{int(st.st_mtime):x}"


def thumb_dir(video: Path, cache: Path) -> Path:
    """`cache/thumbs/ab/abcdef…/` — one directory per video, keyed on the path.

    Per video for the same reason as the contact sheets: deleting the video
    drops the lot without having to know its size and mtime any more.
    Thumbnails used to sit in `cache/00…ff` directly, mixed in with `sheets/`
    and `notes/` (the inbox memos, which must never be evicted);
    `migrate_thumb_layout()` moves those old directories in on startup.
    """
    key = hashlib.md5(str(video).encode()).hexdigest()
    return cache / THUMB_DIR / key[:2] / key


def thumb_cache_path(video: Path, pct: float, cache: Path, size: int = 640,
                     stamp: Optional[str] = None) -> Path:
    """`…/<stamp>_<pct>_<size>.jpg` inside the video's directory."""
    if stamp is None:
        stamp = file_stamp(video)
    return thumb_dir(video, cache) / f"{stamp}_{pct}_{size}.jpg"


def drop_stale(directory: Path, stamp: str) -> int:
    """Remove the cache files in `directory` made for another version of the
    file (a different stamp). Called right after making a fresh one, so a
    replaced video leaves no picture of its old content behind."""
    n = 0
    try:
        entries = list(directory.iterdir())
    except OSError:
        return 0
    for f in entries:
        if f.is_file() and not f.name.startswith(stamp + "_"):
            try:
                f.unlink()
                n += 1
            except OSError:
                pass
    return n


def drop_legacy_cache(cache: Path) -> dict:
    """Delete the cache files from before the stamp was in the name.

    Thumbnails were files directly in `thumbs/ab/`; now every video has a
    directory there, so any file at that level is old. Old contact sheets
    are named `<interval>_<index>_…`, without the `<size>-<mtime>_` prefix.
    Neither could ever be served again; this frees the space at once instead
    of waiting for the size limit. Safe to run every start.
    """
    out = {"thumbs": 0, "sheets": 0}
    for sub in (cache / THUMB_DIR).glob("??"):
        try:
            for f in sub.iterdir():
                if f.is_file():
                    f.unlink()
                    out["thumbs"] += 1
        except OSError:
            continue
    for f in (cache / "sheets").glob("??/*/*.jpg"):
        if "-" not in f.name.split("_", 1)[0]:
            try:
                f.unlink()
                out["sheets"] += 1
            except OSError:
                pass
    if out["thumbs"] or out["sheets"]:
        log.info("cache: removed %d thumbnails and %d contact sheets from the "
                 "old layout", out["thumbs"], out["sheets"])
    return out


def migrate_thumb_layout(cache: Path) -> int:
    """Move pre-existing `cache/00…ff` directories under `cache/thumbs/`.

    Cheap (a rename per directory, 256 at most) and safe to run every start —
    it only moves directories whose names look like the old hash prefixes.
    """
    if not cache.exists():
        return 0
    dest = cache / THUMB_DIR
    moved = 0
    for d in list(cache.iterdir()):
        if not d.is_dir() or len(d.name) != 2:
            continue
        if any(c not in "0123456789abcdef" for c in d.name):
            continue
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / d.name
        try:
            if target.exists():
                for f in d.iterdir():          # merge, then drop the old dir
                    f.replace(target / f.name)
                d.rmdir()
            else:
                d.replace(target)
            moved += 1
        except OSError:
            log.exception("could not move the old thumbnail directory %s", d)
    if moved:
        log.info("thumbnail cache layout: moved %d directories into %s/",
                 moved, THUMB_DIR)
    return moved


# Generating a thumbnail costs one ffprobe + one ffmpeg. A browser will happily
# request 50+ at once for a big folder, which would thrash the CPU (and the NAS).
# Cap concurrent generation the same way the desktop app's thread pool did.
import threading as _threading
_THUMB_SLOTS = _threading.Semaphore(4)


def get_or_make_thumb(video: Path, cache: Path,
                      pct: float = 0.1, size: int = 640) -> Optional[Path]:
    """Return a cached thumbnail path, generating it once if missing.

    One stat of the video per request (the stamp): the price of never
    serving a picture of what the file used to be."""
    stamp = file_stamp(video)
    dst = thumb_cache_path(video, pct, cache, size, stamp)
    if dst.exists():
        return dst                      # cache hit → no ffmpeg, no queueing
    with _THUMB_SLOTS:
        if dst.exists():                # another request may have just made it
            return dst
        dur, _, _ = probe_video(video)
        t = dur * pct if dur > 0 else 3.0
        make_thumbnail(video, dst, t, size)
    if dst.exists():
        drop_stale(dst.parent, stamp)
        return dst
    return None


# A poster is only ever a same-named image *in the same directory* — never one
# fetched from a parent or a sibling. That is the whole rule, and it is narrow
# on purpose: `cover.jpg` sitting beside twenty videos would become the
# thumbnail for all twenty, which is worse than the frame grabs it replaced.
#
# Measured over the library: 272 of 22,083 videos have one (1.2%), but they are
# not spread evenly — H:\裏番 is at 91% (62/68) while the three Social Media
# roots are at 0.04–6%. That lopsidedness is why this is a per-group option
# rather than something applied everywhere.
#
# Extension order is fixed rather than whatever the directory happens to yield,
# so a folder holding both `x.jpg` and `x.png` picks the same one every time —
# an unstable choice here would mean the card changes picture between reloads.
POSTER_EXT = ('.jpg', '.jpeg', '.png', '.webp', '.bmp')


POSTER_SUFFIX = "-poster.jpg"
FANART_SUFFIX = "-fanart.jpg"


def _named(video: Path, suffix: str) -> List[Path]:
    """`<stem><suffix>`, then `<番號><suffix>` — the name the DMM download
    writes (core/dmmcover.py), so `MIDE-844-1.mp4` and `MIDE-844-2.mp4` share
    one. Only `.jpg`: each name is a stat per card over SMB, and every writer
    produces `.jpg`."""
    out = [video.with_name(video.stem + suffix)]
    vid = detect_video_id(video.name)
    if vid and vid.lower() != video.stem.lower():
        out.append(video.with_name(vid + suffix))
    return out


def poster_candidates(video: Path, landscape: bool = False) -> List[Path]:
    """Where a cover for `video` may sit, in the order they win.

    The Kodi / Jellyfin convention, which JAV tools follow too (the user
    spotted it, 2026-10-27): `-poster` is the portrait front, `-fanart` the
    landscape sleeve. So the card's shape picks the kind first — a 16:9 card
    tries `-fanart`, a portrait one the same-named picture and `-poster` —
    and the other kind is the fallback (photothumb crops a sleeve's front
    for a portrait card).
    """
    portrait = [video.with_suffix(ext) for ext in POSTER_EXT]
    portrait += _named(video, POSTER_SUFFIX)
    wide = _named(video, FANART_SUFFIX)
    return wide + portrait if landscape else portrait + wide


def poster_for(video: Path, landscape: bool = False) -> Optional[Path]:
    """The cover image beside `video` (see `poster_candidates`), or None."""
    for p in poster_candidates(video, landscape):
        # Windows is case-insensitive, so one probe per extension covers .JPG
        # too; this stays a plain stat rather than a scandir because the caller
        # is a per-card HTTP request and the directory can hold thousands.
        try:
            if p.is_file() and p.stat().st_size > 0:
                return p
        except OSError:
            continue
    return None


def poster_stamp(video: Path, stamps: dict) -> float:
    """Newest mtime among `video`'s covers of either kind, from the `posters`
    dict `scan_videos_with_stat` fills; 0 when there is none. The listing does
    not know the card's shape, so any cover appearing changes the URL."""
    return max((stamps.get(str(p).lower(), 0) for p in poster_candidates(video)),
               default=0)


# A folder-level cover picture, for characters with no chosen 大頭照 — 89% of
# them (68 of 623 have one).
#
# Four names rather than one. `<資料夾名>_poster` is the convention the user
# intends to use going forward, so it wins; but measured over the library it
# currently matches *nothing*, while `folder.*` already exists in four folders
# and `poster.*` in one — `folder.jpg` being the Windows/Plex/Jellyfin name.
# Accepting only the intended one would ship a feature that does nothing on day
# one.
#
# Fixed order, same reason as `poster_for`: with two candidates present, letting
# directory order decide means the card changes picture between reloads.
#
# This one scans instead of probing each candidate name, which is the opposite
# of `poster_for` — and the measurement is the reason. A character folder's
# first level holds a median of 2 files (the photos live in subfolders), so one
# scandir over 623 folders costs 0.28 s against 0.68 s for 20 stat probes each.
# For videos the directory can hold thousands of files, so the probe wins there.
POSTER_NAMES = ("{name}_poster", "poster", "folder", "cover")


def folder_poster(folder: Path, name: str) -> Optional[Path]:
    """A cover image sitting directly in the character's folder, or None."""
    wanted = {n.format(name=name).lower(): i
              for i, n in enumerate(POSTER_NAMES)}
    best = None
    try:
        with os.scandir(folder) as it:
            for entry in it:
                try:
                    if not entry.is_file(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                stem, ext = os.path.splitext(entry.name)
                if ext.lower() not in POSTER_EXT:
                    continue
                rank = wanted.get(stem.lower())
                # `best` compares rank, so the winner is decided by POSTER_NAMES
                # and never by the order the directory happens to hand them over
                if rank is not None and (best is None or rank < best[0]):
                    best = (rank, Path(entry.path))
    except OSError:
        return None
    return best[1] if best else None


# Subtitles beside a video. Same rule as the poster: same name, same directory,
# fixed extension order. Measured over the library, 31 of 22,183 videos have one
# (0.14%) and every single one is `.ass`, all in J:\動畫.
#
# `.srt` and `.vtt` are listed anyway because they cost nothing and are what a
# newly downloaded file is most likely to bring. Ordered by how much the format
# can express, so a folder holding both keeps the richer one.
SUBTITLE_EXT = ('.ass', '.ssa', '.srt', '.vtt')


def subtitle_for(video: Path) -> Optional[Path]:
    """The same-named subtitle file beside `video`, or None."""
    for ext in SUBTITLE_EXT:
        p = video.with_suffix(ext)
        try:
            if p.is_file() and p.stat().st_size > 0:
                return p
        except OSError:
            continue
    return None


# ── Characters / folders ─────────────────────────────────────────────────────

def _readable_dir(path: str) -> bool:
    """Can we actually enumerate it? `exists()` says yes for dead SMB entries."""
    try:
        with os.scandir(path):
            return True
    except OSError:
        return False


def list_characters(roots: List[Path]) -> List[dict]:
    """Immediate subfolders of each media root = characters.

    Folders with the SAME name across different roots are reported as one
    "merged" entry holding every path (the desktop app shows these with a ⚠ and
    the drive letters, e.g. "Urabukkake (F:+G:+K:)").
    """
    by_name: dict = {}
    for root in roots:
        try:
            with os.scandir(str(root)) as it:
                for e in it:
                    if e.is_dir(follow_symlinks=False) and not e.name.startswith('.'):
                        by_name.setdefault(e.name, []).append(e.path)
        except OSError:
            continue

    # A folder that cannot be opened is not a second copy of anything. This
    # happens after a merge on a Synology share: the emptied source goes to
    # #recycle but a dead entry can linger in the parent listing, and counting
    # it kept the character showing as "merged" with nothing behind it.
    # Only merge candidates are probed — one scandir each, ~2 ms on a warm
    # connection, and there are rarely more than a handful.
    for name, paths in list(by_name.items()):
        if len(paths) < 2:
            continue
        alive = [p for p in paths if _readable_dir(p)]
        if alive and len(alive) < len(paths):
            by_name[name] = alive

    chars = []
    for name, paths in by_name.items():
        if len(paths) == 1:
            chars.append({"name": name, "path": paths[0],
                          "merged": False, "paths": paths})
        else:
            paths = sorted(paths)
            vols = [(Path(p).drive or str(Path(p).anchor)) for p in paths]
            one_volume = len(set(v.lower() for v in vols)) == 1
            # Two folders on the same NAS share used to render as
            # "\\host\share+\\host\share", which reads as a cross-drive split
            # when it is nothing of the sort. Name the roots instead.
            label = ("+".join(Path(p).parent.name for p in paths)
                     if one_volume else "+".join(vols))
            chars.append({"name": name, "path": paths[0], "merged": True,
                          "paths": paths, "drives": label,
                          "same_volume": one_volume})
    chars.sort(key=lambda c: c["name"].lower())
    return chars


def count_videos(folder: Path) -> int:
    """Number of video files under folder (recursive), IG subfolder excluded."""
    n = 0
    for p, _s, _m in scan_videos_with_stat(folder):
        if not is_under_ig(p, folder):
            n += 1
    return n


def character_stats(folder: Path) -> dict:
    """Count, total bytes and newest video time — all from one folder walk.

    "newest" is the newest video's mtime rather than the folder's own mtime:
    dropping a file into 角色/IG/ does not touch the parent folder's timestamp,
    so the folder mtime would be misleading.
    """
    count = 0
    total = 0
    newest = 0.0
    for p, size, mtime in scan_videos_with_stat(folder):
        if is_under_ig(p, folder):
            continue
        count += 1
        total += size
        if mtime > newest:
            newest = mtime
    return {"count": count, "bytes": total, "newest": newest}


import re as _re

# codec / quality tokens that look like JAV IDs but aren't
_NOT_JAV = {'MP4', 'MKV', 'AVI', 'MOV', 'WMV', 'FLV', 'TS', 'FHD', 'UHD', 'HD',
            'SD', 'AAC', 'AC3', 'DTS', 'H264', 'H265', 'HEVC', 'AVC', 'CBR',
            'VBR', 'FPS', 'BPS', 'GB', 'MB', 'KB', 'DL', 'OL', 'VR', 'BD',
            'DC', 'SP', 'CM', 'PART', 'EP', 'VOL', 'NO', 'CH', 'PT'}

# Social-media exports (Instagram, TikTok, Stripchat…) carry media ids and unix
# timestamps of ten digits or more. No catalogue number looks remotely like
# that, and those filenames are the main source of false positives here.
_SOCIAL_ID = _re.compile(r'\d{10,}')
# Download tools often name files after a UUID or a content hash; the middle
# groups of a UUID (…-eefe-4782-…) look exactly like a catalogue number.
_UUID = _re.compile(r'(?i)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-'
                    r'[0-9a-f]{4}-[0-9a-f]{12}')
_HASHY = _re.compile(r'(?i)(?<![0-9a-f])[0-9a-f]{24,}(?![0-9a-f])')

_FC2_PPV = _re.compile(r'(?i)fc2[-_\s]?ppv[-_\s]?(\d{5,8})')
_FC2     = _re.compile(r'(?i)\bfc2[-_\s]?(\d{5,8})\b')
_HEYZO   = _re.compile(r'(?i)heyzo[-_\s]?(\d{4})')
# Catalogue numbers are written with a hyphen (STARS-786, MIDA-493). Allowing an
# underscore as well used to match social handles such as HARU_LOVE_86.
# A trailing p/i is a resolution or field order (Erika_2160p), not an ID.
_STD     = _re.compile(r'(?<![A-Za-z0-9])([A-Za-z]{2,6})-(\d{2,5})(?![\dPpIi])')
# Amateur labels (1pondo, Caribbeancom…) use MMDDYY_NNN. Stream recordings are
# named ...-2026-03-19-113020-429, whose HHMMSS-ms tail has the very same shape,
# so the month/day have to be real and an ISO date must not precede it.
_AMATEUR = _re.compile(r'(?<!\d)(?<!\d-)(\d{2})(\d{2})(\d{2})[-_](\d{3})(?!\d)')
_ISO_DATE_BEFORE = _re.compile(r'\d{4}[-_]\d{2}[-_]\d{2}[-_]$')


def detect_video_id(filename: str) -> Optional[str]:
    """Detect a catalogue ID (FC2 / HEYZO / standard JAV) from a filename.

    Deliberately conservative: a wrong ID sends a video to the wrong performer,
    which is worse than not recognising one at all.
    """
    stem = Path(filename).stem

    # these prefixes are unambiguous, so they win even in a noisy filename
    m = _FC2_PPV.search(stem)
    if m: return f"FC2-PPV-{m.group(1)}"
    m = _FC2.search(stem)
    if m: return f"FC2-PPV-{m.group(1)}"
    m = _HEYZO.search(stem)
    if m: return f"HEYZO-{m.group(1)}"

    if _SOCIAL_ID.search(stem) or _UUID.search(stem) or _HASHY.search(stem):
        return None

    m = _STD.search(stem)
    if m:
        code, num = m.group(1).upper(), m.group(2)
        if code not in _NOT_JAV:
            return f"{code}-{int(num):03d}"
    for m in _AMATEUR.finditer(stem):
        mm, dd, yy, num = m.groups()
        if not (1 <= int(mm) <= 12 and 1 <= int(dd) <= 31):
            continue
        if _ISO_DATE_BEFORE.search(stem[:m.start()]):
            continue        # it is the time part of a stream recording's name
        return f"{mm}{dd}{yy}-{num}"
    return None


def move_with_progress(src: Path, dst: Path, progress_cb,
                       chunk: int = 4 << 20) -> None:
    """Move src → dst reporting byte-level progress.
    Same filesystem: instant rename. Cross-drive: chunked copy + delete source.
    """
    import time
    dst.parent.mkdir(parents=True, exist_ok=True)
    size = src.stat().st_size
    try:
        src.rename(dst)                       # same volume → instant
        progress_cb(size, size, 0.0)
        return
    except OSError:
        pass                                  # cross-device → copy in chunks
    copied = 0
    t0 = time.monotonic()
    # The `except` below cannot run when the service is killed mid-copy (a
    # restart, a crash), so the job also records what it is writing and the
    # next start removes it while the source is still there — see jobs.py.
    from .. import jobs
    jobs.writing(src, dst)
    try:
        with open(src, 'rb') as fsrc, open(dst, 'wb') as fdst:
            while True:
                buf = fsrc.read(chunk)
                if not buf:
                    break
                fdst.write(buf)
                copied += len(buf)
                elapsed = time.monotonic() - t0
                progress_cb(copied, size,
                            copied / elapsed if elapsed > 1e-3 else 0.0)
    except BaseException:
        # A half-written file at the destination is worse than no file: it
        # looks like a real video, and the source is still there, so it would
        # later read as a second copy. Take it away before reporting failure.
        try:
            dst.unlink(missing_ok=True)
        except OSError:
            pass
        jobs.wrote(dst)
        raise
    src.unlink()
    jobs.wrote(dst)


def same_volume(a: Path, b: Path) -> bool:
    """Would a move between these be a rename rather than a copy?

    Compares the drive — "C:" locally, "\\\\server\\share" for UNC — because
    two folders on one NAS share cost nothing to merge, while a genuine
    cross-volume move needs room for a full copy.
    """
    try:
        da, db = Path(a).drive.lower(), Path(b).drive.lower()
        if da and db:
            return da == db
        return Path(a).anchor.lower() == Path(b).anchor.lower()
    except Exception:
        return False


# ── notes ────────────────────────────────────────────────────────────────────
# Mirrors the desktop app: videos still in the inbox keep their note in the
# cache (they move around), archived videos get a sidecar .txt next to the file.

def note_cache_path(video: Path, cache: Path) -> Path:
    key = hashlib.md5(str(video).encode()).hexdigest()
    return cache / "notes" / f"{key}.txt"


def read_note(video: Path, cache: Path) -> str:
    side = video.with_suffix('.txt')
    for p in (side, note_cache_path(video, cache)):
        try:
            if p.exists():
                return p.read_text(encoding='utf-8')
        except OSError:
            continue
    return ""


def write_note(video: Path, cache: Path, text: str, sidecar: bool) -> Path:
    """sidecar=True → .txt next to the video; else the cache copy."""
    dst = video.with_suffix('.txt') if sidecar else note_cache_path(video, cache)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if text.strip():
        dst.write_text(text, encoding='utf-8')
    elif dst.exists():
        dst.unlink()          # empty note → remove the file
    return dst


def move_note(src: Path, dst: Path, cache: Path):
    """Carry a note over when a video is archived (cache → sidecar)."""
    text = read_note(src, cache)
    if text.strip():
        write_note(dst, cache, text, sidecar=True)
    old = note_cache_path(src, cache)
    try:
        if old.exists():
            old.unlink()
        side = src.with_suffix('.txt')
        if side.exists() and side != dst.with_suffix('.txt'):
            side.unlink()
    except OSError:
        pass


# Files that belong to one video by name: Kodi/Jellyfin's NFO and artwork,
# and subtitles. Moving a whole folder takes them along anyway (`move_tree`);
# archiving one video used to move the video and its note only, leaving these
# behind in the inbox describing a file that is no longer there.
_COMPANION_ART = _re.compile(
    r"^-(poster|fanart|thumb|landscape|banner|clearlogo|clearart|disc)"
    r"\.(jpe?g|png|webp)$", _re.I)


def companions(video: Path) -> List[Path]:
    """`<stem>.nfo`, `<stem>-poster.jpg` and the like, and same-named
    subtitles — never the note (`move_note` handles that) or another video."""
    stem, out = video.stem.lower(), []
    try:
        with os.scandir(video.parent) as it:
            for e in it:
                if e.name == video.name or not e.name.lower().startswith(stem):
                    continue
                rest = e.name[len(video.stem):]
                low = rest.lower()
                if (low == ".nfo" or low in SUBTITLE_EXT
                        or _COMPANION_ART.match(rest)) and e.is_file():
                    out.append(Path(e.path))
    except OSError:
        pass
    return out


def move_companions(src: Path, dst: Path) -> List[str]:
    """Move `src`'s companions beside `dst`, renamed to its stem (an archive
    can rename on a collision). One already there is left alone, not
    overwritten — it belongs to whatever video is already at `dst`'s name."""
    moved = []
    for c in companions(src):
        target = dst.with_name(dst.stem + c.name[len(src.stem):])
        if target.exists():
            log.info("companion not moved, %s already exists", target)
            continue
        try:
            move_with_progress(c, target, lambda *_a: None)
            moved.append(str(target))
        except OSError:
            log.exception("could not move companion %s", c)
    return moved


def purge_derived(video: Path, cache: Path):
    """Drop everything we generated *about* a file, after it has been deleted."""
    targets = [video.with_suffix('.txt'), note_cache_path(video, cache)]
    for p in targets:
        try:
            if p.exists():
                p.unlink()
        except OSError:
            continue
    # thumbnails and contact sheets live one directory per video, because
    # their names carry the file's size and mtime (and, for sheets, the
    # sampling interval and index) — none of it knowable once it is gone
    shutil.rmtree(thumb_dir(video, cache), ignore_errors=True)
    from . import contactsheet
    try:
        shutil.rmtree(contactsheet.sheet_dir(video, cache), ignore_errors=True)
    except Exception:
        # not silent: a swallowed NameError here already made this look like it
        # worked while leaving every sheet on disk
        log.exception("could not remove the contact sheets for %s", video)


def folder_stats(folder: Path) -> dict:
    """Total bytes and video count for a whole folder (all files counted)."""
    total = files = videos = 0
    for dirpath, _dirs, filenames in os.walk(str(folder), onerror=lambda _: None):
        for fn in filenames:
            p = Path(dirpath) / fn
            try:
                total += p.stat().st_size
            except OSError:
                continue
            files += 1
            if p.suffix.lower() in VIDEO_EXT:
                videos += 1
    return {"bytes": total, "files": files, "videos": videos}


def move_tree(src_dir: Path, dst_dir: Path, progress_cb=None) -> dict:
    """Move an entire folder into dst_dir, preserving the subfolder structure.

    Every file is moved (videos plus companions like .jpg/.txt/.nfo). Conflicts:
      identical size at the same relative path → source deleted (already there)
      same name, different size                → moved as name_1.ext
    Empty source directories are removed afterwards.
    """
    moved = deleted = failed = 0
    entries = []
    for dirpath, _dirs, filenames in os.walk(str(src_dir), onerror=lambda _: None):
        for fn in filenames:
            entries.append(Path(dirpath) / fn)
    total = len(entries)

    for i, sf in enumerate(entries, 1):
        try:
            rel = sf.relative_to(src_dir)
            df = dst_dir / rel
            if df.exists():
                try:
                    same = df.stat().st_size == sf.stat().st_size
                except OSError:
                    same = False
                if same:
                    sf.unlink()          # already archived → drop the source copy
                    deleted += 1
                    if progress_cb:
                        progress_cb({"phase": "skip", "name": sf.name,
                                     "index": i, "count": total,
                                     "moved": moved, "deleted": deleted})
                    continue
                stem, suffix = df.stem, df.suffix
                n = 1
                while (df.parent / f"{stem}_{n}{suffix}").exists():
                    n += 1
                df = df.parent / f"{stem}_{n}{suffix}"

            def cb(done, size, speed, nm=sf.name, idx=i):
                if progress_cb:
                    progress_cb({"phase": "move", "name": nm, "index": idx,
                                 "count": total, "done": done, "size": size,
                                 "speed": speed, "moved": moved, "deleted": deleted})
            move_with_progress(sf, df, cb)
            moved += 1
        except Exception:
            failed += 1

    # prune empty directories, deepest first
    for dirpath, _dirs, _files in sorted(
            os.walk(str(src_dir), onerror=lambda _: None), reverse=True):
        try:
            Path(dirpath).rmdir()
        except OSError:
            pass
    return {"moved": moved, "deleted": deleted, "failed": failed}


def find_duplicate(src: Path, dest_dir: Path) -> Optional[Path]:
    """First file in dest_dir with the same size as src (fast duplicate hint)."""
    try:
        src_size = src.stat().st_size
        for f in dest_dir.iterdir():
            if (f.is_file() and f.suffix.lower() in VIDEO_EXT
                    and f.stat().st_size == src_size):
                return f
    except OSError:
        pass
    return None


def scan_inbox_grouped(roots: List[Path]) -> List[dict]:
    """Inbox videos, tagged with the immediate subfolder they live in.

    `group` is None for files sitting loose in an inbox root, otherwise the name
    of the first-level subfolder — the UI shows loose files directly and puts the
    rest under their folder name.
    """
    items = []
    for root in roots:
        for path, size, mtime in scan_videos_with_stat(root):
            try:
                rel = path.relative_to(root)
                group = rel.parts[0] if len(rel.parts) > 1 else None
            except ValueError:
                group = None
            items.append({"path": str(path), "name": path.name,
                          "size": size, "mtime": mtime, "group": group})
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items


# ── IG detection ─────────────────────────────────────────────────────────────

def find_ig_dir(folder: Path) -> Optional[Path]:
    """Return the direct 'IG' subfolder (case-insensitive) of folder, or None."""
    try:
        with os.scandir(str(folder)) as it:
            for entry in it:
                if entry.is_dir(follow_symlinks=False) and entry.name.lower() == 'ig':
                    return Path(entry.path)
    except OSError:
        pass
    return None


def is_under_ig(path: Path, root: Path) -> bool:
    """True if `path` lives inside the direct 'IG' subfolder of `root`."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        return False
    return len(rel.parts) >= 2 and rel.parts[0].lower() == 'ig'


# ── VAM's own files inside a character folder ────────────────────────────────
# A picture VAM fetched for the avatar (core/gfriends.py) has to live in the
# folder so it moves and merges with it — `profile_photo` is a path relative to
# the folder — but it is not a photo the user collected. Every walk that lists
# photos (the grid, duplicate scan, person scan) skips this directory.
VAM_DIR = ".vam"


def in_vam_dir(path, root) -> bool:
    """True if `path` is inside `root`/.vam (any depth)."""
    try:
        rel = Path(path).relative_to(root)
    except ValueError:
        return False
    return any(part.lower() == VAM_DIR for part in rel.parts[:-1])


# ── Photos / profile media ───────────────────────────────────────────────────

def scan_photos(folder: Path) -> List[Path]:
    """Recursively scan folder for images, newest first."""
    entries: List[Tuple[Path, float]] = []

    def _walk(dir_path: str):
        try:
            with os.scandir(dir_path) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name.lower() != VAM_DIR:
                                _walk(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            if Path(entry.name).suffix.lower() in IMAGE_EXT:
                                entries.append((Path(entry.path), entry.stat().st_mtime))
                    except OSError:
                        continue
        except OSError:
            return

    _walk(str(folder))
    entries.sort(key=lambda e: e[1], reverse=True)
    return [p for p, _ in entries]


def scan_profile_media(folder: Path) -> List[Tuple[Path, bool]]:
    """All images anywhere + videos inside the direct IG subfolder.
    Returns (path, is_video), newest first."""
    return [(p, v) for p, v, _m, _s in scan_profile_media_stat(folder)]


def scan_profile_media_stat(folder: Path) -> List[Tuple[Path, bool, float, int]]:
    """`scan_profile_media` plus mtime and size, newest first.

    The profile grid sorts by 最近新增 / 名稱 / 大小 the way the video grid does,
    and the stat that produced the mtime already carries the size — returning
    both costs nothing, while asking for them later would be a second stat per
    photo over SMB.
    """
    return [(r["path"], r["is_video"], r["mtime"], r["size"])
            for r in scan_profile_albums(folder) if not r["in_set"]]


def scan_profile_albums(folder: Path) -> List[dict]:
    """Everything the profile tab can show, each tagged with its album.

    `album` is the first-level subfolder the file lives under ('' for the
    folder itself). Photo-set characters keep one set per subfolder — 28 of
    them for `Yeha (예하)`, 141 of 761 characters have two or more — and the
    album view groups by exactly that.

    Videos inside a non-IG subfolder come back with `in_set=True`. They belong
    to the videos tab (and stay there), but a set's clips are part of the set,
    so the album view shows them too. The flat grid filters them out, which
    keeps it exactly what it was. One walk for both: the directories are the
    same ones the photo walk already reads, so the videos cost no extra I/O.
    Loose videos at the top level are never profile media.
    """
    out: List[dict] = []

    def _walk(dir_path: str, album: str, is_ig: bool):
        try:
            with os.scandir(dir_path) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name.lower() == VAM_DIR:
                                continue
                            if album:
                                _walk(entry.path, album, is_ig)
                            else:
                                _walk(entry.path, entry.name,
                                      entry.name.lower() == 'ig')
                        elif entry.is_file(follow_symlinks=False):
                            suf = Path(entry.name).suffix.lower()
                            if suf in IMAGE_EXT:
                                vid = False
                            elif suf in VIDEO_EXT and album:
                                vid = True
                            else:
                                continue
                            st = entry.stat()
                            out.append({"path": Path(entry.path), "is_video": vid,
                                        "mtime": st.st_mtime, "size": st.st_size,
                                        "album": album,
                                        "in_set": vid and not is_ig})
                    except OSError:
                        continue
        except OSError:
            return

    _walk(str(folder), '', False)
    out.sort(key=lambda r: r["mtime"], reverse=True)
    return out


# ── person_info.json ─────────────────────────────────────────────────────────

def split_tags(tags) -> tuple:
    """Separate the link list from the actual tags.

    A survey of the whole library found the `tags` field was not being used as
    tags at all: 627 characters, 27 of them tagged, **44 distinct tags used 44
    times** -- every single one exactly once -- and 39 of the 44 were URLs.
    Zero reuse means zero grouping value, so what was there was a list of
    social-media profiles wearing the wrong name.

    They are split here rather than migrated, which is the user's call and a
    good one: `person_info.json` lives on the NAS where git cannot protect it,
    and deriving the two lists on read costs nothing and cannot corrupt
    anything. `join_tags` puts them back, so the stored shape never changes.

    The rule is the same one the frontend already used to decide whether to
    render a chip as a link, so nothing reclassifies when this is switched on.
    """
    plain, links = [], []
    for t in tags or []:
        # `str(None)` is "None", which would invent a tag by that name out of a
        # `null` in a hand-edited file. Same for the join below.
        if t is None:
            continue
        s = str(t).strip()
        if not s:
            continue
        (links if s[:8].lower().startswith(("http://", "https://")) else
         plain).append(s)
    return plain, links


def join_tags(tags, links) -> list:
    """The inverse of `split_tags`, for writing back.

    Tags first, then links. Saving therefore groups a mixed old list, which is
    the only change this makes to a stored file -- and only when the user saves
    that character anyway.
    """
    out = [str(t).strip() for t in (tags or [])
           if t is not None and str(t).strip()]
    out += [str(l).strip() for l in (links or [])
            if l is not None and str(l).strip()]
    return out


# Old-form kanji and their modern equivalents. Only pairs from the 常用漢字表
# simplification list -- these are the *same* character written two ways, not
# two characters that look alike.
#
# This is needed because of who writes what: the user types Traditional forms,
# and a Japanese wiki writes shinjitai, so importing 浅井花梨 next to an
# existing 淺井花梨 produced two entries for one name. A library scan found
# exactly one such pair today, but there are 367 JAV characters and the import
# has run on two of them.
#
# Deliberately does NOT fold characters that merely resemble each other:
# 浅井花莉 and 浅井花梨 differ by 莉/梨, and the wiki lists both as separate
# stage names. Folding those would erase a real distinction.
_KYUJITAI = str.maketrans({
    '淺': '浅', '澤': '沢', '齋': '斎', '齊': '斉', '邊': '辺', '邉': '辺',
    '濱': '浜', '廣': '広', '榮': '栄', '眞': '真', '惠': '恵', '兒': '児',
    '龍': '竜', '瀧': '滝', '內': '内', '藝': '芸', '國': '国', '會': '会',
    '學': '学', '實': '実', '寶': '宝', '曉': '暁', '萬': '万', '與': '与',
    '應': '応', '櫻': '桜', '條': '条', '來': '来', '戀': '恋', '發': '発',
    '澁': '渋', '嵜': '崎', '﨑': '崎', '髙': '高', '德': '徳', '假': '仮',
    '兩': '両', '醫': '医', '聲': '声', '點': '点', '燈': '灯', '轉': '転',
    '樂': '楽', '爲': '為', '稻': '稲', '經': '経', '緖': '緒', '靜': '静',
    '驛': '駅', '黑': '黒', '鐵': '鉄', '增': '増', '藏': '蔵', '拂': '払',
    '賣': '売', '圓': '円', '氣': '気', '每': '毎', '步': '歩', '禮': '礼',
})


def fold_name(s) -> str:
    """A comparison key for a personal name. Never stored, only compared.

    NFKC first (full-width Latin and half-width kana are the same letters),
    then all whitespace removed, then old kanji forms mapped to modern ones.
    Case-folded last so `Karin Morita` and `karin morita` are one name.

    What is *kept* is whatever was typed first: this decides whether to add a
    name, not how to spell one.
    """
    t = unicodedata.normalize("NFKC", str(s or "")).strip()
    t = "".join(t.split())
    return t.translate(_KYUJITAI).casefold()


def load_person_info(folder: Path) -> dict:
    p = folder / 'person_info.json'
    if p.exists():
        try:
            with open(p, 'r', encoding='utf-8') as f:
                data = json.load(f)
            data.setdefault('aliases', [])
            data.setdefault('tags', [])
            data.setdefault('notes', '')
            data.setdefault('profile_photo', '')
            # framing chosen in the avatar cropper (Instagram-style)
            data.setdefault('profile_photo_pos', {'x': 50, 'y': 50, 'zoom': 1})
            return data
        except Exception:
            pass
    return {'aliases': [], 'tags': [], 'notes': '', 'profile_photo': '',
            'profile_photo_pos': {'x': 50, 'y': 50, 'zoom': 1}}


def save_person_info(folder: Path, info: dict):
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / 'person_info.json', 'w', encoding='utf-8') as f:
        json.dump(info, f, ensure_ascii=False, indent=2)


# ffmpeg's own log-deduplication marker. It is never the error, and because
# it is printed *after* the messages it stands in for, it is exactly what
# `splitlines()[-1]` picks up.
_REPEATED = _re.compile(r"^\s*Last message repeated \d+ times\s*$", _re.I)
# Addresses and sizes differ line to line while the fault is the same one.
_VARYING = _re.compile(r"0x[0-9a-fA-F]+|[0-9a-fA-F]{8,}|-?\d+")


def ffmpeg_error(err: str, returncode: int) -> str:
    """The useful part of ffmpeg's stderr, not merely its last line.

    Taking the last line produced the single least informative thing ffmpeg
    can print. A real report read `gFYwOpwUEVnVcsleBZsN.mp4 — Last message
    repeated 11 times`, which names no fault at all; the file behind it turned
    out to have **51,058** `Invalid NAL unit size` lines, and the user saw none
    of them.

    So the lines are grouped with the varying numbers masked out, and the two
    most frequent faults are reported with their counts. The count is itself
    the diagnosis here: one bad frame is a glitch, 51,058 is a broken file.
    """
    lines = [l.strip() for l in err.splitlines()
             if l.strip() and not _REPEATED.match(l)]
    if not lines:
        return "ffmpeg 失敗（代碼 %d）" % returncode
    groups: dict = {}
    for l in lines:
        g = groups.setdefault(_VARYING.sub("N", l), [0, l])
        g[0] += 1
    out = []
    for n, sample in sorted(groups.values(), key=lambda g: -g[0])[:2]:
        sample = sample if len(sample) <= 160 else sample[:157] + "…"
        out.append(sample if n == 1 else "%s（共 %d 次）" % (sample, n))
    return "；".join(out)
