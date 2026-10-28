"""Kodi-style `.nfo` files beside the videos — read into VAM, written out of it.

Kodi, Jellyfin and Emby all read the same file: `<video stem>.nfo`, an XML
`<movie>` element, beside the video. A handful already sit in this library,
written by Jellyfin in 2020 (their poster paths still point at `K:\\JAV\\…`
and the actress under her earlier name) — and they hold the original
Japanese title, the release date and the cast, which is exactly what VAM's
machine-translated English titles lack.

**Reading** fills only what VAM does not know yet — a title typed by hand, or
imported from a wiki page, always wins over a file somebody's tool wrote.

**Writing merges.** Only the fields VAM manages are touched (`MANAGED`);
everything else in an existing file — the plot, the art paths, Jellyfin's
stream details and actor thumbnails, comments — stays as it was. An existing
file keeps its BOM, XML declaration and line endings. Actors are only ever
added: a name already there under another spelling of the same person (an
alias, as 日高優奈 is of 森田花梨) counts as there.

Nothing here touches the disk except `write_atomic`; the caller decides what
to write and keeps the backups.
"""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

# what VAM writes; anything else in a file is somebody else's and left alone
MANAGED = ("title", "originaltitle", "premiered", "year", "num", "actor")

_KANA = re.compile(r"[\u3040-\u30ff]")
_HAN = re.compile(r"[\u4e00-\u9fff]")
_YEAR = re.compile(r"^\d{4}$")
_DECL = re.compile(rb"^(\xef\xbb\xbf)?\s*(<\?xml[^>]*\?>)")
DEFAULT_DECL = '<?xml version="1.0" encoding="utf-8" standalone="yes"?>'


def nfo_for(video: Path) -> Path:
    return video.with_suffix(".nfo")


def title_lang(title: str) -> str:
    """Which `movie_title` column a title belongs in. Kana is Japanese; Han
    without kana is taken as Chinese; anything else as English."""
    if _KANA.search(title):
        return "ja"
    if _HAN.search(title):
        return "zh"
    return "en"


def _parse(data: bytes) -> ET.Element:
    """The root element, comments kept. Raises ValueError on a broken file."""
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    try:
        parser.feed(data)
        return parser.close()
    except ET.ParseError as e:
        raise ValueError(f"不是有效的 XML：{e}") from None


def _text(root: ET.Element, tag: str) -> str:
    el = root.find(tag)
    return (el.text or "").strip() if el is not None else ""


def read(data: bytes) -> dict:
    """The fields VAM can use from one file. ValueError if it is not a movie."""
    root = _parse(data)
    if root.tag != "movie":
        raise ValueError(f"不是電影的 NFO（根元素是 <{root.tag}>）")
    actors = []
    for a in root.findall("actor"):
        n = (a.findtext("name") or "").strip()
        if n and n not in actors:
            actors.append(n)
    return {
        "title": _text(root, "title"),
        "originaltitle": _text(root, "originaltitle"),
        # Jellyfin writes all three; Kodi reads `premiered`
        "premiered": (_text(root, "premiered") or _text(root, "releasedate")
                      or _text(root, "aired")),
        "year": _text(root, "year"),
        "num": _text(root, "num"),
        "studio": _text(root, "studio"),
        "plot": _text(root, "plot"),
        "actors": actors,
    }


def _insert_scalar(root: ET.Element, tag: str) -> ET.Element:
    """A new field, placed before the cast so the file still reads in the
    usual order (Kodi and Jellyfin do not care; people opening it do)."""
    el = ET.Element(tag)
    kids = list(root)
    first_actor = next((i for i, k in enumerate(kids) if k.tag == "actor"), None)
    root.insert(len(kids) if first_actor is None else first_actor, el)
    return el


def render(existing: Optional[bytes], want: dict,
           same_person: Optional[Callable[[str, str], bool]] = None
           ) -> Tuple[Optional[bytes], List[str]]:
    """The file with VAM's fields merged in, and which fields changed.

    `want`: title, originaltitle, released (YYYY-MM-DD or a bare year),
    num, actors. Empty values are not written — VAM not knowing something is
    no reason to blank what a file says. Returns (None, []) when nothing would
    change, so an unchanged file is never rewritten.
    """
    root = ET.Element("movie") if existing is None else _parse(existing)
    if root.tag != "movie":
        raise ValueError(f"不是電影的 NFO（根元素是 <{root.tag}>），不寫入")
    changed: List[str] = []

    def put(tag: str, value: str, only_if_missing: bool = False):
        value = (value or "").strip()
        if not value:
            return
        el = root.find(tag)
        if el is None:
            el = _insert_scalar(root, tag)
        elif only_if_missing or (el.text or "").strip() == value:
            return
        el.text = value
        changed.append(tag)

    put("title", want.get("title", ""))
    put("originaltitle", want.get("originaltitle", ""))
    released = (want.get("released") or "").strip()
    if _YEAR.match(released):
        put("year", released)              # IAFD gives only the year
    elif released:
        put("premiered", released)
        put("year", released[:4])
        rd = root.find("releasedate")      # Jellyfin's copy of the same date
        if rd is not None and (rd.text or "").strip() != released:
            rd.text = released
    # the number is the file's identity; one already there is not ours to fix
    put("num", want.get("num", ""), only_if_missing=True)

    have = [(a.findtext("name") or "").strip() for a in root.findall("actor")]
    added = False
    for name in want.get("actors") or []:
        if not name or any(h == name or (same_person and same_person(h, name))
                           for h in have):
            continue
        a = ET.SubElement(root, "actor")
        ET.SubElement(a, "name").text = name
        ET.SubElement(a, "type").text = "Actor"
        have.append(name)
        added = True
    if added:
        changed.append("actor")

    if not changed:
        return None, []
    return _serialise(root, existing), changed


def _serialise(root: ET.Element, existing: Optional[bytes]) -> bytes:
    ET.indent(root, "  ")
    body = ET.tostring(root, encoding="unicode")
    bom, decl, crlf = b"", DEFAULT_DECL, False
    if existing is not None:
        m = _DECL.match(existing)
        if m:
            bom = m.group(1) or b""
            decl = m.group(2).decode("utf-8", "replace")
        elif existing.startswith(b"\xef\xbb\xbf"):
            bom = b"\xef\xbb\xbf"
        crlf = b"\r\n" in existing
    text = decl + "\n" + body + "\n"
    if crlf:
        text = text.replace("\n", "\r\n")
    return bom + text.encode("utf-8")


def write_atomic(path: Path, data: bytes, before_replace=None) -> None:
    """Write beside the target and swap it in, so a reader never sees half a
    file. `before_replace(tmp)` lets the caller record the scratch file."""
    tmp = path.with_name(path.name + ".vamtmp")
    if before_replace:
        before_replace(tmp)
    tmp.write_bytes(data)
    os.replace(tmp, path)


def nfo_names(folder: Path) -> Dict[str, str]:
    """{lower-cased stem: file name} of the .nfo files in one folder — one
    listing per folder instead of a stat per video, which over the NAS is the
    difference between seconds and minutes."""
    out: Dict[str, str] = {}
    try:
        with os.scandir(folder) as it:
            for e in it:
                if e.name.lower().endswith(".nfo") and e.is_file():
                    out[e.name[:-4].lower()] = e.name
    except OSError:
        pass
    return out
