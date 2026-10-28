"""Parsing an IAFD performer page, and the fuzzy match that is the risky part.

The parser is easy; the matching is not. A catalogue number identifies one
film, so the JAV path can never pair the wrong video with the wrong title.
Here the only handle is an English title against a filename, so these tests
pin down the two things that decide whether the feature is safe:

* the threshold sits in the **measured empty band** (real matches 0.895–1.00,
  everything else 0.00–0.48, nothing between 0.48 and 0.89),
* a filename that is close to *two* credits produces **no** match rather than
  the higher-scoring one, and
* a studio prefix nobody anticipated does not quietly suppress the match.

The real page (`Elena Varga`, 44 credits) was measured at 43/62 files
matched with 0 ambiguous; the 19 non-matches are webcam and social-media clips
IAFD has no reason to list.
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core import iafd          # noqa: E402

PAGE = b"""<html><head><title>rosalie - iafd.com</title>
<link rel="canonical" href="https://www.iafd.com/person.rme/id=abc"></head><body>
<h1>Rosalie</h1>
<p class="bioheading">Performer AKA</p><div class="biodata">No known aliases</div>
<p class="bioheading">Birthday</p><p class="biodata">
  <a href="x">May 27, 1993</a> (33 years old)</p>
<p class="bioheading">Birthplace</p><p class="biodata">No data&nbsp;</p>
<p class="bioheading">Height</p><p class="biodata">5 feet, 4 inches (162 cm)</p>
<p class="bioheading">Measurements</p><p class="biodata">No data</p>
<p class="bioheading">Hair Color</p><p class="biodata">Blond</p>
<p class="bioheading">Nationality</p><p class="biodata">Russian</p>
<table><tbody>
<tr class="we odd"><td class=""><a href="/title.rme/id=1">Applauding Myself</a></td>
<td class="sorting_1">2014</td><td><a href="/distrib.rme/d=1">Wow Network</a></td>
<td><i>Bald MastOnly</i></td></tr>
<tr class="we even"><td class=""><a href="/title.rme/id=2">Date with Herself</a></td>
<td class="sorting_1">2012</td><td><a href="/distrib.rme/d=1">Wow Network</a></td>
<td><i>MastOnly</i></td></tr>
<tr class="we odd"><td class=""><a href="/title.rme/id=3">Sweet and Petite</a></td>
<td class="sorting_1">2013</td><td><a href="/distrib.rme/d=1">Wow Network</a></td>
<td><i>&nbsp;</i></td></tr>
</tbody></table></body></html>"""


def test_recognised_as_iafd():
    assert iafd.looks_like_iafd(PAGE)
    assert not iafd.looks_like_iafd(b"<html><body>just a page</body></html>")


def test_bio_fields():
    d = iafd.parse(PAGE)
    assert d["name"] == "Rosalie"
    assert d["profile"]["dob"] == "1993-05-27", "生日要正規化成 ISO"
    assert d["profile"]["height"] == "162 cm", "要取括號裡的公制，不要自己換算"
    # "No data" is the site's way of saying nothing, not a value.
    assert "birthplace" not in d["profile"]
    assert "measurements" not in d["profile"]
    assert d["aka"] == [], "「No known aliases」不是一個別名"


def test_extra_fields_are_reported_but_not_written():
    """Widening person_info.json is a decision of its own — those files live on
    the NAS where git cannot protect them. So these are shown, not stored."""
    d = iafd.parse(PAGE)
    assert "Hair Color" in d["extra"] and "Nationality" in d["extra"]
    assert set(d["profile"]) <= {"dob", "height", "measurements", "birthplace"}


def test_filmography():
    d = iafd.parse(PAGE)
    assert len(d["works"]) == 3
    w = d["works"][0]
    assert w["title"] == "Applauding Myself" and w["year"] == "2014"
    assert w["distributor"] == "Wow Network"


def test_match_finds_the_studio_releases():
    d = iafd.parse(PAGE)
    files = ["18OnlyGirls - Rosalie - Applauding Myself.mp4",
             "WowGirls - A Date With Herself - Rosalie.mp4",
             "2023-09-23_at_17-06_id_500000000000000001.mp4",   # a Fansly clip
             "ia99PrJhyQG.mp4"]
    got = iafd.match(d["works"], files, names=["Rosalie"])
    by = {Path(m["name"]).stem: m["title"] for m in got}
    assert by.get("18OnlyGirls - Rosalie - Applauding Myself") == "Applauding Myself"
    assert by.get("WowGirls - A Date With Herself - Rosalie") == "Date with Herself"
    assert len(got) == 2, "社群媒體的檔案不該對到任何作品：%s" % got


def test_a_studio_nobody_listed_still_matches():
    """The bug this exists for: `_NOISE` used to name the studios.

    `wowgirls` and `18onlygirls` were in it, `younglegalporn` was not, so all
    twelve files from that studio scored ~0.65 against titles they match
    exactly — and the symptom was a silent "no match", not an error. Any fix
    that reintroduces a studio list fails here, because this studio is invented.
    """
    d = iafd.parse(PAGE)
    files = ["QuiteImplausibleStudioName - Rosalie - Sweet And Petite.mp4"]
    got = iafd.match(d["works"], files, names=["Rosalie"])
    assert len(got) == 1 and got[0]["title"] == "Sweet and Petite",         "沒列進名單的片商前綴不該讓比對失敗：%s" % got
    assert got[0]["score"] >= 0.95, "片商名不該壓低分數：%.3f" % got[0]["score"]


def test_the_title_may_sit_before_the_performer():
    """`WowGirls - A Date With Herself - Rosalie` — the title is in the middle,
    so suffix candidates alone are not enough; the whole stem is one too."""
    d = iafd.parse(PAGE)
    got = iafd.match(d["works"], ["AnotherStudio - A Date With Herself - Rosalie.mp4"],
                     names=["Rosalie"])
    assert [m["title"] for m in got] == ["Date with Herself"], got


def test_ambiguous_pairs_are_dropped_not_guessed():
    """Two credits the filename cannot tell apart must yield nothing.

    Picking the higher score would be exactly the failure `detect_video_id`
    exists to avoid — a wrong pairing is worse than no pairing.
    """
    works = [{"title": "Sweet and Petite", "year": "2013", "distributor": "",
              "notes": ""},
             {"title": "Sweet and Petit", "year": "2014", "distributor": "",
              "notes": ""}]
    got = iafd.match(works, ["Sweet and Petite.mp4"])
    assert got == [], "分不出來時不可以挑分數高的那個：%s" % got


def test_threshold_sits_in_the_measured_gap():
    """The empty band on real data is 0.48 → 0.89 (43 matches, 19 correct
    non-matches). It was 0.74 → 0.90 before filenames were split on ` - `."""
    assert 0.48 < iafd.MATCH_MIN < 0.89


def test_release_year_is_not_padded_into_a_fake_date():
    """`2014-01-01` would be indistinguishable from a real release date."""
    assert iafd.released_from("2014") == "2014"
    assert iafd.released_from("") == ""
    assert iafd.released_from("20XX") == ""
    # And it still sorts correctly among full dates.
    assert "2014" < "2014-01-01" < "2015"


def test_folder_scores_find_the_folder_by_content_not_name():
    """The page is titled `Rosalie`; the folder is `Elena Varga`."""
    d = iafd.parse(PAGE)
    by_char = {
        "Elena Varga": ["18OnlyGirls - Rosalie - Applauding Myself.mp4",
                             "WowGirls - A Date With Herself - Rosalie.mp4",
                             "random clip.mp4"],
        "Someone Else": ["holiday.mp4", "cat.mp4"],
    }
    got = iafd.folder_scores(d["works"], by_char, names=[d["name"]])
    assert got and got[0]["name"] == "Elena Varga", got
    assert got[0]["score"] == 2
    assert all(g["name"] != "Someone Else" for g in got), \
        "沒有任何命中的角色不該出現在候選裡"


if __name__ == "__main__":
    import traceback
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    bad = 0
    for name, fn in fns:
        try:
            fn()
            print("ok   %s" % name)
        except Exception:
            bad += 1
            print("FAIL %s" % name)
            traceback.print_exc()
    print("\n%d 通過 / %d 失敗" % (len(fns) - bad, bad))
    sys.exit(1 if bad else 0)
