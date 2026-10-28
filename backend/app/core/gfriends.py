"""Gfriends: a public library of JAV performers' headshots, for the avatar.

89% of characters have no avatar, and in the JAV category that is mostly
because the folder holds films and nothing else — there is no photo in it to
pick. Gfriends (github.com/gfriends/gfriends) is a collection of headshots
keyed by stage name, and it knows most of this library (measured 2026-10-27:
274 of 311 JAV characters by folder name + aliases; of the 37 misses about ten
are studio or genre folders, not people).

The idea comes from Peach (github.com/peach-mitao/peach, AGPL); the code here
is our own. What we took from reading it:

* `Filetree.json` (6.5 MB) maps display name → files. Fetch it at most daily:
  the library grows, and a snapshot kept forever answers "not there" for
  everyone added since.
* Look up every name the person goes by, and merge — not stop at the first
  hit. The same performer is often filed under several names.
* The folder's leading character is the *source* priority, not image quality:
  `0-` hand-uploaded, `1-`…`8-` agencies and studios, the big databases after
  that (many of those are 300px originals blown up by AI). So the first
  candidate is "the one to try first", and it is only ever a preselection the
  user can change.
* A single short name is not an identity. `MIRAI`, an alias of 奏みらい, finds
  a picture of somebody else. Such candidates are listed but never preselected.

The pictures are fetched by the server and handed to the browser from a cache,
rather than the browser loading GitHub directly: whoever is looking (possibly
from outside, through the HTTPS front) does not talk to a third party, and the
picture chosen is already on disk when it is written into the folder.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from . import media

log = logging.getLogger("vam")

RAW = "https://raw.githubusercontent.com/gfriends/gfriends/master/"
# Set by main.py to DATA_DIR / "gfriends-filetree.json". Read at call time, so
# a test can point it somewhere else.
INDEX_PATH: Optional[Path] = None
MAX_AGE = 24 * 3600
TIMEOUT = 30
# Choices offered per character. The median person has around ten; a row of
# forty thumbnails is browsing, not choosing.
MAX_CHOICES = 12
# The largest picture in the library is around 3 MB. This is where we stop if
# something answers with far more than a picture.
MAX_IMAGE_BYTES = 16 * 1024 * 1024
QUALITY_ORDER = "0123456789abcdefghijklmnopqrstuvwxyz"
AVATAR_STEM = "avatar-gfriends"

_lock = threading.Lock()
_mem: dict = {"mtime": None, "index": {}, "pairs": set()}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def _rank(cat: str) -> int:
    r = QUALITY_ORDER.find(cat[:1].lower())
    return r if r >= 0 else len(QUALITY_ORDER)


def parse(body: bytes) -> Dict[str, List[Tuple[str, str]]]:
    """`Filetree.json` → name → [(folder, file)], the one to try first first."""
    index: Dict[str, List[Tuple[str, str]]] = {}
    for cat, items in json.loads(body)["Content"].items():
        for shown, stored in items.items():
            key = _norm(shown.rsplit(".", 1)[0])
            index.setdefault(key, []).append((cat, stored.split("?")[0]))
    for rows in index.values():
        rows.sort(key=lambda p: (_rank(p[0]), p[0], p[1]))
    return index


def _get(url: str, limit: int) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "VAM"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        body = r.read(limit + 1)
    if len(body) > limit:
        raise ValueError(f"larger than {limit} bytes: {url}")
    return body


def _refresh(path: Path):
    """Download the index if it is missing or a day old. A failure keeps the
    old copy: yesterday's index is far better than none."""
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        age = None
    if age is not None and age < MAX_AGE:
        return
    try:
        body = _get(RAW + "Filetree.json", 64 * 1024 * 1024)
        parse(body)                         # do not replace a good copy with junk
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")
        part.write_bytes(body)
        os.replace(part, path)
        log.info("gfriends index refreshed (%d KB)", len(body) // 1024)
    except Exception as e:
        log.warning("gfriends index refresh failed%s: %s",
                    "" if age is None else " (keeping the old one)", e)


def index(refresh: bool = True) -> Dict[str, List[Tuple[str, str]]]:
    """The parsed index, from memory while the file is unchanged.

    `refresh=False` never touches the network — for serving a picture, which
    only has to check that the name it was asked for is a real entry.
    """
    path = INDEX_PATH
    if path is None:
        return {}
    with _lock:
        if refresh:
            _refresh(path)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return {}
        if _mem["mtime"] != mtime:
            try:
                _mem["index"] = parse(path.read_bytes())
                _mem["pairs"] = {p for rows in _mem["index"].values()
                                 for p in rows}
                _mem["mtime"] = mtime
            except Exception as e:
                log.warning("gfriends index unreadable: %s", e)
                return {}
        return _mem["index"]


def name_chain(*groups: Iterable[str]) -> List[str]:
    """Every name to look up, in order, each once.

    Two shapes in this library need taking apart: a folder holding two people
    (`夏川くるみ／心愛りな`) and `Name (Other Name)`.
    """
    out: List[str] = []
    for group in groups:
        for raw in group or []:
            if not isinstance(raw, str):
                continue
            for part in [raw, *re.split(r"[／/()（）]", raw)]:
                part = part.strip()
                if part and part not in out:
                    out.append(part)
    return out


def weak(name: str) -> bool:
    """Too generic to stand for one person: one Latin word, or two characters.

    `MIRAI` finds somebody else's picture; `Karin Morita` or `森田花梨` does not.
    """
    n = name.strip()
    if len(n) <= 2:
        return True
    return n.isascii() and " " not in n


def candidates(names: List[str], idx: Optional[dict] = None) -> List[dict]:
    """Every picture any of the names finds, the one to try first first.

    Merged rather than stopping at the first name that hits: Peach measured
    新ありさ finding 1 picture and 橋本ありさ 11 for the same person.
    `by` is the name that found it, so a weak match can be shown as one.
    """
    idx = index() if idx is None else idx
    seen, out = set(), []
    for name in names:
        for cat, f in idx.get(_norm(name), []):
            if (cat, f) in seen:
                continue
            seen.add((cat, f))
            out.append({"cat": cat, "file": f, "by": name, "weak": weak(name),
                        "source": re.sub(r"^[0-9a-z]-", "", cat, flags=re.I)})
    out.sort(key=lambda c: (_rank(c["cat"]), c["weak"]))
    return out[:MAX_CHOICES]


def preselect(choices: List[dict]) -> Optional[int]:
    """The first one not found by a weak name, or None."""
    for i, c in enumerate(choices):
        if not c["weak"]:
            return i
    return None


def known(cat: str, f: str) -> bool:
    """Only entries in the index are ever fetched — the URL is built from what
    the browser sends, and this keeps it to pictures in the library."""
    index(refresh=False)
    return (cat, f) in _mem.get("pairs", ())


def url(cat: str, f: str) -> str:
    return (RAW + "Content/" + urllib.parse.quote(cat) + "/"
            + urllib.parse.quote(f))


def _cached(cat: str, f: str, cache: Path) -> Path:
    key = hashlib.md5(f"{cat}/{f}".encode()).hexdigest()
    return cache / key[:2] / key


def image(cat: str, f: str, cache: Path) -> Path:
    """The picture, downloaded once. Raises if it is not a usable image."""
    dst = _cached(cat, f, cache)
    try:
        if dst.stat().st_size > 0:
            return dst
    except OSError:
        pass
    body = _get(url(cat, f), MAX_IMAGE_BYTES)
    from PIL import Image
    with Image.open(io.BytesIO(body)) as im:
        im.load()                           # a truncated download fails here
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_name(f"{dst.name}.{os.getpid()}.part")
    part.write_bytes(body)
    os.replace(part, dst)
    return dst


def install(folder: Path, cat: str, f: str, cache: Path) -> str:
    """Copy the chosen picture into the character's folder; return its path
    relative to the folder, the form `profile_photo` stores.

    Into `.vam/`, which the photo grid, the duplicate scan and the person scan
    all skip: a JAV folder should not grow a photo the user never collected,
    but the file has to live in the folder so it moves and merges with it.
    """
    src = image(cat, f, cache)
    from PIL import Image
    with Image.open(src) as im:
        ext = {"PNG": ".png", "WEBP": ".webp"}.get(im.format, ".jpg")
    d = folder / media.VAM_DIR
    d.mkdir(parents=True, exist_ok=True)
    # One name per picture, not one fixed name: the avatar URL carries only
    # the path and the browser keeps images for a month, so replacing the
    # file under the same name would keep showing the old face (the black
    # thumbnails of 2026-10-27 were exactly that).
    tag = hashlib.md5(f"{cat}/{f}".encode()).hexdigest()[:8]
    dst = d / f"{AVATAR_STEM}-{tag}{ext}"
    part = d / (dst.name + ".part")
    part.write_bytes(src.read_bytes())
    os.replace(part, dst)
    # Earlier picks from the library are ours to remove, once the new one is
    # in place. Anything else in the folder is the user's and stays.
    for old in d.glob(AVATAR_STEM + "*"):
        if old != dst and not old.name.endswith(".part"):
            try:
                old.unlink()
            except OSError:
                pass
    return f"{media.VAM_DIR}/{dst.name}"
