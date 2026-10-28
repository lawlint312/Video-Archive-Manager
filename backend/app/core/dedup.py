"""
Duplicate detection — ported from the desktop app.
Tier 1: size + partial MD5 (exact/near-exact duplicates).
Tier 2: duration clustering + perceptual hash (same content, different quality).

Perceptual hash is computed via ffmpeg (8x8 grayscale frame → 64-bit average
hash), so no OpenCV dependency is needed.
"""
from __future__ import annotations

import hashlib
import logging
import os
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any, List, Optional, Callable, Dict, Tuple

from . import scancount
from .checkcache import MISS, Store
from .media import (scan_videos_with_stat, probe_video, COLOUR_FIX,
                    SUBPROCESS_FLAGS)
from .progress import Reporter

log = logging.getLogger("vam.dedup")

PERSON_INFO = "person_info.json"


# ── Tier 1: partial hash ─────────────────────────────────────────────────────

def partial_hash(path: Path, chunk: int = 4 << 20) -> Optional[str]:
    """Size + MD5 of the first 4 MB — a fast, cheap duplicate key."""
    try:
        size = path.stat().st_size
        if size == 0:
            return None
        h = hashlib.md5()
        h.update(str(size).encode())
        with open(path, "rb") as f:
            h.update(f.read(chunk))
        return h.hexdigest()
    except Exception:
        return None


# ── Tier 2: perceptual hash via ffmpeg ───────────────────────────────────────

def _frame_ahash(video: Path, t: float) -> Optional[int]:
    """Extract one frame at time t, downscale to 8x8 gray, return 64-bit aHash.

    The retry with `COLOUR_FIX` is for files whose colour metadata swscale
    refuses; `format=gray` is a colour conversion, so this path fails on them
    exactly as thumbnails do. Here the symptom is even quieter — the file just
    never matches anything, and a missing duplicate is invisible.
    """
    for vf in ("scale=8:8,format=gray", f"{COLOUR_FIX},scale=8:8,format=gray"):
        data = b""
        try:
            r = subprocess.run(
                ["ffmpeg", "-ss", str(max(0, t)), "-i", str(video), "-vframes", "1",
                 "-vf", vf, "-f", "rawvideo", "-"],
                capture_output=True, timeout=30, creationflags=SUBPROCESS_FLAGS)
            data = r.stdout
        except Exception:
            return None
        if len(data) < 64:
            continue
        px = data[:64]
        mean = sum(px) / 64.0
        h = 0
        for b in px:
            h = (h << 1) | (1 if b > mean else 0)
        return h
    return None


def video_phash(video: Path,
                pcts=(0.10, 0.30, 0.50, 0.70, 0.90)) -> Optional[List[int]]:
    dur, _, _ = probe_video(video)
    if dur <= 0:
        return None
    hashes = []
    for p in pcts:
        h = _frame_ahash(video, dur * p)
        if h is not None:
            hashes.append(h)
    return hashes or None


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def phash_distance(ha: List[int], hb: List[int]) -> Optional[float]:
    """Mean Hamming distance over the sampled frames, 0 (identical) to 64."""
    n = min(len(ha), len(hb))
    if n == 0:
        return None
    return sum(_hamming(ha[i], hb[i]) for i in range(n)) / n


def phash_similar(ha: List[int], hb: List[int], max_avg: float = 10.0) -> bool:
    d = phash_distance(ha, hb)
    return d is not None and d <= max_avg


# ── Remembered answers ───────────────────────────────────────────────────────
# The three things a scan computes per file — the head hash, the duration and
# the frame hashes — each kept on (path, size, mtime), so a second scan of the
# same library, or a scan rerun after the service restarted half way through,
# only pays for files it has not seen. Same rule and store as the convert
# scan's checks (`checkcache`): a file replaced on disk is a miss.
#
# A failure is never remembered (no hash, no duration): over the NAS it is as
# often a hiccup as a property of the file, and a remembered one would hide a
# duplicate for good. Bump a version when what that kind computes changes.
CACHE_VERSIONS = {"head": 1, "probe": 1, "phash": 1}


def _remembered(store: Optional[Store], kind: str, path: Path, size: int,
                mtime: float, compute: Callable[[], Any], ok: Callable[[Any], bool],
                stats: Optional[dict] = None):
    if store is None:
        return compute()
    v = store.get(kind, CACHE_VERSIONS[kind], path, size, mtime)
    if v is not MISS:
        if stats is not None:
            stats[kind] = stats.get(kind, 0) + 1
        return v
    v = compute()
    if ok(v):
        store.put(kind, CACHE_VERSIONS[kind], path, size, mtime, v)
    return v


def _probe(store, path: Path, size: int, mtime: float, stats=None):
    dur, w, h = _remembered(store, "probe", path, size, mtime,
                            lambda: list(probe_video(path)),
                            lambda v: v[0] > 0, stats)
    return dur, w, h


# ── Result building ──────────────────────────────────────────────────────────

def _file_info(path: Path, size: int, mtime: float, label: str = "",
               probe: bool = True, store: Optional[Store] = None) -> dict:
    dur = w = h = 0
    if probe:
        dur, w, h = _probe(store, path, size, mtime)
    return {"path": str(path), "name": path.name, "size": size, "mtime": mtime,
            "duration": dur, "width": w, "height": h, "label": label}


# ── Main scan ────────────────────────────────────────────────────────────────

def _walk_all(root: Path) -> List[Tuple[Path, int]]:
    """Every file under root — not just videos — as (path, size)."""
    out = []
    for dirpath, _dirs, names in os.walk(root):
        for n in names:
            p = Path(dirpath) / n
            try:
                out.append((p, p.stat().st_size))
            except OSError:
                continue
    return out


def build_merge_plan(src: Path, dst: Path) -> List[dict]:
    """Categorise everything in `src` for a merge into `dst`:
       delete     – an identical file (same partial hash) already exists in dst
       rename     – that path is taken by different content → move aside
       move       – no conflict
       merge_info – both sides have a person_info.json; combine, do not rename

    Covers *all* files, not only videos. Moving the videos alone used to leave
    the photos, the notes and person_info.json (aliases, tags, the avatar
    choice) behind in a folder the UI still listed as a character.

    Relative paths are preserved, so 角色/IG/ stays 角色/IG/ — flattening it
    would drop those videos out of the profile tab and into the video list.
    """
    src_files = _walk_all(src)
    src_sizes = {size for _f, size in src_files}

    dst_hashes = set()
    for f, size in _walk_all(dst):
        # partial_hash mixes the file size into the digest, so a destination
        # file of a size no source file has can never match one — and hashing
        # it means reading 4 MB off the NAS for nothing. On a 935-file target
        # folder that was 3.7 GB of reads and ~60 s before the plan appeared.
        if size not in src_sizes:
            continue
        h = partial_hash(f)
        if h:
            dst_hashes.add(h)

    plan = []
    for src_file, size in src_files:
        rel = src_file.relative_to(src)
        target = dst / rel
        entry = {"src": str(src_file), "name": str(rel), "size": size}

        if src_file.name == PERSON_INFO and target.exists():
            plan.append({**entry, "action": "merge_info", "dst": str(target)})
            continue

        h = partial_hash(src_file) if dst_hashes else None
        if h and h in dst_hashes:
            plan.append({**entry, "action": "delete", "dst": None})
        elif target.exists():
            stem, suffix = target.stem, target.suffix
            n = 1
            while (target.parent / f"{stem}_{n}{suffix}").exists():
                n += 1
            plan.append({**entry, "action": "rename",
                         "dst": str(target.parent / f"{stem}_{n}{suffix}")})
        else:
            plan.append({**entry, "action": "move", "dst": str(target)})
    return plan


def _merge_person_info(src_file: Path, dst_file: Path):
    """Combine two person_info.json files instead of keeping both.

    The destination wins on single-valued fields (it is the folder being kept);
    aliases and tags are unioned, since losing an alias silently breaks the
    catalogue lookup's name matching.
    """
    import json

    def load(p):
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}

    a, b = load(dst_file), load(src_file)
    out = dict(b)
    out.update({k: v for k, v in a.items() if v not in (None, "", [], {})})
    for key in ("aliases", "tags"):
        seen, merged = set(), []
        for v in list(a.get(key) or []) + list(b.get(key) or []):
            k = str(v).strip().lower()
            if v and k not in seen:
                seen.add(k)
                merged.append(v)
        out[key] = merged
    notes = [n for n in (a.get("notes"), b.get("notes")) if n and n.strip()]
    out["notes"] = "\n".join(dict.fromkeys(notes))
    dst_file.write_text(json.dumps(out, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    src_file.unlink()


def _is_empty(p: Path) -> bool:
    """Empty, with the directory handle closed before we act on the answer.

    `any(p.rglob('*'))` short-circuits on the first entry and abandons the
    generator, leaving a scandir handle open until the garbage collector gets
    to it. Racing an rmdir against that on an SMB share is asking for trouble.
    """
    try:
        with os.scandir(p) as it:
            return next(it, None) is None
    except OSError:
        return False


def _prune_empty_dirs(root: Path):
    """Remove leftover empty subfolders, deepest first (e.g. an emptied IG/)."""
    for dirpath, dirnames, _files in os.walk(root, topdown=False):
        for d in dirnames:
            p = Path(dirpath) / d
            if _is_empty(p):
                try:
                    p.rmdir()
                except OSError:
                    pass


def run_merge(plan: List[dict], src: Path, dst: Path,
              progress=None) -> dict:
    """Execute a merge plan; removes the source folder if it ends up empty."""
    from .media import move_with_progress
    moved = deleted = failed = 0
    errors: List[str] = []
    total = sum(1 for p in plan if p["action"] in ("move", "rename"))
    idx = 0
    dst.mkdir(parents=True, exist_ok=True)

    for item in plan:
        sf = Path(item["src"])
        action = item["action"]
        try:
            if action == "delete":
                sf.unlink()
                deleted += 1
                if progress:
                    progress({"phase": "delete", "name": sf.name,
                              "moved": moved, "deleted": deleted, "total": total})
            elif action == "merge_info":
                _merge_person_info(sf, Path(item["dst"]))
                moved += 1
            else:
                idx += 1
                df = Path(item["dst"])
                df.parent.mkdir(parents=True, exist_ok=True)
                def cb(done, tot, speed, i=idx, nm=sf.name):
                    if progress:
                        progress({"phase": "move", "name": nm, "index": i,
                                  "total": total, "done": done, "size": tot,
                                  "speed": speed})
                move_with_progress(sf, df, cb)
                moved += 1
        except Exception as e:
            failed += 1
            errors.append(f"{item['name']}: {e}")
            log.warning("merge: %s failed on %s: %s", action, sf, e)

    # Tidy up the emptied source. A Synology share with the Recycle Bin on
    # turns this rmdir into a move to #recycle, and that has been seen to leave
    # an entry behind that lists but cannot be opened — so check afterwards and
    # say so, instead of leaving the user with an unexplained Windows error.
    leftover = None
    _prune_empty_dirs(src)
    if src.exists() and _is_empty(src):
        try:
            src.rmdir()
        except OSError as e:
            leftover = f"來源資料夾無法刪除：{e}"
            log.warning("merge: could not remove %s: %s", src, e)
    elif src.exists():
        n = len(_walk_all(src))
        leftover = f"來源資料夾仍有 {n} 個檔案未處理"
        log.warning("merge: %s still holds %d files", src, n)

    if leftover is None and src.exists():
        # rmdir reported success but the entry is still listed
        leftover = ("來源資料夾在 NAS 上仍殘留一個項目，"
                    "可能需要到 DSM File Station 手動刪除")
        log.warning("merge: %s still listed after rmdir", src)

    log.info("merge %s → %s: moved=%d deleted=%d failed=%d%s",
             src.name, dst, moved, deleted, failed,
             f" leftover={leftover!r}" if leftover else "")
    return {"moved": moved, "deleted": deleted, "failed": failed,
            "errors": errors[:20], "leftover": leftover}


def find_duplicates(roots: List[Path], tier2: bool = False,
                    threshold: int = 1,
                    root_labels: Optional[Dict[str, str]] = None,
                    progress: Optional[Callable[[Any], None]] = None,
                    count_db: Optional[Path] = None,
                    cache_db: Optional[Path] = None,
                    stats: Optional[dict] = None) -> List[dict]:
    """Duplicate groups across `roots`.

    Reports structured progress (see `core/progress.py`) and stops early when
    the job is cancelled, returning the groups found so far — the Tier 1
    results are complete and real even if Tier 2 never ran, so throwing them
    away would waste the minutes already spent.

    `cache_db` remembers each file's hashes and duration (see
    `CACHE_VERSIONS`); `stats` is filled with how many of each were reused.
    """
    store = Store(cache_db) if cache_db else None
    try:
        return _find(roots, tier2, threshold, root_labels or {}, progress,
                     count_db, store, stats if stats is not None else {})
    finally:
        if store:
            store.close()


def _find(roots, tier2, threshold, root_labels, progress, count_db, store,
          stats) -> List[dict]:
    rp = Reporter(progress, phases=4 if tier2 else 2)

    # ── phase 1: walk ──
    # The only phase whose size cannot be known in advance; last run's counts
    # stand in so there is still something to measure against.
    expect = scancount.expected(count_db, "videos", roots)
    rp.phase(1, "走訪檔案", total=expect, approx=True,
             hint=f"上次共 {expect:,} 個" if expect else "")

    # (path, size, mtime, label) — the label says where the file lives, which is
    # the first tie-breaker when suggesting which copy to keep.
    all_files = []
    seen_paths = set()
    per_root: Dict[str, int] = {}
    for r in roots:
        rp.note(f"掃描：{r.name or r}")
        label = root_labels.get(str(r), "歸檔")
        n = 0
        for path, size, mtime in scan_videos_with_stat(r):
            key = str(path)
            if key in seen_paths:       # nested roots must not double-count
                continue
            seen_paths.add(key)
            all_files.append((path, size, mtime, label))
            n += 1
            rp.step(path.name)
        per_root[str(r)] = n
        if rp.cancelled:
            return []                   # nothing has been compared yet
    scancount.save(count_db, "videos", per_root)

    groups: List[dict] = []
    tier1_paths = set()

    # ── phase 2: Tier 1 ──
    by_size = defaultdict(list)
    for entry in all_files:
        by_size[entry[1]].append(entry)
    # Only same-sized files get hashed, and that set is known before the loop
    # starts — the total costs nothing to compute.
    rp.phase(2, "比對相同大小",
             total=sum(len(g) for g in by_size.values() if len(g) >= 2))
    for size, grp in by_size.items():
        if len(grp) < 2:
            continue
        if rp.cancelled:
            return groups
        by_hash = defaultdict(list)
        for path, size, mtime, label in grp:
            rp.step(path.name)
            hh = _remembered(store, "head", path, size, mtime,
                             lambda: partial_hash(path), bool, stats)
            if hh:
                by_hash[hh].append((path, size, mtime, label))
        for _, g in by_hash.items():
            if len(g) >= 2:
                groups.append({"tier": 1,
                               "files": [_file_info(p, s, m, lb, store=store)
                                         for p, s, m, lb in g]})
                for p, _, _, _ in g:
                    tier1_paths.add(str(p))

    # ── Tier 2 ──
    if tier2:
        if rp.cancelled:
            return groups
        pending = [e for e in all_files
                   if str(e[0]) not in tier1_paths
                   # .ts has no frame index; seeking it is unusably slow
                   and e[0].suffix.lower() != ".ts"]
        rp.phase(3, "讀取時長", total=len(pending))
        rich = []
        for path, size, mtime, label in pending:
            if rp.cancelled:
                return groups
            rp.step(path.name)
            dur, w, h = _probe(store, path, size, mtime, stats)
            rich.append({"path": path, "size": size, "mtime": mtime,
                         "label": label,
                         "duration": dur, "width": w, "height": h})
        rich.sort(key=lambda e: e["duration"])

        clusters = []
        seen = set()
        for i, ei in enumerate(rich):
            if ei["duration"] <= 0 or str(ei["path"]) in seen:
                continue
            cluster = [ei]
            for ej in rich[i + 1:]:
                if ej["duration"] - ei["duration"] > threshold * 2:
                    break
                cluster.append(ej)
            if len(cluster) < 2:
                continue
            clusters.append(cluster)
            for e in cluster:
                seen.add(str(e["path"]))

        # Only files that share a duration with another get hashed, so this
        # total is known now that clustering is done.
        rp.phase(4, "幀比對", total=sum(len(c) for c in clusters))
        assigned = set()
        for cluster in clusters:
            if rp.cancelled:
                return groups
            hashes = {}
            for e in cluster:
                rp.step(e["path"].name)
                hashes[str(e["path"])] = _remembered(
                    store, "phash", e["path"], e["size"], e["mtime"],
                    lambda p=e["path"]: video_phash(p), bool, stats)
            for a in range(len(cluster)):
                ea = cluster[a]; ka = str(ea["path"])
                if ka in assigned or not hashes.get(ka):
                    continue
                group = [ea]
                for b in range(a + 1, len(cluster)):
                    eb = cluster[b]; kb = str(eb["path"])
                    if kb in assigned or not hashes.get(kb):
                        continue
                    if phash_similar(hashes[ka], hashes[kb]):
                        group.append(eb)
                if len(group) >= 2:
                    for e in group:
                        assigned.add(str(e["path"]))
                    dists = [d for d in
                             (phash_distance(hashes[ka], hashes[str(e["path"])])
                              for e in group[1:]) if d is not None]
                    worst = round(max(dists), 1) if dists else None
                    groups.append({"tier": 2, "distance": worst,
                                   "files": [_file_info(e["path"], e["size"],
                                                        e["mtime"], e["label"],
                                                        probe=False)
                                             | {"duration": e["duration"],
                                                "width": e["width"],
                                                "height": e["height"]}
                                             for e in group]})

    rp.finish()
    return groups
