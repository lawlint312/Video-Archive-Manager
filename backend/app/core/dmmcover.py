"""DMM's covers for a film, saved beside it as `<番號>-fanart.jpg` and
`<番號>-poster.jpg`.

Part of 角色查詢 → 片名 (a checkbox): a catalogue number identifies exactly
one film, so — like the titles — there is no identity to rule on and no
preview. What the user chose (2026-10-27):

  * the card's picture follows the card's shape — and the Kodi / Jellyfin
    convention (which the user spotted) already says which file is which:
    `-poster` is the portrait front, `-fanart` the landscape sleeve. So both
    are saved and the card picks (media.poster_candidates)
  * named by the catalogue number, so every part of a film shares them, and
    Kodi / Jellyfin find them too
  * never overwrites a cover that is already there

Where the two pictures come from: the sleeve is DMM's `largeUrl`. The front
is DMM's own `mediumUrl` when that is a real picture (1032x1468 for recent
films), otherwise it is cut from the sleeve (old films: a 147x200 front,
against a front of about 376x535 inside the sleeve).

The JAV folders already hold 1,009 portrait `-poster.jpg` (from Jellyfin,
counted 2026-10-27): those films get only the `-fanart.jpg` they lack.

Measured on 40 random library films: 28 are on DMM and all 28 have covers;
the other 12 are mostly Prestige / MGS (ABW, ONEZ) and uncensored (SKYHD).

For those, `from_javdatabase` (2026-10-28). MGS itself was the plan, but
mgstage.com and prestige-av.com answer 403 to Taiwan from their CloudFront —
the whole site, front page included; reaching them would mean a Japanese
proxy, i.e. getting around their region block, which this does not do. Their
*image* servers do answer. The page that names the pictures is javdatabase's
movie page, which the title lookup already reads:

  * a DMM content id (`118onez00103`, `1oks00120`, `h_1495bank00018`) whose
    sleeve is still on DMM's image server though the GraphQL catalogue no
    longer lists the film — and the id's label prefix cannot be guessed
  * an MGS image folder (`prestige/abw/085`), from which MGS's sleeve
    (`pb_e_`, 840x566) and front (`pf_e_`, 422x600) follow

Measured over 25 numbers DMM's catalogue lacks: 15 got a cover this way (OKS,
FSDSS, GVH at 2184 wide; ABW, WPS from MGS). The other 10 have no
javdatabase page at all — MGS amateur (ARA, NTK, SHIBP) and uncensored.
"""
from __future__ import annotations

import io
import logging
import os
import re
from pathlib import Path
from typing import Dict, Iterable, List
from urllib.request import Request, urlopen

from .. import jobs
from . import javdb, media, photothumb

log = logging.getLogger("vam")

TIMEOUT = 30
MAX_BYTES = 16 * 1024 * 1024
# DMM's placeholders and old fronts are tiny; a usable picture is wider
MIN_WIDTH = 300
FRONT_QUALITY = 92


def target(folder: Path, video_id: str, suffix: str) -> Path:
    return folder / (video_id.upper() + suffix)


def _exists(p: Path) -> bool:
    try:
        return p.is_file() and p.stat().st_size > 0
    except OSError:
        return False


def _size(data: bytes) -> tuple:
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        return im.size


def plan(videos: Iterable[Path], video_id: str) -> Dict[Path, dict]:
    """Per folder holding the film: which kinds are missing. Empty when every
    folder already has both — then DMM is not asked."""
    by_folder: Dict[Path, List[Path]] = {}
    for v in videos:
        by_folder.setdefault(Path(v).parent, []).append(Path(v))
    out = {}
    for folder, vids in by_folder.items():
        fanart = any(_exists(c) for v in vids
                     for c in media._named(v, media.FANART_SUFFIX))
        poster = any(_exists(c) for v in vids
                     for c in media.poster_candidates(v)
                     if not c.name.lower().endswith(media.FANART_SUFFIX))
        if not fanart or not poster:
            out[folder] = {"fanart": not fanart, "poster": not poster}
    return out


def fetch(url: str) -> bytes:
    """A picture's bytes, checked to be whole and real."""
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=TIMEOUT) as r:
        final = r.geturl()
        body = r.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise ValueError("larger than 16 MB")
    if "now_printing" in final or "noimage" in final:
        raise ValueError("DMM has no picture yet (placeholder)")
    from PIL import Image
    with Image.open(io.BytesIO(body)) as im:
        im.load()                           # a cut-off download fails here
        if im.size[0] < MIN_WIDTH:
            raise ValueError(f"too small: {im.size[0]}x{im.size[1]}")
    return body


def front_from(sleeve: bytes) -> bytes:
    """The front cut out of a sleeve (photothumb.front_box), as JPEG."""
    from PIL import Image
    with Image.open(io.BytesIO(sleeve)) as im:
        im = im.convert("RGB")
        box = photothumb.front_box(*im.size)
        if box:
            im = im.crop(box)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=FRONT_QUALITY)
    return buf.getvalue()


_DMM_CID = re.compile(r"pics\.dmm\.co\.jp/digital/video/([a-z0-9_]+)/\1pl\.jpg")
_MGS_DIR = re.compile(r"image\.mgstage\.com/images/([a-z0-9_]+)/([a-z0-9_]+)/([a-z0-9_]+)/")


def javdatabase_urls(html: str) -> dict:
    """The cover URLs a javdatabase movie page leads to (see the module
    docstring): {"sleeves": [...], "fronts": [...]}, best first."""
    sleeves, fronts = [], []
    m = _MGS_DIR.search(html or "")
    if m:
        base = "https://image.mgstage.com/images/{}/{}/{}/".format(*m.groups())
        tail = f"{m.group(2)}-{m.group(3)}"
        sleeves.append(base + f"pb_e_{tail}.jpg")
        fronts.append(base + f"pf_e_{tail}.jpg")
    m = _DMM_CID.search(html or "")
    if m:
        cid = m.group(1)
        # the high-resolution path first: 2184 wide where DMM has it, and the
        # same 800 as the old path where it does not
        sleeves.insert(0, f"https://awsimgsrc.dmm.co.jp/pics_dig/digital/video/{cid}/{cid}pl.jpg")
        sleeves.append(f"https://pics.dmm.co.jp/digital/video/{cid}/{cid}pl.jpg")
    return {"sleeves": sleeves, "fronts": fronts}


def from_javdatabase(video_id: str) -> tuple:
    """(sleeve bytes or b"", front bytes or b"") for a film DMM's catalogue
    does not list. Every candidate is fetched and the widest kept: they
    differ by source (2184 from DMM's high-resolution path, 840 from MGS, 800
    from DMM's old one). A "sleeve" that turns out portrait — COM-001's is —
    is a front, not a sleeve."""
    html = javdb._fetch(f"{javdb.BASE}/movies/{video_id.lower()}/") or ""
    urls = javdatabase_urls(html)
    sleeve, front_bytes = b"", b""
    for url in urls["sleeves"] + urls["fronts"]:
        try:
            data = fetch(url)
        except Exception as e:
            log.info("cover candidate %s skipped: %s", url, e)
            continue
        w, h = _size(data)
        if w >= h * photothumb.SLEEVE_MIN_ASPECT:
            if not sleeve or w > _size(sleeve)[0]:
                sleeve = data
        elif h > w and (not front_bytes or w > _size(front_bytes)[0]):
            front_bytes = data
    if sleeve:
        cut = front_from(sleeve)
        if not front_bytes or _size(cut)[0] > _size(front_bytes)[0]:
            front_bytes = cut
    return sleeve, front_bytes


def front(sleeve: bytes, front_url: str) -> bytes:
    """DMM's own front when it is a real picture, else cut from the sleeve."""
    if front_url:
        try:
            data = fetch(front_url)
            w, h = _size(data)
            if h > w:                       # a portrait front, as it should be
                return data
        except Exception as e:
            log.info("DMM front %s not usable, cutting the sleeve: %s", front_url, e)
    return front_from(sleeve)


def _write(dst: Path, data: bytes) -> bool:
    if dst.exists():
        return False
    part = dst.with_name(dst.name + ".part")
    jobs.writing("dmm", part)               # a restart mid-write cleans it up
    try:
        part.write_bytes(data)
        os.replace(part, dst)
    finally:
        jobs.wrote(part)
    return True


def save(todo: Dict[Path, dict], video_id: str, sleeve: bytes,
         front_bytes: bytes) -> dict:
    """Write what `plan` found missing; returns the paths written."""
    wrote = []
    for folder, need in todo.items():
        for kind, data, suffix in (("fanart", sleeve, media.FANART_SUFFIX),
                                   ("poster", front_bytes, media.POSTER_SUFFIX)):
            if need[kind] and data:         # a film may have only one kind
                dst = target(folder, video_id, suffix)
                if _write(dst, data):
                    wrote.append(dst)
    return wrote
