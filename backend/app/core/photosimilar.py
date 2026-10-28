"""Grouping a character's photos by visual similarity, for the 相似 sort.

Two layers. The first pulls together shots with nearly the same framing (a
burst); the second, optional, pulls together shots with the same colours (the
same outfit and set in a different pose). Nothing here writes to the photos.

── Layer 1: framing (256-bit dHash, shared with the duplicate scan) ──────────

The duplicate scan asks "is this the same picture?" and sits at a distance of
20. This asks "was this taken in the same sitting?", so the threshold is a
separate setting, measured for it. Pairs sampled by distance on two real
folders (Rocksylight, 4,392 photos; rinag_momo rina, 259) and looked at:

    41-60    6 of 6 the same shoot, different pose
    61-80    5 of 6 (Rocksylight), 8 of 8 (rina)
    81-92    0 of 6 — unrelated photos

Two random photos sit at a median of 128, so `MAX_THRESHOLD` stops at 90.

Chaining is capped: two clusters merge only when every pair across them is
within `threshold + CHAIN_SLACK`. Uncapped, threshold 80 glued sittings into a
48-photo group and 90 into 639; capped, both top out at 9.

**What layer 1 cannot do**, and why layer 2 exists. dHash compares light and
dark between neighbouring cells of a 16x16 thumbnail. Change the pose and the
silhouette moves, so to dHash the same outfit in another pose is as far away
as a different person. Measured: the 14 photos nearest to a 6-photo witch-
costume burst were all at 91-99 and **none of them was the witch costume**.
The user saw exactly this — one outfit split into blocks several photos apart.

── Layer 2: colour (HSV histogram, 18 hue x 4 saturation x 3 value) ─────────

Colour does not care about pose: the outfit and the set keep their palette.
Similarity is the Bhattacharyya coefficient of the normalised histograms,
which is one matrix product on square-rooted vectors.

HSV rather than RGB, measured. Ten pairs sampled per band, from pairs layer 1
could not link, looked at by eye:

    similarity    RGB same outfit    HSV same outfit
    >= 0.95          10 / 10            10 / 10
    0.93-0.95         8 / 10            10 / 10
    0.90-0.93        ~5 / 10            ~6 / 10

So `COLOR_DEFAULT` is 0.93, with the same kind of chaining cap as layer 1
(every cross pair >= threshold - `COLOR_SLACK`). With that, the largest four
groups on both folders and four random mid-size groups were each one outfit
in different poses; the witch costume went from 6 photos to 13, all of it.
Photos grouped: Rocksylight 16% -> 69%, rina 7% -> 81%.

Layer 2 works on photos, not on layer-1 groups, so a single shot of an outfit
still joins the rest of it; layer-1 groups are merged as they stand.

Reading is the only real cost and it is paid once: the colour signature is
computed from the same read as the dHash (the file is read into memory once
and decoded twice), and cached beside it. Rocksylight's 4,392 photos took 8 s
on a local drive.
"""
from __future__ import annotations

import io
import sqlite3
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import imgdedup
from .progress import Reporter
from .videoflags import key
from ..db import connect

DEFAULT_THRESHOLD = 70
MIN_THRESHOLD = 20
MAX_THRESHOLD = 90
CHAIN_SLACK = 10

# Colour similarity in whole percent, so the column and the slider stay integers.
COLOR_DEFAULT = 93
COLOR_MIN = 90
COLOR_MAX = 98
COLOR_SLACK = 3

_H_BINS, _S_BINS, _V_BINS = 18, 4, 3
COLOR_BINS = _H_BINS * _S_BINS * _V_BINS


def clamp(threshold) -> int:
    try:
        t = int(threshold)
    except (TypeError, ValueError):
        return DEFAULT_THRESHOLD
    return max(MIN_THRESHOLD, min(MAX_THRESHOLD, t))


def clamp_color(value) -> int:
    """0 means off; anything else is held to the measured range."""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return COLOR_DEFAULT
    if v <= 0:
        return 0
    return max(COLOR_MIN, min(COLOR_MAX, v))


# ── signatures and their cache ───────────────────────────────────────────────

def color_signature(src) -> Optional[bytes]:
    """216-bin HSV histogram as uint16 counts, or None if it will not decode."""
    try:
        import numpy as np
        from PIL import Image
        with Image.open(src) as im:
            try:
                im.draft("RGB", (128, 128))
            except Exception:
                pass
            im = im.convert("RGB")
            im.thumbnail((64, 64))
            hsv = np.asarray(im.convert("HSV")).reshape(-1, 3).astype(np.int32)
        hb = hsv[:, 0] * _H_BINS // 256
        sb = hsv[:, 1] * _S_BINS // 256
        vb = hsv[:, 2] * _V_BINS // 256
        idx = (hb * _S_BINS + sb) * _V_BINS + vb
        return np.bincount(idx, minlength=COLOR_BINS).astype(np.uint16).tobytes()
    except Exception:
        return None


class ColorCache:
    """Same shape and invalidation rule as `imgdedup.HashCache`, own table.

    Lives in the same file as the hashes so one path + size + mtime check
    covers both, and a photo replaced in place is recomputed for both.
    """

    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.execute("""CREATE TABLE IF NOT EXISTS img_color (
                                 path  TEXT PRIMARY KEY,
                                 size  INTEGER NOT NULL,
                                 mtime REAL    NOT NULL,
                                 sig   BLOB)""")
        self._pending: list = []

    def get(self, path: Path, size: int, mtime: float) -> Optional[bytes]:
        row = self.conn.execute(
            "SELECT size, mtime, sig FROM img_color WHERE path = ?",
            (str(path),)).fetchone()
        if not row or row[0] != size or abs(row[1] - mtime) > 1:
            return None
        return row[2]

    def put(self, path: Path, size: int, mtime: float, sig: Optional[bytes]):
        self._pending.append((str(path), size, mtime, sig))
        if len(self._pending) >= 200:
            self.flush()

    def flush(self):
        if self._pending:
            self.conn.executemany(
                "INSERT OR REPLACE INTO img_color (path, size, mtime, sig) "
                "VALUES (?, ?, ?, ?)", self._pending)
            self.conn.commit()
            self._pending.clear()

    def close(self):
        self.flush()
        self.conn.close()


def split_cached(photos: List[Tuple[Path, int, float]], cache_db: Path
                 ) -> Tuple[Dict[str, bytes], Dict[str, bytes],
                            List[Tuple[Path, int, float]]]:
    """Cached hashes, cached colour signatures, and photos still needing a read.

    A photo is "missing" when either is absent. Reads the caches only — no
    photo is opened — so it is safe from a request handler.
    """
    hc = imgdedup.HashCache(cache_db)
    cc = ColorCache(cache_db)
    have: Dict[str, bytes] = {}
    colors: Dict[str, bytes] = {}
    missing = []
    try:
        for p, size, mtime in photos:
            h = hc.get(p, size, mtime)
            c = cc.get(p, size, mtime)
            if h:
                have[str(p)] = h
            if c:
                colors[str(p)] = c
            if not h or not c:
                missing.append((p, size, mtime))
    finally:
        hc.close()
        cc.close()
    return have, colors, missing


def fill(missing: List[Tuple[Path, int, float]], cache_db: Path,
         progress: Optional[Callable] = None) -> dict:
    """Compute whatever each photo is missing, from a single read of the file.

    The read is the cost (~33 ms per photo over SMB); decoding a second time
    from memory with a draft-sized JPEG is not.
    """
    rp = Reporter(progress, phases=1)
    rp.phase(1, "讀取照片", total=len(missing))
    hc = imgdedup.HashCache(cache_db)
    cc = ColorCache(cache_db)
    done = failed = 0
    started = time.monotonic()
    try:
        for p, size, mtime in missing:
            if rp.cancelled:
                break
            rp.step(p.name)
            need_h = hc.get(p, size, mtime) is None
            need_c = cc.get(p, size, mtime) is None
            try:
                data = p.read_bytes()
            except OSError:
                data = None
            h = c = None
            if data is not None:
                if need_h:
                    h = imgdedup.image_hash(io.BytesIO(data))
                if need_c:
                    c = color_signature(io.BytesIO(data))
            # Stored even when None, as the duplicate scan does: a file that
            # will not decode should not be re-read on every open.
            if need_h:
                hc.put(p, size, mtime, h)
            if need_c:
                cc.put(p, size, mtime, c)
            if data is not None and (h or not need_h) and (c or not need_c):
                done += 1
            else:
                failed += 1
    finally:
        hc.close()
        cc.close()
    rp.finish()
    return {"hashed": done, "unreadable": failed,
            "seconds": round(time.monotonic() - started, 1)}


# ── grouping ─────────────────────────────────────────────────────────────────

def groups(hashes: List[bytes], threshold: int,
           colors: Optional[List[Optional[bytes]]] = None,
           color_threshold: int = 0) -> List[List[int]]:
    """Indices of photos that belong together; singletons are left out.

    `colors` is parallel to `hashes` (None where a photo has no signature);
    layer 2 runs only when it is given and `color_threshold` is not 0.
    """
    import numpy as np

    n = len(hashes)
    if n < 2:
        return []
    threshold = clamp(threshold)
    arr = (np.frombuffer(b"".join(hashes), dtype=np.uint8)
           .reshape(n, -1).view(np.uint16))
    pop = imgdedup._popcount_table()

    root = list(range(n))
    members: Dict[int, List[int]] = {}

    def find(x: int) -> int:
        while root[x] != x:
            root[x] = root[root[x]]
            x = root[x]
        return x

    def union_edges(ei, ej, order, cross_ok):
        for k in order:
            a, b = find(int(ei[k])), find(int(ej[k]))
            if a == b:
                continue
            ma, mb = members.get(a, [a]), members.get(b, [b])
            if len(ma) * len(mb) > 1 and not cross_ok(ma, mb):
                continue
            if len(ma) < len(mb):
                a, b, ma, mb = b, a, mb, ma
            root[b] = a
            members[a] = ma + mb
            members.pop(b, None)

    # Layer 1: framing. Row by row, so no n x n matrix is ever held.
    cap = threshold + CHAIN_SLACK
    ei, ej, ed = [], [], []
    for i in range(n - 1):
        d = pop[arr[i + 1:] ^ arr[i]].sum(axis=1, dtype=np.int32)
        hit = np.nonzero(d <= threshold)[0]
        if len(hit):
            ei.append(np.full(len(hit), i, dtype=np.int32))
            ej.append(hit.astype(np.int32) + i + 1)
            ed.append(d[hit])
    if ei:
        ei, ej, ed = np.concatenate(ei), np.concatenate(ej), np.concatenate(ed)
        union_edges(ei, ej, np.argsort(ed, kind="stable"),
                    lambda ma, mb: int(pop[arr[ma][:, None, :] ^ arr[mb][None, :, :]]
                                       .sum(axis=2, dtype=np.int32).max()) <= cap)

    # Layer 2: colour, over photos that have a signature.
    cth = clamp_color(color_threshold) if colors is not None else 0
    if cth:
        ok = [i for i in range(n) if colors[i]]
        if len(ok) >= 2:
            X = (np.frombuffer(b"".join(colors[i] for i in ok), dtype=np.uint16)
                 .reshape(len(ok), COLOR_BINS).astype(np.float32))
            X = np.sqrt(X / np.maximum(X.sum(axis=1, keepdims=True), 1))
            lo_link, lo_cross = cth / 100.0, (cth - COLOR_SLACK) / 100.0
            full = np.full(n, -1, dtype=np.int64)
            full[ok] = np.arange(len(ok))
            ci, cj, cs = [], [], []
            block = 512
            for s in range(0, len(ok), block):
                S = X[s:s + block] @ X.T
                for r in range(S.shape[0]):
                    row = S[r, s + r + 1:]
                    hit = np.nonzero(row >= lo_link)[0]
                    if len(hit):
                        ci.append(np.full(len(hit), ok[s + r], dtype=np.int32))
                        cj.append(np.asarray(ok, dtype=np.int32)[hit + s + r + 1])
                        cs.append(row[hit])
            if ci:
                ci, cj, cs = np.concatenate(ci), np.concatenate(cj), np.concatenate(cs)

                def colour_ok(ma, mb):
                    xa, xb = full[ma], full[mb]
                    if (xa < 0).any() or (xb < 0).any():
                        return False       # a member with no signature cannot vouch
                    return float((X[xa] @ X[xb].T).min()) >= lo_cross

                union_edges(ci, cj, np.argsort(-cs, kind="stable"), colour_ok)

    return [sorted(v) for v in members.values() if len(v) > 1]


def group_map(have: Dict[str, bytes], threshold: int,
              colors: Optional[Dict[str, bytes]] = None,
              color_threshold: int = 0) -> Tuple[Dict[str, int], int]:
    """{path: group id} for photos in a group, and the number of groups."""
    paths = list(have)
    col = [colors.get(p) for p in paths] if colors is not None else None
    out: Dict[str, int] = {}
    gs = groups([have[p] for p in paths], threshold, col, color_threshold)
    for gid, idx in enumerate(gs):
        for i in idx:
            out[paths[i]] = gid
    return out, len(gs)


# ── the chosen settings, per character folder ────────────────────────────────

def saved(folder) -> Optional[dict]:
    """{threshold, color} an editor applied here, or None if never.

    `color` is None for folders applied before the colour layer existed —
    distinct from 0, which is an editor turning it off.
    """
    conn = connect()
    try:
        r = conn.execute("SELECT threshold, color_threshold FROM photo_similar "
                         "WHERE folder = ?", (key(folder),)).fetchone()
        if not r:
            return None
        c = r["color_threshold"]
        return {"threshold": int(r["threshold"]),
                "color": None if c is None else int(c)}
    finally:
        conn.close()


def save(folder, threshold, color) -> dict:
    from datetime import datetime, timezone
    th, co = clamp(threshold), clamp_color(color)
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO photo_similar (folder, threshold, color_threshold, applied_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(folder) DO UPDATE SET "
            "threshold = excluded.threshold, color_threshold = excluded.color_threshold, "
            "applied_at = excluded.applied_at",
            (key(folder), th, co, datetime.now(timezone.utc).isoformat()))
        conn.commit()
    finally:
        conn.close()
    return {"threshold": th, "color": co}


def move_folder(src, dst) -> None:
    """Carry the setting when a character folder is renamed or merged.

    Same obligation as `videoflags.move_tree`: the key is a path, so a move
    that forgets this makes the 相似 button quietly vanish in browse mode. On a
    merge the destination keeps its own setting if it has one — that folder's
    editor tuned it for that folder's photos.
    """
    a, b = key(src), key(dst)
    if a == b:
        return
    conn = connect()
    try:
        has_dst = conn.execute("SELECT 1 FROM photo_similar WHERE folder = ?",
                               (b,)).fetchone()
        if has_dst:
            conn.execute("DELETE FROM photo_similar WHERE folder = ?", (a,))
        else:
            conn.execute("UPDATE photo_similar SET folder = ? WHERE folder = ?",
                         (b, a))
        conn.commit()
    finally:
        conn.close()
