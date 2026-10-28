"""Read a saved seesaawiki performer page into the few fields VAM can use.

Why this exists: `movie_title.ja` has been empty since the column was created,
because both routes to Japanese titles are closed -- javlibrary sits behind a
Cloudflare challenge and DMM is a JS-only SPA that also geo-blocks. This wiki
publishes the same thing in plain HTML: a performer's stage names, and every
one of their releases with its catalogue number and original Japanese title.

**The parser is a whitelist, and that is the privacy boundary.** The user's
instruction was that real-world identity -- legal name, address, contact
details -- is not wanted. That is enforced by structure rather than by
discipline: `parse()` returns exactly the fields below and there is nowhere
for anything else to go, so a page that carries such a section cannot leak it
into `person_info.json` even by accident.

Input is bytes, never str. The page declares `charset=EUC-JP` and that is
not quite true -- see `decode()`.
"""
from __future__ import annotations

import html
import logging
import re
from typing import Optional

log = logging.getLogger("vam.wikiimport")

# In order of preference. `euc_jis_2004` first because the declared charset is
# a trap: the page says EUC-JP, but the content contains NEC extended
# characters (row 13 -- the circled digits and Roman numerals) which plain
# `euc_jp` cannot decode. On the real page that is one byte at offset 234,676,
# inside a game title (闘神都市Ⅲ). Python's `euc_jp` raises there, and the
# obvious "fix" of errors="replace" silently corrupts the character instead --
# which is worse, because the import would look like it worked.
DECODERS = ("euc_jis_2004", "utf-8", "cp932", "euc_jp")


def decode(raw: bytes) -> str:
    """Text of a saved page, trying the encodings this wiki actually uses."""
    for enc in DECODERS:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    # Last resort: the least-lossy of the candidates rather than a hard error,
    # because one bad byte should not cost the other 85 titles on the page.
    log.warning("no decoder matched cleanly; falling back with replacements")
    return raw.decode(DECODERS[0], errors="replace")


_TAG = re.compile(r"(?is)<[^>]+>")
_SCRIPT = re.compile(r"(?is)<(script|style)[^>]*>.*?</\1>")


def _text(fragment: str) -> str:
    """Visible text of an HTML fragment, with entities resolved."""
    return html.unescape(_TAG.sub("", fragment)).strip()


# `2018/04/13 NSPS-694` -- the date is the release date and the id is what
# every other part of this project keys films by.
#
# The trailing `（MADV-277）` is a second catalogue number for the same film:
# the rental and sell editions are numbered separately and the library may hold
# either, so both have to end up pointing at the same title. Measured on the
# real page: one entry in eighty, and without this it was silently dropped.
_ID = r"[A-Za-z][A-Za-z0-9]{1,7}-\d{2,5}"
_HEAD = re.compile(
    rf"^\s*(\d{{4}}/\d{{1,2}}/\d{{1,2}})\s+({_ID})"
    rf"(?:\s*[（(]\s*({_ID})\s*[）)])?\s*$")
_PERFORMERS = re.compile(r"^\s*出演者\s*[：:]\s*(.+)$")
_NOTE = re.compile(r"^\s*※\s*(.+)$")

# A stage name may carry its reading: `美咲エリ（みさきえり）`
_KANA = re.compile(r"^(.+?)\s*[（(]([぀-ゟ゠-ヿー\s]+)[）)]\s*$")


def _split_names(s: str) -> list:
    """`A（あ）／B／C` -> [(A, あ), (B, ''), (C, '')]"""
    out = []
    for part in re.split(r"[／/]", s):
        part = part.strip()
        if not part:
            continue
        m = _KANA.match(part)
        if m:
            out.append((m.group(1).strip(), m.group(2).strip()))
        else:
            out.append((part, ""))
    return out


def _strip_label(title: str) -> tuple:
    """`…赤く染まった乳首…（ながえスタイル）` -> (title, 'ながえスタイル').

    The maker is appended to every title on this wiki in full-width parens.
    Only the *last* group is taken, and only when the title has something left
    afterwards -- a title that is nothing but a parenthesised phrase is a
    title, not a label.
    """
    m = re.match(r"^(.*\S)\s*[（(]([^（()）]{1,40})[）)]\s*$", title)
    if not m:
        return title.strip(), ""
    return m.group(1).strip(), m.group(2).strip()


def page_name(text: str) -> str:
    """The performer this page is about, from the title tag.

    `美咲エリ - 素人系総合 wiki` -> `美咲エリ`. Taken from the markup rather
    than the filename because a saved page can be renamed, and because the
    filename is what the user's browser chose.
    """
    m = re.search(r"(?is)<title>(.*?)</title>", text)
    raw = _text(m.group(1)) if m else ""
    return re.sub(r"\s*[-－]\s*[^-－]*wiki\s*$", "", raw, flags=re.I).strip()


def source_url(text: str) -> str:
    m = re.search(r'(?is)<link[^>]+rel="canonical"[^>]+href="([^"]+)"', text)
    return m.group(1) if m else ""


def parse(raw: bytes) -> dict:
    """Everything VAM can use from one saved performer page.

    Returns only: the performer's name, their other stage names, and one row
    per release (date, catalogue number, Japanese title, maker, co-performers,
    note). Nothing else has a field to live in -- see the module docstring.
    """
    text = decode(raw)
    body = _SCRIPT.sub(" ", text)

    name = page_name(text)
    aliases = _aliases(body)
    works = _works(body)
    return {
        "name": name,
        "url": source_url(text),
        "aliases": [{"name": n, "kana": k} for n, k in aliases],
        "works": works,
    }


def _aliases(body: str) -> list:
    """The stage-name line, which is the first section heading of the page.

    These are the names the performer has released under -- the same kind of
    thing `person_info.json` already stores, and the reason a catalogue lookup
    can find 星川優子 when the folder is named something else.
    """
    m = re.search(r'(?is)<div class="title-1">.*?<h3[^>]*>(.*?)</h3>', body)
    if not m:
        return []
    # Drop the "edit this section" pencil before taking the text, or its title
    # attribute becomes part of the name.
    frag = re.sub(r'(?is)<a[^>]+class="[^"]*part-edit[^"]*".*?</a>', "", m.group(1))
    return _split_names(_text(frag))


# Where the page's own content stops and the site furniture begins. Without
# this the region ran to the end of the document, taking in the comment thread
# and the site-wide menu -- which on this wiki is an index of hundreds of
# releases. It happened not to produce false works on the three pages measured,
# but "it did not bite yet" is not a reason to leave a loaded gun lying around.
_END_MARKERS = ("このページを編集する", "このページへのコメント",
                '<div id="page-footer')

# Section headings inside the page: 単体作品 / 総集編作品 / VR作品 / 配信作品…
# Worth tracking because the totals are meaningless without them -- one
# performer's page listed 742 releases, of which 88 were VR and 94 streaming.
_SECTION = re.compile(r'(?is)<div class="title-[2-5]">(.*?)</div>')

# A release with no catalogue number: streaming and some indie titles have
# none, so the line carries only a date. They cannot ever be matched to a file,
# but they are still releases and leaving them out undercounted every page
# (measured: 83 parsed where the page showed 92).
_DATE_ONLY = re.compile(r"^\s*(\d{4}/\d{1,2}/\d{1,2})\s*$")


def _works(body: str) -> list:
    """One row per release.

    The entries are a flat run of `<br>`-separated lines rather than a list or
    a table, so the shape is: a date line opens a record and the lines under it
    fill it in, until the next one opens.
    """
    m = re.search(r'(?is)<div id="content_block_1-body".*?>', body)
    start = m.end() if m else 0
    end = len(body)
    for mk in _END_MARKERS:
        at = body.find(mk, start)
        if at > 0:
            end = min(end, at)
    region = body[start:end]
    lines = re.split(r"(?i)<br\s*/?>", region)

    out, cur, section = [], None, ""
    for chunk in lines:
        # The section heading arrives inside the chunk, not as its own line,
        # so it has to be read before the text is flattened.
        sec = _SECTION.search(chunk)
        if sec:
            t = _text(re.sub(r'(?is)<a[^>]+part-edit.*?</a>', "", sec.group(1)))
            if t and t != "目次":
                section = t
        # A `<br>` chunk is not always one line: a section heading is a block
        # element, so `総集編作品` and the first release under it arrive
        # together and an anchored match on the chunk finds neither. Splitting
        # again on newlines is what makes the sections after the first one
        # parse at all -- three of eighty entries were lost to this.
        for plain in (l.strip() for l in _text(chunk).splitlines()):
            if not plain:
                continue
            head = _HEAD.match(plain)
            bare = None if head else _DATE_ONLY.match(plain)
            if head or bare:
                if cur:
                    out.append(cur)
                alt = head.group(3) if head else None
                cur = {"date": (head or bare).group(1),
                       "video_id": head.group(2).upper() if head else "",
                       "alt_ids": [alt.upper()] if alt else [],
                       "title_ja": "", "label": "", "section": section,
                       "performers": [], "note": ""}
                continue
            if cur is None:
                continue
            _fill(cur, plain)
    if cur:
        out.append(cur)
    return [w for w in out if w["title_ja"]]


# Sections that are not video releases. A deny-list rather than an allow-list
# on purpose: a section name nobody has seen before is far more likely to be a
# kind of release than a kind of merchandise, so the safe default is to count
# it and let the per-section breakdown show the user what it was.
#
# 写真集 are photobooks and その他情報 is a list of public appearances -- neither
# will ever be a file in the library, and counting them makes "how many of her
# releases do I have" answer a question nobody asked.
_NOT_VIDEO = {"写真集等", "写真集", "その他商品", "その他情報", "SNS",
              "プロフィール", "グラビアサイト", "事務所", "ブログ",
              "Twitter", "Instagram", "X", "目次", "関連ページ",
              # Found on real pages after the first version shipped. None of
              # them contributed a single work -- they happen not to use the
              # "date + title" line shape -- so the totals were right by luck
              # rather than by rule. Naming them makes it a rule.
              "雑誌", "トレーディングカード", "イベント", "TV出演",
              "撮影会", "X（旧Twitter）", "ラジオ", "書籍", "グッズ"}


def is_video_section(name: str) -> bool:
    return (name or "") not in _NOT_VIDEO


def summarise(parsed: dict) -> dict:
    """Per-section counts, which is the only honest way to show a total.

    "742 works" reads as "she starred in 742 films" and is not what the number
    means: on the page that produced it, 88 were VR, 94 were streaming, 6 were
    compilations of existing footage, and the rest included multi-performer
    titles. The breakdown is what makes the figure interpretable, so it travels
    with it rather than being computed for display and thrown away.
    """
    works = [w for w in parsed.get("works", [])
             if is_video_section(w.get("section"))]
    order, counts, ids = [], {}, {}
    for w in works:
        # The first section carries no heading of its own on this wiki.
        s = w.get("section") or "単体作品"
        if s not in counts:
            order.append(s)
            ids[s] = []
        counts[s] = counts.get(s, 0) + 1
        # Kept per section, not just in one flat list, so the video list can
        # say how many of *this kind* are here -- "543 単体作品, you have 40"
        # answers a question that "750 listed, you have 55" does not.
        for vid in [w["video_id"]] + list(w.get("alt_ids") or []):
            if vid:
                ids[s].append(vid)
    return {
        "total": len(works),
        "sections": [{"name": s, "count": counts[s], "ids": ids[s]}
                     for s in order],
        # Stated separately because it is the reason the numbers on screen can
        # never add up: a release with no catalogue number cannot be matched to
        # a file, however many copies of it are sitting in the folder.
        "no_id": sum(1 for w in works if not w.get("video_id")),
    }


def _fill(cur: dict, plain: str) -> None:
    """Attach one detail line to the release record it belongs to."""
    p = _PERFORMERS.match(plain)
    if p:
        cur["performers"] = [n for n, _k in _split_names(p.group(1))]
        return
    n = _NOTE.match(plain)
    if n:
        cur["note"] = n.group(1).strip()
        return
    if not cur["title_ja"]:
        # The title line also carries a "(レーベル一覧)" link; it is a
        # navigation link on every entry, so it is never part of a title.
        t = re.sub(r"\s*\(?レーベル一覧\)?\s*$", "", plain).strip()
        if t:
            cur["title_ja"], cur["label"] = _strip_label(t)


def titles_by_id(parsed: dict) -> dict:
    """`{catalogue number: Japanese title}` -- what `movie_title.ja` wants.

    A release numbered twice (rental and sell editions) contributes both
    numbers, because the copy in the library may be named after either.
    """
    out = {}
    for w in parsed.get("works", []):
        if not w.get("title_ja"):
            continue
        # A release with no catalogue number still counts towards the totals,
        # but it has no key to be stored under -- without this guard every one
        # of them wrote its title into the row for the empty string, and the
        # last one won.
        for vid in [w["video_id"]] + list(w.get("alt_ids") or []):
            if vid:
                out[vid] = w["title_ja"]
    return out


def released_by_id(parsed: dict) -> dict:
    """`{catalogue number: YYYY-MM-DD}`.

    The wiki writes `2018/04/13`; stored with dashes so a plain string sort is
    a chronological sort, which is what the video list needs.
    """
    out = {}
    for w in parsed.get("works", []):
        d = (w.get("date") or "").strip()
        if not d:
            continue
        parts = d.split("/")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            continue
        iso = f"{parts[0]}-{int(parts[1]):02d}-{int(parts[2]):02d}"
        for vid in [w["video_id"]] + list(w.get("alt_ids") or []):
            if vid:
                out[vid] = iso
    return out


def performers_by_id(parsed: dict) -> dict:
    """`{catalogue number: [performer, ...]}`.

    Kept separate from the alias import on purpose. This is the data
    `/api/javdb/seed` needed and guessed at -- it shoved 28 aliases into one
    character, most of them unrelated co-stars. Here the co-performers are
    stated per release rather than inferred, but knowing *which folder* each
    name belongs to is still a separate problem, so this map is offered for
    matching and never written into anyone's aliases.
    """
    return {w["video_id"]: list(w["performers"])
            for w in parsed.get("works", [])
            if w.get("performers") and w.get("video_id")}


def match_score(parsed: dict, folder_ids) -> dict:
    """How much this page and a character folder have in common.

    Name agreement is not enough to write to `person_info.json` with: stage
    names repeat between people, and performers change them. Catalogue numbers
    are evidence -- if the page lists releases that are sitting in the folder,
    it is that person. `/api/javdb/seed` is the cautionary tale; it assumed,
    and the result had to be restored by hand.
    """
    ids = {w["video_id"] for w in parsed.get("works", [])}
    have = {str(v).upper() for v in folder_ids}
    shared = sorted(ids & have)
    return {"page_ids": len(ids), "folder_ids": len(have),
            "shared": shared, "score": len(shared)}
