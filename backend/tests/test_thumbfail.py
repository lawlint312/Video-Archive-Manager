"""A card thumbnail that cannot be made is not tried again on every visit.

AVOP-071.mp4 (VC-1 in MP4, 5.4 GB) could not be decoded with ffmpeg's VC-1
parser on: the frame grab read the file until its 30 s timeout, then the colour retry
does it again. Every visit to the folder repeated it, and the gigabytes it
pulled off the NAS held up the character page and the home page behind it
(/api/characters 29 s, 2026-11-21).

  * VC-1 (bare frames, no start codes) is grabbed with the parser off
  * a timeout is not followed by the colour retry
  * a fast refusal still is (that is what the retry is for)
  * a failure is remembered: the next request runs no ffmpeg
  * a replaced file (new stamp) is tried at once
  * after THUMB_RETRY_AFTER it is tried again
  * a success forgets the failure

ffmpeg and ffprobe are replaced; only temporary files are used.

Run:  python tests/test_thumbfail.py      (from backend/)
"""
import _tmpdata  # noqa: F401  (first: never the real data/ or cache/)
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def main():
    from app.core import media

    tmp = Path(tempfile.mkdtemp())
    cache = tmp / "thumbs"
    video = tmp / "AVOP-071.mp4"
    video.write_bytes(b"\0" * 1024)

    calls = []
    mode = {"how": "timeout"}

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if mode["how"] == "timeout":
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
        out = Path(cmd[cmd.index("-vf") + 2])
        if mode["how"] == "refuse":
            out.write_bytes(b"")           # swscale refused: empty file
        else:
            out.write_bytes(b"\xff\xd8jpeg")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    real_run, real_probe = media.subprocess.run, media.probe_video_codec
    codec = {"name": "h264"}
    media.subprocess.run = fake_run
    media.probe_video_codec = lambda p: (7269.0, 1280, 720, codec["name"])
    media._thumb_failed.clear()
    try:
        print("VC-1 is grabbed with ffmpeg's parser off (media.demux_fix)")
        codec["name"] = "vc1"
        mode["how"] = "ok"
        got = media.get_or_make_thumb(video, cache)
        check(got is not None and len(calls) == 1, "made, in one ffmpeg")
        cmd = calls[0] if calls else []
        check("+noparse+nofillin" in cmd and cmd.index("-fflags") < cmd.index("-i"),
              "with -fflags +noparse+nofillin before -i")
        for p in cache.rglob("*.jpg"):
            p.unlink()
        calls.clear()
        codec["name"] = "h264"
        media.get_or_make_thumb(video, cache)
        check(calls and "-fflags" not in calls[0], "other codecs keep their parser")
        for p in cache.rglob("*.jpg"):
            p.unlink()
        calls.clear()
        mode["how"] = "timeout"

        print("\na file ffmpeg cannot decode")
        got = media.get_or_make_thumb(video, cache)
        check(got is None, "no thumbnail")
        check(len(calls) == 1, f"one ffmpeg, no colour retry after a timeout ({len(calls)})")

        calls.clear()
        t0 = time.perf_counter()
        got = media.get_or_make_thumb(video, cache)
        check(got is None and not calls, "the next request runs no ffmpeg")
        check(time.perf_counter() - t0 < 0.1, "and answers at once")

        print("\na replaced file is tried at once")
        video.write_bytes(b"\0" * 2048)        # new size → new stamp
        calls.clear()
        media.get_or_make_thumb(video, cache)
        check(len(calls) == 1, "new stamp, new attempt")

        print("\nafter THUMB_RETRY_AFTER it is tried again")
        for k in list(media._thumb_failed):
            media._thumb_failed[k] -= media.THUMB_RETRY_AFTER + 1
        calls.clear()
        media.get_or_make_thumb(video, cache)
        check(len(calls) == 1, "tried again")

        print("\na fast refusal still gets the colour retry")
        media._thumb_failed.clear()
        mode["how"] = "refuse"
        calls.clear()
        media.get_or_make_thumb(video, cache)
        check(len(calls) == 2, f"both filters tried ({len(calls)})")
        check(any(media.COLOUR_FIX in " ".join(c) for c in calls),
              "the second one with COLOUR_FIX")

        print("\na success forgets the failure")
        for k in list(media._thumb_failed):
            media._thumb_failed[k] -= media.THUMB_RETRY_AFTER + 1
        mode["how"] = "ok"
        got = media.get_or_make_thumb(video, cache)
        check(got is not None and got.exists(), "thumbnail made")
        check(not media._thumb_failed, "nothing remembered as failed")
    finally:
        media.subprocess.run, media.probe_video_codec = real_run, real_probe

    print("\nFAIL (%d)" % len(FAIL) if FAIL else "\nPASS")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
