"""Hand-typed titles for videos that have no catalogue number.

**Why this exists separately from `javdb.movie_title`.** That table is keyed by
catalogue number, which is the right key when there is one: a number identifies
the film itself, so one edit covers every copy and no rename can lose it. But
that only reaches 1,478 of the 28,146 videos in this library — **5%**. The
other 26,668 have no number at all, and until now there was nowhere to put a
title for them.

**The key is the path, and the filename was measured and rejected.** Keying on
the filename stem would have been tempting (it survives moves, and copies would
share a title), but among the files with no catalogue number there are 1,030
groups of identical stems covering 2,864 files -- the worst single group is
15 unrelated videos all named `✨@ofxmega on [TELEGRAM] (1)`. A stem key would
have put one title onto all fifteen.

The cost of a path key is the same one `videoflags` pays, and it is paid in the
same place: every operation that moves a file has to carry the row over, or the
title silently disappears. See `move`/`move_tree`/`forget` below; their call
sites sit next to the `videoflags` ones in `main.py`, deliberately, so that
anyone adding a seventh move operation trips over both at once.

Titles typed here are display-and-search only. Nothing fetches them -- there is
no source to fetch from once the catalogue number is gone.
"""
from __future__ import annotations

import os
from typing import Iterable

from ..db import connect
from .videoflags import key          # same normcase'd form, deliberately shared


def get(path) -> str:
    conn = connect()
    try:
        r = conn.execute("SELECT title FROM file_title WHERE path = ?",
                         (key(path),)).fetchone()
        return r["title"] if r else ""
    finally:
        conn.close()


def all_titles() -> dict:
    """{path key: title} for bulk display, mirroring `javdb.titles()`."""
    conn = connect()
    try:
        return {r["path"]: r["title"]
                for r in conn.execute("SELECT path, title FROM file_title")}
    finally:
        conn.close()


def set_title(path, title: str) -> str:
    """Store a title; clearing it removes the row rather than storing ''.

    Same rule as notes and as the manual JAV title: clearing the box means
    "there is no title", not "the title is blank".

    **But the row is only dropped when nothing else is left in it.** Since the
    IAFD import added `released`, deleting on an empty title would throw away a
    release date the user never touched — the same failure the JAV side avoids
    by keeping title and release date in separate columns of one row.
    """
    t = (title or "").strip()
    k = key(path)
    conn = connect()
    try:
        if t:
            conn.execute(
                "INSERT INTO file_title (path, title) VALUES (?, ?) "
                "ON CONFLICT(path) DO UPDATE SET title = excluded.title", (k, t))
        else:
            conn.execute("UPDATE file_title SET title = '' WHERE path = ?", (k,))
            conn.execute("DELETE FROM file_title WHERE path = ? AND "
                         "(title IS NULL OR title = '') AND "
                         "(released IS NULL OR released = '') AND "
                         "(released_manual IS NULL OR released_manual = '')", (k,))
        conn.commit()
    finally:
        conn.close()
    return t


def set_released(path, released: str) -> str:
    """Store a release date (or the bare year IAFD gives). Empty clears it."""
    r = (released or "").strip()
    k = key(path)
    conn = connect()
    try:
        if r:
            conn.execute(
                "INSERT INTO file_title (path, title, released) VALUES (?, '', ?) "
                "ON CONFLICT(path) DO UPDATE SET released = excluded.released",
                (k, r))
        else:
            conn.execute("UPDATE file_title SET released = NULL WHERE path = ?", (k,))
            conn.execute("DELETE FROM file_title WHERE path = ? AND "
                         "(title IS NULL OR title = '') AND "
                         "(released_manual IS NULL OR released_manual = '')", (k,))
        conn.commit()
    finally:
        conn.close()
    return r


def set_many_released(mapping: dict) -> int:
    """One transaction for a whole import."""
    rows = [(key(p), (r or "").strip()) for p, r in (mapping or {}).items()]
    rows = [(k, r) for k, r in rows if r]
    if not rows:
        return 0
    conn = connect()
    try:
        conn.executemany(
            "INSERT INTO file_title (path, title, released) VALUES (?, '', ?) "
            "ON CONFLICT(path) DO UPDATE SET released = excluded.released", rows)
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def set_released_manual(path, value: str) -> dict:
    """Type a release date for one file; "" clears it back to the imported
    one (an IAFD year). `value` is already normalised by `releasedate`.
    Its own column, like `javdb.movie_title.released_manual`, so a later IAFD
    import writes `released` and never touches what was typed."""
    k = key(path)
    conn = connect()
    try:
        if value:
            conn.execute(
                "INSERT INTO file_title (path, title, released_manual) "
                "VALUES (?, '', ?) ON CONFLICT(path) DO UPDATE SET "
                "released_manual = excluded.released_manual", (k, value))
        else:
            conn.execute("UPDATE file_title SET released_manual = NULL "
                         "WHERE path = ?", (k,))
            conn.execute("DELETE FROM file_title WHERE path = ? AND "
                         "(title IS NULL OR title = '') AND "
                         "(released IS NULL OR released = '')", (k,))
        conn.commit()
        r = conn.execute("SELECT released, released_manual FROM file_title "
                         "WHERE path = ?", (k,)).fetchone()
    finally:
        conn.close()
    imported = (r["released"] or "") if r else ""
    return {"released": (r["released_manual"] if r else "") or imported,
            "imported": imported}


def all_released() -> dict:
    """{path key: release date} for bulk display, mirroring `javdb.released()`.
    A date typed by hand wins over an imported one."""
    conn = connect()
    try:
        return {r["path"]: r["d"] for r in conn.execute(
            "SELECT path, COALESCE(NULLIF(released_manual, ''), released) AS d "
            "FROM file_title WHERE COALESCE(NULLIF(released_manual, ''), "
            "released, '') <> ''")}
    finally:
        conn.close()


def move(src, dst) -> None:
    """Carry the title over when a file moves. Silent if there is none."""
    a, b = key(src), key(dst)
    if a == b:
        return
    conn = connect()
    try:
        r = conn.execute("SELECT title FROM file_title WHERE path = ?",
                         (a,)).fetchone()
        if r:
            conn.execute("DELETE FROM file_title WHERE path = ?", (b,))
            conn.execute("UPDATE file_title SET path = ? WHERE path = ?", (b, a))
            conn.commit()
    finally:
        conn.close()


def move_tree(src_dir, dst_dir) -> int:
    """Rewrite the prefix of every title under a folder that has moved.

    Same shape as `videoflags.move_tree`, including the ESCAPE: a real folder
    name may contain `_` or `%`, which LIKE would otherwise treat as wildcards
    and match unrelated paths. The separator is part of the prefix so that
    renaming `Ann` does not also rewrite `Annette`.

    Unlike `video_flags`, `path` is the whole primary key here, so a merge that
    lands two files on one path would make the UPDATE collide. The destination
    rows are cleared first: the file being moved onto is the one being replaced,
    so its title should go with it.
    """
    a, b = key(src_dir), key(dst_dir)
    if a == b:
        return 0
    sep = os.sep
    pre = a if a.endswith(sep) else a + sep
    new = b if b.endswith(sep) else b + sep
    pat = (pre.replace("\\", "\\\\").replace("_", "\\_").replace("%", "\\%")
           + "%")
    conn = connect()
    try:
        conn.execute(
            "DELETE FROM file_title WHERE path IN ("
            "  SELECT ? || SUBSTR(path, ?) FROM file_title"
            "  WHERE path LIKE ? ESCAPE '\\')", (new, len(pre) + 1, pat))
        cur = conn.execute(
            "UPDATE file_title SET path = ? || SUBSTR(path, ?) "
            "WHERE path LIKE ? ESCAPE '\\'", (new, len(pre) + 1, pat))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def forget(path) -> None:
    conn = connect()
    try:
        conn.execute("DELETE FROM file_title WHERE path = ?", (key(path),))
        conn.commit()
    finally:
        conn.close()


def forget_many(paths: Iterable) -> None:
    ks = [(key(p),) for p in paths]
    if not ks:
        return
    conn = connect()
    try:
        conn.executemany("DELETE FROM file_title WHERE path = ?", ks)
        conn.commit()
    finally:
        conn.close()
