"""
Minimal in-memory background job runner (single-process). Good enough for a
self-hosted single-worker deployment. For multi-worker, move this to a queue.

Jobs carry a `kind` so the UI can reattach to them after a page reload: the
work keeps running server-side, but the browser forgets the job id, and
without a way to ask "what is still running?" the result is stranded and the
CPU/NAS time is wasted.

Finished jobs are dropped after RETAIN seconds. They used to be kept forever,
which slowly leaked memory — a duplicate report over a large library is not a
small object.

Cancellation is cooperative and *advisory*: `cancel()` only raises a flag on
the job, which the task is expected to notice at a point where stopping is
safe. Nothing is killed — a running ffprobe finishes its file (a fraction of a
second) rather than leaving a half-read handle behind. A task that ignores the
flag simply runs to completion, and its status stays "done".

Surviving a restart
-------------------
A job lives in this process, so restarting the service (an update, a crash,
a reboot) used to end it without a trace. Jobs started with `resume=` are
also written to `REGISTRY` while they run: the endpoint, its request body and
whose request it was. Whatever is still listed when the next process starts
was interrupted, and `recover()` hands it back to main.py, which reruns the
scans by itself and asks before continuing anything that writes files.

Two things make a rerun safe:

  * `item_done()` records each finished item of a many-file job, so a
    continue only does the rest.
  * `writing(src, dst)` records the file a job is creating *right now*. A
    killed process leaves it half written — a file that looks like a real
    video — and `recover()` deletes it, but only while its source still
    exists: then nothing is lost, the work is simply done again.

`REGISTRY` and `NOTICES` stay None until main.py's startup sets them, so a
test that imports main and starts jobs never writes the real files.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
import uuid
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

log = logging.getLogger("vam")

RETAIN = 30 * 60        # seconds a finished job stays readable

REGISTRY: Optional[Path] = None   # running resumable jobs (data/jobs_running.json)
NOTICES: Optional[Path] = None    # interrupted ones waiting for their owner

_jobs: Dict[str, dict] = {}
_lock = threading.Lock()

# what goes into REGISTRY, by job id; guarded by _lock
_records: Dict[str, dict] = {}
_file_lock = threading.Lock()     # one writer of the JSON files at a time

# the job this thread is running, for `writing()` called deep in core code
_current = threading.local()
# set by `resuming()`: the next `start()` continues this entry
_resume_as = threading.local()


def _reap_locked():
    """Drop finished jobs nobody came back for. Caller must hold the lock."""
    now = time.time()
    for jid in [j for j, v in _jobs.items()
                if v["status"] != "running"
                and v.get("finished_at") and now - v["finished_at"] > RETAIN]:
        _jobs.pop(jid, None)


# ── the JSON files ───────────────────────────────────────────────────────────

def _write_json(path: Path, data) -> None:
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Optional[Path]) -> list:
    if not path:
        return []
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except FileNotFoundError:
        return []
    except Exception:
        log.exception("could not read %s", path)
        return []


def _save_records() -> None:
    """Write the registry. Called with _lock *not* held."""
    path = REGISTRY
    if not path:
        return
    with _lock:
        snap = [json.loads(json.dumps(r)) for r in _records.values()]
    with _file_lock:
        try:
            _write_json(path, snap)
        except Exception:
            log.exception("could not write the job registry")


def _update_record(job_id: Optional[str], fn) -> bool:
    with _lock:
        rec = _records.get(job_id) if job_id else None
        if rec is None:
            return False
        fn(rec)
    _save_records()
    return True


# ── progress, items, files in flight ─────────────────────────────────────────

class _Progress:
    """The callback handed to a task, carrying the job's cancel flag.

    A callable object rather than a function so `progress.cancelled` can ride
    along: every existing task calls `progress(msg)` and keeps working, and a
    task that wants to be stoppable reads one attribute. Passing a second
    argument would have meant touching every call site for the two tasks that
    care.
    """
    __slots__ = ("job_id",)

    def __init__(self, job_id: str):
        self.job_id = job_id

    def __call__(self, msg):
        with _lock:
            if self.job_id in _jobs:
                _jobs[self.job_id]["progress"] = msg

    @property
    def cancelled(self) -> bool:
        with _lock:
            j = _jobs.get(self.job_id)
            return bool(j and j.get("cancel"))

    def item_done(self, key: str, error: str = "") -> None:
        """One item of a many-file job is finished — a continue skips it.

        Whatever it was writing is finished too, so nothing is left to clean.
        """
        def fn(rec):
            rec["done"][key] = error
            rec["writing"] = []
        _update_record(self.job_id, fn)


def each(progress, items: Iterable, key: Callable[[Any], str]):
    """`items`, marking each one done once the loop moves past it.

    A generator so a long loop body full of `continue`s needs no change: an
    item counts as finished when the loop asks for the next one, whichever way
    its iteration ended — failures included, since running them again would
    most likely fail the same way. An item whose iteration never ended (the
    process was killed in the middle of it) is not marked; a continue redoes it.
    """
    mark = getattr(progress, "item_done", None)
    for it in items:
        yield it
        if mark:
            mark(key(it))


def writing(src, dst) -> None:
    """The current job is about to create `dst` from `src`.

    Call it only once `dst` is known not to exist, so that anything there from
    then on is this job's. Cleared by `wrote()` or the item finishing. Does
    nothing outside a resumable job.
    """
    pair = [str(src), str(dst)]
    _update_record(getattr(_current, "job_id", None),
                   lambda rec: rec["writing"].append(pair))


def wrote(dst) -> None:
    """`dst` is complete, or was taken away: nothing to clean up after all."""
    d = str(dst)
    _update_record(getattr(_current, "job_id", None),
                   lambda rec: rec.__setitem__(
                       "writing", [w for w in rec["writing"] if w[1] != d]))


# ── running ──────────────────────────────────────────────────────────────────

def start(target: Callable[[Callable[[Any], None]], Any],
          kind: str = "job", label: str = "",
          resume: Optional[dict] = None, owner=None) -> str:
    """Run target(progress_cb) in a background thread. Returns a job id.

    `resume` — `{"endpoint", "body", "user"}` — makes the job survive a
    restart (see the module docstring). Inside `resuming()` the job takes over
    the interrupted one's id, so a page still polling that id carries on.
    `owner` names the account for a job that need not survive a restart but
    is still followed through the owner-only `/api/jobs/{id}`.
    """
    again = getattr(_resume_as, "entry", None)
    job_id = again["id"] if again else uuid.uuid4().hex
    now = time.time()
    keep = bool(resume and REGISTRY)
    with _lock:
        _reap_locked()
        _jobs[job_id] = {"id": job_id, "kind": kind, "label": label,
                         "status": "running", "progress": "", "result": None,
                         "error": None, "cancel": False,
                         "user": resume.get("user") if resume else owner,
                         "started_at": now, "finished_at": None}
        if keep:
            _records[job_id] = {
                "id": job_id, "kind": kind, "label": label, "started_at": now,
                "endpoint": resume["endpoint"], "body": resume["body"],
                "user": resume.get("user"),
                # how many restarts have already brought it back
                "attempt": (again or {}).get("attempt", 0),
                "done": {}, "writing": [], "cancel": False}
    if keep:
        _save_records()

    _progress = _Progress(job_id)

    def _run():
        _current.job_id = job_id
        try:
            result = target(_progress)
            with _lock:
                if job_id in _jobs:
                    # A task that stopped early still returns what it found;
                    # the status is what tells the UI the result is partial.
                    done = "cancelled" if _jobs[job_id].get("cancel") else "done"
                    _jobs[job_id].update(result=result, status=done,
                                         finished_at=time.time())
        except Exception as e:
            with _lock:
                if job_id in _jobs:
                    _jobs[job_id].update(status="error", error=str(e),
                                         finished_at=time.time())
        finally:
            # ended one way or another: nothing to resume
            with _lock:
                had = _records.pop(job_id, None) is not None
            if had:
                _save_records()

    threading.Thread(target=_run, daemon=True).start()
    return job_id


class resuming:
    """`with jobs.resuming(entry): endpoint(...)` — the job that call starts
    continues `entry`: the same id, one more attempt."""

    def __init__(self, entry: dict):
        self.entry = {**entry, "attempt": int(entry.get("attempt") or 0) + 1}

    def __enter__(self):
        _resume_as.entry = self.entry
        return self

    def __exit__(self, *exc):
        _resume_as.entry = None
        return False


def get(job_id: str) -> Optional[dict]:
    with _lock:
        j = _jobs.get(job_id)
        return dict(j) if j else None


def owner(job_id: str):
    """Whose request started it — known for resumable jobs only."""
    with _lock:
        j = _jobs.get(job_id)
        return j.get("user") if j else None


def active(kind: Optional[str] = None) -> list:
    """Running jobs, newest first — what a freshly loaded page reattaches to."""
    with _lock:
        _reap_locked()
        out = [dict(j) for j in _jobs.values()
               if j["status"] == "running" and (kind is None or j["kind"] == kind)]
    out.sort(key=lambda j: j["started_at"], reverse=True)
    return out


def cancel(job_id: str) -> bool:
    """Ask a running job to stop. True if there was one to ask."""
    with _lock:
        j = _jobs.get(job_id)
        if not j or j["status"] != "running":
            return False
        j["cancel"] = True
    # a restart before it actually stops must not bring it back
    _update_record(job_id, lambda rec: rec.__setitem__("cancel", True))
    return True


def cleanup(job_id: str):
    with _lock:
        _jobs.pop(job_id, None)


# ── after a restart ──────────────────────────────────────────────────────────

# the tools a job runs as child processes, which outlive a killed service
_WRITERS = {"ffmpeg.exe", "ffmpeg", "ffprobe.exe", "ffprobe", "mp4box.exe", "mp4box"}


def _stop_writers(target: Path) -> None:
    """End the orphaned ffmpeg still writing `target`.

    Windows does not end a process's children with it: a service that crashed
    or was killed on its own (rather than with `taskkill /t`, as stop.bat
    does) leaves its ffmpeg encoding on, holding the half-written file open —
    it cannot be deleted, and it keeps the GPU and CPU busy for a job nobody is
    waiting for. Measured: the leftover encode kept the file locked and the
    continue then failed with 「同名的 .mp4 已存在」.

    Only a tool whose command line names this very file (or a file in this
    scratch folder) is ended — never anything else that happens to be running.
    """
    try:
        import psutil
    except ImportError:
        return
    want = os.path.normcase(str(target))
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            if (p.info["name"] or "").lower() not in _WRITERS:
                continue
            args = [os.path.normcase(a) for a in p.info["cmdline"] or []]
            if any(a == want or a.startswith(want + os.sep) for a in args):
                p.kill()
                p.wait(10)
                log.info("interrupted job: ended %s (pid %d) still writing %s",
                         p.info["name"], p.pid, target)
        except (psutil.Error, OSError):
            continue


def _discard_partial(src: str, dst: str) -> None:
    """Remove what a killed job was in the middle of writing.

    Only while the source is still there — then the half-written copy holds
    nothing the source does not. A source that is gone means the job got past
    the point of no return (moved, or converted and deleted), and whatever is
    at `dst` may be the only copy left.
    """
    s, d = Path(src), Path(dst)
    try:
        if os.path.normcase(str(s)) == os.path.normcase(str(d)) or not s.exists():
            return
        if d.exists():
            _stop_writers(d)
        if d.is_dir():
            # only VAM's own scratch folders (clip's `.vamclip-*`)
            if d.name.startswith(".vam"):
                shutil.rmtree(d, ignore_errors=True)
                log.info("interrupted job: removed scratch folder %s", d)
        elif d.exists():
            # the handle of a process just ended can take a moment to go
            for attempt in range(10):
                try:
                    d.unlink()
                    break
                except PermissionError:
                    if attempt == 9:
                        raise
                    time.sleep(0.3)
            log.info("interrupted job: removed half-written %s", d)
    except OSError:
        log.exception("interrupted job: could not remove %s", d)


def recover() -> List[dict]:
    """The jobs the previous process left running, half-written files removed.

    Clears the registry: each entry is now the caller's to rerun, turn into a
    notice, or drop. Jobs that had been asked to stop are dropped here.
    """
    entries = [e for e in _read_json(REGISTRY) if isinstance(e, dict)]
    for e in entries:
        for pair in e.get("writing") or []:
            if isinstance(pair, list) and len(pair) == 2:
                _discard_partial(*pair)
    if REGISTRY and entries:
        with _file_lock:
            try:
                _write_json(REGISTRY, [])
            except Exception:
                log.exception("could not clear the job registry")
    return [e for e in entries if not e.get("cancel")]


def _change_notices(fn):
    if not NOTICES:
        return None
    with _file_lock:
        items, out = fn(_read_json(NOTICES))
        try:
            _write_json(NOTICES, items)
        except Exception:
            log.exception("could not write the interrupted-job notices")
        return out


def add_notice(entry: dict, reason: str = "") -> None:
    """Keep an interrupted job until its owner continues or dismisses it.

    In a file rather than in memory, so a second restart before anyone has
    looked does not lose it.
    """
    item = {**entry, "reason": reason, "interrupted_at": time.time()}
    _change_notices(lambda items: (
        [i for i in items if i.get("id") != item["id"]] + [item], None))


def notices_for(user_id) -> List[dict]:
    if not NOTICES:
        return []
    with _file_lock:
        return [i for i in _read_json(NOTICES) if i.get("user") == user_id]


def take_notice(notice_id: str, user_id) -> Optional[dict]:
    """Remove one notice and return it — only to the account it belongs to."""
    def fn(items):
        hit = next((i for i in items if i.get("id") == notice_id
                    and i.get("user") == user_id), None)
        return [i for i in items if i is not hit], hit
    return _change_notices(fn)
