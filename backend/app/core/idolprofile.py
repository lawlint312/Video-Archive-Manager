"""Fill a character's profile from javdatabase.com.

Two things this deliberately does NOT do:

  * translate the folder name. javdatabase cannot be searched in Japanese —
    measured: "山村花子" returns zero performer pages, "Hanako Yamamura"
    returns hers. Japanese personal-name readings are ambiguous enough that no
    offline dictionary gets them right, so the catalogue numbers already in
    the folder are the bridge instead: /movies/{id}/ names the performer.

  * trust a single film. `/api/javdb/seed` treated every performer of every
    catalogue number in a folder as belonging to that folder, which is false
    for co-starred work: it wrote 28 aliases onto 森田花梨, most of them
    unrelated co-stars. Nothing here is written without a preview.

Measured on three real folders:

    森田花梨   47 lookups / 15 performers   top 白石美里 (33, 70%)
    星川優子   30 lookups /  2 performers   top 星川優子 (29, 97%)
    井上さくら    5 lookups /  1 performer    top 井上さくら (5, 100%)

Note the first one: 白石美里 is 森田花梨 — the same performer's pre- and
post-retirement stage names. So the *winner* there was right and only the
long tail was junk, which is why a strong majority under a different name is
offered as a suggestion rather than discarded. Renamed performers are common
enough in this library to matter.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import re
import sqlite3
import time
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import gfriends, javdb, media

log = logging.getLogger("vam")

MIN_FILMS = 3
MIN_SHARE = 0.60

# ── translation ──────────────────────────────────────────────────────────────
# A fixed table, not a translation service: birthplace is one of 47
# prefectures, so a table is deterministic, offline and free. Anything missing
# passes through untouched and is logged, so the table grows from real data
# instead of guesswork.
_ZH = {
    # blood / misc
    "a": "A 型", "b": "B 型", "o": "O 型", "ab": "AB 型",
    # prefectures — birthplace is one of these, or "Japan"
    "japan": "日本", "tokyo": "東京都", "osaka": "大阪府", "kyoto": "京都府",
    "hokkaido": "北海道", "aomori": "青森縣", "iwate": "岩手縣",
    "miyagi": "宮城縣", "akita": "秋田縣", "yamagata": "山形縣",
    "fukushima": "福島縣", "ibaraki": "茨城縣", "tochigi": "栃木縣",
    "gunma": "群馬縣", "saitama": "埼玉縣", "chiba": "千葉縣",
    "kanagawa": "神奈川縣", "niigata": "新潟縣", "toyama": "富山縣",
    "ishikawa": "石川縣", "fukui": "福井縣", "yamanashi": "山梨縣",
    "nagano": "長野縣", "gifu": "岐阜縣", "shizuoka": "靜岡縣",
    "aichi": "愛知縣", "mie": "三重縣", "shiga": "滋賀縣", "hyogo": "兵庫縣",
    "nara": "奈良縣", "wakayama": "和歌山縣", "tottori": "鳥取縣",
    "shimane": "島根縣", "okayama": "岡山縣", "hiroshima": "廣島縣",
    "yamaguchi": "山口縣", "tokushima": "德島縣", "kagawa": "香川縣",
    "ehime": "愛媛縣", "kochi": "高知縣", "fukuoka": "福岡縣",
    "saga": "佐賀縣", "nagasaki": "長崎縣", "kumamoto": "熊本縣",
    "oita": "大分縣", "miyazaki": "宮崎縣", "kagoshima": "鹿兒島縣",
    "okinawa": "沖繩縣",
}

_missing: set = set()


def zh(term: str) -> str:
    """Translate one closed-set term; unknown terms survive unchanged."""
    t = (term or "").strip()
    hit = _ZH.get(t.lower())
    if hit:
        return hit
    if t and t not in _missing:
        _missing.add(t)
        log.info("idol profile: no Chinese for %r — passing through", t)
    return t


def missing_terms() -> list:
    return sorted(_missing)


# ── page parsing ─────────────────────────────────────────────────────────────
# The profile is a run of "<b>Label:</b> value" separated by " - ", with the
# values sometimes wrapped in filter links. Unknown fields are simply ignored.
_FIELD = re.compile(
    r"<b>\s*([A-Za-z][A-Za-z ()]{1,20}?)\s*:\s*</b>(.{0,160}?)(?=<b|<br|</p)",
    re.S)


def _clean(html: str) -> str:
    txt = re.sub(r"<[^>]+>", "", html)
    return re.sub(r"\s+", " ", txt).strip(" - \t")


def parse_profile(html: str) -> dict:
    """Wanted fields only. '?' means javdatabase has no value — skip it."""
    raw: Dict[str, str] = {}
    for m in _FIELD.finditer(html):
        val = _clean(m.group(2))
        if val and val != "?":
            raw[m.group(1).strip().lower()] = val

    out = {}
    if raw.get("dob") and raw["dob"] != "?":
        out["dob"] = raw["dob"]
    if raw.get("height"):
        out["height"] = raw["height"]
    if raw.get("measurements"):
        out["measurements"] = raw["measurements"]
    if raw.get("cup"):
        out["cup"] = raw["cup"]
    if raw.get("birthplace"):
        out["birthplace"] = zh(raw["birthplace"])
    return out


# ── age ──────────────────────────────────────────────────────────────────────
# javdatabase prints an age, but storing it would be wrong within a year.
# Store the date of birth and derive. Many entries are year-only
# ("2000-??-??"), which can still give an approximate age.

def parse_dob(dob: str) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    m = re.match(r"^(\d{4})-(\?\?|\d{2})-(\?\?|\d{2})$", (dob or "").strip())
    if not m:
        return (None, None, None)
    y = int(m.group(1))
    if not 1900 <= y <= _dt.date.today().year:
        return (None, None, None)
    mo = None if m.group(2) == "??" else int(m.group(2))
    d = None if m.group(3) == "??" else int(m.group(3))
    if mo and d:
        try:
            _dt.date(y, mo, d)          # rejects 2000-13-40 and 2001-02-29
        except ValueError:
            return (y, None, None)      # keep the year, drop the bad day
    elif mo and not 1 <= mo <= 12:
        return (y, None, None)
    return (y, mo, d)


def age_from_dob(dob: str, today: Optional[_dt.date] = None) -> Optional[dict]:
    """{'age': n, 'approx': bool} — approximate when the day is unknown."""
    y, mo, d = parse_dob(dob)
    if not y:
        return None
    today = today or _dt.date.today()
    if mo and d:
        age = today.year - y - ((today.month, today.day) < (mo, d))
        return {"age": age, "approx": False}
    # year only: the true age is one of two values, so say "about"
    return {"age": today.year - y, "approx": True}


# ── who is this folder? ──────────────────────────────────────────────────────

def vote_performer(folders: List[Path], folder_name: str = "",
                   aliases: Optional[List[str]] = None) -> dict:
    """Work out who a folder belongs to, from the catalogue numbers in it."""
    ids = []
    for f in folders:
        for path, _size, _mtime in media.scan_videos_with_stat(f):
            vid = media.detect_video_id(path.name)
            if vid:
                ids.append(vid)
    ids = list(dict.fromkeys(ids))          # de-duplicate, keep order

    votes: Counter = Counter()
    slugs: Dict[str, str] = {}
    checked = 0
    for vid in ids:
        try:
            res = javdb.lookup_movie(vid)
        except Exception as e:
            log.info("idol profile: lookup %s failed: %s", vid, e)
            continue
        if not res or res.get("status") != "ok":
            continue
        checked += 1
        for p in res["performers"]:
            key = p["slug"]
            votes[key] += 1
            slugs[key] = p.get("jp") or p.get("romaji") or key

    if not votes:
        return {"ok": False, "reason": "沒有查到任何演員", "films": len(ids),
                "checked": checked, "votes": []}

    top, n = votes.most_common(1)[0]
    share = n / max(checked, 1)
    breakdown = [{"slug": s, "name": slugs[s], "n": c}
                 for s, c in votes.most_common(6)]
    base = {"films": len(ids), "checked": checked, "votes": breakdown,
            "slug": top, "name": slugs[top], "n": n, "share": share}

    # The vote alone is the wrong test. Measured on the real library:
    #
    #   Reina Kiritani   1/1   voted "Reina Kiritani"  ← obviously right
    #   一ノ瀬みう        2/2   voted "一ノ瀬みう"       ← obviously right
    #   森田花梨       33/47  voted "白石美里"        ← 70%, a different name
    #
    # A count threshold rejects the first two and waves the third through. The
    # name is what settles identity, so it decides; the count only matters when
    # the name does not match. And the third case is not necessarily wrong —
    # 白石美里 is the same performer after a rename — so it becomes a suggestion
    # for a human rather than either an automatic write or a silent drop.
    if name_matches(slugs[top], romaji_of(top), folder_name, aliases):
        return {**base, "ok": True, "why": "name"}
    if checked >= MIN_FILMS and share >= MIN_SHARE:
        # A strong majority under a different name. Often this is the same
        # performer renamed (森田花梨 / 白石美里), sometimes a misfiled folder —
        # only the user can tell, so offer it and let them decide.
        return {**base, "ok": False, "suggest": True,
                "reason": f"多數是「{slugs[top]}」（{n}/{checked}），"
                          f"名稱與資料夾不同 —— 可能是改藝名，請確認"}
    return {**base, "ok": False,
            "reason": f"票數不足或分散（{n}/{checked}）"}


def romaji_of(slug: str) -> str:
    try:
        return javdb.idol_romaji(slug)
    except Exception:
        return ""


def _norm(s: str) -> str:
    return re.sub(r"[\s・･·.,'’\-_]+", "", (s or "")).lower()


def name_matches(jp: str, romaji: str, folder: str, aliases: List[str]) -> bool:
    """Is the voted performer the person this folder is named after?

    Compared loosely on punctuation and case only. Variant spellings
    (井上さくら vs 井上桜) deliberately do not match — those go to the user.
    """
    want = {_norm(folder)} | {_norm(a) for a in (aliases or []) if a}
    want.discard("")
    return bool(want & {_norm(jp), _norm(romaji)} - {""})


# ── proposal / apply ─────────────────────────────────────────────────────────
# Nothing is written without the user seeing it first. person_info.json lives
# on the NAS, outside git, and this same code path once wrote 28 wrong aliases.

def fetch_profile(slug: str, refresh: bool = False) -> dict:
    """Parsed profile for one performer, cached so a re-scan costs nothing."""
    if not refresh:
        cached = javdb.cached_profile(slug)
        if cached is not None:
            try:
                return json.loads(cached) if cached else {}
            except Exception:
                pass
    html = javdb.idol_page(slug)
    data = parse_profile(html) if html else {}
    javdb.remember_profile(slug, json.dumps(data, ensure_ascii=False))
    return data


def build_proposal(name: str, folders: List[Path], recheck: bool = False) -> dict:
    """What this character's profile would become. Writes nothing.

    `recheck` (the dialog's 「也查詢已有的個資與大頭照」) also offers what is
    already there: a field whose new value differs is listed as current → new
    for the user to tick one by one, and an existing avatar gets the Gfriends
    choices with none preselected. Without it, only what is empty is filled.
    """
    existing0 = media.load_person_info(folders[0])
    vote = vote_performer(folders, name, existing0.get("aliases") or [])
    row = {"name": name, "paths": [str(f) for f in folders],
           "ok": vote["ok"], "vote": vote}
    # Avatar choices only for characters without one (the user's call): the
    # rest of this tool only ever fills what is empty, and so does this.
    # Looked up even when the vote is undecided — the folder's own name and
    # aliases are the same evidence either way. The vote's winner is not: when
    # undecided it can be a co-star, so it only joins once it is a candidate.
    current_photo = existing0.get("profile_photo") or ""
    want_avatar = not current_photo or recheck
    if want_avatar:
        _avatar_choices(row, name, existing0, current_photo,
                        [vote.get("name")] if vote["ok"] or vote.get("suggest")
                        else [])
    # A "suggest" row is a real candidate the user has to rule on, so it needs
    # the same payload as an accepted one — otherwise confirming it has
    # nothing to write, which is exactly what happened: the row carried no
    # profile, no fields and no has_changes, so the write button stayed dead.
    # Only a genuinely unresolved row stops here.
    if not vote["ok"] and not vote.get("suggest"):
        # kept in the report rather than dropped: the user asked to handle
        # these by hand, and a silently missing character looks like a bug
        row["has_changes"] = bool(row.get("avatar"))
        return row

    profile = fetch_profile(vote["slug"])
    romaji = javdb.idol_romaji(vote["slug"])
    row["performer"] = vote["name"]
    row["romaji"] = romaji
    row["profile"] = profile
    if want_avatar and romaji:
        # the romaji is the one name the folder did not already have
        _avatar_choices(row, name, existing0, current_photo,
                        [vote.get("name"), romaji])

    existing = existing0
    old = existing.get("profile") or {}
    row["fields"] = []
    for key, val in profile.items():
        cur = old.get(key)
        # never overwrite unasked: what is already there was either put there
        # by hand or checked once already. With `recheck` a *different* value
        # is offered as a replacement, which only an explicit tick writes.
        replace = bool(cur) and recheck and cur != val
        row["fields"].append({
            "key": key, "value": val, "current": cur,
            "skip": bool(cur) and not replace,
            "replace": replace,
        })
    aliases = existing.get("aliases") or []
    row["new_aliases"] = [a for a in (romaji, vote["name"])
                          if a and a != name and a not in aliases]
    row["has_changes"] = (any(not f["skip"] for f in row["fields"])
                          or bool(row["new_aliases"]) or bool(row.get("avatar")))
    return row


def _avatar_choices(row: dict, name: str, info: dict, current: str,
                    extra: list):
    """Gfriends pictures for this character, with the first good one
    preselected (the user's call) — never one found by a weak name, and never
    when the character already has an avatar (`current`, a recheck): then a
    picture replaces it only if the user clicks one."""
    try:
        names = gfriends.name_chain([name], info.get("aliases") or [],
                                    [x for x in extra if x])
        choices = gfriends.candidates(names)
    except Exception as e:
        log.warning("gfriends lookup failed for %s: %s", name, e)
        return
    if choices:
        row["avatar"] = {"choices": choices,
                         "pick": None if current else gfriends.preselect(choices),
                         "current": current,
                         "current_path": (str(Path(row["paths"][0]) / current)
                                          if current else "")}
    else:
        row.pop("avatar", None)


def apply_proposal(row: dict) -> dict:
    """Write one approved proposal. Adds what is empty; replaces a value only
    where the user ticked that replacement (a recheck)."""
    folder = Path(row["paths"][0])
    info = media.load_person_info(folder)
    profile = dict(info.get("profile") or {})
    written = []
    for f in row.get("fields", []):
        if f.get("skip") or f.get("approved") is False:
            continue
        # a replacement is written only when ticked, never by default
        if f.get("replace") and f.get("approved") is not True:
            continue
        profile[f["key"]] = f["value"]
        written.append(f["key"])
    if written:
        info["profile"] = profile
        info["profile_source"] = {
            "site": "javdatabase.com",
            "slug": row["vote"]["slug"],
            "at": time.time(),
        }
    aliases = list(info.get("aliases") or [])
    added = [a for a in row.get("new_aliases", []) if a not in aliases]
    if added:
        info["aliases"] = aliases + added
    if written or added:
        media.save_person_info(folder, info)
    return {"name": row["name"], "fields": written, "aliases": added}


# Fields the profile block may contain. A hand edit goes through the same
# whitelist as a fetch, so the UI cannot invent keys and a malformed request
# cannot turn person_info.json into a junk drawer.
# The site's own "Tags" (Twenties, Slim...) are deliberately not here: they
# were vague and near-identical between performers, so they earned no space.
PROFILE_KEYS = ("dob", "height", "measurements", "cup", "birthplace")


def clean_profile(data: dict) -> dict:
    out = {}
    for k in PROFILE_KEYS:
        v = (data or {}).get(k)
        if isinstance(v, str) and v.strip():
            out[k] = v.strip()[:80]
    return out


# ── remembering what happened ────────────────────────────────────────────────
# Re-running is cheap (a processed character re-resolves in ~0 s from cache),
# so this is not a performance cache — it is memory. Without it the picker
# cannot say which characters are already done, and a suggestion the user
# turned down would be offered again on every run.
#
# Kept in our own table rather than in person_info.json: a rejection is part
# of this tool's workflow, not part of the user's data, and those files live
# on the NAS.

_DEC_SCHEMA = """
CREATE TABLE IF NOT EXISTS idol_decision (
    name      TEXT PRIMARY KEY,
    status    TEXT,      -- ok | suggest | unresolved
    slug      TEXT,      -- who the last scan picked
    rejected  TEXT,      -- JSON list of slugs the user said no to
    at        REAL
);
"""


def _dconn():
    conn = sqlite3.connect(str(javdb.DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.executescript(_DEC_SCHEMA)
    return conn


def record_scan(name: str, status: str, slug: str = ""):
    conn = _dconn()
    try:
        conn.execute(
            "INSERT INTO idol_decision (name, status, slug, rejected, at) "
            "VALUES (?,?,?,COALESCE((SELECT rejected FROM idol_decision "
            "WHERE name=?),'[]'),?) "
            "ON CONFLICT(name) DO UPDATE SET status=excluded.status, "
            "slug=excluded.slug, at=excluded.at",
            (name, status, slug, name, time.time()))
        conn.commit()
    finally:
        conn.close()


def set_rejected(name: str, slug: str, rejected: bool):
    """Remember that this performer is (not) the person in this folder."""
    conn = _dconn()
    try:
        row = conn.execute("SELECT rejected FROM idol_decision WHERE name=?",
                           (name,)).fetchone()
        try:
            cur = json.loads(row["rejected"]) if row and row["rejected"] else []
        except Exception:
            cur = []
        if rejected and slug not in cur:
            cur.append(slug)
        elif not rejected and slug in cur:
            cur.remove(slug)
        conn.execute(
            "INSERT INTO idol_decision (name, status, slug, rejected, at) "
            "VALUES (?,'',' ',?,?) ON CONFLICT(name) DO UPDATE SET "
            "rejected=excluded.rejected",
            (name, json.dumps(cur), time.time()))
        conn.commit()
        return cur
    finally:
        conn.close()


def decisions() -> Dict[str, dict]:
    conn = _dconn()
    try:
        out = {}
        for r in conn.execute("SELECT * FROM idol_decision"):
            try:
                rej = json.loads(r["rejected"]) if r["rejected"] else []
            except Exception:
                rej = []
            out[r["name"]] = {"status": r["status"] or "", "slug": r["slug"] or "",
                              "rejected": rej, "at": r["at"] or 0}
        return out
    finally:
        conn.close()


def character_state(name: str, info: dict, dec: dict) -> str:
    """What the picker shows: done / rejected / unresolved / none.

    "done" comes from the file itself rather than our table, so a profile
    filled in by hand counts too — the question the user is asking is "does
    this character still need attention", not "did this tool touch it".
    """
    if info.get("profile_source") or info.get("profile"):
        return "done"
    d = dec.get(name) or {}
    if d.get("slug") and d["slug"] in (d.get("rejected") or []):
        return "rejected"
    if d.get("status") in ("suggest", "unresolved"):
        return "unresolved"
    return "none"
