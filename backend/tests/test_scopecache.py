"""The folder pickers answer from the last walk; the full rebuilds take turns.

On 2026-11-21 opening 轉檔為 MP4 left the folder list empty for 6–13 s. The
walk it needs takes ~50 ms, but it queued on the NAS behind the search-index
and count rebuilds (both on the same ten-minute clock) and a video playing.

  * `character_paths` walks once, then answers from memory
  * a stale answer is returned at once and refreshed behind the caller
  * VAM's own changes (`forget_names`) are seen at once
  * a walk begun before `forget_names` does not put its old answer back
  * any `list_characters` call (the home page's) warms it
  * the search-index and count rebuilds never walk at the same time
  * `/api/scope` uses it

Only temporary folders; the two caches' files are pointed at a temp dir.

Run:  python tests/test_scopecache.py      (from backend/)
"""
import _tmpdata  # noqa: F401  (first: never the real data/ or cache/)
import inspect
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def wait_for(cond, secs=3.0):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def names(pairs):
    return sorted(n for n, _p in pairs)


def main():
    from app.core import media, charstats, searchindex

    tmp = Path(tempfile.mkdtemp())
    charstats._FILE = tmp / "counts_cache.json"
    searchindex._FILE = tmp / "search_index.json"
    a, b = tmp / "A", tmp / "B"
    for d in (a / "Alice", a / "Bob", b / "Alice"):
        d.mkdir(parents=True)
    roots = [a, b]

    print("walks once, then answers from memory")
    media.forget_names()
    calls = []
    real = media.list_characters

    def counted(r):
        calls.append(1)
        return real(r)
    media.list_characters = counted
    try:
        got = media.character_paths(roots)
        check(names(got) == ["Alice", "Bob"], f"first answer walks: {names(got)}")
        alice = dict(got)["Alice"]
        check(len(alice) == 2, "a name on two roots keeps both paths")
        (a / "Carol").mkdir()
        got = media.character_paths(roots)
        check(len(calls) == 1, "a fresh answer does not walk again")
        check("Carol" not in names(got), "so a folder made outside VAM waits")

        print("\nVAM's own changes are seen at once")
        media.forget_names()
        got = media.character_paths(roots)
        check("Carol" in names(got), "after forget_names the next call walks")

        print("\na stale answer comes back at once and is refreshed behind")
        (a / "Dave").mkdir()
        old_fresh = media.NAMES_FRESH
        media.NAMES_FRESH = -1.0          # time.time() ticks every ~15 ms on Windows
        try:
            slow = threading.Event()

            def slow_walk(r):
                slow.wait(2)           # a NAS that takes its time
                return real(r)
            media.list_characters = slow_walk
            t0 = time.perf_counter()
            got = media.character_paths(roots)
            took = time.perf_counter() - t0
            check(took < 0.5, f"returned without waiting ({took * 1000:.0f} ms)")
            check("Dave" not in names(got), "with the previous answer")
            slow.set()
            check(wait_for(lambda: not media._names_busy), "the background walk ends")
            media.NAMES_FRESH = 60.0
            got = media.character_paths(roots)
            check("Dave" in names(got), "and the next caller gets the new folder")
        finally:
            media.NAMES_FRESH = old_fresh
            media.list_characters = counted
    finally:
        media.list_characters = real

    print("\na walk begun before forget_names does not undo it")
    media.forget_names()
    gen = media._names_gen
    media.forget_names()                     # e.g. an archive finished meanwhile
    media._remember_names(roots, [{"name": "Ghost", "paths": ["x"]}], gen)
    check(not media._names, "the old walk's answer is dropped")

    print("\nthe home page's list_characters warms it")
    media.forget_names()
    media.list_characters(roots)
    calls.clear()
    media.list_characters = counted
    try:
        media.character_paths(roots)
        check(not calls, "the picker after the home page needs no walk")
    finally:
        media.list_characters = real

    print("\nthe two rebuilds take turns")
    inside, most = [0], [0]
    lock = threading.Lock()

    def busy(*_a):
        with lock:
            inside[0] += 1
            most[0] = max(most[0], inside[0])
        time.sleep(0.3)
        with lock:
            inside[0] -= 1
        return {"dirs": [], "v": [], "at": time.time(), "nfo": {}}
    old = (charstats._compute, searchindex._compute, searchindex._announce)
    charstats._compute = busy
    searchindex._compute = busy
    searchindex._announce = lambda *_a: None
    try:
        charstats._refreshing = searchindex._refreshing = True
        ts = [threading.Thread(target=charstats._refresh, args=(roots,)),
              threading.Thread(target=searchindex._refresh, args=(roots, []))]
        for t in ts:
            t.start()
        for t in ts:
            t.join(5)
        check(most[0] == 1, f"never both walking (at most {most[0]} at once)")
    finally:
        charstats._compute, searchindex._compute, searchindex._announce = old

    print("\n/api/scope asks the cached list")
    from app import main as m
    src = inspect.getsource(m.folder_scope)
    check("character_paths" in src and "list_characters(" not in src,
          "folder_scope uses character_paths")
    check("forget_names" in inspect.getsource(m._files_changed),
          "_files_changed clears it")

    print("\nFAIL (%d)" % len(FAIL) if FAIL else "\nPASS")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
