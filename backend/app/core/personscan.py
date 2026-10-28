"""Finding photos with no person in them.

Social-media folders collect scenery and food shots alongside the person the
folder is about, and browsing them is noise. This flags the person-free ones so
they can be reviewed in the ordinary photo grid and deleted there, one at a
time — it never deletes anything itself.

### Why a person detector and not the face detector we already have

Measured on 150 library photos: the face detector found no face in 26 of them,
but running a person detector over those same 26 found a person in **21**.
They are back views, body shots and crops — exactly the photos you keep. Using
face detection for this question would have been wrong 81% of the time.
Lowering the face threshold does not close the gap either (0.5 -> 0.1 recovered
only 9 of the 26).

### What this is not

**It is not a way to save space.** Measured: person-free photos are the small
ones — of the sample's 336 MB, they were 4.6 MB (1.4%), and *no* photo above
2 MB was person-free. Deleting every one of them across the library would
reclaim about 0.8 GB of 60.5 GB. Re-encoding the large photos would reclaim
~38 GB, which is a different feature. This one is for tidiness.

### Cost, measured on this machine

    GPU  x1      68 ms/photo   0.9 CPU cores   +364 MiB VRAM
    CPU  x1     323 ms/photo   7.7 CPU cores
    CPU  x4     199 ms/photo  10.9 CPU cores   (3.5 GB RSS, four model copies)

So the GPU is not merely 4.75x faster, it leaves the machine usable — the same
"who pays for the compute" trade the contact sheets settled. GPU utilisation
sits at ~20%, so this does not starve anything else on the card either.

One character's folder (1,136 photos) takes about 77 seconds on the GPU.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
from pathlib import Path
from typing import Callable, List, Optional, Tuple

log = logging.getLogger("vam")

# The confidence a detection needs before it counts as a person.
#
# 0.35 is the ultralytics default and every measurement above used it. Measured
# on a 457-photo folder by recording each photo's best detection once and then
# deriving every threshold from it:
#
#     0.20 →  3 flagged (0.7%)      0.50 → 15 (3.3%)
#     0.35 →  6 flagged (1.3%)      0.70 → 43 (9.4%)
#                                   0.80 → 87 (19.0%)
#
# Lower means "more willing to call it a person", so FEWER photos are flagged —
# the conservative direction. The range is clamped because 0.80 flags 19% of
# photos as person-free and almost all of them do contain a person, just at
# confidence 0.7 rather than 0.9; handing that to a batch delete is a footgun.
#
# It barely matters for most photos: the detector is decisive (median
# confidence 0.894 on photos where it finds anyone), and only 5.3% sit in the
# 0.20-0.60 band where moving the threshold changes the answer at all.
DEFAULT_CONF = 0.35
CONF_MIN = 0.20
CONF_MAX = 0.60


def clamp_conf(v) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return DEFAULT_CONF
    return round(min(CONF_MAX, max(CONF_MIN, f)), 2)

MODEL_NAME = "person_yolov8m-seg.pt"

_model = None
_model_lock = threading.Lock()
_deps_error: Optional[str] = None


def model_path(data_dir: Path) -> Path:
    """The weights live under `data/models/`, copied in at setup.

    Deliberately not read from ComfyUI's model folder: this feature should not
    break because another project on this machine was moved or reinstalled.
    """
    return data_dir / "models" / MODEL_NAME


def probe(data_dir: Path) -> dict:
    """What this can actually do — mirrors `faces.probe()` in shape."""
    info = {"deps": False, "model": False, "gpu": False, "device": "cpu",
            "reason": ""}
    info["model"] = model_path(data_dir).exists()
    try:
        import torch                    # noqa: F401
        import ultralytics              # noqa: F401
        import cv2                      # noqa: F401
        info["deps"] = True
        info["gpu"] = bool(torch.cuda.is_available())
        info["device"] = 0 if info["gpu"] else "cpu"
    except Exception as e:
        info["reason"] = f"缺少套件：{e.__class__.__name__}"
        return info
    if not info["model"]:
        info["reason"] = f"找不到模型：{model_path(data_dir)}"
    elif not info["gpu"]:
        # Not fatal — it still works, just slowly and at the cost of the whole
        # CPU. Say so rather than pretending it is fine.
        info["reason"] = "CUDA 不可用，將以 CPU 執行（約慢 4.75 倍，且會佔滿 CPU）"
    return info


def available(data_dir: Path) -> bool:
    p = probe(data_dir)
    return p["deps"] and p["model"]


def get_model(data_dir: Path):
    global _model
    with _model_lock:
        if _model is None:
            from ultralytics import YOLO
            p = model_path(data_dir)
            log.info("loading person model %s", p)
            _model = YOLO(str(p))
        return _model


def device_for() -> object:
    """0 for the first GPU, else 'cpu'.

    Asked fresh each scan rather than cached: `ultralytics` sets
    CUDA_VISIBLE_DEVICES=-1 the moment anything requests device='cpu', so a
    cached "we have a GPU" can outlive the truth. That exact trap produced a
    bogus GPU benchmark during this feature's measurements.
    """
    try:
        import torch
        return 0 if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


# ── cache ────────────────────────────────────────────────────────────────────
# Same shape as imgdedup's hash cache, and the same key: (path, size, mtime).
# Re-running over a folder you already scanned should cost nothing.

def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("""CREATE TABLE IF NOT EXISTS person (
                        path   TEXT PRIMARY KEY,
                        size   INTEGER NOT NULL,
                        mtime  REAL    NOT NULL,
                        conf   REAL    NOT NULL,
                        people INTEGER NOT NULL)""")
    return conn


class Cache:
    """Verdicts, keyed on (path, size, mtime, conf).

    `conf` is in the key because a verdict only means anything at the threshold
    it was produced with. Change the slider and every row becomes a miss, which
    is correct — and why the UI has to say a rescan is needed rather than
    quietly showing an answer computed under different rules.
    """

    def __init__(self, db_path: Path, conf: float = DEFAULT_CONF):
        self.conn = _connect(db_path)
        self.conf = clamp_conf(conf)
        self._pending: List[tuple] = []

    def get(self, path: Path, size: int, mtime: float) -> Optional[int]:
        row = self.conn.execute(
            "SELECT size, mtime, conf, people FROM person WHERE path = ?",
            (str(path),)).fetchone()
        if not row:
            return None
        # the 1-second mtime slack is imgdedup's, for the same reason: SMB
        # reports a slightly different precision after a copy
        if (row[0] != size or abs(row[1] - mtime) > 1
                or abs(row[2] - self.conf) > 1e-9):
            return None
        return row[3]

    def put(self, path: Path, size: int, mtime: float, people: int):
        self._pending.append((str(path), size, mtime, self.conf, people))
        if len(self._pending) >= 200:
            self.flush()

    def flush(self):
        if not self._pending:
            return
        self.conn.executemany(
            "INSERT OR REPLACE INTO person (path, size, mtime, conf, people) "
            "VALUES (?, ?, ?, ?, ?)", self._pending)
        self.conn.commit()
        self._pending.clear()

    def forget(self, path: Path):
        self.conn.execute("DELETE FROM person WHERE path = ?", (str(path),))
        self.conn.commit()

    def forget_tree(self, folder: Path):
        """Drop a whole folder — used when it is renamed or merged away.

        ESCAPE because a real folder name can contain `_` or `%`: this library
        has an archive root called `Social Media_亞洲`, and an unescaped LIKE
        matched the wrong rows once already (see videoflags.move_tree).
        """
        pre = os.path.join(str(folder), "")
        pat = pre.replace("\\", "\\\\").replace("_", "\\_").replace("%", "\\%")
        self.conn.execute("DELETE FROM person WHERE path LIKE ? ESCAPE '\\'",
                          (pat + "%",))
        self.conn.commit()

    def last_conf(self, folder: Path) -> Optional[float]:
        """Which threshold this folder was last scanned at.

        Without it the grid would ask for verdicts at whatever the slider
        happens to show and get "everything pending" for a folder that was
        scanned perfectly well at another threshold — the answer exists, it is
        just filed under a different key.

        ESCAPE for the same reason as `forget_tree`: an archive root is called
        `Social Media_亞洲`.
        """
        pre = os.path.join(str(folder), "")
        pat = pre.replace("\\", "\\\\").replace("_", "\\_").replace("%", "\\%")
        row = self.conn.execute(
            "SELECT conf, COUNT(*) c FROM person WHERE path LIKE ? ESCAPE '\\' "
            "GROUP BY conf ORDER BY c DESC LIMIT 1", (pat + "%",)).fetchone()
        return row[0] if row else None

    def close(self):
        self.flush()
        self.conn.close()


# ── scanning ─────────────────────────────────────────────────────────────────

def count_people(model, path: Path, device,
                 conf: float = DEFAULT_CONF) -> Optional[int]:
    """How many people the detector sees. None if the file could not be read."""
    try:
        r = model.predict(str(path), device=device, conf=conf, verbose=False)[0]
        return 0 if r.boxes is None else len(r.boxes)
    except Exception:
        return None


def scan(photos: List[Tuple[Path, int, float]], data_dir: Path, db_path: Path,
         progress: Optional[Callable] = None,
         conf: float = DEFAULT_CONF) -> dict:
    """Classify `photos`, using and filling the cache.

    Returns {"total", "scanned", "cached", "failed", "no_person": [paths],
             "cancelled"}. The caller decides what to do with the list — this
    module never deletes.
    """
    conf = clamp_conf(conf)
    model = get_model(data_dir)
    device = device_for()
    cache = Cache(db_path, conf)
    out = {"total": len(photos), "scanned": 0, "cached": 0, "failed": 0,
           "no_person": [], "cancelled": False, "device": str(device),
           "conf": conf}
    try:
        for i, (p, size, mtime) in enumerate(photos):
            if progress is not None and getattr(progress, "cancelled", False):
                out["cancelled"] = True
                break
            n = cache.get(p, size, mtime)
            if n is None:
                n = count_people(model, p, device, conf)
                if n is None:
                    out["failed"] += 1
                    continue
                cache.put(p, size, mtime, n)
                out["scanned"] += 1
            else:
                out["cached"] += 1
            if n == 0:
                out["no_person"].append(str(p))
            if progress is not None and (i % 20 == 0 or i == len(photos) - 1):
                progress({"phase": 1, "phases": 1, "label": "辨識人物",
                          "done": i + 1, "total": len(photos),
                          "name": p.name})
    finally:
        cache.close()
    return out
