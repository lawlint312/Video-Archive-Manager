"""VC-1 copied out of a WMV into MP4 is read with ffmpeg's parser off.

The samples of these files (`Lavf58`, tagged maxJAV.com) are bare frames
with no start codes; the sequence header sits in the `dvc1` box. With the
VC-1 parser on, ffmpeg buffered the whole file and failed — a re-encode of
AVOP-071.mp4 read 5.4 GB for nine minutes and wrote nothing (2026-11-21).
With `-fflags +noparse+nofillin` the same files decode: a frame in 0.2–1.3 s,
a 60 s re-encode with 1800 frames and matching audio, a clean full decode.

ffmpeg has no VC-1 encoder, so no sample can be made here; this checks that
every place that decodes these files asks for it, and that nothing else does.

  * convert: the re-encode command (and only for vc1, keeping +genpts)
  * contact sheet: CPU and GPU grabs
  * decode check: the full decode
  * the card thumbnail is in test_thumbfail.py

Run:  python tests/test_vc1.py      (from backend/)
"""
import _tmpdata  # noqa: F401  (first: never the real data/ or cache/)
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def before_input(cmd, flag="+noparse+nofillin"):
    # ffprobe takes the file as its last argument, without -i
    end = cmd.index("-i") if "-i" in cmd else len(cmd) - 1
    return any(flag in str(x) for x in cmd[:end])


def main():
    from app.core import media, convert, contactsheet, decodecheck

    print("media.demux_fix")
    check(media.demux_fix("vc1") == ["-fflags", "+noparse+nofillin"], "vc1 → parser off")
    check(all(media.demux_fix(c) == [] for c in ("h264", "hevc", "mpeg2video", "av1", "")),
          "everything else keeps its parser")

    print("\nconvert")
    src, dst = Path("x.mp4"), Path("x.converting.mp4")
    cmd = convert._build_cmd(src, dst, "reencode", {"vcodec": "vc1", "duration": 60})
    check(before_input(cmd), "vc1 re-encode: parser off, before -i")
    check(any("+genpts" in str(x) for x in cmd[:cmd.index("-i")]), "still +genpts")
    cmd = convert._build_cmd(src, dst, "reencode", {"vcodec": "h264", "duration": 60})
    check(not before_input(cmd, "noparse"), "h264 re-encode untouched")

    print("\ncontact sheet")
    tmp = Path(tempfile.mkdtemp())
    v = tmp / "v.mp4"
    v.write_bytes(b"\0")
    real = contactsheet.video_codec
    try:
        contactsheet.video_codec = lambda p: "vc1"
        for gpu in (False, True):
            cmd = contactsheet._grab_cmd(v, 10, tmp / "o.jpg", 320, gpu, False)
            check(before_input(cmd), f"vc1 grab ({'GPU' if gpu else 'CPU'}): parser off")
        contactsheet.video_codec = lambda p: "h264"
        cmd = contactsheet._grab_cmd(v, 10, tmp / "o.jpg", 320, False, False)
        check(not before_input(cmd, "noparse"), "h264 grab untouched")
    finally:
        contactsheet.video_codec = real

    print("\ndecode check")
    real_probe = media.probe_video_codec
    seen = []
    real_run = decodecheck.subprocess.Popen

    class Done(Exception):
        pass

    def fake_popen(cmd, **kw):
        seen.append(cmd)
        raise Done()
    try:
        media.probe_video_codec = lambda p: (60.0, 1280, 720, "vc1")
        decodecheck.subprocess.Popen = fake_popen
        try:
            decodecheck._run(v, False)
        except Done:
            pass
        except Exception as e:
            seen.append(["error", repr(e)])
        check(seen and seen[0][0] == "ffmpeg" and before_input(seen[0]),
              f"vc1 full decode: parser off ({seen[:1]})")
    finally:
        media.probe_video_codec = real_probe
        decodecheck.subprocess.Popen = real_run

    print("\nduplicate scan (phash frames)")
    from app.core import dedup, shrink, clip
    grabbed = []
    real_dprobe, real_drun = dedup.probe_video_codec, dedup.subprocess.run

    def fake_drun(cmd, **kw):
        grabbed.append(cmd)
        import subprocess as sp
        return sp.CompletedProcess(cmd, 0, bytes(range(64)), b"")
    try:
        dedup.probe_video_codec = lambda p: (100.0, 1280, 720, "vc1")
        dedup.subprocess.run = fake_drun
        h = dedup.video_phash(v)
        check(h and len(grabbed) == 5 and all(before_input(c) for c in grabbed),
              f"all 5 frames with the parser off ({len(grabbed)})")
        grabbed.clear()
        dedup.probe_video_codec = lambda p: (100.0, 1280, 720, "h264")
        dedup.video_phash(v)
        check(grabbed and not any(before_input(c, "noparse") for c in grabbed),
              "h264 frames untouched")
    finally:
        dedup.probe_video_codec, dedup.subprocess.run = real_dprobe, real_drun

    print("\nshrink")
    info = {"vcodec": "vc1", "duration": 600, "width": 1280, "height": 720}
    for gpu in (False, True):
        cmd = shrink.build_cmd(v, tmp / "o.mp4", 23, 0, info, gpu=gpu)
        check(before_input(cmd), f"vc1 encode ({'GPU' if gpu else 'CPU'}): parser off")
    cmd = shrink.build_cmd(v, tmp / "o.mp4", 23, 0, {**info, "vcodec": "h264"})
    check(not before_input(cmd, "noparse"), "h264 encode untouched")
    src_text = (Path(shrink.__file__)).read_text(encoding="utf-8")
    check(src_text.count('*demux_fix(info.get("vcodec", ""))') == 2,
          "the comparison's original slice too")

    print("\nclip")
    real_codec, real_crun = clip.video_codec_of, clip.subprocess.run
    probes = []

    def fake_crun(cmd, **kw):
        probes.append(cmd)
        import subprocess as sp
        return sp.CompletedProcess(cmd, 0, b'{"frames": [{"pts_time": "0.0"}]}', b"")
    try:
        clip.subprocess.run = fake_crun
        import app.core.media as _m
        real_mcodec = _m.video_codec_of
        _m.video_codec_of = lambda p: "vc1"
        clip.video_codec_of = lambda p: "vc1"
        clip.keyframe_before(v, 30)
        clip.first_keyframe(v)
        check(len(probes) >= 2 and all(c[0] == "ffprobe" and before_input(c) for c in probes),
              f"keyframe probes with the parser off ({len(probes)})")
        enc = clip._build_encode_cmd(v, tmp / "c.mp4", 10, 20, 20, False)
        check(before_input(enc), "frame-accurate single cut: parser off")
        join = clip._build_join_cmd(v, tmp / "c.mp4", [(10, 20), (30, 40)], 20, False)
        n_in = join.count("-i")
        n_fix = sum(1 for x in join if x == "+noparse+nofillin")
        check(n_in == 2 and n_fix == 2, f"frame-accurate join: every input ({n_fix}/{n_in})")
        p = clip.plan(v, [(10, 20)], precise=False, total_duration=600, total_size=1)
        check(p.get("ok") is False and "VC-1" in p.get("error", ""),
              f"stream copy refused with the reason: {p.get('error')}")
        p = clip.plan(v, [(10, 20)], precise=True, total_duration=600, total_size=1)
        check(p.get("ok") is True, "frame-accurate allowed")
        try:
            clip.run(v, tmp / "c.mp4", [{"start": 10, "end": 20, "actual_start": 10,
                                         "duration": 10}], False, {"vcodec": "vc1"})
            got = "ran"
        except RuntimeError as e:
            got = str(e)
        check("VC-1" in got, "run() refuses stream copy too")
        _m.video_codec_of = lambda p: "h264"
        clip.video_codec_of = lambda p: "h264"
        enc = clip._build_encode_cmd(v, tmp / "c.mp4", 10, 20, 20, False)
        check(not before_input(enc, "noparse"), "h264 cut untouched")
    finally:
        clip.subprocess.run, clip.video_codec_of = real_crun, real_codec
        _m.video_codec_of = real_mcodec

    print("\nFAIL (%d)" % len(FAIL) if FAIL else "\nPASS")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
