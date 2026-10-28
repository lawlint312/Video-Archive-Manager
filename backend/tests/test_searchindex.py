"""A restricted account's search index comes from the shared one.

It used to walk that account's folders on every request — about 4 s over the
NAS, with no cache — and the sidebar asks again on every remount. Now the
shared index is filtered to the account's roots:

  * the filtered index is exactly what walking those roots would give
  * a root that is a prefix of another's name (E:\\JAV vs E:\\JAV2) does not leak
  * inbox rows need an inbox folder, archive rows an archive root
  * a restricted request never walks, and never writes the shared cache

Run:  python tests/test_searchindex.py      (from backend/)
"""
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import settings                               # noqa: E402
from app.core import searchindex, javdb, filetitle, costar     # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def touch(p: Path):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\0")


def paths(data: dict) -> set:
    return {(data["dirs"][r[2]] + "\\" + r[0], r[1], r[3]) for r in data["v"]}


tmp = Path(tempfile.mkdtemp(prefix="vam-search-"))
saved = (searchindex._FILE, searchindex._cache, javdb.titles, filetitle.all_titles,
         settings.archive_roots, settings.inbox_paths, searchindex._compute,
         costar.all_names)
try:
    # nothing here may read the real title databases or the real index file
    searchindex._FILE = tmp / "search_index.json"
    searchindex._cache = None
    javdb.titles = lambda: {}
    filetitle.all_titles = lambda: {}
    costar.all_names = lambda: {}          # vam.db: the co-star names

    jav, jav2, web = tmp / "JAV", tmp / "JAV2", tmp / "Web"
    box, box2 = tmp / "Downloads", tmp / "Downloads2"
    touch(jav / "森田花梨" / "STARS-786.mp4")
    touch(jav / "森田花梨" / "sub" / "MIDV-087.mp4")
    touch(jav / "三浦真央" / "SSIS-001.mp4")
    touch(jav2 / "secret" / "ABP-001.mp4")        # JAV is a prefix of JAV2
    touch(web / "guoguo" / "a.mp4")
    touch(box / "IPX-100.mp4")
    touch(box2 / "IPX-200.mp4")
    for r in (jav, jav2, web, box, box2):
        r.mkdir(exist_ok=True)

    archive, inbox = [jav, jav2, web], [box, box2]
    settings.archive_roots = lambda: archive
    settings.inbox_paths = lambda: inbox

    print("filtered index equals walking the account's roots")
    full = searchindex.get(archive, inbox)
    for roots, ibox, label in [
        ([jav], [box], "one archive root + one inbox"),
        ([jav2, web], [], "two roots, no inbox"),
        ([], [box2], "inbox only"),
    ]:
        want = searchindex._compute(roots, ibox)
        got = searchindex.get(roots, ibox, scoped=True)
        check(paths(got) == paths(want), label)

    print("\nno leaks through a prefix of a name")
    got = searchindex.get([jav], [box], scoped=True)
    check(not any("ABP-001" in r[0] for r in got["v"]), "JAV2 not visible from JAV")
    check(not any("IPX-200" in r[0] for r in got["v"]), "Downloads2 not visible from Downloads")

    print("\nrows keep their meaning")
    got = searchindex.get([jav], [box], scoped=True)
    check(all(got["dirs"][r[2]] for r in got["v"]), "every row points at a kept folder")
    check(len(got["dirs"]) == len({r[2] for r in got["v"]}), "no unused folders sent")
    check({r[0] for r in got["v"] if r[3]} == {"IPX-100.mp4"}, "inbox rows stay inbox")

    print("\nrestricted requests never walk or write the shared cache")
    before = searchindex._FILE.read_text(encoding="utf-8")
    calls = []
    searchindex._compute = lambda *a: calls.append(a) or {"dirs": [], "v": [], "at": 0}
    searchindex.get([jav], [box], scoped=True)
    check(calls == [], "no walk")
    check(searchindex._FILE.read_text(encoding="utf-8") == before, "disk copy untouched")
    check(len(searchindex._cache["v"]) == len(full["v"]), "memory copy still complete")
finally:
    (searchindex._FILE, searchindex._cache, javdb.titles, filetitle.all_titles,
     settings.archive_roots, settings.inbox_paths, searchindex._compute,
     costar.all_names) = saved
    shutil.rmtree(tmp, ignore_errors=True)

print("\nFAIL: %d" % len(FAIL) if FAIL else "\nPASS")
sys.exit(1 if FAIL else 0)
