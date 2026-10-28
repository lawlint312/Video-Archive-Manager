"""Saved AVBase pages (avbase.net): titles, cast, stage names — and which film
is cut from which.

AVBase sits behind Cloudflare and answers scripts with a challenge (measured
403 from this machine, the same with Peach's exact request), so it is read the
way the wiki is: the user saves the page in their browser, VAM reads the file.
Next.js embeds the page's data as JSON (`__NEXT_DATA__`), so nothing here
scrapes markup. Two kinds of page are understood:

  * a talent page `/talents/<name>?page=N` — the performer's stage names with
    their readings, a profile, and 30 works per page, each with the works it
    was cut from (`relworks.parents`, numbers only) and the ones cut from it
  * a work page `/works/<maker>:<number>` — one work, with its parents and
    children in full (titles and release dates)

**The relation is the reason to read AVBase at all.** The same footage is sold
again under another number, title and name: JDG-112 「みう」 (2026) is cut
from MAAN-239 (2018); AKO-599 「YURIさん 2発目」 from UFD-061; of the 30 newest
works on 森田花梨's page, 28 had a parent. But AVBase records a compilation the
same way — `VRKM-1814` 「BEST 55人5時間」 is the parent of twenty full films —
so a parent is not always the bigger thing. `judge()` decides by date:

  parent released before the child   the original; the child is an excerpt
  parent after, uncut (ノーカット…)     a compilation holding the child whole
  parent after, otherwise             a digest — the child is the fuller one
  a date unknown                      no verdict

**A work's title is its own product's**, not the work's `title` field: AVBase
takes that from one of the products, which can be a compilation or a remaster
(UFD-061's reads 「【4Kリマスター版】…」; Peach met `259LUXU-1514` titled as the
FANZA compilation it is in). A product counts as this work's own when its id
has the same letters and number (`ako599`, `210AKO-599`; not `244kufd00061`).
"""
from __future__ import annotations

import json
import re
from typing import Dict, List, Optional

from . import media

_NEXT = re.compile(rb'<script id="__NEXT_DATA__" type="application/json"[^>]*>(.*?)</script>',
                   re.S)
_DATE = re.compile(r"^[A-Za-z]{3} ([A-Za-z]{3}) (\d{1,2}) (\d{4})")
_MONTHS = {m: i for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul",
                                       "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}
# a product id: label digits (`210`, `24`, `h_1462`), letters, number, suffix
_PRODUCT = re.compile(r"^(?:h_)?\d*([A-Za-z]+?)[-_]?0*(\d{1,6})([A-Za-z]*)$")
_CODE = re.compile(r"^([A-Za-z]+)-0*(\d+)")
# a compilation that says it holds its parts whole
_UNCUT = re.compile(r"ノーカット|完全収録")
PRODUCT_ORDER = ("fanza", "mgstage", "duga")


def looks_like_avbase(raw: bytes) -> bool:
    return b"__NEXT_DATA__" in raw and b"avbase" in raw[:400000].lower()


def listing_date(raw) -> str:
    """`Sat Mar 07 2026 00:00:00 GMT+0900` is already Japan's day."""
    m = _DATE.match(str(raw or ""))
    if not m or m.group(1) not in _MONTHS:
        return ""
    return f"{m.group(3)}-{_MONTHS[m.group(1)]:02d}-{int(m.group(2)):02d}"


def canonical(work_id: str) -> str:
    """The number as VAM's own detection writes it (`UFD-061`), so it meets
    the library's files; anything it cannot read stays as AVBase wrote it."""
    w = str(work_id or "").strip()
    return (media.detect_video_id(w + ".mp4") or w).upper()


def _letters_num(code: str):
    m = _CODE.match(code or "")
    return (m.group(1).lower(), int(m.group(2))) if m else (None, None)


def own_products(work: dict) -> List[dict]:
    """This work's own products, FANZA first (see the module docstring)."""
    letters, num = _letters_num(str(work.get("work_id") or ""))
    out = []
    for p in work.get("products") or []:
        m = _PRODUCT.match(str(p.get("product_id") or ""))
        if m and letters and m.group(1).lower() == letters and int(m.group(2)) == num:
            out.append(p)
    rank = {s: i for i, s in enumerate(PRODUCT_ORDER)}
    return sorted(out, key=lambda p: rank.get(p.get("source"), len(rank)))


def _names(entries) -> List[str]:
    out = []
    for e in entries or []:
        n = ((e.get("actor") or {}).get("name") if "actor" in e else e.get("name")) or ""
        n = n.strip()
        if n and n not in out:
            out.append(n)
    return out


def _work(w: dict) -> dict:
    own = own_products(w)
    title = next((str(p.get("title") or "").strip() for p in own if p.get("title")), "")
    date = next((listing_date(p.get("date")) for p in own if listing_date(p.get("date"))), "")
    return {"id": canonical(w.get("work_id")),
            "title": " ".join((title or str(w.get("title") or "")).split()),
            "date": date or listing_date(w.get("min_date")),
            "cast": _names(w.get("casts") or w.get("actors")),
            "own_title": bool(title)}


def judge(parent_date: str, child_date: str, parent_title: str = "") -> str:
    """original | uncut | digest | unknown — see the module docstring."""
    if not parent_date or not child_date or parent_date == child_date:
        return "unknown"
    if parent_date < child_date:
        return "original"
    return "uncut" if _UNCUT.search(parent_title or "") else "digest"


def _relation(parent: dict, child: dict) -> dict:
    return {"parent": parent["id"], "child": child["id"],
            "parent_date": parent.get("date", ""), "child_date": child.get("date", ""),
            "parent_title": parent.get("title", ""), "child_title": child.get("title", ""),
            "kind": judge(parent.get("date", ""), child.get("date", ""),
                          parent.get("title", ""))}


def _bare(entry: dict) -> dict:
    """A related work that the page names by number only."""
    return {"id": canonical(entry.get("work_id")), "title": "", "date": "", "cast": []}


def parse(raw: bytes) -> dict:
    """{kind, name, url, aliases, profile, works, relations, page, total}.
    ValueError when the file is not a talent or work page."""
    # A talent page is read from what is on screen, not from the JSON: the
    # JSON is the page the tab *first* loaded (Next.js moves between pages
    # without replacing it), and a page merged by the user's bookmarklet has
    # all sixteen pages' cards on screen but only one page's JSON.
    if "/talents/" in saved_from(raw) and _GRID in raw:
        return parse_screen(raw)
    m = _NEXT.search(raw)
    if not m:
        raise ValueError("檔案裡沒有頁面資料（__NEXT_DATA__），可能不是完整存檔的 AVBase 頁面")
    data = json.loads(m.group(1).decode("utf-8", "replace"))
    page = data.get("page") or ""
    pp = (data.get("props") or {}).get("pageProps") or {}
    q = data.get("query") or {}

    if page.startswith("/works/"):
        w = pp.get("work") or {}
        me = _work(w)
        parents = [_work(x) for x in pp.get("parents") or []]
        children = [_work(x) for x in pp.get("children") or []]
        rels = ([_relation(p, me) for p in parents]
                + [_relation(me, c) for c in children])
        key = f"{w['prefix']}:{w.get('work_id')}" if w.get("prefix") else str(w.get("work_id"))
        return {"kind": "work", "name": f"{me['id']} {me['title']}".strip(),
                "url": f"https://www.avbase.net/works/{key}",
                "aliases": [], "profile": {}, "talent": "",
                "works": [me] + parents + children, "relations": rels,
                "page": 0, "total": 1}

    if page.startswith("/talents/"):
        t = pp.get("talent") or {}
        primary = (t.get("primary") or {}).get("name") or q.get("name") or ""
        aliases = []
        for a in t.get("actors") or []:
            n = (a.get("name") or "").strip()
            if n and n != primary and n not in aliases:
                aliases.append(n)
        works = [_work(x) for x in pp.get("works") or []]
        by_id = {w["id"]: w for w in works}
        rels = []
        for raw_w, me in zip(pp.get("works") or [], works):
            rw = raw_w.get("relworks") or {}
            for p in rw.get("parents") or []:
                rels.append(_relation(by_id.get(canonical(p.get("work_id"))) or _bare(p), me))
            for c in rw.get("children") or []:
                rels.append(_relation(me, by_id.get(canonical(c.get("work_id"))) or _bare(c)))
        return {"kind": "talent", "name": primary,
                "url": f"https://www.avbase.net/talents/{q.get('name') or primary}"
                       + (f"?page={q['page']}" if q.get("page") else ""),
                "aliases": aliases, "profile": profile(t), "talent": primary,
                "works": works, "relations": rels,
                "page": int(pp.get("page") or q.get("page") or 1),
                "total": int(pp.get("total") or len(works))}

    raise ValueError(f"不是作品頁或演員頁（{page or '不明的頁面'}）")


def profile(talent: dict) -> Dict[str, str]:
    """`idolprofile.PROFILE_KEYS`, written the way the javdatabase import
    writes them (`153 cm`, `88-57-82`, `F`), so the two never disagree."""
    b = ((talent.get("meta") or {}).get("basic_info")) or {}
    out = {}
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(b.get("birthday") or "")):
        out["dob"] = b["birthday"]
    if str(b.get("height") or "").isdigit():
        out["height"] = f"{b['height']} cm"
    if all(str(b.get(k) or "").isdigit() for k in ("bust", "waist", "hip")):
        out["measurements"] = f"{b['bust']}-{b['waist']}-{b['hip']}"
    if re.fullmatch(r"[A-Za-z]{1,2}", str(b.get("cup") or "")):
        out["cup"] = b["cup"].upper()
    if b.get("prefectures"):
        out["birthplace"] = str(b["prefectures"]).replace("県", "縣")
    return out


# ── reading the page as shown ────────────────────────────────────────────────
# Measured on a merged 森田花梨 page (461 cards from 16 pages): every card
# carries its number, date, title and cast, and up to two strips of related
# works — the plain strip is the works it is in (parents), the right-aligned
# (`flex-row-reverse`) strip the works cut from it (children). Cross-checked
# over the whole file: 76 of 76 parent links and 67 of 67 child links agree
# with the other work's own card.

_GRID = b"sm:grid-cols-2 gap-4"
_CARD = re.compile(r'(?=<div class="bg-background border border-light rounded-lg '
                   r'overflow-hidden h-full">)')
_C_ID = re.compile(r'<span class="font-bold text-gray-500">([^<]+)</span>')
_C_DATE = re.compile(r'href="https://www\.avbase\.net/works/date/(\d{4}-\d{2}-\d{2})"')
# the title is the card's button-styled link to the work; its class list ends
# in `line-clamp-3` or `line-clamp-5` by title length, so it is not matched on
# that (matching `-3` missed 1,113 of 1,730 titles). The date link is not a
# button, and points at /works/date/.
_C_TITLE = re.compile(r'<a data-slot="button"[^>]*?href="https://www\.avbase\.net/works/'
                      r'(?!date/)[^"]+"[^>]*>(.*?)</a>', re.S)
_C_CAST = re.compile(r'href="https://www\.avbase\.net/talents/([^"?#]+)"')
_C_STRIP = re.compile(r'<div class="flex flex-wrap h-14 gap-0 bg-muted overflow-hidden grow ?'
                      r'([^"]*)">(.*?)</div>', re.S)
_C_LINK = re.compile(r'href="https://www\.avbase\.net/works/([^"]+)"')
_KANA = re.compile(r"^[\u3040-\u309f\u30fc・ ]+$")


def saved_from(raw: bytes) -> str:
    """The address the browser saved the file from (its own comment, else the
    canonical link) — the JSON may describe an earlier page."""
    head = raw[:200000].decode("utf-8", "replace")
    m = (re.search(r"saved from url=\(\d+\)(\S+)", head)
         or re.search(r'rel="canonical" href="([^"]+)"', head))
    return m.group(1) if m else ""


def _text(fragment: str) -> str:
    import html
    return " ".join(html.unescape(re.sub(r"<[^>]+>", "", fragment)).split())


def _tokens(fragment: str) -> List[str]:
    import html
    fragment = re.sub(r"<(style|script)\b.*?</\1>", "", fragment, flags=re.S)
    return [t for t in (" ".join(html.unescape(x).split())
                        for x in re.split(r"<[^>]+>", fragment)) if t]


def _sidebar(head: str):
    """(name, other names, profile) from the talent column left of the works."""
    tok = _tokens(head)
    try:
        start = tok.index("作品コメント") + 1
        end = tok.index("フォロー", start)
    except ValueError:
        return "", [], {}
    # name, then its reading in kana, then perhaps a note in parentheses;
    # a kana word *not* right after a name is a name itself (かりんまん)
    names, after_name = [], False
    for t in tok[start:end]:
        if t.startswith("(") or t.startswith("（"):
            after_name = False
        elif after_name and _KANA.match(t):
            after_name = False
        else:
            if t not in names:
                names.append(t)
            after_name = True
    info = {}
    try:
        b = tok.index("基本情報")
        rest = tok[b + 1:]
        for i, t in enumerate(rest[:-1]):
            if t.startswith("最終更新"):
                break
            info.setdefault(t, rest[i + 1])
    except ValueError:
        pass
    prof = {}
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", info.get("生年月日", "")):
        prof["dob"] = info["生年月日"]
    m = re.fullmatch(r"(\d{2,3})\s*cm", info.get("身長", ""))
    if m:
        prof["height"] = f"{m.group(1)} cm"
    m = re.search(r"B(\d+)(?:\(([A-Za-z]{1,2})\))?\s*W(\d+)\s*H(\d+)", info.get("サイズ", ""))
    if m:
        prof["measurements"] = f"{m.group(1)}-{m.group(3)}-{m.group(4)}"
        if m.group(2):
            prof["cup"] = m.group(2).upper()
    if info.get("出身地") and info["出身地"] not in ("-", "－"):
        prof["birthplace"] = info["出身地"].replace("県", "縣")
    total = 0
    for i, t in enumerate(tok[:-1]):
        if t == "単体" and tok[i + 1].isdigit():
            total = int(tok[i + 1])
            break
    return (names[0] if names else ""), names[1:], prof, total


def parse_screen(raw: bytes) -> dict:
    """A talent page — one page or all of them merged — read from its cards."""
    from urllib.parse import unquote
    text = raw.decode("utf-8", "replace")
    at = text.find(_GRID.decode())
    name, aliases, prof, total = _sidebar(text[:at])
    works, parents, children = {}, {}, {}
    for card in _CARD.split(text[at:])[1:]:
        m = _C_ID.search(card)
        if not m:
            continue
        wid = canonical(m.group(1))
        if wid in works:
            continue            # the same work twice (pages overlapped)
        d = _C_DATE.search(card)
        t = _C_TITLE.search(card)
        cast = []
        for c in _C_CAST.findall(card):
            n = unquote(c).strip()
            if n and n not in cast:
                cast.append(n)
        works[wid] = {"id": wid, "title": _text(t.group(1)) if t else "",
                      "date": d.group(1) if d else "", "cast": cast, "own_title": True}
        area = card.split('<div class="flex bg-muted pl-2">', 1)
        for cls, inner in (_C_STRIP.findall(area[1][:8000]) if len(area) > 1 else []):
            ids = [canonical(k.split(":")[-1]) for k in _C_LINK.findall(inner)]
            (children if "flex-row-reverse" in cls else parents).setdefault(wid, []).extend(ids)
    rels, seen = [], set()

    def add(parent, child):
        if (parent, child) in seen or parent == child:
            return
        seen.add((parent, child))
        rels.append(_relation(works.get(parent) or {"id": parent},
                              works.get(child) or {"id": child}))
    for wid, ps in parents.items():
        for p in ps:
            add(p, wid)
    for wid, cs in children.items():
        for c in cs:
            add(wid, c)
    return {"kind": "talent", "name": name, "url": saved_from(raw),
            "aliases": aliases, "profile": prof, "talent": name,
            "works": list(works.values()), "relations": rels,
            "page": 0, "total": total or len(works)}


def relation_counts(relations: List[dict], have: set,
                    on_page: set = frozenset()) -> Dict[str, int]:
    """How many relations touch the library, for the preview's summary.

    `on_page`: the works the page lists — hers. A compilation of fifty-five
    performers relates to every one of their films, and those other films are
    not on her page and have no date here: measured on 森田花梨's full page,
    2,418 of 2,786 relations were like that, and of the 368 between two of
    her own works only one could not be judged. Counted apart, so the summary
    is not a wall of 「無法判斷」 about other people's films.
    """
    hers = [r for r in relations if r["parent"] in on_page and r["child"] in on_page]
    return {"total": len(relations),
            "hers": len(hers),
            "hers_judged": sum(1 for r in hers if r["kind"] != "unknown"),
            "both": sum(1 for r in relations if r["parent"] in have and r["child"] in have),
            "redundant": sum(1 for r in relations if r["parent"] in have
                             and r["child"] in have and r["kind"] in ("original", "uncut"))}


# ── stored relations (in javdb.sqlite3, beside the titles) ───────────────────
# Every relation a page shows is kept, not only the ones touching the library:
# a relation is a fact about two films, and the one you lack today is the one
# you download tomorrow. A later page can only add what an earlier one lacked
# (a date, a title) — the verdict is worked out again from the merged row.

_SCHEMA = """
CREATE TABLE IF NOT EXISTS work_relation (
    parent       TEXT NOT NULL,   -- the work the footage is in (UFD-061)
    child        TEXT NOT NULL,   -- the work cut from it, or held by it (AKO-599)
    parent_date  TEXT,
    child_date   TEXT,
    parent_title TEXT,
    child_title  TEXT,
    kind         TEXT NOT NULL,   -- original | uncut | digest | unknown
    source       TEXT,
    at           REAL NOT NULL,
    PRIMARY KEY (parent, child)
);
"""


def _connect():
    from . import javdb
    conn = javdb._connect()
    conn.executescript(_SCHEMA)
    return conn


def save_relations(relations: List[dict], source: str = "") -> int:
    import time
    conn = _connect()
    try:
        for r in relations:
            old = conn.execute("SELECT * FROM work_relation WHERE parent = ? AND child = ?",
                               (r["parent"], r["child"])).fetchone()
            old = dict(old) if old else {}
            row = {k: r.get(k) or old.get(k) or "" for k in
                   ("parent_date", "child_date", "parent_title", "child_title")}
            conn.execute(
                "INSERT OR REPLACE INTO work_relation (parent, child, parent_date, child_date,"
                " parent_title, child_title, kind, source, at) VALUES (?,?,?,?,?,?,?,?,?)",
                (r["parent"], r["child"], row["parent_date"], row["child_date"],
                 row["parent_title"], row["child_title"],
                 judge(row["parent_date"], row["child_date"], row["parent_title"]),
                 source or old.get("source") or "", time.time()))
        conn.commit()
    finally:
        conn.close()
    return len(relations)


def relations() -> List[dict]:
    conn = _connect()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM work_relation")]
    finally:
        conn.close()


def relations_by_work() -> Dict[str, List[dict]]:
    """{number: [{o: the other number, k: verdict, p: True when this one is
    the parent}]} — every relation, seen from both ends, for the video list's
    cards. Short keys: a compilation can relate to fifty films."""
    out: Dict[str, List[dict]] = {}
    for r in relations():
        out.setdefault(r["child"], []).append({"o": r["parent"], "k": r["kind"], "p": False})
        out.setdefault(r["parent"], []).append({"o": r["child"], "k": r["kind"], "p": True})
    return out


def relations_of(vid: str) -> List[dict]:
    """Every stored relation of one film, from its side: [{other, kind,
    this_is ('parent'|'child'), other_date, other_title}]."""
    vid = (vid or "").upper()
    conn = _connect()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM work_relation WHERE parent = ? OR child = ?", (vid, vid))]
    finally:
        conn.close()
    out = []
    for r in rows:
        mine_parent = r["parent"] == vid
        o = "child" if mine_parent else "parent"
        out.append({"other": r[o], "kind": r["kind"],
                    "this_is": "parent" if mine_parent else "child",
                    "other_date": r[f"{o}_date"] or "", "other_title": r[f"{o}_title"] or ""})
    return out


# ── the same footage twice in the library ────────────────────────────────────
# Read straight off the stored relations and the library's index — no scan:
# the relation is already known, so all that is left to find out is which
# numbers are here (the user's call, 2026-10-27).

# the verdicts where the parent holds the child whole, so with the parent here
# the child adds nothing; a digest holds *less* than the child, an unknown has
# no dates to tell
HOLDS_WHOLE = ("original", "uncut")


def redundant_groups(owned: Dict[str, list], rels: List[dict]) -> List[dict]:
    """[{parent, parent_date, children: [{id, date, kind, via}]}] for the
    numbers in `owned` (any non-empty value = here).

    A child is filed under one parent only (an original before a
    compilation, then the oldest), so no file is offered twice; Y ⊃ X ⊃ Z
    with all three here is one group under Y (Z `via` X) — X's own group
    would offer X for deletion in one place and keep it in another. A loop
    (bad data) has no top and is left out."""
    usable = [r for r in rels
              if r.get("kind") in HOLDS_WHOLE and r["parent"] != r["child"]
              and owned.get(r["parent"]) and owned.get(r["child"])]
    usable.sort(key=lambda r: (HOLDS_WHOLE.index(r["kind"]), r.get("parent_date") or "9999"))
    up: Dict[str, dict] = {}
    for r in usable:
        up.setdefault(r["child"], r)

    def top(vid):
        seen = {vid}
        while vid in up and up[vid]["parent"] not in seen:
            vid = up[vid]["parent"]
            seen.add(vid)
        return vid
    children: Dict[str, list] = {}
    for child, r in up.items():
        root = top(child)
        if root != child and top(root) == root:
            children.setdefault(root, []).append(r)
    return [{"parent": p,
             "parent_date": next((r.get("parent_date") for r in rs
                                  if r["parent"] == p and r.get("parent_date")), ""),
             "children": [{"id": r["child"], "date": r.get("child_date") or "",
                           "kind": r["kind"], "via": r["parent"]}
                          for r in sorted(rs, key=lambda r: r["child"])]}
            for p, rs in sorted(children.items())]
