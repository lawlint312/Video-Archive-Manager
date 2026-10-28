"""
Face recognition: index characters' videos, then suggest a name for an unfiled
video. GPU-only by design — without CUDA the feature reports itself unavailable
and the UI disables it (CPU inference is far too slow for whole libraries).
"""
from __future__ import annotations

import sqlite3
import logging
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict
from typing import List, Optional, Callable

log = logging.getLogger("vam.faces")

SAMPLE_PCTS = [0.08, 0.20, 0.35, 0.50, 0.65, 0.80, 0.92]
MIN_FACE_PX = 60
MATCH_THRESH = 0.28

_face_app = None            # lazily-created insightface app
_deps_error: Optional[str] = None


# ── capability probing ───────────────────────────────────────────────────────

def preload_cuda() -> None:
    """Put the pip-installed CUDA/cuDNN DLLs on the search path.

    Without this, onnxruntime-gpu on Windows cannot find the DLLs that ship in
    the `nvidia-*-cu12` wheels, and **every session silently falls back to the
    CPU** — no exception, no warning, and `get_available_providers()` still
    lists CUDA because that reports what the build supports, not what a session
    managed to load. Measured here: all five buffalo_l models reported
    `CPUExecutionProvider` while the settings page said the GPU was in use.

    Idempotent and safe to call on a machine with no GPU; older onnxruntime
    releases have no such function, hence the guard.
    """
    try:
        import onnxruntime as ort
        if hasattr(ort, "preload_dlls"):
            ort.preload_dlls()
    except Exception as e:                  # never block the CPU path
        log.info("preload_dlls 沒有生效（%s），將以 CPU 執行", e.__class__.__name__)


def probe() -> dict:
    """What the server can actually do — drives the UI's enabled/disabled state.

    `gpu` keeps its old meaning — the build offers CUDA — because it is what
    `_require_faces()` gates on, and the feature demonstrably works on the CPU
    (measured: the same speed, see `preload_cuda`). Turning a working-but-slow
    setup into a hard 503 would be a worse bug than the one being fixed.

    `applied` is the new, honest field: what a real session actually got. The
    page used to infer GPU from `get_available_providers()`, which lists CUDA
    on any onnxruntime-gpu install — so it claimed acceleration while all five
    models ran on the CPU.
    """
    info = {"deps": False, "gpu": False, "providers": [], "applied": "",
            "accelerated": False, "reason": ""}
    try:
        import onnxruntime as ort      # noqa: F401
        import insightface             # noqa: F401
        import cv2                     # noqa: F401
        info["deps"] = True
        info["providers"] = list(ort.get_available_providers())
        info["gpu"] = any("CUDA" in p or "Tensorrt" in p
                          for p in info["providers"])
        preload_cuda()
        # Only a session answers this honestly. det_10g is already on disk with
        # buffalo_l, so this costs no download.
        try:
            from pathlib import Path as _P
            det = (_P.home() / ".insightface" / "models" / "buffalo_l"
                   / "det_10g.onnx")
            if det.exists():
                s = ort.InferenceSession(
                    str(det), providers=["CUDAExecutionProvider",
                                         "CPUExecutionProvider"])
                info["applied"] = s.get_providers()[0]
        except Exception:
            info["applied"] = "CPUExecutionProvider"
        info["accelerated"] = "CUDA" in info["applied"] \
            or "Tensorrt" in info["applied"]
        if not info["gpu"]:
            info["reason"] = "未偵測到 CUDA GPU，人臉辨識已停用"
        elif info["applied"] and not info["accelerated"]:
            info["reason"] = "CUDA 無法載入，人臉功能正以 CPU 執行"
    except Exception as e:
        info["reason"] = f"缺少套件：{e.__class__.__name__}"
    return info


def available() -> bool:
    p = probe()
    return p["deps"] and p["gpu"]


# ── model ────────────────────────────────────────────────────────────────────

def get_face_app():
    global _face_app
    if _face_app is None:
        preload_cuda()              # must precede the first session, see above
        from insightface.app import FaceAnalysis
        log.info("loading buffalo_l model")
        _face_app = FaceAnalysis(
            name="buffalo_l",
            providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
        _face_app.prepare(ctx_id=0, det_size=(640, 640))
        got = [(t, m.session.get_providers()[0])
               for t, m in _face_app.models.items()
               if getattr(m, "session", None)]
        log.info("model loaded: %s", ", ".join(f"{t}={p}" for t, p in got))
    return _face_app


# ── database ─────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS face_index (
    id          INTEGER PRIMARY KEY,
    person_name TEXT NOT NULL,
    video_path  TEXT NOT NULL,
    embedding   BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_person ON face_index(person_name);
CREATE TABLE IF NOT EXISTS processed_videos (
    video_path  TEXT PRIMARY KEY,
    person_name TEXT NOT NULL,
    indexed_at  TEXT NOT NULL
);
"""


def open_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def stats(db_path: Path) -> dict:
    if not db_path.exists():
        return {"persons": 0, "embeddings": 0}
    conn = open_db(db_path)
    try:
        persons = conn.execute(
            "SELECT COUNT(DISTINCT person_name) FROM face_index").fetchone()[0]
        embs = conn.execute("SELECT COUNT(*) FROM face_index").fetchone()[0]
        return {"persons": persons, "embeddings": embs}
    finally:
        conn.close()


# ── embeddings ───────────────────────────────────────────────────────────────

def extract_embeddings(video: Path) -> list:
    import cv2
    import numpy as np   # noqa: F401
    fa = get_face_app()
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        return []
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total <= 0:
        cap.release()
        return []
    out = []
    for pct in SAMPLE_PCTS:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * pct))
        ok, frame = cap.read()
        if not ok:
            continue
        faces = fa.get(frame)
        if not faces:
            continue
        best = max(
            (f for f in faces
             if (f.bbox[2] - f.bbox[0]) >= MIN_FACE_PX
             and (f.bbox[3] - f.bbox[1]) >= MIN_FACE_PX
             and f.embedding is not None),
            key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]),
            default=None)
        if best is not None:
            out.append(best.embedding.copy())
    cap.release()
    return out


def _blob(e):
    import numpy as np
    return e.astype(np.float32).tobytes()


def _unblob(b):
    import numpy as np
    return np.frombuffer(b, dtype=np.float32)


def _cos(a, b) -> float:
    import numpy as np
    n = np.linalg.norm(a) * np.linalg.norm(b)
    return float(np.dot(a, b) / n) if n > 1e-8 else 0.0


# ── indexing / matching ──────────────────────────────────────────────────────

def index_folders(db_path: Path, folders: dict,
                  progress: Optional[Callable] = None) -> dict:
    """folders: {person_name: folder_path}. Incremental — skips indexed videos."""
    from .media import scan_videos_with_stat
    conn = open_db(db_path)
    indexed = 0
    try:
        for person, folder in folders.items():
            root = Path(folder)
            if not root.exists():
                continue
            done = {r[0] for r in conn.execute(
                "SELECT video_path FROM processed_videos WHERE person_name=?",
                (person,))}
            for video, _s, _m in scan_videos_with_stat(root):
                if str(video) in done:
                    continue
                if progress:
                    progress({"person": person, "file": video.name,
                              "indexed": indexed})
                embs = extract_embeddings(video)
                for e in embs:
                    conn.execute(
                        "INSERT INTO face_index (person_name, video_path, embedding) "
                        "VALUES (?,?,?)", (person, str(video), _blob(e)))
                conn.execute(
                    "INSERT OR REPLACE INTO processed_videos "
                    "(video_path, person_name, indexed_at) VALUES (?,?,?)",
                    (str(video), person, datetime.now(timezone.utc).isoformat()))
                conn.commit()
                if embs:
                    indexed += 1
    finally:
        conn.close()
    return {"indexed": indexed}


def index_single(db_path: Path, person: str, video: Path) -> int:
    """Index one video (used right after archiving). Returns embeddings added."""
    conn = open_db(db_path)
    try:
        already = conn.execute(
            "SELECT 1 FROM processed_videos WHERE video_path=?",
            (str(video),)).fetchone()
        if already:
            return 0
        embs = extract_embeddings(video)
        for e in embs:
            conn.execute(
                "INSERT INTO face_index (person_name, video_path, embedding) "
                "VALUES (?,?,?)", (person, str(video), _blob(e)))
        conn.execute(
            "INSERT OR REPLACE INTO processed_videos "
            "(video_path, person_name, indexed_at) VALUES (?,?,?)",
            (str(video), person, datetime.now(timezone.utc).isoformat()))
        conn.commit()
        return len(embs)
    finally:
        conn.close()


def rename_video(db_path: Path, old: Path, new: Path) -> int:
    """Repoint the index after a file is converted or moved.

    The embeddings stay valid — the pictures inside the file are the same —
    so this is a path update, not a reason to re-run recognition.
    """
    if not db_path.exists():
        return 0
    conn = open_db(db_path)
    try:
        n = conn.execute("UPDATE face_index SET video_path = ? WHERE video_path = ?",
                         (str(new), str(old))).rowcount
        conn.execute("UPDATE processed_videos SET video_path = ? WHERE video_path = ?",
                     (str(new), str(old)))
        conn.commit()
        return n
    except Exception:
        log.exception("could not repoint the face index %s -> %s", old, new)
        return 0
    finally:
        conn.close()


def forget_video(db_path: Path, video: Path) -> int:
    """Drop a deleted video's embeddings so it stops skewing future matches."""
    if not db_path.exists():
        return 0
    conn = open_db(db_path)
    try:
        n = conn.execute("DELETE FROM face_index WHERE video_path = ?",
                         (str(video),)).rowcount
        conn.execute("DELETE FROM processed_videos WHERE video_path = ?",
                     (str(video),))
        conn.commit()
        return n
    except Exception:
        log.exception("could not remove %s from the face index", video)
        return 0
    finally:
        conn.close()


def match(db_path: Path, video: Path, top_n: int = 5) -> list:
    import numpy as np
    if not db_path.exists():
        return []
    q = extract_embeddings(video)
    if not q:
        return []
    conn = open_db(db_path)
    try:
        rows = conn.execute(
            "SELECT person_name, embedding FROM face_index").fetchall()
    finally:
        conn.close()
    if not rows:
        return []

    scores = defaultdict(list)
    for person, blob in rows:
        db_emb = _unblob(blob)
        for qe in q:
            scores[person].append(_cos(qe, db_emb))

    ranked = sorted(
        ((p, float(np.mean(sorted(v, reverse=True)[:5]))) for p, v in scores.items()),
        key=lambda x: x[1], reverse=True)

    return [{"name": p, "score": round(s, 3),
             "confidence": round(min(100.0, max(0.0,
                 (s - MATCH_THRESH) / (1 - MATCH_THRESH) * 100)), 1)}
            for p, s in ranked[:top_n] if s >= MATCH_THRESH]
