"""Read a saved IAFD performer page (bio + filmography).

**Why a saved file and not a fetch.** The page footer says plainly that
"archiving, reproduction, re-distribution or transmission of this site by any
means without the prior permission of iafd.com is prohibited". So this works
the way the seesaawiki import does: the user saves the page themselves and
hands us the file. No crawling, no rate limiting, no cache to invalidate, and
the site changing its layout breaks nothing until someone saves a new page.

**What it is good for, and what it is not.** IAFD covers Western *studio*
releases. Measured on the one real case (`Elena Varga`, 44 credits, 62
video files in the folder): 43 files matched a credit, 19 did not — and those
19 are correct non-matches, webcam recordings and social-media clips that IAFD
has no reason to list. So this fills in a performer's studio filmography; it
says nothing about the rest of a folder.

**Matching is fuzzy, which the catalogue-number path never was.** A JAV number
identifies exactly one film. Here the only handle is an English title against a
filename like `18OnlyGirls - Rosalie - Applauding Myself.mp4`. That works better
than it sounds — the studio's filenames contain the title, so the measured
score distribution is sharply bimodal: real matches at 0.895–1.00, everything
else at 0.00–0.48, with **an empty band between 0.48 and 0.89**. `MATCH_MIN`
sits in that gap, the same way `subtitles.MERGE_RATIO` sits in its own. Nothing
in the sample landed close enough to two credits to be ambiguous.

Titles are deliberately *not* written from here — only the release year of a
matched film. The user's call: a wrong title is visible and annoying, a wrong
year is neither useful nor harmful enough to be worth the fuzzy-match risk on
the more valuable field.
"""
from __future__ import annotations

import difflib
import html
import re
from pathlib import Path
from typing import Iterable, Optional

# In the measured empty band (0.74 → 0.90). Anything below is not offered.
MATCH_MIN = 0.82
# Two credits this close to each other means the filename does not distinguish
# them, so neither is offered rather than picking the higher one.
AMBIGUOUS = 0.08

_MONTHS = {m: i + 1 for i, m in enumerate(
    ("january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"))}

_NO_DATA = ("no data", "no known aliases", "")


def looks_like_iafd(raw: bytes) -> bool:
    """Cheap check so one upload endpoint can serve two very different sites."""
    head = raw[:4000].lower()
    return b"iafd.com" in head and (b"person.rme" in head or b"bioheading" in head)


def _text(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)) \
        .replace("\xa0", " ").strip()


def _bio(raw: str, label: str) -> str:
    m = re.search(r'<p class="bioheading">\s*%s\s*</p>\s*'
                  r'<(?:p|div) class="biodata">(.*?)</(?:p|div)>'
                  % re.escape(label), raw, re.S | re.I)
    v = _text(m.group(1)) if m else ""
    return "" if v.lower() in _NO_DATA else v


def _dob(s: str) -> str:
    """`May 27, 1993 (33 years old)` → `1993-05-27`.

    ISO so that string order is time order, same rule as `movie_title.released`.
    The age in brackets is dropped: it is wrong next year.
    """
    m = re.match(r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", s.strip())
    if not m:
        return ""
    mon = _MONTHS.get(m.group(1).lower())
    return "%s-%02d-%02d" % (m.group(3), mon, int(m.group(2))) if mon else ""


def _height_cm(s: str) -> str:
    """`5 feet, 4 inches (162 cm)` → `162 cm`. The metric value is already
    there, so nothing is converted and nothing can be converted wrongly."""
    m = re.search(r"\((\d+)\s*cm\)", s)
    return "%s cm" % m.group(1) if m else ""


def parse(raw: bytes) -> dict:
    """Bio and filmography from one saved performer page."""
    s = raw.decode("utf-8", "replace")

    name = ""
    m = re.search(r"<h1>\s*(.*?)\s*</h1>", s, re.S)
    if m:
        name = _text(m.group(1))

    aka = []
    m = re.search(r'AKA</p>\s*<div class="biodata">(.*?)</div>', s, re.S)
    if m:
        t = _text(m.group(1))
        if t.lower() not in _NO_DATA:
            aka = [x.strip() for x in re.split(r"[,;/]|<br\s*/?>", t) if x.strip()]

    # Only the five keys `idolprofile.PROFILE_KEYS` already allows. The page
    # also carries hair colour, eye colour, nationality, ethnicity and years
    # active — deliberately left out, because adding them means widening the
    # shape of person_info.json, and those files live on the NAS where git
    # cannot protect them. They are reported under `extra` so the preview can
    # show what was passed over.
    profile = {}
    if (d := _dob(_bio(s, "Birthday"))):
        profile["dob"] = d
    if (h := _height_cm(_bio(s, "Height"))):
        profile["height"] = h
    for key, label in (("measurements", "Measurements"), ("birthplace", "Birthplace")):
        if (v := _bio(s, label)):
            profile[key] = v

    extra = {}
    for label in ("Ethnicity", "Nationality", "Hair Color", "Eye Color",
                  "Years Active", "Gender", "Weight", "Tattoos", "Piercings"):
        if (v := _bio(s, label)):
            extra[label] = v

    works = []
    for m in re.finditer(
            r'<tr class="[^"]*">\s*<td[^>]*>\s*<a href="[^"]*title\.rme[^"]*">'
            r'(.*?)</a>\s*</td>\s*<td class="sorting_1">(\d{4})</td>'
            r'\s*<td>.*?>([^<]*)</a>\s*</td>\s*<td><i>(.*?)</i>', s, re.S):
        works.append({"title": _text(m.group(1)), "year": m.group(2),
                      "distributor": _text(m.group(3)),
                      "notes": re.sub(r"\s+", " ", _text(m.group(4))).strip()})

    return {"site": "iafd.com", "name": name, "aka": aka,
            "profile": profile, "extra": extra, "works": works}


# Generic words that say how a file was distributed, not which film it is.
# **Studio names are deliberately not listed here.** They used to be
# (`wowgirls`, `18onlygirls`) and that was the bug: `younglegalporn` was not,
# so all twelve files from that studio scored ~0.65 against titles they match
# *exactly*. A hardcoded list silently fails for every studio nobody thought
# of, and the failure looks like "no match" rather than like a bug.
_NOISE = re.compile(
    r"(com|net|pornhub|xvideos|xhamster|hd|porn|videos?"
    r"|sex|movies?|tube|watch|online|full|scene|copy|mp4|1080p?|720p?|4k)\b")


def _norm(s: str, drop: Iterable[str] = ()) -> str:
    s = s.lower()
    for d in drop:
        if d:
            s = s.replace(d.lower(), " ")
    s = _NOISE.sub(" ", s)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


_SEG = re.compile(r"\s+-\s+")


def _candidates(stem: str, drop: Iterable[str] = ()) -> list:
    """Every ` - `-separated suffix of a filename, normalised.

    These filenames carry structure the uploader put there: `Studio - Performer
    - Title`. Rather than guess which leading segments are not the title, try
    them all and keep the best score. The segment boundaries are given, so this
    needs no list of studios and works on the first file from a studio nobody
    has seen before.

    More candidates means more chances to match something wrong, so this was
    measured, not assumed. On the 62-file reference folder it *widened* the gap
    instead of narrowing it: real matches bottom out at 0.895 and the best
    wrong pairing reaches 0.476, where the whole-string version left a genuine
    non-match sitting at 0.737. `MATCH_MIN` is in the band either way.
    """
    parts = _SEG.split(stem)
    out = []
    for i in range(len(parts)):
        n = _norm(" - ".join(parts[i:]), drop)
        if n and n not in out:
            out.append(n)
    return out


def match(works: list, paths: Iterable[Path], names: Iterable[str] = ()) -> list:
    """Pair video files with credits. Only confident, unambiguous pairs.

    `names` are the performer's own names, stripped before comparing — they
    appear in every filename in the folder and so carry no signal, while
    inflating the similarity of every pair equally.
    """
    drop = [n for n in names if n]
    norm_works = [(w, _norm(w["title"], drop)) for w in works]
    out = []
    for p in paths:
        cands = _candidates(Path(p).stem, drop)
        if not cands:
            continue
        # Best candidate *per credit*, then rank credits. Ranking candidates
        # instead would let two segments of one filename occupy both top slots
        # and trip the ambiguity guard against itself.
        scored = sorted(
            ((max(difflib.SequenceMatcher(None, c, t).ratio() for c in cands), w)
             for w, t in norm_works if t), key=lambda x: -x[0])
        if not scored or scored[0][0] < MATCH_MIN:
            continue
        if len(scored) > 1 and scored[0][0] - scored[1][0] < AMBIGUOUS:
            continue                      # the filename cannot tell them apart
        score, w = scored[0]
        out.append({"path": str(p), "name": Path(p).name, "score": round(score, 3),
                    "title": w["title"], "year": w["year"],
                    "distributor": w["distributor"], "notes": w["notes"]})
    return out


_STOP = {"the", "a", "an", "of", "and", "my", "me", "is", "it", "in", "to",
         "with", "for", "on", "i", "you", "girl", "blonde", "blondie", "sweet"}


def _tokens(s: str) -> set:
    return {w for w in s.split() if len(w) > 3 and w not in _STOP}


def folder_scores(works: list, by_char: dict, names: Iterable[str] = (),
                  limit: int = 5) -> list:
    """Which character folder this page is about, judged by content.

    The wiki import picks the folder by catalogue-number overlap, with the name
    only as a tie-breaker — "a shared id is evidence, a shared name is a
    coincidence waiting to happen". The same reasoning applies here and it has
    to, because **the IAFD name frequently is not the folder name**: this page
    is titled `Rosalie` while the folder is `Elena Varga`. Matching on the
    name alone would have found nothing.

    A token prefilter runs first. Comparing 29,197 filenames against 44 titles
    with difflib is 1.3 M comparisons; requiring one shared significant word
    first cuts that to a handful per character.
    """
    drop = [x for x in names if x]
    wt = [(w, _norm(w["title"], drop), _tokens(_norm(w["title"], drop)))
          for w in works]
    out = []
    for name, files in (by_char or {}).items():
        # The performer's name is in nearly every filename in their folder, so
        # it carries no signal -- but it is a long shared slab of text that
        # *lowers* the ratio against a short title. Measured: `WowGirls - A Date
        # With Herself - Rosalie` against `Date with Herself` scores 0.79 with
        # the name left in and clears the threshold without it. The folder name
        # goes too, for the same reason.
        here = drop + [name]
        hit = set()
        for f in files:
            cands = _candidates(Path(f).stem, here)
            if not cands:
                continue
            tk = set()
            for c in cands:
                tk |= _tokens(c)
            for w, t, wtok in wt:
                if not (tk & wtok):
                    continue                  # cannot be the same film
                if max(difflib.SequenceMatcher(None, c, t).ratio()
                       for c in cands) >= MATCH_MIN:
                    hit.add(w["title"])
                    break
        if hit:
            out.append({"name": name, "score": len(hit),
                        "shared": sorted(hit)[:8]})
    out.sort(key=lambda x: (-x["score"], x["name"]))
    return out[:limit]


def released_from(year: str) -> str:
    """IAFD gives a year, not a date.

    Stored as the bare year rather than faking `YYYY-01-01`: a made-up month
    and day would be indistinguishable from a real release date, and the sort
    still works because `"2014" < "2014-01-01"` lexicographically.
    """
    y = str(year or "").strip()
    return y if re.fullmatch(r"\d{4}", y) else ""
