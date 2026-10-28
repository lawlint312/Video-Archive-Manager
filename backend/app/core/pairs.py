"""Files this app produced *beside* an original, waiting on a decision.

Two features leave two files where there was one, and neither can decide for
the user which to keep:

  * **shrink** — a smaller re-encode. Whether the quality is acceptable is a
    judgement, not a measurement.
  * **repair** — a rescued copy of a damaged file, minus whatever could not be
    read. Only a person can say if the result is worth keeping.

Both used to end with "…and now go find those files yourself". This is the
list that lets the decision happen later, which is when it actually happens:
a batch of 125 files takes long enough that nobody sits and watches it.

Shaped after the duplicate report, and for the same reasons — a pair whose
other half is gone has been dealt with and drops off by itself, so "done"
needs no state of its own. The one thing that does need recording is "I am
keeping both", or those pairs would nag for ever.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger("vam.pairs")

_lock = threading.Lock()


def _read(store: Path) -> list:
    try:
        return json.loads(store.read_text(encoding="utf-8"))
    except Exception:
        return []


def _write(store: Path, rows: list):
    try:
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    except Exception:
        log.exception("could not save the pending-pairs list")


def record(store: Path, original: Path, produced: Path, kind: str, **meta):
    """Note that `produced` was made from `original` and both now exist."""
    with _lock:
        rows = [r for r in _read(store)
                if not (r.get("original") == str(original)
                        and r.get("produced") == str(produced))]
        rows.append({"original": str(original), "produced": str(produced),
                     "kind": kind, "at": time.time(), "kept_both": False,
                     **meta})
        _write(store, rows)


def resolve(store: Path, original: Path, produced: Path,
            kept_both: bool = False):
    """Drop a pair, or mark it as deliberately kept."""
    with _lock:
        rows = _read(store)
        out = []
        for r in rows:
            same = (r.get("original") == str(original)
                    and r.get("produced") == str(produced))
            if not same:
                out.append(r)
            elif kept_both:
                out.append({**r, "kept_both": True})
        _write(store, out)


def pending(store: Path, allowed: Optional[Callable[[Path], bool]] = None) -> list:
    """Pairs still awaiting a decision, biggest saving first.

    Ordered by what is at stake rather than by when it was produced. A batch
    can leave a hundred pairs, and they are not worth equal attention: the one
    holding 1.08 GB deserves a side-by-side look, the one holding 2.8 MB gets
    a glance. Creation order carries no information for the person deciding.

    A pair is dropped once either side is gone — that is what "dealt with"
    means here, and it needs no extra bookkeeping. Sizes are read fresh rather
    than trusted from the record, because the point of the list is to show
    what is on disk right now.
    """
    out = []
    for r in _read(store):
        if r.get("kept_both"):
            continue
        a, b = Path(r["original"]), Path(r["produced"])
        if allowed and not (allowed(a) and allowed(b)):
            continue
        try:
            if not (a.is_file() and b.is_file()):
                continue
            osz, psz = a.stat().st_size, b.stat().st_size
        except OSError:
            continue
        out.append({**r, "original_name": a.name, "produced_name": b.name,
                    "original_size": osz, "produced_size": psz,
                    "saved": osz - psz,
                    "ratio": round(1 - psz / osz, 4) if osz else 0.0})
    # `at` breaks ties so the order is stable across reloads — a list that
    # reshuffles between visits is impossible to work through.
    out.sort(key=lambda r: (-r["saved"], -r.get("at", 0)))
    return out


def prune(store: Path):
    """Forget rows whose files are both gone, so the file cannot grow for ever."""
    with _lock:
        rows = _read(store)
        keep = []
        for r in rows:
            try:
                if Path(r["original"]).is_file() or Path(r["produced"]).is_file():
                    keep.append(r)
            except OSError:
                keep.append(r)
        if len(keep) != len(rows):
            _write(store, keep)
    return len(rows) - len(keep)
