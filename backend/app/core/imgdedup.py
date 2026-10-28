"""Finding duplicate and near-duplicate images.

Same two tiers as the video scan, but the differences matter:

  * Tier 2 is cheap here. Videos need ffmpeg to extract five frames per file
    and a duration pre-cluster to keep the comparisons down; an image just
    gets decoded once. What costs money instead is *reading* the file — the
    library lives on a NAS, so every hash is a network round trip. Hence the
    hash cache, which makes a re-scan nearly free.

  * Tier 2 compares within one character folder at a time, not across the
    whole scope. 39k images library-wide is 770M pairs, and two people owning
    a copy of the same picture is not a duplicate anyone wants deleted.

Perceptual hash is a 256-bit dHash — 16x16 rather than the video scan's 8x8
aHash. dHash compares each pixel with its neighbour instead of with the frame
mean, which is steadier under the re-compression and brightness shifts that
re-saved images pick up, and the extra bits were measured to be necessary:
at 64 bits, real matches and unrelated photos both landed at a distance of
3-5, so no threshold separated them. Checked against a 256-bit hash and a
mean pixel difference, `Asian_Sexdoll_1000` and `_1073` scored 5/64 — visibly
different pictures. The bits are nearly free because reading the file over
SMB dominates; decoding to 17x16 instead of 9x8 costs nothing.
"""
from __future__ import annotations

import os
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import scancount, watermark
from .progress import Reporter

from .dedup import partial_hash
from .media import IMAGE_EXT, VAM_DIR

HASH_BITS = 256
HASH_N = 16                    # 16x16 comparisons -> 256 bits

# Out of 256. Measured on 3192 real photos, scoring every match by mean pixel
# difference (independent of the hash) and counting how many looked wrong:
#
#     distance   groups   suspicious
#      0-14        20        0%
#     15-19        21       10%
#     20-24         6       33%
#
# 20 keeps nearly all the recall for a little noise, which is the right trade
# when every match is reviewed against a thumbnail. Batch deletion is
# restricted to byte-identical files regardless of this setting.
DEFAULT_MAX_DISTANCE = 20
SAFE_BATCH_DISTANCE = 14        # the band where nothing looked wrong

_POP16 = None


def _popcount_table():
    """128 KB lookup table; numpy has no native popcount for uint64."""
    global _POP16
    if _POP16 is None:
        import numpy as np
        _POP16 = np.array([bin(i).count("1") for i in range(1 << 16)],
                          dtype=np.uint8)
    return _POP16


# ── hashing ──────────────────────────────────────────────────────────────────

def image_hash(path: Path) -> Optional[bytes]:
    """256-bit difference hash as 32 bytes, or None if it will not decode."""
    try:
        import numpy as np
        from PIL import Image
        with Image.open(path) as im:
            # draft() lets the JPEG decoder skip straight to a small size,
            # which is most of the decode cost on a 4000px photo
            try:
                im.draft("L", (HASH_N * 4, HASH_N * 4))
            except Exception:
                pass
            im = im.convert("L").resize((HASH_N + 1, HASH_N), Image.LANCZOS)
            a = np.asarray(im, dtype=np.int16)
        bits = (a[:, 1:] > a[:, :-1]).flatten()
        return np.packbits(bits).tobytes()
    except Exception:
        return None


def image_size(path: Path) -> Tuple[int, int]:
    """(width, height) from the header alone — no full decode."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.size
    except Exception:
        return (0, 0)


# ── hash cache ───────────────────────────────────────────────────────────────
# Keyed on (path, size, mtime) so an edited or replaced file re-hashes itself.

def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    # BLOB, not INTEGER: the hash is 256 bits and SQLite's INTEGER is a signed
    # 64-bit one, which overflows on anything wider (and on any 64-bit hash
    # with the top bit set).
    conn.execute("""CREATE TABLE IF NOT EXISTS img_hash (
                        path  TEXT PRIMARY KEY,
                        size  INTEGER NOT NULL,
                        mtime REAL    NOT NULL,
                        dhash BLOB)""")
    return conn


class HashCache:
    def __init__(self, db_path: Path):
        self.conn = _connect(db_path)
        self._pending: List[tuple] = []

    def get(self, path: Path, size: int, mtime: float) -> Optional[bytes]:
        row = self.conn.execute(
            "SELECT size, mtime, dhash FROM img_hash WHERE path = ?",
            (str(path),)).fetchone()
        if not row:
            return None
        # a float mtime survives the round trip through SQLite exactly, but
        # SMB can report it with a slightly different precision after a copy
        if row[0] != size or abs(row[1] - mtime) > 1:
            return None
        return row[2]

    def put(self, path: Path, size: int, mtime: float, h: Optional[bytes]):
        self._pending.append((str(path), size, mtime, h))
        if len(self._pending) >= 200:
            self.flush()

    def flush(self):
        if not self._pending:
            return
        self.conn.executemany(
            "INSERT OR REPLACE INTO img_hash (path, size, mtime, dhash) "
            "VALUES (?, ?, ?, ?)", self._pending)
        self.conn.commit()
        self._pending.clear()

    def forget(self, path: Path):
        self.conn.execute("DELETE FROM img_hash WHERE path = ?", (str(path),))
        self.conn.commit()

    def close(self):
        self.flush()
        self.conn.close()


# ── scanning ─────────────────────────────────────────────────────────────────

def scan_images(root: Path) -> List[Tuple[Path, int, float]]:
    out = []
    for dirpath, dirs, names in os.walk(root):
        # VAM's own avatar copies are not the user's photos (media.VAM_DIR)
        dirs[:] = [d for d in dirs if d.lower() != VAM_DIR]
        for n in names:
            if Path(n).suffix.lower() not in IMAGE_EXT:
                continue
            p = Path(dirpath) / n
            try:
                st = p.stat()
            except OSError:
                continue
            out.append((p, st.st_size, st.st_mtime))
    return out


def _owner(path: Path, root: Path) -> str:
    """The character folder a file belongs to — Tier 2's comparison unit.

    Files loose in a root are their own bucket rather than one giant pool, so
    a stray photo never gets compared against the entire library.
    """
    try:
        rel = path.relative_to(root)
    except ValueError:
        return str(path.parent)
    return str(root / rel.parts[0]) if len(rel.parts) > 1 else str(path.parent)


def _info(path: Path, size: int, mtime: float, label: str,
          dims: Optional[Tuple[int, int]] = None) -> dict:
    w, h = dims if dims is not None else image_size(path)
    return {"path": str(path), "name": path.name, "size": size, "mtime": mtime,
            "width": w, "height": h, "label": label}


def _pair_units(hashes: List[bytes], max_distance: int) -> List[List[tuple]]:
    """Indices grouped by hash proximity, one hop from an anchor.

    Deliberately not transitive closure. Similarity chains — A matches B and B
    matches C while A and C are unrelated — which is exactly how the video
    Tier 2 produced its worst false positives. Anchoring caps the damage at
    "everything in this group resembles the first file".

    Returns groups of indices; the anchor is first and the caller can recover
    each member's distance from it.
    """
    import numpy as np

    # (n, 32) bytes -> (n, 16) uint16 so one table lookup covers 16 bits
    raw = np.frombuffer(b"".join(hashes), dtype=np.uint8)
    arr = raw.reshape(len(hashes), -1).view(np.uint16)
    pop = _popcount_table()

    out: List[List[tuple]] = []
    taken = np.zeros(len(hashes), dtype=bool)
    for i in range(len(hashes)):
        if taken[i]:
            continue
        d = pop[arr[i:] ^ arr[i]].sum(axis=1, dtype=np.int32)
        hit = np.nonzero((d <= max_distance) & ~taken[i:])[0] + i
        if len(hit) < 2:
            continue
        taken[hit] = True
        out.append([(int(j), int(d[j - i])) for j in hit])
    return out


def hamming(a: bytes, b: bytes) -> int:
    return sum(bin(x ^ y).count("1") for x, y in zip(a, b))


def find_duplicates(roots: List[Path], tier2: bool = True,
                    max_distance: int = DEFAULT_MAX_DISTANCE,
                    cache_db: Optional[Path] = None,
                    root_labels: Optional[Dict[str, str]] = None,
                    progress: Optional[Callable[[Any], None]] = None,
                    count_db: Optional[Path] = None
                    ) -> List[dict]:
    """Same phase/count/cancel contract as the video scan — see `dedup.py`."""
    root_labels = root_labels or {}
    rp = Reporter(progress, phases=4 if tier2 else 2)

    expect = scancount.expected(count_db, "images", roots)
    rp.phase(1, "走訪圖片", total=expect, approx=True,
             hint=f"上次共 {expect:,} 張" if expect else "")

    files = []                       # (path, size, mtime, label, owner)
    seen = set()
    per_root: Dict[str, int] = {}
    for r in roots:
        rp.note(f"掃描：{r.name or r}")
        label = root_labels.get(str(r), "歸檔")
        n = 0
        for path, size, mtime in scan_images(r):
            key = str(path)
            if key in seen:          # nested roots must not double-count
                continue
            seen.add(key)
            files.append((path, size, mtime, label, _owner(path, r)))
            n += 1
            rp.step(path.name)
        per_root[str(r)] = n
        if rp.cancelled:
            return []
    scancount.save(count_db, "images", per_root)

    # ── Tier 1: byte-identical, anywhere in scope ──
    # The result is *units*: a unit is either one file or a set of files that
    # are byte-for-byte the same. Tier 2 then compares units rather than
    # files, which is what lets "two identical copies plus one resized copy"
    # come out as a single group of three. Comparing files directly would drop
    # the identical pair out of Tier 2 entirely and hide the resized one.
    units: List[List[Tuple[Path, int, float, str]]] = []
    unit_owner: List[str] = []        # parallel to units
    grouped = set()

    by_size = defaultdict(list)
    for e in files:
        by_size[e[1]].append(e)
    rp.phase(2, "比對相同大小",
             total=sum(len(g) for g in by_size.values() if len(g) >= 2))
    for size, grp in by_size.items():
        if len(grp) < 2:
            continue
        if rp.cancelled:
            return []           # units are half-built; nothing safe to report
        by_hash = defaultdict(list)
        for path, sz, mtime, label, own in grp:
            rp.step(path.name)
            hh = partial_hash(path)
            if hh:
                by_hash[hh].append((path, sz, mtime, label, own))
        for _, g in by_hash.items():
            if len(g) >= 2:
                units.append([(p, s, m, lb) for p, s, m, lb, _o in g])
                # a byte-identical set can straddle folders; the first member
                # decides which folder's Tier 2 pass it takes part in
                unit_owner.append(g[0][4])
                for e in g:
                    grouped.add(str(e[0]))
    exact_units = len(units)          # units below this index are Tier 1 sets

    for path, size, mtime, label, own in files:
        if str(path) in grouped:
            continue
        units.append([(path, size, mtime, label)])
        unit_owner.append(own)

    if not tier2:
        return [{"tier": 1, "files": [_info(p, s, m, lb) for p, s, m, lb in u]}
                for u in units[:exact_units]]

    # ── Tier 2: visually similar, within one character folder ──
    groups: List[dict] = []
    absorbed = set()
    cache = HashCache(cache_db) if cache_db else None
    try:
        by_owner = defaultdict(list)
        for i, own in enumerate(unit_owner):
            by_owner[own].append(i)

        rp.phase(3, "相似比對", total=len(units))
        for own, idxs in by_owner.items():
            name = Path(own).name
            if rp.cancelled:
                # Break out rather than return: the Tier 1 sets below are
                # already final, and they are the cheap half of the answer.
                break
            usable, hashes = [], []
            for i in idxs:
                rp.step(f"{name}／{Path(units[i][0][0]).name}")
                path, size, mtime, _lb = units[i][0]
                h = cache.get(path, size, mtime) if cache else None
                if h is None:
                    h = image_hash(path)
                    if cache:
                        cache.put(path, size, mtime, h)
                if h is None:
                    continue
                usable.append(i)
                hashes.append(h)
            if len(usable) < 2:
                continue
            for hits in _pair_units(hashes, max_distance):
                members = [(usable[j], dist) for j, dist in hits]
                out_files = []
                for slot, (ui, dist) in enumerate(members):
                    absorbed.add(ui)
                    for p, s, m, lb in units[ui]:
                        # files sharing an `exact` value are byte-identical;
                        # the UI can say so instead of implying "merely similar"
                        out_files.append(_info(p, s, m, lb)
                                         | {"distance": dist, "exact": slot})
                groups.append({"tier": 2, "folder": name,
                               "distance": max(d for _u, d in members),
                               "files": out_files})
    finally:
        if cache:
            cache.close()

    # ── watermarks, within each similar group ──
    # Each file gets `wm`: the share of the picture covered by an overlay the
    # other copies lack (0 = clean), or None where it cannot be told. The
    # keeper ladder uses it; see `watermark.py` for why the old ladder kept
    # the watermarked copy. Skipped on cancel: the groups are still right,
    # they just fall back to the old ladder.
    if not rp.cancelled:
        rp.phase(4, "比對浮水印", total=len(groups))
        for g in groups:
            if rp.cancelled:
                break
            rp.step(f"{g['folder']}／{g['files'][0]['name']}")
            try:
                areas = watermark.group_areas([Path(f["path"])
                                               for f in g["files"]])
            except Exception:
                areas = [None] * len(g["files"])
            for f, a in zip(g["files"], areas):
                f["wm"] = None if a is None else round(a, 5)

    # exact-duplicate sets that no similar file joined stay Tier 1 on their own
    for i in range(exact_units):
        if i not in absorbed:
            groups.append({"tier": 1,
                           "files": [_info(p, s, m, lb)
                                     for p, s, m, lb in units[i]]})
    rp.finish()
    return groups
