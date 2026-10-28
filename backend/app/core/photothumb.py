"""Downscaled copies of photos, for the grids that only ever show them small.

The profile photo grid used to load originals — the same file the viewer opens
full-screen. That is fine for a folder of social-media images and miserable for
a folder of phone-camera originals, which is exactly what the user reported:
one character's photos scrolled smoothly, another stuttered.

Measured on the two folders in question:

    Shadowifey   466 photos    172 MB   1440x1440  (1.9 MP)  median 275 KB
    Phbambi     2258 photos   3039 MB   3024x4032 (11.1 MP)  median 1472 KB

Per screenful of 30 cells that is 5.3 MB against **43.9 MB**, and 56 against
**333 megapixels** to decode. Reading from the NAS was not the problem (0.42s
vs 0.77s) — the transfer and the decode were. The cell is 280x350 CSS pixels,
so a 3024px-wide source has ~29x more pixels than the screen can use.

SHORT edge, not long: the grid uses `object-fit: cover`, so a portrait photo
sized by its long edge comes out too narrow for the cell and gets upscaled.

Cost, measured over 40 of the 12 MP originals: 34 ms and 57 KB each at 560.
`draft()` is what makes that affordable — it lets the JPEG decoder emit at
1/2, 1/4 or 1/8 scale instead of decoding 12 MP and throwing most of it away
(76 ms -> 27 ms, a 2.8x saving on the same images).
"""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Optional

log = logging.getLogger("vam")

DIR = "photos"
# 2x the 280px grid cell, so nothing is visibly softer than it is today. The
# whole library (54,047 photos) comes to ~2.9 GB at this size.
SHORT_EDGE = 560
QUALITY = 82

# Animation would be lost, and there are 36 of them library-wide — not worth
# a second code path, so they keep being served as originals.
SKIP_EXT = {".gif"}


def wanted(photo: Path) -> bool:
    return photo.suffix.lower() not in SKIP_EXT


def cache_path(photo: Path, cache: Path, st: Optional[os.stat_result] = None,
               edge: int = SHORT_EDGE, front: bool = False) -> Optional[Path]:
    """`cache/photos/ab/abcdef….jpg`, keyed on the file's identity.

    Size and mtime are in the key, unlike the video thumbnails, because photos
    do get re-saved in place (the image de-duplication work leans on the same
    fact for its hash cache). A replaced photo therefore lands on a new key and
    cannot show the previous picture. The entry it leaves behind is an orphan
    that nothing can identify later — which is precisely what the cache limit
    exists to handle, so this trades a little space for never being wrong.
    """
    try:
        st = st or photo.stat()
    except OSError:
        return None
    key = hashlib.md5(
        f"{photo}|{st.st_size}|{int(st.st_mtime)}|{edge}"
        # only the cropped variant extends the key: every existing entry
        # keeps its name and stays a hit
        f"{'|front' if front else ''}".encode()).hexdigest()
    return cache / DIR / key[:2] / f"{key}.jpg"


def _usable(p: Path) -> bool:
    """Present *and* non-empty — see contactsheet.py for why size matters."""
    try:
        return p.stat().st_size > 0
    except OSError:
        return False


_SOS = b"\xff\xda"
_EOI = b"\xff\xd9"


def _salvage_candidates(data: bytes):
    """Shorter readings of a damaged JPEG, best first.

    Some photos in the library are genuinely damaged and browsers draw what is
    there, so failing here made the grid serve the *original* for exactly the
    files least worth downloading in full. Two shapes seen:

    * truncated ("image file is truncated (34 bytes not processed)"): the
      whole thing plus an end marker decodes, the missing part grey.
    * a progressive JPEG whose later scans run into foreign bytes (an
      undefined `FF 99` marker, no end marker — 4 of 1040 photos in
      `2km2km a.k.a DJ MIU`). libjpeg gives up on the whole picture, so the
      earlier scans, which are a complete if softer image, are cut out and
      decoded on their own; that is what the browser shows too.

    `ImageFile.LOAD_TRUNCATED_IMAGES` used to do this job and must not come
    back: it is a process-wide switch, and the second shape decodes as solid
    black under it. Once a thumbnail had turned it on, the image
    de-duplication hashed every such photo as all zeros — a duplicate of each
    other — and the similarity sort gave them one colour.
    """
    if not data.startswith(b"\xff\xd8"):
        return
    yield data + _EOI
    # Every SOS after the headers starts a scan; cut before each, last first.
    # An SOS inside an EXIF thumbnail is also found, but only after the real
    # ones failed, and a cut there does not decode strictly anyway.
    cuts, i = [], data.find(_SOS, 2)
    while i != -1:
        cuts.append(i)
        i = data.find(_SOS, i + 2)
    for cut in reversed(cuts[1:]):       # before the first one there is no image
        yield data[:cut] + _EOI


def _decode(photo: Path, edge: int):
    """The photo, decoded strictly, or the best salvage of it. Raises if none."""
    import io
    from PIL import Image
    data = photo.read_bytes()
    first_error = None
    for attempt, raw in enumerate([data, *_salvage_candidates(data)]):
        try:
            im = Image.open(io.BytesIO(raw))
            # ask the decoder for a small image up front; on JPEG this skips
            # most of the work rather than downscaling afterwards
            try:
                im.draft("RGB", (edge, edge))
            except Exception:
                pass                      # not a JPEG; harmless
            im.load()
        except Exception as e:
            first_error = first_error or e
            continue
        if attempt:
            log.info("photo thumbnail salvaged from a damaged file: %s (%s)",
                     photo, first_error)
        return im
    raise first_error


# The front of a JAV sleeve, as a share of its height: DMM's separate front
# image is 1032x1468 against a 2184x1468 sleeve (measured 2026-10-27 on 15
# films, all 0.703). The sleeve is back | spine | front, front on the right.
FRONT_ASPECT = 0.703
# Wider than this is a sleeve; a portrait or square picture is already a front.
SLEEVE_MIN_ASPECT = 1.2


def front_box(w: int, h: int) -> Optional[tuple]:
    """The front cover's box in a sleeve of w x h, or None if it is not one."""
    if w < h * SLEEVE_MIN_ASPECT:
        return None
    return (max(0, w - round(h * FRONT_ASPECT)), 0, w, h)


def render(photo: Path, dst: Path, edge: int = SHORT_EDGE,
           front: bool = False) -> bool:
    """`front`: for a portrait card showing a sleeve — the user's rule is
    "choose the picture by the card's shape": a 16:9 card shows the whole
    sleeve, a 4:5 or 9:16 one only its front."""
    from PIL import Image
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        with _decode(photo, edge) as im:
            if im.mode in ("RGBA", "LA", "P"):
                # JPEG has no alpha. Flatten onto the card colour rather than
                # the default black, so a transparent PNG does not gain a
                # hard black frame inside a grid cell.
                im = im.convert("RGBA")
                bg = Image.new("RGB", im.size, (30, 30, 46))
                bg.paste(im, mask=im.split()[-1])
                im = bg
            else:
                im = im.convert("RGB")
            box = front_box(*im.size) if front else None
            if box:
                im = im.crop(box)
            w, h = im.size
            scale = edge / min(w, h)
            if scale < 1:
                im = im.resize((max(1, round(w * scale)),
                                max(1, round(h * scale))), Image.LANCZOS)
            # Write to a temporary name and rename into place. Saving straight
            # to `dst` publishes the filename before the bytes: measured on the
            # contact sheets, 959 of 961 samples during a write saw 0 bytes,
            # and the grid would serve that empty file as a broken image.
            part = dst.with_name(f"{dst.name}.{os.getpid()}.part")
            im.save(part, "JPEG", quality=QUALITY, optimize=True)
            os.replace(part, dst)
        return True
    except Exception as e:
        # One line, not a traceback: an unreadable source file is an expected
        # condition here (the caller falls back to serving the original), and
        # a stack trace per broken photo buries the entries that do matter.
        log.warning("photo thumbnail skipped for %s: %s", photo, e)
        try:
            part = dst.with_name(f"{dst.name}.{os.getpid()}.part")
            if part.exists():
                part.unlink()
        except OSError:
            pass
        return False


def get(photo: Path, cache: Path, edge: int = SHORT_EDGE,
        front: bool = False) -> Optional[Path]:
    """Cached thumbnail, made on first request. None if it cannot be made."""
    dst = cache_path(photo, cache, edge=edge, front=front)
    if dst is None:
        return None
    if _usable(dst):
        return dst
    return dst if render(photo, dst, edge, front) else None


def purge(photo: Path, cache: Path, edge: int = SHORT_EDGE):
    """Drop the entry for a photo. Must run *before* the file is deleted.

    The key includes size and mtime, so once the original is gone the entry
    can no longer be located — the same one-way-hash limitation the cache
    limit exists to cover. Calling this first turns the common case (deleting
    through the app) into an exact cleanup; anything missed is just space,
    and the sweep reclaims it.
    """
    for front in (False, True):
        dst = cache_path(photo, cache, edge=edge, front=front)
        if dst is None:
            return
        try:
            dst.unlink()
        except OSError:
            pass
