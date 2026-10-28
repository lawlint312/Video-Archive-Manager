"""Every work an imported page lists — the ones not here too — shared between
the sources (seesaawiki, AVBase).

Why this exists: `movie_title` deliberately holds only the films the user
owns, so an import used to give a title and a date to the films already here
and nothing to the one downloaded next week; it stayed untitled until the page
was imported again. And each source kept its own idea of a performer's
output, so the character's ⓘ panel knew the wiki page and not the AVBase one.

Three tables, in javdb.sqlite3 beside the titles:

  catalog          one row per (catalogue number, source): that page's title
                   and release date for the film. Read as a fallback below
                   what VAM itself holds — see `javdb.TITLE_ORDER`.
  catalog_page     one row per (character folder, source): which page it was.
  catalog_member   which works that page lists for the character, and under
                   which section. The ⓘ panel counts the union.

A wiki page is a complete list, so importing it again replaces its members;
an AVBase file is often one page of sixteen, so its members only accumulate.
"""
from __future__ import annotations

import json
import time
from typing import Dict, Iterable, List, Optional

# Which source's title and date win when two pages disagree. The wiki first —
# the user's choice: its titles are the film's, AVBase's are a product's and
# often carry sales copy (「【4Kリマスター版】…」).
SOURCES = ("wiki", "avbase")

# Sections for works only AVBase lists. AVBase has no sections; the card's
# cast is what tells a solo film from a group one. Keys, not display text —
# `src/wikisections.js` translates them.
AVBASE_SOLO = "AVBase:単体"
AVBASE_GROUP = "AVBase:共演"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS catalog (
    video_id  TEXT NOT NULL,
    source    TEXT NOT NULL,      -- wiki | avbase
    title     TEXT,               -- Japanese
    released  TEXT,               -- YYYY-MM-DD
    at        REAL NOT NULL,
    PRIMARY KEY (video_id, source)
);
CREATE TABLE IF NOT EXISTS catalog_page (
    folder     TEXT NOT NULL,     -- normcase'd, see videoflags.key()
    source     TEXT NOT NULL,
    page_name  TEXT,
    url        TEXT,
    total      INTEGER NOT NULL DEFAULT 0,   -- what the page itself says
    -- Works the page lists without a catalogue number (old films the wiki
    -- did not record one for): JSON [{t, d, s}]. Real releases, so they
    -- count, but nothing can key them.
    unnumbered TEXT,
    at         REAL NOT NULL,
    PRIMARY KEY (folder, source)
);
CREATE TABLE IF NOT EXISTS catalog_member (
    folder    TEXT NOT NULL,
    source    TEXT NOT NULL,
    video_id  TEXT NOT NULL,
    section   TEXT,
    PRIMARY KEY (folder, source, video_id)
);
"""

# PRAGMA user_version of javdb.sqlite3 once the old wiki_profile rows have
# been copied over. Checked on every connection, not once per process: tests
# point DB_PATH at a fresh file each time.
_VERSION = 1


def _connect():
    from . import javdb
    conn = javdb._connect()
    conn.executescript(_SCHEMA)
    if conn.execute("PRAGMA user_version").fetchone()[0] < _VERSION:
        _from_wiki_profile(conn)
        conn.execute(f"PRAGMA user_version = {_VERSION}")
        conn.commit()
    return conn


def _from_wiki_profile(conn) -> None:
    """Copy what earlier wiki imports stored per character. `wiki_profile`
    stays as it was; nothing reads it for display any more."""
    now = time.time()
    for r in conn.execute("SELECT * FROM wiki_profile").fetchall():
        try:
            works = json.loads(r["works"] or "[]")
        except ValueError:
            works = []
        try:
            sections = json.loads(r["sections"] or "[]")
        except ValueError:
            sections = []
        if works:
            members = [(w.get("i") or "", w.get("s") or "") for w in works]
        else:
            # imported before the release list was stored: the numbers are
            # in the section breakdown, the titles nowhere
            members = ([(i, s.get("name", "")) for s in sections for i in s.get("ids", [])]
                       or [(i, "") for i in (r["ids"] or "").split(",") if i])
        _write_page(conn, r["folder"], "wiki", r["page_name"], r["url"], r["total"],
                    [(i.upper(), s) for i, s in members if i],
                    [{"t": w.get("t") or "", "d": w.get("d") or "", "s": w.get("s") or ""}
                     for w in works if not w.get("i")],
                    replace=True, at=r["at"] or now)
        _write_works(conn, "wiki", works, keep_existing=True)


def _write_works(conn, source: str, works: Iterable[dict], keep_existing=False) -> int:
    """`works`: [{i, t, d}]. A value the page lacks never blanks one stored."""
    rows = [((w.get("i") or "").strip().upper(), (w.get("t") or "").strip(),
             (w.get("d") or "").strip()) for w in works or []]
    rows = [r for r in rows if r[0] and (r[1] or r[2])]
    if keep_existing:
        sql = ("INSERT OR IGNORE INTO catalog (video_id, source, title, released, at) "
               "VALUES (?, ?, ?, ?, ?)")
        conn.executemany(sql, [(v, source, t, d, time.time()) for v, t, d in rows])
    else:
        conn.executemany(
            "INSERT INTO catalog (video_id, source, title, released, at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(video_id, source) DO UPDATE SET "
            "title = COALESCE(NULLIF(excluded.title, ''), catalog.title), "
            "released = COALESCE(NULLIF(excluded.released, ''), catalog.released), "
            "at = excluded.at",
            [(v, source, t, d, time.time()) for v, t, d in rows])
    return len(rows)


def _write_page(conn, folder, source, page_name, url, total, members, unnumbered,
                replace: bool, at: float) -> None:
    if replace:
        conn.execute("DELETE FROM catalog_member WHERE folder = ? AND source = ?",
                     (folder, source))
    old = conn.execute("SELECT unnumbered FROM catalog_page WHERE folder = ? AND source = ?",
                       (folder, source)).fetchone()
    if not replace and old and not unnumbered:
        unnumbered = json.loads(old["unnumbered"] or "[]")
    conn.execute(
        "INSERT OR REPLACE INTO catalog_page (folder, source, page_name, url, total, "
        "unnumbered, at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (folder, source, page_name or "", url or "", int(total or 0),
         json.dumps(unnumbered or [], ensure_ascii=False, separators=(",", ":")), at))
    # the first section a number appears under is the one it is filed in
    conn.executemany(
        "INSERT OR IGNORE INTO catalog_member (folder, source, video_id, section) "
        "VALUES (?, ?, ?, ?)", [(folder, source, v, s) for v, s in members])


# ── writing ──────────────────────────────────────────────────────────────────

def put_works(source: str, works: Iterable[dict]) -> int:
    """Store a page's titles and dates, `[{i, t, d}]`, for every work on it."""
    conn = _connect()
    try:
        n = _write_works(conn, source, works)
        conn.commit()
        return n
    finally:
        conn.close()


def set_page(folder: str, source: str, page_name: str, url: str, total: int,
             members: List[tuple], unnumbered: Optional[list] = None,
             replace: bool = True) -> None:
    """Which works one page lists for this character: `members` [(id, section)]."""
    conn = _connect()
    try:
        _write_page(conn, folder, source, page_name, url, total,
                    [(str(v).strip().upper(), s or "") for v, s in members if str(v).strip()],
                    unnumbered or [], replace=replace, at=time.time())
        conn.commit()
    finally:
        conn.close()


def move_folder(old_key: str, new_key: str) -> None:
    """Follow a renamed or merged character folder, like `move_wiki_profile`."""
    if old_key == new_key:
        return
    conn = _connect()
    try:
        for t in ("catalog_page", "catalog_member"):
            conn.execute(f"DELETE FROM {t} WHERE folder = ?", (new_key,))
            conn.execute(f"UPDATE {t} SET folder = ? WHERE folder = ?", (new_key, old_key))
        conn.commit()
    finally:
        conn.close()


# ── reading ──────────────────────────────────────────────────────────────────

def _rank(source: str) -> int:
    return SOURCES.index(source) if source in SOURCES else len(SOURCES)


def best() -> Dict[str, dict]:
    """{id: {"title", "released", "title_from"}} — the best of each source."""
    conn = _connect()
    try:
        rows = conn.execute("SELECT video_id, source, title, released FROM catalog").fetchall()
    finally:
        conn.close()
    out: Dict[str, dict] = {}
    for r in sorted(rows, key=lambda r: _rank(r["source"])):
        e = out.setdefault(r["video_id"], {"title": "", "released": "", "title_from": ""})
        if not e["title"] and r["title"]:
            e["title"], e["title_from"] = r["title"], r["source"]
        if not e["released"] and r["released"]:
            e["released"] = r["released"]
    return out


def entry(video_id: str) -> dict:
    vid = (video_id or "").strip().upper()
    conn = _connect()
    try:
        rows = conn.execute("SELECT source, title, released FROM catalog WHERE video_id = ?",
                            (vid,)).fetchall()
    finally:
        conn.close()
    e = {"title": "", "released": "", "title_from": ""}
    for r in sorted(rows, key=lambda r: _rank(r["source"])):
        if not e["title"] and r["title"]:
            e["title"], e["title_from"] = r["title"], r["source"]
        if not e["released"] and r["released"]:
            e["released"] = r["released"]
    return e


def summary(folder: str) -> Optional[dict]:
    """What the imported pages say about one character, merged; None if none.

    The shape the video list's panel has always read (`page_name`, `url`,
    `total`, `no_id`, `sections` with their ids, `ids`), plus `pages`, one per
    source. A number listed by both sources is one work, filed under the
    wiki's section.
    """
    conn = _connect()
    try:
        pages = conn.execute("SELECT * FROM catalog_page WHERE folder = ?",
                             (folder,)).fetchall()
        if not pages:
            return None
        members = conn.execute(
            "SELECT source, video_id, section FROM catalog_member WHERE folder = ? "
            "ORDER BY rowid", (folder,)).fetchall()
    finally:
        conn.close()
    pages = sorted(pages, key=lambda p: _rank(p["source"]))
    section_of: Dict[str, str] = {}
    per_source: Dict[str, int] = {}
    for m in sorted(members, key=lambda m: _rank(m["source"])):   # stable: page order kept
        per_source[m["source"]] = per_source.get(m["source"], 0) + 1
        section_of.setdefault(m["video_id"], m["section"] or "")
    unnumbered = []
    for p in pages:
        try:
            unnumbered += json.loads(p["unnumbered"] or "[]")
        except ValueError:
            pass
    sections: Dict[str, dict] = {}
    for vid, sec in section_of.items():
        sections.setdefault(sec, {"name": sec, "count": 0, "ids": []})
        sections[sec]["count"] += 1
        sections[sec]["ids"].append(vid)
    for w in unnumbered:
        sec = w.get("s") or ""
        sections.setdefault(sec, {"name": sec, "count": 0, "ids": []})["count"] += 1
    first = pages[0]
    return {"page_name": first["page_name"], "url": first["url"],
            "total": len(section_of) + len(unnumbered), "no_id": len(unnumbered),
            "sections": list(sections.values()), "ids": list(section_of),
            "pages": [{"source": p["source"], "page_name": p["page_name"], "url": p["url"],
                       "total": p["total"], "listed": per_source.get(p["source"], 0)
                       + (len(json.loads(p["unnumbered"] or "[]")) if p["unnumbered"] else 0)}
                      for p in pages],
            "unnumbered": unnumbered, "section_of": section_of,
            "at": max(p["at"] for p in pages)}
