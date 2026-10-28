"""DMM / FANZA's own catalogue: the original Japanese title of a film.

`video.dmm.co.jp` is a JavaScript shell (titles-jav.md: "DMM 也拿不到日文片名"),
but the shell gets its data from a GraphQL endpoint, `api.video.dmm.co.jp/graphql`,
that answers plain POSTs: no Cloudflare, no age gate, measured 200 from this
machine on 2026-10-25 in about a second. It is the site's own interface, not a
published API, so it may change without notice — a failure here falls back to
javdatabase's English and is never fatal. (Found by reading how Peach and
OpenAver fetch it; the code here is our own.)

From a catalogue number to DMM's product id (cid):

  * guess first: `STARS-786` → `stars00786` — but SOD's are `1stars00786`, and
    labels with a number prefix (`h_1721fkos00006`) are only found by search
  * search `STARS 786` and keep only ids whose letters *and* number match:
    `SSIS 057` lists `ssis00570` first, `ERK 116` brings `gerk116`
  * the detail's `makerContentId` must be this number, or it is not this film
  * an AI remaster (`48midv00012ai`) sorts after the original

Only the plain studio shape is asked (`ABC-123`). FC2, HEYZO and the dated
amateur numbers are not on this catalogue; Prestige left FANZA, and MGS
amateur numbers (`300MAAN-…`) were never on it.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import javdb

log = logging.getLogger("vam.dmm")

URL = "https://api.video.dmm.co.jp/graphql"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
_MIN_GAP = 2.0          # seconds between requests — a guest, as with javdatabase
_TIMEOUT = 20
MISS_DAYS = 7           # a number DMM did not have is asked again after this
SEARCH_LIMIT = 30       # `SSIS 057` → `ssis00057` came 11th, sorted by date
JST = timezone(timedelta(hours=9))

_DETAIL = ("query VamContent($id: ID!) { ppvContent(id: $id) {"
           " id title makerContentId makerReleasedAt actresses { name } maker { name } } }")
_SEARCH = ("query VamSearch($limit: Int!, $sort: ContentSearchPPVSort!, $queryWord: String)"
           " { legacySearchPPV(limit: $limit, sort: $sort, queryWord: $queryWord)"
           " { result { contents { id } } } }")

_CODE = re.compile(r"^([A-Z]{2,6})-(\d{2,5})$")
# studio-shaped, but uncensored: never on this catalogue
_NOT_HERE = {"HEYZO"}
# a cid: optional label prefix (`1`, `118`, `h_1721`), letters, zero-padded
# number, optional suffix (`ai` for a remaster, `a`/`b` for parts)
_CID = re.compile(r"^(?:h_)?\d*([a-z]{2,6})0*(\d{1,5})([a-z]*)$")
_MAKER_ID = re.compile(r"^([A-Za-z]{2,6})[-_ ]?0*(\d{1,5})$")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS dmm_lookup (
    video_id TEXT PRIMARY KEY,
    status   TEXT NOT NULL,       -- ok | not_found
    cid      TEXT,
    at       REAL NOT NULL
);
"""

_lock = threading.Lock()
_last = 0.0


def askable(video_id: str) -> bool:
    """Whether DMM's catalogue can have this number at all."""
    m = _CODE.match((video_id or "").strip().upper())
    return bool(m) and m.group(1) not in _NOT_HERE


def _post(query: str, variables: dict) -> dict:
    """One GraphQL request, rate-limited. RuntimeError when DMM cannot be
    reached or refuses — the caller falls back rather than failing."""
    global _last
    with _lock:
        gap = time.time() - _last
        if gap < _MIN_GAP:
            time.sleep(_MIN_GAP - gap)
        _last = time.time()
    req = Request(URL, data=json.dumps({"query": query, "variables": variables}).encode(),
                  headers={"Content-Type": "application/json", "Accept": "application/json",
                           "User-Agent": _UA})
    try:
        with urlopen(req, timeout=_TIMEOUT) as r:
            body = json.loads(r.read().decode("utf-8", "replace"))
    except HTTPError as e:
        raise RuntimeError(f"DMM 回應 HTTP {e.code}") from e
    except (URLError, OSError) as e:
        raise RuntimeError(f"無法連線到 DMM：{getattr(e, 'reason', e)}") from e
    except ValueError as e:
        raise RuntimeError("DMM 回的不是 JSON") from e
    if body.get("errors") and not body.get("data"):
        raise RuntimeError(f"DMM 拒絕了這個查詢：{body['errors'][0].get('message', '')}")
    return body.get("data") or {}


def jst_date(stamp: str) -> str:
    """`makerReleasedAt` is midnight in Japan written in UTC
    (`2023-03-22T15:00:00Z` is the 23rd); the day that matters is Japan's."""
    try:
        t = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return ""
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(JST).date().isoformat()


def _cid_matches(cid: str, letters: str, num: str) -> Optional[str]:
    """The cid's suffix when it is this film's letters and number, else None."""
    m = _CID.match(cid or "")
    if not m or m.group(1) != letters.lower() or int(m.group(2)) != int(num):
        return None
    return m.group(3)


def _is_this(detail: Optional[dict], letters: str, num: str) -> bool:
    if not detail or not detail.get("title"):
        return False
    m = _MAKER_ID.match(str(detail.get("makerContentId") or "").strip())
    return bool(m and m.group(1).upper() == letters and int(m.group(2)) == int(num))


def _answer(detail: dict) -> dict:
    return {"cid": detail.get("id") or "",
            "title": " ".join(str(detail.get("title") or "").split()),
            "released": jst_date(detail.get("makerReleasedAt") or ""),
            "actresses": [a.get("name") for a in detail.get("actresses") or []
                          if a.get("name")],
            "maker": (detail.get("maker") or {}).get("name") or ""}


def lookup(video_id: str) -> Optional[dict]:
    """{cid, title, released, actresses, maker} from DMM, or None when DMM
    does not have this film. RuntimeError when DMM could not be asked."""
    if not askable(video_id):
        return None
    letters, num = _CODE.match(video_id.strip().upper()).groups()
    guess = f"{letters.lower()}{int(num):05d}"
    d = _post(_DETAIL, {"id": guess}).get("ppvContent")
    if _is_this(d, letters, num):
        return _answer(d)
    found = (_post(_SEARCH, {"limit": SEARCH_LIMIT, "sort": "RELEASE_DATE",
                             "queryWord": f"{letters} {num}"})
             .get("legacySearchPPV") or {}).get("result") or {}
    cids = []
    for c in found.get("contents") or []:
        cid = c.get("id") or ""
        suffix = _cid_matches(cid, letters, num)
        if suffix is not None and cid != guess and cid not in cids:
            cids.append(cid)
    # the original before a remaster or a split part: no suffix first
    cids.sort(key=lambda c: _cid_matches(c, letters, num) != "")
    for cid in cids[:2]:
        d = _post(_DETAIL, {"id": cid}).get("ppvContent")
        if _is_this(d, letters, num):
            return _answer(d)
    return None


_IMAGE = ("query VamCover($id: ID!) { ppvContent(id: $id) {"
          " packageImage { largeUrl mediumUrl } } }")


def package_images(cid: str) -> tuple:
    """(sleeve URL, front URL), "" for what DMM does not give.

    The sleeve is back | spine | front in one landscape picture; the front is
    DMM's own portrait cut. DMM's own fields, so no URL is built by hand.
    Measured 2026-10-27 over 28 films: every one had both. Recent films: a
    2184x1468 sleeve and a 1032x1468 front. Older ones: an 800x538 sleeve and
    a 147x200 front — too small, so the caller cuts the front from the sleeve.
    The DVD path Peach also tries (`pics.dmm.co.jp/mono/…`) answered with the
    "now printing" placeholder for all 28 — these are the digital editions'
    ids — so it is not asked.
    """
    d = (_post(_IMAGE, {"id": cid}).get("ppvContent") or {}).get("packageImage") or {}
    return ((d.get("largeUrl") or "").strip(), (d.get("mediumUrl") or "").strip())


# ── remembered answers (in javdb.sqlite3, beside the titles) ─────────────────

def remembered_cid(video_id: str) -> str:
    """The cid an earlier lookup found, or "" — saves asking DMM again."""
    conn = _connect()
    try:
        r = conn.execute("SELECT status, cid FROM dmm_lookup WHERE video_id = ?",
                         (video_id.strip().upper(),)).fetchone()
    finally:
        conn.close()
    return (r["cid"] or "") if r and r["status"] == "ok" else ""

def _connect():
    conn = javdb._connect()
    conn.executescript(_SCHEMA)
    return conn


def recently_missed(video_id: str) -> bool:
    """DMM said it does not have this one, less than MISS_DAYS ago."""
    conn = _connect()
    try:
        r = conn.execute("SELECT status, at FROM dmm_lookup WHERE video_id = ?",
                         (video_id.strip().upper(),)).fetchone()
    finally:
        conn.close()
    return bool(r and r["status"] == "not_found"
                and time.time() - r["at"] < MISS_DAYS * 86400)


def remember(video_id: str, hit: Optional[dict]) -> None:
    conn = _connect()
    try:
        conn.execute("INSERT OR REPLACE INTO dmm_lookup (video_id, status, cid, at) "
                     "VALUES (?, ?, ?, ?)",
                     (video_id.strip().upper(), "ok" if hit else "not_found",
                      (hit or {}).get("cid"), time.time()))
        conn.commit()
    finally:
        conn.close()
