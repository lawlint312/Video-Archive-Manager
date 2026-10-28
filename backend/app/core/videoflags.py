"""Per-user "watched" and "favourite" marks for individual videos.

Two decisions worth knowing before touching this:

**It lives in `data/vam.db`, not in the cache and not in `person_info.json`.**
The cache is swept by an LRU limit, so anything stored there is expected to be
regenerable — these marks are not. `person_info.json` lives on the NAS where
git cannot protect it, and these are not the user's own written content in the
way notes and aliases are.

**The key is the path, because most of this library has no catalogue number.**
Only 1,478 of 28,146 videos have one — 5%; the social-media half is all
`LaurelLeier-20260825-194508`-shaped names with no id at all. The cost of
keying on the path is that every operation which changes a path has to carry
the row over, exactly like `media.move_note` — see `move`/`move_tree` below and
their call sites in `main.py`. Getting that wrong is silent: you would only
notice weeks later that archiving a video forgets you had watched it.

`core/filetitle.py` pays the same cost for the same reason and is hooked in at
the same seven call sites, deliberately right next to these ones. Add an eighth
move operation and you have to remember both.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from ..db import connect

# The `video_flags` table itself is declared in `db.py` with every other table,
# so there is one place that knows the schema. Columns: user_id, path (see
# `key`), watched (ISO time of the first view, NULL = never), fav, fav_at.


def key(path) -> str:
    """The stored form of a path.

    `normcase` rather than `casefold`: on Windows it folds to lower case, on
    Linux it leaves the path alone — so the behaviour always matches the file
    system underneath, which matters once this runs in a container. The same
    reasoning is why the case-insensitive folder merge was specified this way.
    """
    return os.path.normcase(os.path.abspath(str(path)))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get(user_id: int, path) -> dict:
    conn = connect()
    try:
        r = conn.execute(
            "SELECT watched, fav FROM video_flags WHERE user_id = ? AND path = ?",
            (user_id, key(path))).fetchone()
    finally:
        conn.close()
    return {"watched": bool(r and r["watched"]), "fav": bool(r and r["fav"])}


def flags_for(user_id: int) -> dict:
    """Every mark this user has, as {key: {watched, fav}}.

    One query for a whole listing, then a dict lookup per file — the same shape
    as `javdb.titles()`, and for the same reason: the inbox holds 2,016 videos
    and a query per card would be 2,016 round trips to answer a question that
    fits in one.
    """
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT path, watched, fav FROM video_flags WHERE user_id = ?",
            (user_id,)).fetchall()
    finally:
        conn.close()
    return {r["path"]: {"watched": bool(r["watched"]), "fav": bool(r["fav"])}
            for r in rows}


def mark_watched(user_id: int, path) -> dict:
    """Record a view. The timestamp is of the *first* one and never moves.

    Re-watching does not update it: the question this answers is "have I seen
    this", not "when did I last see it". Keeping the first time makes the value
    stable, which matters if it is ever shown or sorted on.
    """
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO video_flags (user_id, path, watched) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, path) DO UPDATE SET watched = COALESCE(watched, ?)",
            (user_id, key(path), _now(), _now()))
        conn.commit()
    finally:
        conn.close()
    return get(user_id, path)


def set_watched(user_id: int, path, on: bool) -> dict:
    """Clearing exists because a mis-tap must be undoable.

    The five-second delay before a view counts makes accidents rare, not
    impossible — a video left open while answering the door still counts.
    """
    if on:
        return mark_watched(user_id, path)
    conn = connect()
    try:
        conn.execute(
            "UPDATE video_flags SET watched = NULL WHERE user_id = ? AND path = ?",
            (user_id, key(path)))
        _drop_if_empty(conn, user_id, key(path))
        conn.commit()
    finally:
        conn.close()
    return get(user_id, path)


def set_fav(user_id: int, path, on: bool) -> dict:
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO video_flags (user_id, path, fav, fav_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id, path) DO UPDATE SET fav = ?, fav_at = ?",
            (user_id, key(path), int(on), _now() if on else None,
             int(on), _now() if on else None))
        _drop_if_empty(conn, user_id, key(path))
        conn.commit()
    finally:
        conn.close()
    return get(user_id, path)


def _drop_if_empty(conn, user_id: int, k: str):
    """Rows that say nothing are deleted rather than left as zeroes.

    Otherwise un-favouriting a video you never watched leaves a permanent row
    per (user, video), and `flags_for` — which loads the whole table on every
    listing — grows without bound in exchange for no information at all.
    """
    conn.execute(
        "DELETE FROM video_flags WHERE user_id = ? AND path = ? "
        "AND watched IS NULL AND fav = 0", (user_id, k))


def fav_count(path, exclude_user: Optional[int] = None) -> int:
    """How many accounts have favourited this file — a number, never names.

    The admin's force-delete confirmation shows it, so the person deciding
    knows how many people lose something, without the dialog becoming a way
    to read who likes what.
    """
    conn = connect()
    try:
        if exclude_user is None:
            r = conn.execute("SELECT COUNT(*) FROM video_flags WHERE path = ? "
                             "AND fav = 1", (key(path),)).fetchone()
        else:
            r = conn.execute("SELECT COUNT(*) FROM video_flags WHERE path = ? "
                             "AND fav = 1 AND user_id != ?",
                             (key(path), exclude_user)).fetchone()
    finally:
        conn.close()
    return int(r[0] or 0)


def others_fav_counts(user_id: int) -> dict:
    """{key: how many *other* accounts favourited it}, for a whole listing.

    One query for the listing, same shape as `flags_for`. Without it a card
    favourited only by somebody else looked deletable — the trash icon was
    ordinary — and the refusal arrived only after the click, telling the
    admin to un-favourite something their own heart showed as off.
    """
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT path, COUNT(*) AS n FROM video_flags WHERE fav = 1 "
            "AND user_id != ? GROUP BY path", (user_id,)).fetchall()
    finally:
        conn.close()
    return {r["path"]: r["n"] for r in rows}


def any_fav(path) -> bool:
    """Has *anyone* marked this as a favourite?

    Not scoped to the caller, deliberately. This exists to guard deletion, and
    deletion is permanent and shared: a file one account favourited is gone for
    every account once it is unlinked. Reporting it without naming who keeps
    the guard useful without turning it into a way to read someone else's list.
    """
    conn = connect()
    try:
        r = conn.execute("SELECT 1 FROM video_flags WHERE path = ? AND fav = 1 "
                         "LIMIT 1", (key(path),)).fetchone()
    finally:
        conn.close()
    return r is not None


def move(src, dst):
    """Carry every user's marks from one path to another.

    Called wherever a video changes path: archiving out of the inbox, the
    rename after a convert, taking over the original's name when resolving a
    produced/original pair. Missing one of those is how "I already watched this"
    quietly disappears at exactly the moment it was most useful.
    """
    a, b = key(src), key(dst)
    if a == b:
        return
    conn = connect()
    try:
        # The destination can already have rows (a converted .ts and .mp4 share
        # a stem, so the same user may hold marks for both). The source is the
        # one being kept, so it wins.
        conn.execute("DELETE FROM video_flags WHERE path = ? AND user_id IN "
                     "(SELECT user_id FROM video_flags WHERE path = ?)", (b, a))
        conn.execute("UPDATE video_flags SET path = ? WHERE path = ?", (b, a))
        conn.commit()
    finally:
        conn.close()


def move_tree(src_dir, dst_dir) -> int:
    """Rewrite the prefix of every path under a folder that has been moved.

    Renaming a character folder, or merging one into another, moves hundreds of
    files in one operation and never touches them individually — so this is a
    prefix rewrite rather than a loop. Files renamed on the way (a merge that
    hits a name collision) fall outside it and lose their marks; that is a rare
    case and the alternative is threading a per-file callback through the merge
    planner for it.
    """
    a, b = key(src_dir), key(dst_dir)
    if a == b:
        return 0
    sep = os.sep
    pre = a if a.endswith(sep) else a + sep
    new = b if b.endswith(sep) else b + sep
    conn = connect()
    try:
        # ESCAPE because a real folder name may contain _ or %, which LIKE
        # would otherwise treat as wildcards and match unrelated paths.
        pat = pre.replace("\\", "\\\\").replace("_", "\\_").replace("%", "\\%")
        cur = conn.execute(
            "UPDATE video_flags SET path = ? || SUBSTR(path, ?) "
            "WHERE path LIKE ? ESCAPE '\\'", (new, len(pre) + 1, pat + "%"))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def forget(path):
    """Drop the marks for a deleted file, for every user.

    Not just tidiness: a new file landing on the same path would otherwise
    inherit the old one's "watched", which reads as the feature being broken.
    """
    conn = connect()
    try:
        conn.execute("DELETE FROM video_flags WHERE path = ?", (key(path),))
        conn.commit()
    finally:
        conn.close()


def forget_many(paths: Iterable):
    for p in paths:
        forget(p)


def favourites(user_id: int, allowed: Optional[callable] = None) -> list:
    """Every favourited path, newest mark first.

    `allowed` is the caller's `is_allowed` check: this table has no idea about
    per-user root restrictions, and a favourite made before a restriction was
    applied must not leak the path afterwards.
    """
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT path, fav_at FROM video_flags WHERE user_id = ? AND fav = 1 "
            "ORDER BY fav_at DESC", (user_id,)).fetchall()
    finally:
        conn.close()
    out = [r["path"] for r in rows]
    if allowed is not None:
        out = [p for p in out if allowed(Path(p))]
    return out
