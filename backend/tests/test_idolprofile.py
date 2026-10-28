"""Filling character profiles from javdatabase.

The expensive mistakes this guards against:

  * storing an age. It is wrong within a year; only the date of birth is
    stable, and most javdatabase entries give the year alone.
  * a bad date silently producing an age anyway.
  * trusting one film. `/api/javdb/seed` did, and wrote 28 unrelated aliases
    onto one character. Every folder is a majority vote now, and these tests
    pin the thresholds that were measured to work.

Run:  python tests/test_idolprofile.py       (from backend/)
"""
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import idolprofile as ip          # noqa: E402

# The real profile block, copied from /idols/hanako-yamamura/ — parsing is
# against the site's actual markup, not a tidied-up version of it.
REAL = '''<h1 class="idol-name">Hanako Yamamura - JAV Profile</h1>
<b>Age:</b> <a href="/idols/?_birth_year=2000" class="idol-box-link">26</a>
- <b>DOB:</b> <a href="/idols/?_birth_year=2000" class="idol-box-link">2000-??-??</a>
- <b>Debut:</b> ? - <b>Debut Age: </b> ? - <b>Birthplace:</b> ? - <b>Sign:</b> ?
- <b>Blood:</b> ?<br> <b>Measurements:</b> 88-57-82
- <b>Cup:</b> <a href="/idols/?_cup_size=f" class="idol-box-link">F</a>
- <b>Height:</b> <a href="/idols/?_height=153" class="idol-box-link">153 cm</a>
- <b>Shoe Size:</b> ?<br>
<b>Hair Length(s):</b> <a href="/x" class="idol-box-link">Medium</a>
- <b>Hair Color(s):</b> <a href="/x" class="idol-box-link">Brown</a>
<p> <b>Tags:</b> <a href="/x" class="idol-box-link">Twenties</a>
- <a href="/x" class="idol-box-link">Slim</a>
<a href="/suggest" class="btn btn-primary btn-sm">Suggest Tags</a></br><br> </p>'''

WITH_PLACE = REAL.replace("<b>Birthplace:</b> ?",
                          '<b>Birthplace:</b> <a href="/x" class="idol-box-link">Akita</a>')

failed = 0


def check(name, got, want):
    global failed
    ok = got == want
    print(f"  {'OK  ' if ok else 'FAIL'} {name}: {got!r}"
          + ("" if ok else f"  (expected {want!r})"))
    if not ok:
        failed += 1


print("parsing the real page")
p = ip.parse_profile(REAL)
check("dob", p.get("dob"), "2000-??-??")
check("height", p.get("height"), "153 cm")
check("measurements", p.get("measurements"), "88-57-82")
check("cup", p.get("cup"), "F")
# the site's own Tags field is deliberately not collected
check("site tags not collected", "tags" in p, False)
# "?" means javdatabase has no value; storing it would look like real data
check("unknown fields are dropped", "birthplace" in p, False)
# fields we deliberately do not collect
check("no age stored", "age" in p, False)
check("no hair stored", "hair" in p or "hair color(s)" in p, False)
check("only the wanted keys", sorted(p),
      ["cup", "dob", "height", "measurements"])

print("\nbirthplace when present")
check("translated", ip.parse_profile(WITH_PLACE).get("birthplace"), "秋田縣")

print("\na suggestion must carry the same payload as an accepted row")
# Confirming a renamed performer used to do nothing: build_proposal returned
# early for anything that was not `ok`, so the row had no profile, no fields
# and no has_changes — the write button could never enable.
import tempfile                                     # noqa: E402
from app.core import media                          # noqa: E402

_tmp = Path(tempfile.mkdtemp()) / "someone"
_tmp.mkdir(parents=True)
_row = {
    "name": "someone", "paths": [str(_tmp)], "ok": False,
    "vote": {"suggest": True, "slug": "azusa-natsume", "name": "夏目あずき"},
    "fields": [{"key": "cup", "value": "F", "skip": False}],
    "new_aliases": ["夏目あずき"], "has_changes": True,
}
_res = ip.apply_proposal(_row)
_info = media.load_person_info(_tmp)
check("confirming writes the fields", _res["fields"], ["cup"])
check("and the alias", _info.get("aliases"), ["夏目あずき"])
check("and records where it came from",
      (_info.get("profile_source") or {}).get("slug"), "azusa-natsume")

print("\nthe whitelist also governs hand edits")
check("unknown key rejected",
      ip.clean_profile({"height": "150 cm", "hacker": "x"}), {"height": "150 cm"})
# characters filled in before the Tags field was dropped still have it on
# disk; the next save quietly cleans it out rather than preserving dead data
check("stale tags from an older version are dropped",
      ip.clean_profile({"height": "150 cm", "tags": ["x"]}), {"height": "150 cm"})

print("\ntranslation table (birthplace is all that needs it now)")
check("prefecture", ip.zh("Hokkaido"), "北海道")
check("case-insensitive", ip.zh("AKITA"), "秋田縣")
check("unknown passes through", ip.zh("Kanagawa-ish"), "Kanagawa-ish")
check("empty stays empty", ip.zh(""), "")

print("\nage is derived, never stored")
today = datetime.date(2026, 8, 1)
check("full date, birthday passed",
      ip.age_from_dob("1995-01-15", today), {"age": 31, "approx": False})
check("full date, birthday not yet",
      ip.age_from_dob("1995-11-15", today), {"age": 30, "approx": False})
check("year only is approximate",
      ip.age_from_dob("2000-??-??", today), {"age": 26, "approx": True})
check("no year at all", ip.age_from_dob("????-??-??", today), None)
check("empty", ip.age_from_dob("", today), None)

print("\nbad dates must not produce a confident age")
check("month 13 / day 40", ip.age_from_dob("2000-13-40", today),
      {"age": 26, "approx": True})
check("29 Feb in a common year", ip.age_from_dob("2001-02-29", today),
      {"age": 25, "approx": True})
check("year in the future", ip.age_from_dob("2099-01-01", today), None)
check("not a date at all", ip.age_from_dob("soon", today), None)

print("\nvote thresholds (the seed disaster is why these exist)")
check("needs at least 3 films", ip.MIN_FILMS, 3)
check("needs a 60% majority", ip.MIN_SHARE, 0.60)
# the numbers measured on the real library: 森田花梨 33/47 of one performer,
# 星川優子 29/30, 井上さくら 5/5 — the first is 70%, so all three pass
for name, n, checked, want in [("森田花梨", 33, 47, True),
                               ("星川優子", 29, 30, True),
                               ("井上さくら", 5, 5, True),
                               ("薄弱多數", 2, 5, False),
                               ("片數不足", 2, 2, False)]:
    ok = checked >= ip.MIN_FILMS and n / checked >= ip.MIN_SHARE
    check(f"{name} {n}/{checked}", ok, want)

print("PASS" if not failed else f"FAIL ({failed})")
sys.exit(1 if failed else 0)
