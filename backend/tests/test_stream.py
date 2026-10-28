"""Serving video over /api/stream.

The bug this exists to prevent cost a whole debugging session: streaming from
a sync generator leaked the file handle whenever a client disconnected
mid-transfer, because Starlette's `iterate_in_threadpool` never closes the
underlying iterator. Measured on one 2.25 GB file, ten seconds of scrubbing
opened 100 range requests and 65 of them were never released — which made
deleting that file fail with WinError 32 permanently, not temporarily.

So the load-bearing test here is the last one: abandon a stream part-way and
assert the process holds no handle on the file afterwards. The Range tests
guard the behaviour we used to implement by hand and now delegate.

Run:  python -m pytest tests/ -q        (from backend/)
      python tests/test_stream.py
"""
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psutil                                            # noqa: E402
from fastapi.testclient import TestClient                # noqa: E402

from app import auth, main                               # noqa: E402

BODY = bytes(range(256)) * 400          # 102_400 bytes, every value distinct


def build():
    tmp = Path(tempfile.mkdtemp())
    p = tmp / "clip.mp4"
    p.write_bytes(BODY)
    return tmp, p


def client(target: Path):
    """A client with auth and the path gate stubbed out.

    Both are covered elsewhere; stubbing them keeps this file about streaming.
    """
    main.app.dependency_overrides[auth.current_user] = lambda: {"id": 1}
    main._checked_path = lambda path, user: Path(path)
    return TestClient(main.app), {"path": str(target)}


def handles_on(p: Path) -> int:
    target = os.path.normcase(str(p.resolve()))
    return sum(1 for f in psutil.Process().open_files()
               if os.path.normcase(f.path) == target)


def brief(v):
    """Bodies here are 100 KB; printing one in full buries the result."""
    if isinstance(v, bytes):
        return f"<{len(v)} bytes>" if len(v) > 32 else repr(v)
    return repr(v)


def check(name, got, want):
    ok = got == want
    print(f"  {'OK  ' if ok else 'FAIL'} {name}: {brief(got)}"
          + ("" if ok else f"  (expected {brief(want)})"))
    return ok


def main_():
    tmp, p = build()
    c, q = client(p)
    ok = True

    print("whole file")
    r = c.get("/api/stream", params=q)
    ok &= check("status", r.status_code, 200)
    ok &= check("body", r.content, BODY)
    ok &= check("content-type", r.headers["content-type"], "video/mp4")
    ok &= check("accept-ranges", r.headers.get("accept-ranges"), "bytes")
    # a Content-Disposition here would make the browser download instead of play
    ok &= check("no content-disposition", "content-disposition" in r.headers, False)

    print("closed range")
    r = c.get("/api/stream", params=q, headers={"Range": "bytes=10-19"})
    ok &= check("status", r.status_code, 206)
    ok &= check("body", r.content, BODY[10:20])
    ok &= check("content-range", r.headers["content-range"],
                f"bytes 10-19/{len(BODY)}")
    ok &= check("content-length", r.headers["content-length"], "10")

    print("open-ended range (what Chrome actually sends)")
    r = c.get("/api/stream", params=q, headers={"Range": "bytes=100-"})
    ok &= check("status", r.status_code, 206)
    ok &= check("body", r.content, BODY[100:])
    ok &= check("content-range", r.headers["content-range"],
                f"bytes 100-{len(BODY) - 1}/{len(BODY)}")

    print("open-ended range is capped, and says so honestly")
    big = tmp / "big.mp4"
    cap = main._RANGE_CAP
    # must exceed the tail allowance, or the whole file counts as "the tail"
    # and is served in one piece — which is correct, but not what this checks
    size = main._TAIL_WHOLE + cap * 2
    big.write_bytes(b"\0" * size)
    r = c.get("/api/stream", params={"path": str(big)},
              headers={"Range": "bytes=0-"})
    ok &= check("status", r.status_code, 206)
    ok &= check("served one cap", len(r.content), cap)
    # the client must be told what it actually got, or it will assume the rest
    # arrived and play silence
    ok &= check("content-range", r.headers["content-range"],
                f"bytes 0-{cap - 1}/{size}")
    r = c.get("/api/stream", params={"path": str(big)},
              headers={"Range": f"bytes={size - 10}-"})
    ok &= check("short tail is not padded", len(r.content), 10)

    # An MP4 without faststart keeps its `moov` index at the tail, and the
    # player has to read that index *whole* before it can seek. Capping it is
    # fatal, because Chrome does not come back for the remainder — measured on
    # a real 721 MB file with a 4.19 MB moov, it re-requested the tail from a
    # slightly different offset over and over and finally restarted the video
    # from 0. Any range that runs to EOF and fits in `_TAIL_WHOLE` goes whole.
    print("a range running to EOF is not capped (that is where the index is)")
    tail_start = size - int(cap * 1.75)         # 1.75 caps' worth of tail
    r = c.get("/api/stream", params={"path": str(big)},
              headers={"Range": f"bytes={tail_start}-"})
    ok &= check("whole tail in one response", len(r.content), size - tail_start)
    ok &= check("content-range reaches the last byte",
                r.headers["content-range"],
                f"bytes {tail_start}-{size - 1}/{size}")
    r = c.get("/api/stream", params={"path": str(big)},
              headers={"Range": f"bytes={tail_start}-{size - 1}"})
    ok &= check("same when the end is stated explicitly",
                len(r.content), size - tail_start)
    # the `bytes=0-` case above already proves the other half: a tail larger
    # than the allowance is still capped, so one request cannot pull an
    # arbitrary amount of the file into memory

    print("suffix range")
    r = c.get("/api/stream", params=q, headers={"Range": "bytes=-50"})
    ok &= check("status", r.status_code, 206)
    ok &= check("body", r.content, BODY[-50:])

    print("unsatisfiable range")
    r = c.get("/api/stream", params=q,
              headers={"Range": f"bytes={len(BODY) + 10}-"})
    ok &= check("status", r.status_code, 416)

    # A 206 must carry the same ETag/Last-Modified as the 200 for the same
    # file. The browser uses them to confirm nothing changed between range
    # requests; validators that appear on one response and vanish on the next
    # read as "the resource was replaced", and Chrome restarts the video from
    # the beginning. Reported as "seeking sometimes jumps back to 0", and
    # intermittent because it depends on whether a 200 was seen first.
    print("every response for one file carries the same validators")
    full = c.get("/api/stream", params=q)
    part = c.get("/api/stream", params=q, headers={"Range": "bytes=10-20"})
    bad = c.get("/api/stream", params=q,
                headers={"Range": f"bytes={len(BODY) + 10}-"})
    for name, resp in (("206", part), ("416", bad)):
        for h in ("etag", "last-modified"):
            ok &= check(f"{name} {h} matches the 200",
                        resp.headers.get(h), full.headers.get(h))
    ok &= check("the validators are actually present",
                bool(full.headers.get("etag")), True)

    print("mime is pinned, not guessed from the registry")
    ts = tmp / "clip.ts"
    ts.write_bytes(BODY)
    r = c.get("/api/stream", params={"path": str(ts)})
    ok &= check("content-type", r.headers["content-type"], "video/mp2t")

    # /api/image shares `_chunked_file`. When its signature gained a start
    # offset, this caller still passed (path, size) — which reads as "start at
    # the end" and serves an EMPTY image with a 200 and no error anywhere.
    # Nothing tested this route; now something does.
    print("/api/image serves the whole picture")
    jpg = tmp / "pic.jpg"
    jpg.write_bytes(BODY)
    r = c.get("/api/image", params={"path": str(jpg)})
    ok &= check("status", r.status_code, 200)
    ok &= check("body", r.content, BODY)

    # The point of the change: a response leaves the disk in small reads, so
    # its first byte goes out after one of them rather than after the whole
    # slice. Each piece is bounded, and they add up to exactly the range.
    print("responses are read in small pieces that add up to the range")
    pieces = list(main._chunked_file(big, 1000, main._RANGE_CAP))
    ok &= check("no piece larger than _PIECE",
                max(len(x) for x in pieces) <= main._PIECE, True)
    ok &= check("pieces add up to the range",
                sum(len(x) for x in pieces), main._RANGE_CAP)
    ok &= check("more than one piece", len(pieces) > 1, True)

    ok &= leak_test()

    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


def leak_test():
    """The real shape of the bug: a client that stops reading but stays connected.

    Getting this wrong cost two failed fixes. An earlier version of this test
    killed the socket with SO_LINGER 0 (an RST), which every implementation
    survives — it passed while the server was still pinning handles in
    production and told us the bug was fixed when it was not.

    A video player does not vanish. It buffers what it wants, stops reading,
    and keeps the connection open for when playback needs more. Anything that
    streams from an open file is then stuck mid-send holding the handle, and
    Windows refuses to delete the file for as long as that lasts. So the
    assertion is: read a little, then just sit there, and the server must
    still not be holding the file.

    Needs a real uvicorn socket; TestClient cannot express "connected but
    idle".
    """
    import socket
    import threading
    import time as _time
    import uvicorn

    tmp = Path(tempfile.mkdtemp())
    p = tmp / "big.mp4"
    p.write_bytes(b"\0" * (64 << 20))     # far more than any socket buffer
    main.app.dependency_overrides[auth.current_user] = lambda: {"id": 1}
    main._checked_path = lambda path, user: Path(path)

    cfg = uvicorn.Config(main.app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(cfg)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):                  # wait for the socket to be bound
        if server.started and server.servers:
            break
        _time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]

    def idle_client(extra: str):
        """Ask, read a little, then sit there — and see what is still held."""
        s = None
        try:
            s = socket.create_connection(("127.0.0.1", port))
            s.sendall((f"GET /api/stream?path={quote(str(p))} HTTP/1.1\r\n"
                       f"Host: 127.0.0.1\r\n{extra}\r\n").encode())
            s.settimeout(2)
            got = 0
            try:
                while got < (1 << 20):    # buffer 1 MB, like a player would
                    got += len(s.recv(65536))
            except socket.timeout:
                pass
            _time.sleep(3)                # ... and then just sit there
            return handles_on(p)
        finally:
            if s:
                s.close()

    try:
        print("an idle-but-connected client must not pin the file")
        held = idle_client("Range: bytes=0-\r\n")
        ok = check("handles while client sits idle (Range)", held, 0)
        # The same client *without* a Range header takes the FileResponse
        # path, which streams straight from an open file — the very shape the
        # Range path was rewritten to avoid. It was left that way on the
        # grounds that "players always send Range", and nothing ever checked.
        print("...and the same without a Range header")
        held_nr = idle_client("")
        ok &= check("handles while client sits idle (no Range)", held_nr, 0)
    finally:
        server.should_exit = True
        t.join(timeout=10)
    if not ok:
        print("       ^ this is the WinError 32 bug: the file cannot be deleted")
        print("         while a player holds a connection open, and no retry")
        print("         window helps because nothing is waiting to finish")
    return ok


def test_stream():
    assert main_() == 0


if __name__ == "__main__":
    sys.exit(main_())
