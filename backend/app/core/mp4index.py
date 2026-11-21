"""Video packet times of an MP4, read from its index instead of its data.

The recording-dropout check (`convert._blank_check`) needs every video
packet's time. `ffprobe -show_entries packet=…` gives that, but gets there by
reading every packet: the whole file. On a local disk that is about a second;
over the NAS it was minutes per file — 27 webcam recordings (23.8 GB) on
2026-11-16 went at about 6 MB/s with eight in flight, and the scan sat on that
one step for most of an hour.

An MP4 keeps the decode durations of every sample in `moov` (`stts`, with the
timescale in `mdhd`). That box was 0.5–12 MB on those files, against
360–1,700 MB of data.

**Used as a screen, not as the answer.** Compared with ffprobe on 64 real
files, the video packet count and the gaps between packets matched every
time; what differed was a constant shift of the whole track (edit lists,
B-frame delay — up to 8.3 s), the audio packet count where an edit list trims
audio, and keyframe flags on one file. None of that changes "is there a gap
of N seconds between two video packets", which is all the screen asks. A file
the screen passes has no dropout; one it does not pass, or one it cannot
read, is checked with ffprobe exactly as before, so every plan that leads to
a cut still comes from ffprobe.

Returns None (ask ffprobe) for anything not understood: no `moov`, a
fragmented file (`moof`: its samples are not in `moov`), an edit list with
more than one real edit or an empty edit after the start (those can open a
gap the sample table does not show), a box that runs past its parent, and an
MPEG-TS named `.mp4` — two of those 27 were.
"""
from __future__ import annotations

import struct
from pathlib import Path
from typing import List, Optional

# A moov larger than this is not an index worth holding in memory; ffprobe
# instead. The largest seen is 12 MB (a 1.7 GB, two-hour recording).
MAX_MOOV = 256 * 1024 * 1024


def _boxes(buf: bytes, start: int = 0, end: Optional[int] = None):
    """(type, payload start, payload end) for each box in buf[start:end]."""
    end = len(buf) if end is None else end
    pos = start
    while pos + 8 <= end:
        size, typ = struct.unpack_from(">I4s", buf, pos)
        head = 8
        if size == 1:
            if pos + 16 > end:
                raise ValueError("truncated box")
            size = struct.unpack_from(">Q", buf, pos + 8)[0]
            head = 16
        elif size == 0:
            size = end - pos
        if size < head or pos + size > end:
            raise ValueError("box runs past its parent")
        yield typ, pos + head, pos + size
        pos += size


def _child(buf: bytes, start: int, end: int, typ: bytes):
    for t, s, e in _boxes(buf, start, end):
        if t == typ:
            return s, e
    return None


def _find_moov(path: Path) -> Optional[bytes]:
    """The moov payload, reading only box headers on the way to it."""
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        pos = 0
        moov = None
        while pos + 8 <= size:
            f.seek(pos)
            h = f.read(16)
            if len(h) < 8:
                return None
            n, typ = struct.unpack_from(">I4s", h, 0)
            head = 8
            if n == 1:
                if len(h) < 16:
                    return None
                n = struct.unpack_from(">Q", h, 8)[0]
                head = 16
            elif n == 0:
                n = size - pos
            if n < head or pos + n > size:
                return None                 # not a box: not an MP4 read this way
            if typ == b"moof":
                return None                 # fragmented
            if typ == b"moov":
                if n - head > MAX_MOOV:
                    return None
                f.seek(pos + head)
                moov = f.read(n - head)
                if len(moov) != n - head:
                    return None
                # keep walking: a moof after the moov still means fragmented
            pos += n
        return moov


def _simple_edits(buf: bytes, s: int, e: int) -> bool:
    """No edit list, or one real edit with at most an empty edit before it.
    Anything else can cut or open time the sample table does not show."""
    edts = _child(buf, s, e, b"edts")
    elst = _child(buf, *edts, b"elst") if edts else None
    if not elst:
        return True
    v = buf[elst[0]]
    n = struct.unpack_from(">I", buf, elst[0] + 4)[0]
    step = 20 if v == 1 else 12
    real = 0
    for i in range(n):
        o = elst[0] + 8 + i * step
        if o + step > elst[1]:
            return False
        media = (struct.unpack_from(">q", buf, o + 8)[0] if v == 1
                 else struct.unpack_from(">i", buf, o + 4)[0])
        if media == -1:
            if real:
                return False                # an empty edit after real time
            continue
        real += 1
    return real <= 1


def _video_track(buf: bytes, s: int, e: int) -> Optional[List[float]]:
    mdia = _child(buf, s, e, b"mdia")
    if not mdia:
        return None
    hdlr = _child(buf, *mdia, b"hdlr")
    if not hdlr or buf[hdlr[0] + 8:hdlr[0] + 12] != b"vide":
        return None
    mdhd = _child(buf, *mdia, b"mdhd")
    minf = _child(buf, *mdia, b"minf")
    if not (mdhd and minf):
        raise ValueError("video track without mdhd/minf")
    version = buf[mdhd[0]]
    timescale = struct.unpack_from(">I", buf, mdhd[0] + (20 if version == 1 else 12))[0]
    stbl = _child(buf, *minf, b"stbl")
    stts = _child(buf, *stbl, b"stts") if stbl else None
    if not timescale or not stts:
        raise ValueError("video track without a timescale or stts")
    if not _simple_edits(buf, s, e):
        raise ValueError("edit list")
    count = struct.unpack_from(">I", buf, stts[0] + 4)[0]
    if stts[0] + 8 + count * 8 > stts[1]:
        raise ValueError("stts runs past its box")
    out, t = [], 0
    for i in range(count):
        n, delta = struct.unpack_from(">II", buf, stts[0] + 8 + i * 8)
        for _ in range(n):
            out.append(t / timescale)
            t += delta
    return out


def video_times(path: Path) -> Optional[List[float]]:
    """Decode times (seconds, in order, up to a constant shift) of the first
    video track's samples, or None when ffprobe should be asked instead."""
    try:
        moov = _find_moov(path)
        if not moov:
            return None
        for t, s, e in _boxes(moov):
            if t == b"trak":
                v = _video_track(moov, s, e)
                if v is not None:
                    return v or None        # empty: fragmented after all
        return None
    except (OSError, ValueError, struct.error, IndexError):
        return None


def longest_gap(times: List[float]) -> float:
    """The longest step between consecutive samples (0 for fewer than two)."""
    ts = sorted(times)
    return max((b - a for a, b in zip(ts, ts[1:])), default=0.0)
