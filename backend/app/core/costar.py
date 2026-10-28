"""Co-stars: one video, filed under its lead, also listed under the others.

A video lives in exactly one character folder — its lead's. Anyone else in it
(a co-star, a guest appearance) used to be invisible from their own page, and
searching their name found the character but never that film. Moving or
copying files is not the answer: the folder is the lead's, and a copy would be
a duplicate the dedup scan then asks you to delete.

So it is a mark, stored here and shown as a filter (「共演」) on the co-star's
video tab. Nothing on disk changes.

**Two keys, the same two the titles use.** A video with a catalogue number is
marked by the number (`id:SDMU-066`): the cast belongs to the film, so every
copy of it carries the mark and no rename can lose it. Everything else — 95%
of this library — is marked by its path (`path:…`), which means every move has
to carry the row over. `move`/`move_tree`/`forget` exist for that, and their
call sites sit beside `filetitle`'s in main.py so a new move operation trips
over all of them at once.

**Turned-down suggestions are remembered** (state 0). Suggestions come from
cast lists the library already holds (wiki imports, javdatabase); without this
the same wrong guess would be offered on every playback.
"""
from __future__ import annotations

import os
from typing import Iterable, Optional

from ..db import connect
from . import media
from .videoflags import key as path_key

CONFIRMED, REJECTED = 1, 0


def video_key(path) -> str:
    """'id:<number>' for a numbered film, else 'path:<normcase'd path>'."""
    vid = media.detect_video_id(os.path.basename(str(path)))
    return f"id:{vid.upper()}" if vid else f"path:{path_key(path)}"


def folder_key(folder) -> str:
    return path_key(folder)


def for_video(path, state: int = CONFIRMED) -> list:
    """Folder keys marked on this video (confirmed by default)."""
    conn = connect()
    try:
        return [r["folder"] for r in conn.execute(
            "SELECT folder FROM costar WHERE video = ? AND state = ? "
            "ORDER BY rowid", (video_key(path), state))]
    finally:
        conn.close()


def set_for_video(path, folders: Iterable) -> list:
    """Replace the confirmed co-stars of a video; returns the stored keys.

    Removing someone here does not turn them into a rejection — taking a
    name off is an edit, not a verdict on a suggestion.
    """
    vk = video_key(path)
    want = []
    for f in folders or []:
        k = folder_key(f)
        if k not in want:
            want.append(k)
    conn = connect()
    try:
        conn.execute("DELETE FROM costar WHERE video = ? AND state = ?",
                     (vk, CONFIRMED))
        for k in want:
            conn.execute(
                "INSERT INTO costar (video, folder, state) VALUES (?, ?, ?) "
                "ON CONFLICT(video, folder) DO UPDATE SET state = excluded.state",
                (vk, k, CONFIRMED))
        conn.commit()
        return want
    finally:
        conn.close()


# ── names with no folder ─────────────────────────────────────────────────────
# A co-star the library has no character for. Kept as typed (or as the cast
# list writes it) and matched to folders and aliases only when read, so a
# folder created later picks up every film already marked with the name.

def names_for(path) -> list:
    conn = connect()
    try:
        return [r["name"] for r in conn.execute(
            "SELECT name FROM costar_name WHERE video = ? ORDER BY at, rowid",
            (video_key(path),))]
    finally:
        conn.close()


def set_names(path, names: Iterable) -> list:
    """Replace the name-only co-stars of a video; returns them."""
    import time
    vk = video_key(path)
    want = []
    for n in names or []:
        n = " ".join(str(n).split())
        if n and n not in want:
            want.append(n)
    conn = connect()
    try:
        conn.execute("DELETE FROM costar_name WHERE video = ?", (vk,))
        now = time.time()
        conn.executemany("INSERT INTO costar_name (video, name, at) VALUES (?, ?, ?)",
                         [(vk, n, now + i * 1e-6) for i, n in enumerate(want)])
        conn.commit()
        return want
    finally:
        conn.close()


def all_names() -> dict:
    """{video key: [names]} — for the search index."""
    conn = connect()
    try:
        out: dict = {}
        for r in conn.execute("SELECT video, name FROM costar_name ORDER BY at, rowid"):
            out.setdefault(r["video"], []).append(r["name"])
        return out
    finally:
        conn.close()


def videos_named(keys: set, norm) -> list:
    """Video keys marked with a name whose `norm()` is one of `keys` — the
    films a character co-stars in under a name marked before her folder (or
    that alias) existed."""
    if not keys:
        return []
    conn = connect()
    try:
        return sorted({r["video"] for r in conn.execute("SELECT video, name FROM costar_name")
                       if norm(r["name"]) in keys})
    finally:
        conn.close()


def reject(path, folder) -> None:
    """Remember that a suggested co-star is wrong, so it is not offered again."""
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO costar (video, folder, state) VALUES (?, ?, ?) "
            "ON CONFLICT(video, folder) DO UPDATE SET state = excluded.state",
            (video_key(path), folder_key(folder), REJECTED))
        conn.commit()
    finally:
        conn.close()


def videos_for(folders: Iterable) -> list:
    """Video keys confirmed for any of these character folders."""
    ks = [folder_key(f) for f in folders]
    if not ks:
        return []
    conn = connect()
    try:
        q = ",".join("?" * len(ks))
        return [r["video"] for r in conn.execute(
            f"SELECT DISTINCT video FROM costar WHERE state = ? AND folder IN ({q})",
            (CONFIRMED, *ks))]
    finally:
        conn.close()


def counts() -> dict:
    """{folder key: number of confirmed co-star marks}."""
    conn = connect()
    try:
        return {r["folder"]: r["n"] for r in conn.execute(
            "SELECT folder, COUNT(*) AS n FROM costar WHERE state = ? "
            "GROUP BY folder", (CONFIRMED,))}
    finally:
        conn.close()


# ── keeping path keys and folder keys correct when things move ─────────────

def move(src, dst) -> None:
    """Carry a path-keyed mark over when a file moves. A numbered film's mark
    is keyed by the number and needs nothing."""
    a, b = video_key(src), video_key(dst)
    if a == b or not a.startswith("path:"):
        return
    conn = connect()
    try:
        # rows already on the destination belong to the file being replaced
        for t in ("costar", "costar_name"):
            conn.execute(f"DELETE FROM {t} WHERE video = ?", (b,))
            conn.execute(f"UPDATE {t} SET video = ? WHERE video = ?", (b, a))
        conn.commit()
    finally:
        conn.close()


def _like(prefix: str) -> str:
    return (prefix.replace("\\", "\\\\").replace("_", "\\_")
            .replace("%", "\\%") + "%")


def move_tree(src_dir, dst_dir) -> int:
    """A folder moved (character renamed or merged): rewrite both the videos
    under it and the co-star folder values pointing at it.

    Same rules as `filetitle.move_tree`: the separator is part of the prefix so
    renaming `Ann` leaves `Annette` alone, and LIKE's `_`/`%` are escaped
    because real folder names (`Social Media_亞洲`) contain them.
    """
    a, b = path_key(src_dir), path_key(dst_dir)
    if a == b:
        return 0
    sep = os.sep
    pre = a if a.endswith(sep) else a + sep
    new = b if b.endswith(sep) else b + sep
    vpre, vnew = "path:" + pre, "path:" + new
    conn = connect()
    try:
        n = 0
        # videos under the folder; INSERT OR IGNORE + DELETE so a merge that
        # lands a row on an existing (video, folder) pair cannot collide
        for old in [r["video"] for r in conn.execute(
                "SELECT DISTINCT video FROM costar WHERE video LIKE ? ESCAPE '\\'",
                (_like(vpre),))]:
            nv = vnew + old[len(vpre):]
            conn.execute("INSERT OR IGNORE INTO costar (video, folder, state) "
                         "SELECT ?, folder, state FROM costar WHERE video = ?",
                         (nv, old))
            n += conn.execute("DELETE FROM costar WHERE video = ?", (old,)).rowcount
        for old in [r["video"] for r in conn.execute(
                "SELECT DISTINCT video FROM costar_name WHERE video LIKE ? ESCAPE '\\'",
                (_like(vpre),))]:
            nv = vnew + old[len(vpre):]
            conn.execute("INSERT OR IGNORE INTO costar_name (video, name, at) "
                         "SELECT ?, name, at FROM costar_name WHERE video = ?", (nv, old))
            n += conn.execute("DELETE FROM costar_name WHERE video = ?", (old,)).rowcount
        # the folder itself as a co-star
        for row in list(conn.execute(
                "SELECT video, folder, state FROM costar WHERE folder = ? "
                "OR folder LIKE ? ESCAPE '\\'", (a, _like(pre)))):
            nf = b if row["folder"] == a else new + row["folder"][len(pre):]
            conn.execute("INSERT OR IGNORE INTO costar (video, folder, state) "
                         "VALUES (?, ?, ?)", (row["video"], nf, row["state"]))
            conn.execute("DELETE FROM costar WHERE video = ? AND folder = ?",
                         (row["video"], row["folder"]))
            n += 1
        conn.commit()
        return n
    finally:
        conn.close()


def forget(path) -> None:
    """A file was deleted. Only a path key is dropped: a numbered film may
    still have other copies, and the cast is a fact about the film."""
    k = video_key(path)
    if not k.startswith("path:"):
        return
    conn = connect()
    try:
        conn.execute("DELETE FROM costar WHERE video = ?", (k,))
        conn.execute("DELETE FROM costar_name WHERE video = ?", (k,))
        conn.commit()
    finally:
        conn.close()


# ── suggestions ────────────────────────────────────────────────────────────

def suggest(names: Iterable[str], char_index: dict, exclude: set,
            norm) -> list:
    """Characters whose folder name or alias matches one of the cast names.

    `char_index` is `performers.index()` ({normalised name → character}),
    `exclude` the folder keys not to offer (the lead, those already marked or
    turned down). Order follows the cast list; each character once.
    """
    out, seen = [], set()
    for n in names:
        c = char_index.get(norm(n)) if n else None
        if not c:
            continue
        fk = folder_key(c["path"])
        if fk in exclude or fk in seen:
            continue
        seen.add(fk)
        out.append({"folder": c["path"], "name": c["name"], "matched": n})
    return out


def owner_folder(path, chars: Iterable[dict]) -> Optional[dict]:
    """The character whose folder holds this file, if any (the lead)."""
    p = path_key(path)
    best = None
    for c in chars:
        for f in c.get("paths") or [c["path"]]:
            k = path_key(f)
            pre = k if k.endswith(os.sep) else k + os.sep
            if p.startswith(pre) and (best is None or len(k) > best[0]):
                best = (len(k), c)
    return best[1] if best else None
