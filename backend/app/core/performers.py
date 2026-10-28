"""Tie a performer name from javdatabase to one of the user's own folders.

javdatabase answers with a romaji name and a Japanese one ("Yuko Hoshikawa" /
"星川優子"). The archive is organised by whatever the user typed, which may be
either of those or a variant spelling (井上さくら vs 井上桜). The bridge is the
`aliases` list already present in person_info.json — matching reads it, and the
seeding pass fills it in automatically from files that are *already* filed.
"""
from __future__ import annotations

import logging
import unicodedata
from pathlib import Path
from typing import Iterable, Optional

from . import media

log = logging.getLogger("vam.performers")


def norm(name: str) -> str:
    """Fold the differences that are never meaningful in a performer name."""
    s = unicodedata.normalize("NFKC", (name or "").strip().lower())
    return "".join(ch for ch in s if not ch.isspace() and ch not in "・･.,-_")


def index(chars: Iterable[dict]) -> dict:
    """{normalised name → character} over folder names and their aliases."""
    out = {}
    for c in chars:
        keys = [c["name"]]
        try:
            keys += media.load_person_info(Path(c["path"])).get("aliases", [])
        except Exception:
            pass
        for k in keys:
            k = norm(k)
            # first writer wins: the folder's own name outranks someone's alias
            if k and k not in out:
                out[k] = c
    return out


def match(performer: dict, char_index: dict) -> Optional[dict]:
    """The user's folder for this performer, or None if they have none yet."""
    for key in (performer.get("jp"), performer.get("romaji")):
        if key:
            hit = char_index.get(norm(key))
            if hit:
                return hit
    return None


def add_aliases(folder: Path, names: Iterable[str]) -> list:
    """Record spellings on a character, skipping ones already known.

    Returns the names actually added, so a caller can report what changed.
    """
    info = media.load_person_info(folder)
    existing = {norm(a) for a in info.get("aliases", [])}
    existing.add(norm(folder.name))
    added = []
    for n in names:
        n = (n or "").strip()
        if not n or norm(n) in existing:
            continue
        info.setdefault("aliases", []).append(n)
        existing.add(norm(n))
        added.append(n)
    if added:
        media.save_person_info(folder, info)
    return added
