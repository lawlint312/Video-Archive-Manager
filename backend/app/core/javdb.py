"""Look up a catalogue number's performers on javdatabase.com.

Chosen over the alternatives because it is the only one that answers at all:
javlibrary.com and chiba-f.net both sit behind a Cloudflare challenge, and
working around bot protection is not something this project does. javdatabase's
robots.txt only disallows /wp-admin/, so its content pages are fair game.

Two requests per performer, then never again:

    /movies/{id}/           → the performer's slug
    /idols/{slug}/          → "JP: 星川優子", the name that matches a folder

Everything is cached in data/javdb.sqlite3, including the misses — a catalogue
number that is not in the database will never be in it, and re-asking would be
rude as well as pointless.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..config import DATA_DIR

log = logging.getLogger("vam.javdb")

BASE = "https://www.javdatabase.com"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
_MIN_GAP = 1.0          # seconds between requests — we are a guest here
_TIMEOUT = 25

DB_PATH = DATA_DIR / "javdb.sqlite3"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS movie (
    video_id  TEXT PRIMARY KEY,     -- STARS-786
    status    TEXT NOT NULL,        -- ok | not_found | no_performer | error
    slugs     TEXT,                 -- comma-separated idol slugs
    checked_at REAL NOT NULL
);
-- Titles are keyed by catalogue number, not by file path: a title belongs to
-- the film, so it survives renames and moves and covers every copy of the
-- same film at once (98 characters exist on more than one drive).
--   manual : typed by the user, always wins
--   ja     : the original Japanese (DMM via core/dmm.py, a wiki page, an NFO)
--   zh     : a translation of `en` (no service wired up yet)
--   en     : javdatabase's English, which is machine-translated
CREATE TABLE IF NOT EXISTS movie_title (
    video_id  TEXT PRIMARY KEY,
    manual    TEXT,
    ja        TEXT,
    zh        TEXT,
    en        TEXT,
    -- Release date, YYYY-MM-DD, from an imported wiki page. Belongs here for
    -- the same reason the title does: it is a fact about the film, so it
    -- survives renames and covers every copy at once. The file's own mtime
    -- answers a different question -- when *you* got it.
    released  TEXT,
    -- A release date typed by hand. Its own column so a later import can keep
    -- writing `released` without touching it, and clearing it falls back to
    -- the imported one -- the same rule as `manual` for the title.
    released_manual TEXT,
    checked_at REAL
);
CREATE TABLE IF NOT EXISTS idol_profile (
    slug      TEXT PRIMARY KEY,
    data      TEXT,                 -- parsed profile as JSON, "" = no data
    checked_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS idol (
    slug      TEXT PRIMARY KEY,     -- yuko-hoshikawa
    romaji    TEXT,                 -- Yuko Hoshikawa
    jp        TEXT,                 -- 星川優子
    checked_at REAL NOT NULL
);
-- Cast per catalogue number, in Japanese, imported from a wiki page. Separate
-- from `movie.slugs` above on purpose: that holds javdatabase idol slugs and
-- is filled by fetching, this holds the names as a Japanese wiki writes them
-- and is filled by the user importing a page. Mixing them would lose which
-- source said what, and they disagree about spelling more often than not.
--
-- Nothing reads this to write anybody's aliases. `/api/javdb/seed` did exactly
-- that from weaker evidence and had to be undone by hand; this is a lookup for
-- matching, and the decision stays with the user.
CREATE TABLE IF NOT EXISTS wiki_cast (
    video_id TEXT PRIMARY KEY,
    names    TEXT NOT NULL,         -- names joined with U+FF0F
    source   TEXT,                  -- the page it came from
    at       REAL NOT NULL
);
-- What an imported page said about one character, so the video list can show
-- "102 films here, the wiki lists 750" without re-reading the HTML.
--
-- Keyed by folder, like the watched/favourite marks, and moved by the same
-- hooks when a character folder is renamed or merged.
CREATE TABLE IF NOT EXISTS wiki_profile (
    folder     TEXT PRIMARY KEY,    -- normcase'd, see videoflags.key()
    page_name  TEXT,
    url        TEXT,
    total      INTEGER NOT NULL,    -- video releases the page lists
    no_id      INTEGER NOT NULL,    -- of those, how many have no catalogue no.
    sections   TEXT,                -- JSON [{name, count}] -- the breakdown
    ids        TEXT,                -- comma-joined catalogue numbers
    -- The page's whole release list: JSON [{i: id, t: title, d: date,
    -- s: section}]. Needed to answer "which ones am I missing", which is a
    -- question about films the user does *not* own -- so it cannot come from
    -- `movie_title`, which is deliberately only the ones they do.
    -- Measured: 146 KB for the largest real page, ~21 MB if all 367 JAV
    -- characters were imported. `data/` is not in version control.
    works      TEXT,
    at         REAL NOT NULL
);
"""

_lock = threading.Lock()
_last_request = 0.0


# Columns added to a table that already exists somewhere. `_SCHEMA` uses
# CREATE TABLE IF NOT EXISTS, so a new column in it reaches new databases only
# -- an existing one keeps its old shape and every write fails with "no such
# column". SQLite has no ADD COLUMN IF NOT EXISTS, so each is applied only when
# missing, exactly as `db.py` does for the user tables.
#
# This bit once: `wiki_profile.works` shipped, worked in every test (they build
# a fresh database, where CREATE TABLE does include it) and returned 500 on the
# user's real one. A test on a fresh schema can never catch a missing
# migration; `test_wikiimport.py` now builds the old shape on purpose.
_MIGRATIONS = [
    ("wiki_profile", "works", "ALTER TABLE wiki_profile ADD COLUMN works TEXT"),
    ("movie_title", "released",
     "ALTER TABLE movie_title ADD COLUMN released TEXT"),
    ("movie_title", "released_manual",
     "ALTER TABLE movie_title ADD COLUMN released_manual TEXT"),
]
_migrated = False


def _connect() -> sqlite3.Connection:
    global _migrated
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    if not _migrated:
        # Once per process: this runs on every connection otherwise, and the
        # answer cannot change while we are running.
        with _lock:
            if not _migrated:
                _apply_migrations(conn)
                _migrated = True
    return conn


def _apply_migrations(conn: sqlite3.Connection) -> None:
    for table, column, ddl in _MIGRATIONS:
        cols = {r["name"] for r in
                conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in cols:
            log.info("javdb: adding %s.%s", table, column)
            conn.execute(ddl)
            conn.commit()


def _fetch(url: str) -> Optional[str]:
    """GET a page, rate-limited. None when the page does not exist."""
    global _last_request
    with _lock:
        gap = time.time() - _last_request
        if gap < _MIN_GAP:
            time.sleep(_MIN_GAP - gap)
        _last_request = time.time()
    req = Request(url, headers={"User-Agent": _UA,
                                "Accept-Language": "en-US,en;q=0.9"})
    try:
        with urlopen(req, timeout=_TIMEOUT) as r:
            return r.read().decode("utf-8", "replace")
    except HTTPError as e:
        if e.code == 404:
            return None
        raise
    except URLError as e:
        raise RuntimeError(f"無法連線到 javdatabase：{e.reason}") from e


_TITLE = re.compile(r"<title>([^<]*)</title>", re.I)
_IDOL_LINK = re.compile(r'href="' + re.escape(BASE) + r'/idols/([a-z0-9\-]+)/"')
# The profile prints "<b>JP:</b> 星川優子 <br>" — but plenty of performers have
# no Japanese name recorded at all, in which case the field is simply absent.
_JP_NAME = re.compile(r'<b>\s*JP:\s*</b>\s*([^<]{1,24})', re.I)


def _movie_slugs(html: str) -> list:
    """Performer slugs on a movie page, in page order, de-duplicated."""
    out = []
    for s in _IDOL_LINK.findall(html):
        if s not in out:
            out.append(s)
    return out


def lookup_movie(video_id: str, refresh: bool = False) -> dict:
    """{'status', 'performers': [{'slug','romaji','jp'}]} for one catalogue id."""
    vid = video_id.strip().upper()
    conn = _connect()
    try:
        if not refresh:
            row = conn.execute("SELECT * FROM movie WHERE video_id=?",
                               (vid,)).fetchone()
            if row and row["status"] != "error":
                return {"id": vid, "status": row["status"], "cached": True,
                        "performers": _performers(conn, row["slugs"])}

        try:
            html = _fetch(f"{BASE}/movies/{vid.lower()}/")
        except Exception as e:
            log.warning("javdb lookup failed for %s: %s", vid, e)
            return {"id": vid, "status": "error", "cached": False,
                    "performers": [], "error": str(e)}

        # the site answers 200 with a "Page Not Found" title for unknown ids
        title = (_TITLE.search(html).group(1) if html and _TITLE.search(html)
                 else "Page Not Found")
        if html is None or "Page Not Found" in title:
            _remember(conn, vid, "not_found", "")
            return {"id": vid, "status": "not_found", "cached": False,
                    "performers": []}

        slugs = _movie_slugs(html)
        status = "ok" if slugs else "no_performer"
        _remember(conn, vid, status, ",".join(slugs))
        return {"id": vid, "status": status, "cached": False,
                "performers": _performers(conn, ",".join(slugs))}
    finally:
        conn.close()


def _remember(conn: sqlite3.Connection, vid: str, status: str, slugs: str):
    conn.execute(
        "INSERT INTO movie (video_id, status, slugs, checked_at) VALUES (?,?,?,?) "
        "ON CONFLICT(video_id) DO UPDATE SET status=excluded.status, "
        "slugs=excluded.slugs, checked_at=excluded.checked_at",
        (vid, status, slugs, time.time()))
    conn.commit()


def _performers(conn: sqlite3.Connection, slugs: str) -> list:
    out = []
    for slug in [s for s in (slugs or "").split(",") if s]:
        row = conn.execute("SELECT * FROM idol WHERE slug=?", (slug,)).fetchone()
        if row is None:
            row = _load_idol(conn, slug)
        if row:
            # `_load_idol` trims the trailing "-" now, but rows cached before
            # that still read "白石美里  -"
            out.append({"slug": slug, "romaji": row["romaji"] or "",
                        "jp": (row["jp"] or "").strip().rstrip('-–—,/').strip()})
    return out


def _load_idol(conn: sqlite3.Connection, slug: str):
    """Fetch and cache one performer profile."""
    try:
        html = _fetch(f"{BASE}/idols/{slug}/")
    except Exception as e:
        log.warning("javdb idol fetch failed for %s: %s", slug, e)
        return None
    romaji = jp = ""
    if html:
        m = _TITLE.search(html)
        if m:
            romaji = m.group(1).split(" - ")[0].strip()
        m = _JP_NAME.search(html)
        if m:
            # the field often trails into the next element ("白石美里  -")
            jp = m.group(1).strip().rstrip('-–—,/').strip()
    conn.execute(
        "INSERT INTO idol (slug, romaji, jp, checked_at) VALUES (?,?,?,?) "
        "ON CONFLICT(slug) DO UPDATE SET romaji=excluded.romaji, "
        "jp=excluded.jp, checked_at=excluded.checked_at",
        (slug, romaji, jp, time.time()))
    conn.commit()
    return conn.execute("SELECT * FROM idol WHERE slug=?", (slug,)).fetchone()


def cached_ids() -> dict:
    """Every id we already asked about → its status. For the seeding report."""
    conn = _connect()
    try:
        return {r["video_id"]: r["status"]
                for r in conn.execute("SELECT video_id, status FROM movie")}
    finally:
        conn.close()


def stats() -> dict:
    conn = _connect()
    try:
        by = {r["status"]: r["n"] for r in conn.execute(
            "SELECT status, COUNT(*) n FROM movie GROUP BY status")}
        idols = conn.execute("SELECT COUNT(*) FROM idol").fetchone()[0]
        return {"movies": sum(by.values()), "by_status": by, "idols": idols}
    finally:
        conn.close()


def idol_page(slug: str) -> Optional[str]:
    """Raw HTML of a performer's profile page, or None if there is no such page."""
    return _fetch(f"{BASE}/idols/{slug}/")


def cached_profile(slug: str) -> Optional[str]:
    """Stored profile JSON for a performer; None when never fetched."""
    conn = _connect()
    try:
        row = conn.execute("SELECT data FROM idol_profile WHERE slug = ?",
                           (slug,)).fetchone()
        return row["data"] if row else None
    finally:
        conn.close()


def remember_profile(slug: str, data_json: str):
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO idol_profile (slug, data, checked_at) VALUES (?,?,?) "
            "ON CONFLICT(slug) DO UPDATE SET data=excluded.data, "
            "checked_at=excluded.checked_at", (slug, data_json, time.time()))
        conn.commit()
    finally:
        conn.close()


def idol_romaji(slug: str) -> str:
    """The Latin-letter spelling, which is what javdatabase names people by."""
    conn = _connect()
    try:
        row = conn.execute("SELECT romaji FROM idol WHERE slug = ?",
                           (slug,)).fetchone()
        return (row["romaji"] if row else "") or ""
    finally:
        conn.close()


# ── film titles ──────────────────────────────────────────────────────────────

_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S | re.I)

# Resolution order. Manual beats everything because the user typed it; the
# machine-translated English is the last resort. A Japanese title only an
# imported page knows (core/catalog.py, for films added after the import)
# ranks as `ja`: `title_row()` and `titles()` fill it in when `ja` is empty.
TITLE_ORDER = ("manual", "ja", "zh", "en")


def _with_catalog(row: dict, cat: Optional[dict]) -> dict:
    """A `movie_title` row with the catalog's title and date filled in where
    the row has none. `ja_from` / `released_from` say so, for the UI."""
    row = dict(row or {})
    if cat and cat.get("title") and not (row.get("ja") or "").strip():
        row["ja"], row["ja_from"] = cat["title"], cat["title_from"]
    if cat and cat.get("released") and not (row.get("released") or row.get("released_manual")):
        row["released"], row["released_from"] = cat["released"], "catalog"
    return row


def best_title(row: dict) -> str:
    for k in TITLE_ORDER:
        v = (row or {}).get(k)
        if v and str(v).strip():
            return str(v).strip()
    return ""


def _clean_title(raw: str, vid: str, performers: list) -> str:
    """"STARS-786 - Rich Sperm ... Yuko Hoshikawa" -> "Rich Sperm ...".

    javdatabase repeats the catalogue number in front and appends the cast to
    the end — sometimes several of them ("... Aya Hanada, Emi Futaki"), and
    sometimes not quite right ("Yuko Hoshi" for Yuko Hoshikawa). Both are shown
    elsewhere in our UI, so they are noise here. Names are stripped from the
    end repeatedly, accepting a prefix of the real name so the misspelt ones
    go too, but never shorter than 6 characters — below that a real word
    could be eaten.
    """
    t = re.sub(r"<[^>]+>", "", raw or "")
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"^" + re.escape(vid) + r"\s*[-–—:]\s*", "", t, flags=re.I)

    names = []
    for p in performers or []:
        for n in (p.get("romaji"), p.get("jp")):
            if n and len(n) >= 6:
                names.append(n)
    changed = True
    while changed and t:
        changed = False
        for n in names:
            for end in range(len(n), 5, -1):
                cand = n[:end]
                if t.lower().endswith(cand.lower()):
                    t = t[: -len(cand)].strip(" -–—,、")
                    changed = True
                    break
            if changed:
                break
    return t.strip()


def fetch_title(video_id: str) -> str:
    """English title for one catalogue number, cached. "" when unavailable."""
    vid = video_id.strip().upper()
    row = title_row(vid)
    # asked before, or Japanese already known from an imported page: the
    # English would only ever be the fallback below it
    if row.get("checked_at") or row.get("ja"):
        return best_title(row)

    html = _fetch(f"{BASE}/movies/{vid.lower()}/")
    title = ""
    if html:
        m = _H1.search(html)
        if m:
            perf = _performers(_connect(), ",".join(_movie_slugs(html)))
            title = _clean_title(m.group(1), vid, perf)
    set_title(vid, en=title)
    return best_title(title_row(vid))


def set_title(video_id: str, **fields):
    """Store one or more title variants. Unnamed fields are left alone."""
    vid = video_id.strip().upper()
    cols = [k for k in ("manual", "ja", "zh", "en") if k in fields]
    conn = _connect()
    try:
        conn.execute("INSERT OR IGNORE INTO movie_title (video_id) VALUES (?)",
                     (vid,))
        if cols:
            sets = ", ".join(f"{c} = ?" for c in cols)
            conn.execute(f"UPDATE movie_title SET {sets}, checked_at = ? "
                         f"WHERE video_id = ?",
                         [fields[c] for c in cols] + [time.time(), vid])
        else:
            conn.execute("UPDATE movie_title SET checked_at = ? WHERE video_id = ?",
                         (time.time(), vid))
        conn.commit()
    finally:
        conn.close()


def set_released(mapping: dict) -> int:
    """Store release dates, `{catalogue number: YYYY-MM-DD}`.

    Separate from the titles because the two are wanted separately: a film can
    have a date and no title, and re-importing a page should not have to
    rewrite titles the user may have overridden.
    """
    rows = [(str(k).strip().upper(), str(v).strip())
            for k, v in (mapping or {}).items()
            if str(k).strip() and str(v).strip()]
    if not rows:
        return 0
    now = time.time()
    conn = _connect()
    try:
        conn.executemany(
            "INSERT INTO movie_title (video_id, released, checked_at) "
            "VALUES (?, ?, ?) ON CONFLICT(video_id) DO UPDATE SET "
            "released = ?, checked_at = ?",
            [(vid, d, now, d, now) for vid, d in rows])
        conn.commit()
    finally:
        conn.close()
    return len(rows)


def released() -> dict:
    """{video_id: YYYY-MM-DD} for everything known, for bulk display.
    A date typed by hand wins over an imported one, and both over one only an
    imported page's catalogue has (films added after that import)."""
    from . import catalog
    out = {k: e["released"] for k, e in catalog.best().items() if e["released"]}
    conn = _connect()
    try:
        out.update({r["video_id"]: r["d"] for r in conn.execute(
            "SELECT video_id, COALESCE(NULLIF(released_manual, ''), released) AS d "
            "FROM movie_title WHERE COALESCE(NULLIF(released_manual, ''), "
            "released, '') != ''")})
        return out
    finally:
        conn.close()


def set_released_manual(video_id: str, value: str) -> dict:
    """Type a release date for a film; "" clears it back to the imported one.
    `value` is already normalised (`releasedate.normalize`). Returns
    {"released": what now shows, "imported": the import's date}."""
    vid = video_id.strip().upper()
    conn = _connect()
    try:
        conn.execute("INSERT OR IGNORE INTO movie_title (video_id) VALUES (?)",
                     (vid,))
        conn.execute("UPDATE movie_title SET released_manual = ? WHERE video_id = ?",
                     (value or None, vid))
        conn.commit()
        r = conn.execute("SELECT released, released_manual FROM movie_title "
                         "WHERE video_id = ?", (vid,)).fetchone()
        from . import catalog
        imported = r["released"] or catalog.entry(vid)["released"]
        return {"released": r["released_manual"] or imported, "imported": imported}
    finally:
        conn.close()


def set_ja_titles(mapping: dict) -> int:
    """Store many Japanese titles at once, one transaction.

    Used by the wiki import, where one page carries eighty of them. Written
    through `movie_title.ja`, which `TITLE_ORDER` already ranks above the
    machine-translated English -- so an imported title becomes the displayed
    one immediately, and the English stays as the fallback rather than being
    thrown away.
    """
    rows = [(str(k).strip().upper(), str(v).strip())
            for k, v in (mapping or {}).items()
            if str(k).strip() and str(v).strip()]
    if not rows:
        return 0
    now = time.time()
    conn = _connect()
    try:
        conn.executemany(
            "INSERT INTO movie_title (video_id, ja, checked_at) VALUES (?, ?, ?) "
            "ON CONFLICT(video_id) DO UPDATE SET ja = ?, checked_at = ?",
            [(vid, t, now, t, now) for vid, t in rows])
        conn.commit()
    finally:
        conn.close()
    return len(rows)


def set_wiki_cast(mapping: dict, source: str = "") -> int:
    """Store `{catalogue number: [performer, ...]}` from an imported page."""
    rows = [(str(k).strip().upper(), "／".join(str(n).strip() for n in v
                                               if str(n).strip()))
            for k, v in (mapping or {}).items() if str(k).strip() and v]
    rows = [(vid, names) for vid, names in rows if names]
    if not rows:
        return 0
    now = time.time()
    conn = _connect()
    try:
        conn.executemany(
            "INSERT INTO wiki_cast (video_id, names, source, at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(video_id) DO UPDATE SET "
            "names = ?, source = ?, at = ?",
            [(vid, n, source, now, n, source, now) for vid, n in rows])
        conn.commit()
    finally:
        conn.close()
    return len(rows)


def wiki_cast(video_id: str) -> list:
    conn = _connect()
    try:
        r = conn.execute("SELECT names FROM wiki_cast WHERE video_id = ?",
                         (str(video_id).strip().upper(),)).fetchone()
    finally:
        conn.close()
    return [n for n in (r["names"].split("／") if r else []) if n]


def set_wiki_profile(folder_key: str, page_name: str, url: str,
                     summary: dict, ids, works=None, catalog_titles=True) -> None:
    """Remember what an imported page said about one character."""
    secs = json.dumps(summary.get("sections", []), ensure_ascii=False)
    # Compact separators: this is the large column, and the keys are already
    # one letter each for the same reason.
    wjson = json.dumps(works or [], ensure_ascii=False, separators=(",", ":"))
    row = (page_name, url, summary.get("total", 0), summary.get("no_id", 0),
           secs, ",".join(sorted(ids)), wjson, time.time())
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO wiki_profile (folder, page_name, url, total, no_id, "
            "sections, ids, works, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(folder) DO UPDATE SET page_name = ?, url = ?, "
            "total = ?, no_id = ?, sections = ?, ids = ?, works = ?, at = ?",
            (folder_key,) + row + row)
        conn.commit()
    finally:
        conn.close()
    # What the panel and the missing list read now: shared with AVBase.
    from . import catalog
    works = works or []
    if works:
        members = [(w.get("i"), w.get("s") or "") for w in works if w.get("i")]
    else:
        members = ([(i, s.get("name", "")) for s in summary.get("sections", [])
                    for i in s.get("ids", [])] or [(i, "") for i in ids or []])
    catalog.set_page(folder_key, "wiki", page_name, url, summary.get("total", 0),
                     members, [{"t": w.get("t") or "", "d": w.get("d") or "",
                                "s": w.get("s") or ""} for w in works if not w.get("i")])
    if catalog_titles:
        catalog.put_works("wiki", works)


def wiki_profile(folder_key: str) -> Optional[dict]:
    conn = _connect()
    try:
        r = conn.execute("SELECT * FROM wiki_profile WHERE folder = ?",
                         (folder_key,)).fetchone()
    finally:
        conn.close()
    if not r:
        return None
    try:
        sections = json.loads(r["sections"] or "[]")
    except ValueError:
        sections = []
    return {"page_name": r["page_name"], "url": r["url"],
            "total": r["total"], "no_id": r["no_id"], "sections": sections,
            "ids": [i for i in (r["ids"] or "").split(",") if i],
            "at": r["at"]}


def wiki_works(folder_key: str) -> list:
    """The full release list from the imported page, or [] when there is none.

    Read separately from `wiki_profile()` because it is the big column and the
    video list asks for the summary on every load, while this is only wanted
    when the user opens the "not yet acquired" dialog.
    """
    conn = _connect()
    try:
        r = conn.execute("SELECT works FROM wiki_profile WHERE folder = ?",
                         (folder_key,)).fetchone()
    finally:
        conn.close()
    if not r or not r["works"]:
        return []
    try:
        return json.loads(r["works"])
    except ValueError:
        return []


def move_wiki_profile(old_key: str, new_key: str) -> None:
    """Follow a renamed or merged character folder."""
    if old_key == new_key:
        return
    conn = _connect()
    try:
        conn.execute("DELETE FROM wiki_profile WHERE folder = ?", (new_key,))
        conn.execute("UPDATE wiki_profile SET folder = ? WHERE folder = ?",
                     (new_key, old_key))
        conn.commit()
    finally:
        conn.close()
    from . import catalog
    catalog.move_folder(old_key, new_key)


def ja_title_count() -> int:
    """How many films have a Japanese title -- the number this feature exists
    to move, so it is worth being able to report it."""
    conn = _connect()
    try:
        return conn.execute("SELECT COUNT(*) c FROM movie_title "
                            "WHERE ja IS NOT NULL AND ja != ''").fetchone()["c"]
    finally:
        conn.close()


def titles() -> dict:
    """{video_id: best title} for everything we know, for bulk display --
    including films no lookup has touched yet, from imported pages."""
    from . import catalog
    cat = catalog.best()
    out = {k: e["title"] for k, e in cat.items() if e["title"]}
    conn = _connect()
    try:
        for r in conn.execute("SELECT * FROM movie_title"):
            t = best_title(_with_catalog(dict(r), cat.get(r["video_id"])))
            if t:
                out[r["video_id"]] = t
        return out
    finally:
        conn.close()


def title_row(video_id: str) -> dict:
    """The film's `movie_title` row, with an imported page's Japanese title
    and date filled in where it has none ({} when nothing is known)."""
    from . import catalog
    vid = video_id.strip().upper()
    conn = _connect()
    try:
        r = conn.execute("SELECT * FROM movie_title WHERE video_id = ?",
                         (vid,)).fetchone()
    finally:
        conn.close()
    row = _with_catalog(dict(r) if r else {}, catalog.entry(vid))
    return row if r or row else {}
