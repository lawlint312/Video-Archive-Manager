"""Image de-duplication.

The cases that matter are the ones that would cost photos:

  * a re-saved / resized copy must be caught (that is the whole point)
  * two genuinely different pictures must NOT be grouped, however similar
    their subject — deleting is permanent
  * Tier 2 must not compare across character folders, so two people owning a
    copy of the same picture are left alone
  * the hash cache must notice when a file was replaced, or a scan will
    happily group by a hash belonging to the previous contents

Run:  python tests/test_imgdedup.py        (from backend/)
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw                     # noqa: E402

from app.core import imgdedup                        # noqa: E402


def photo(path: Path, seed: int, size=(400, 300)):
    """A deterministic 'photograph' — smooth gradients plus a few shapes."""
    im = Image.new("RGB", size)
    d = ImageDraw.Draw(im)
    for y in range(size[1]):
        d.line([(0, y), (size[0], y)],
               fill=((seed * 37 + y) % 256, (seed * 11 + y * 2) % 256,
                     (seed * 73 + y // 2) % 256))
    for i in range(5):
        x = (seed * (i + 3) * 29) % (size[0] - 60)
        y = (seed * (i + 7) * 17) % (size[1] - 60)
        d.ellipse([x, y, x + 55, y + 55],
                  fill=((seed * i * 53) % 256, (seed + i * 91) % 256, 60))
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)
    return im


def check(name, got, want):
    ok = got == want
    print(f"  {'OK  ' if ok else 'FAIL'} {name}: {got!r}"
          + ("" if ok else f"  (expected {want!r})"))
    return ok


def paths_of(groups, tier):
    return [sorted(Path(f["path"]).name for f in g["files"])
            for g in groups if g["tier"] == tier]


def main():
    tmp = Path(tempfile.mkdtemp())
    root = tmp / "archive"
    alice, bob = root / "Alice", root / "Bob"
    ok = True

    alice.mkdir(parents=True)
    bob.mkdir(parents=True)
    original = photo(alice / "shot.png", seed=5)

    # byte-identical copy -> Tier 1
    (alice / "shot_copy.png").write_bytes((alice / "shot.png").read_bytes())

    # same picture, re-saved smaller and as JPEG -> Tier 2, not Tier 1
    original.resize((200, 150)).save(alice / "shot_small.jpg", quality=70)

    # a different picture in the same folder -> must stay alone
    photo(alice / "other.png", seed=91)

    # the same picture again, but under a different person -> must NOT be
    # grouped with Alice's by Tier 2
    original.resize((300, 225)).save(bob / "same_pic.jpg", quality=80)
    photo(bob / "bobs_own.png", seed=42)

    cache = tmp / "hash.sqlite3"
    groups = imgdedup.find_duplicates([root], tier2=True, cache_db=cache)

    print("scan")
    t1, t2 = paths_of(groups, 1), paths_of(groups, 2)
    # the identical pair and the resized copy are one finding, not two: if the
    # pair were reported alone as Tier 1 and excluded from Tier 2, the resized
    # copy would have nothing left to match and would never surface
    ok &= check("tier 1 groups", t1, [])
    ok &= check("tier 2 groups", t2,
                [["shot.png", "shot_copy.png", "shot_small.jpg"]])

    g2 = next(g for g in groups if g["tier"] == 2)
    exact = {f["name"]: f["exact"] for f in g2["files"]}
    ok &= check("byte-identical pair marked as one unit",
                exact["shot.png"] == exact["shot_copy.png"], True)
    ok &= check("resized copy is a different unit",
                exact["shot_small.jpg"] != exact["shot.png"], True)

    flat = [n for g in t1 + t2 for n in g]
    ok &= check("different picture left alone", "other.png" in flat, False)
    ok &= check("bob's own left alone", "bobs_own.png" in flat, False)
    # Bob's copy is the same image as Alice's, and is deliberately not grouped
    ok &= check("not compared across folders", "same_pic.jpg" in flat, False)

    print("distance is reported, so the UI can rank by confidence")
    d = [g.get("distance") for g in groups if g["tier"] == 2]
    ok &= check("has a distance", all(isinstance(x, int) for x in d) and bool(d),
                True)

    print("cache")
    hits = imgdedup.HashCache(cache)
    h_before = hits.get(alice / "shot_small.jpg",
                        (alice / "shot_small.jpg").stat().st_size,
                        (alice / "shot_small.jpg").stat().st_mtime)
    ok &= check("hash was cached",
                isinstance(h_before, bytes) and len(h_before) * 8 == imgdedup.HASH_BITS,
                True)
    hits.close()

    # replace the file with different content; a stale cache would still hand
    # back the old hash and group it with the wrong picture
    photo(alice / "shot_small.jpg", seed=77, size=(200, 150))
    groups2 = imgdedup.find_duplicates([root], tier2=True, cache_db=cache)
    ok &= check("replaced file re-hashed", paths_of(groups2, 2), [])

    print("tier2=False skips the expensive half")
    g3 = imgdedup.find_duplicates([root], tier2=False, cache_db=cache)
    ok &= check("only tier 1", {g["tier"] for g in g3}, {1})

    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


def test_imgdedup():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
