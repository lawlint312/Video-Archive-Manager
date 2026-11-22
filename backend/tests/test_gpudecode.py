"""A re-encode on the GPU decodes on the GPU too — for the measured formats only.

Measured on real files (2026-11-22, convert.md "轉檔用 GPU 解碼"): with
NVDEC decoding and the frames left on the card, a 60 s re-encode took the
same wall time (NVENC is the limit) and a tenth to a twentieth of the CPU.
AV1 is not decoded by the RTX 2070 at all, and `-hwaccel cuda` did not fail
on it: it sat at 0% for nine hours. Hence a list of what works, and the
shrink tool — which asked for `-hwaccel cuda` on everything — now uses it too.

  * media.nvdec_ok: the list
  * convert._build_cmd: hwaccel before -i, scale_cuda, no -pix_fmt
  * convert.run_one: GPU decode first, CPU decode again if that fails,
    never for formats off the list, never without "use GPU", never for
    the other actions
  * shrink.build_cmd: -hwaccel cuda only for the list
  * a real run on a generated H.264 clip, when this machine has NVENC

Run:  python tests/test_gpudecode.py      (from backend/)
"""
import _tmpdata  # noqa: F401  (first: never the real data/ or cache/)
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def hw(cmd):
    return "-hwaccel" in cmd and cmd.index("-hwaccel") < cmd.index("-i")


def main():
    from app.core import media, convert, shrink

    print("media.nvdec_ok")
    for c, p in (("h264", "yuv420p"), ("hevc", "yuv420p"), ("hevc", "yuv420p10le"),
                 ("vp9", "yuv420p"), ("vc1", "yuv420p"), ("wmv3", "yuv420p")):
        check(media.nvdec_ok(c, p), f"{c} {p}: GPU")
    check(media.nvdec_ok("vc1", "") and media.nvdec_ok("wmv3", ""),
          "VC-1 / WMV3 with no pixel format reported (VC-1 copied into MP4): GPU")
    for c, p in (("av1", "yuv420p"), ("rv40", "yuv420p"), ("mpeg4", "yuv420p"),
                 ("h264", "yuv422p"), ("h264", "yuvj420p"), ("h264", "yuv420p10le"),
                 ("mpeg2video", "yuv420p"), ("", ""), ("h264", "")):
        check(not media.nvdec_ok(c, p), f"{c or '?'} {p or '?'}: CPU")

    print("\nconvert command")
    src, dst = Path("x.avi"), Path("x.converting.mp4")
    info = {"vcodec": "h264", "pix_fmt": "yuv420p", "duration": 60}
    cmd = convert._build_cmd(src, dst, "reencode", info, 20, True, True)
    check(hw(cmd) and "-hwaccel_output_format" in cmd, "hwaccel cuda, frames kept on the card")
    check("scale_cuda=format=nv12" in cmd, "scale_cuda to nv12")
    check("-pix_fmt" not in cmd, "no -pix_fmt (that would need the frames on the CPU)")
    check("h264_nvenc" in cmd and cmd.count("-i") == 1, "NVENC, one input")
    cmd = convert._build_cmd(src, dst, "reencode", info, 20, True)
    check(not hw(cmd) and "-pix_fmt" in cmd and "scale_cuda=format=nv12" not in cmd,
          "CPU decode: as before")
    vc1 = {"vcodec": "vc1", "pix_fmt": "yuv420p", "duration": 60}
    cmd = convert._build_cmd(src, dst, "reencode", vc1, 20, True, True)
    i = cmd.index("-i")
    check(hw(cmd) and any("+noparse+nofillin" in str(x) for x in cmd[:i]),
          "VC-1 on the GPU keeps its parser off")
    check("-drop_changed:a" in cmd[:i], "still drops audio frames that change format")

    print("\nconvert.run_one")
    seen = []
    real = convert._run_ffmpeg
    tmp = Path(tempfile.mkdtemp())
    out = tmp / "o.mp4"

    def ok(cmd, *a, **k):
        seen.append(cmd)

    def gpu_fails(cmd, *a, **k):
        seen.append(cmd)
        if hw(cmd):
            out.write_bytes(b"half")
            raise RuntimeError("No decoder surfaces left")
    try:
        convert._run_ffmpeg = ok
        convert.run_one(src, out, "reencode", info, gpu=True)
        check(len(seen) == 1 and hw(seen[0]), "listed format + GPU: decoded on the GPU")

        seen.clear()
        convert._run_ffmpeg = gpu_fails
        convert.run_one(src, out, "reencode", info, gpu=True)
        check(len(seen) == 2 and hw(seen[0]) and not hw(seen[1]) and "-pix_fmt" in seen[1],
              "GPU decode failed → converted again with the CPU decoding")
        check(not out.exists(), "the half-written output was removed before the retry")

        seen.clear()
        try:
            convert.run_one(src, out, "reencode", info, gpu=True, cancelled=lambda: True)
            got = "ran"
        except convert.Stopped:
            got = "stopped"
        check(got == "stopped" and len(seen) == 1, "stopped meanwhile → no CPU retry")

        convert._run_ffmpeg = ok
        for name, i2, g, act in (("AV1", {**info, "vcodec": "av1"}, True, "reencode"),
                                 ("RV40", {**info, "vcodec": "rv40"}, True, "reencode"),
                                 ("4:2:2", {**info, "pix_fmt": "yuv422p"}, True, "reencode"),
                                 ("no GPU asked", info, False, "reencode"),
                                 ("conceal", info, True, "conceal"),
                                 ("remux", info, True, "remux")):
            seen.clear()
            convert.run_one(src, out, act, i2, gpu=g)
            check(len(seen) == 1 and not hw(seen[0]), f"{name}: CPU decode only")
    finally:
        convert._run_ffmpeg = real

    print("\nshrink")
    sinfo = {"vcodec": "h264", "pix_fmt": "yuv420p", "duration": 600,
             "width": 1920, "height": 1080}
    check(hw(shrink.build_cmd(src, out, 23, 0, sinfo, codec="h265", gpu=True)),
          "H.264 source: -hwaccel cuda")
    for name, s2 in (("AV1", {**sinfo, "vcodec": "av1"}),
                     ("RV40", {**sinfo, "vcodec": "rv40"}),
                     ("no pix_fmt (older probe)", {k: v for k, v in sinfo.items()
                                                   if k != "pix_fmt"})):
        cmd = shrink.build_cmd(src, out, 23, 0, s2, codec="h265", gpu=True)
        check(not hw(cmd) and "hevc_nvenc" in cmd, f"{name}: decoded on the CPU, still NVENC")

    clip = tmp / "src.mkv"
    r = subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y",
                        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=4",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                        "-shortest", str(clip)], capture_output=True)
    sp = shrink.probe(clip)
    check(sp.get("vcodec") == "h264" and sp.get("pix_fmt") == "yuv420p",
          f"shrink.probe reports the pixel format ({sp.get('pix_fmt')})")

    print("\nreal run (needs NVENC/NVDEC)")
    probe = convert.probe(clip)
    have = subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-hwaccel", "cuda",
                           "-hwaccel_output_format", "cuda", "-i", str(clip), "-t", "0.5",
                           "-vf", "scale_cuda=format=nv12", "-c:v", "h264_nvenc",
                           "-f", "null", "-"], capture_output=True).returncode == 0
    if r.returncode != 0 or not have:
        print("  skip (no NVIDIA card here)")
    else:
        seen.clear()
        real_run = convert._run_ffmpeg

        def spy(cmd, *a, **k):
            seen.append(cmd)
            return real_run(cmd, *a, **k)
        try:
            convert._run_ffmpeg = spy
            dst2 = tmp / "src.converting.mp4"
            convert.run_one(clip, dst2, "reencode", probe, gpu=True)
        finally:
            convert._run_ffmpeg = real_run
        check(len(seen) == 1 and hw(seen[0]), "ran once, on the GPU")
        problem = convert.verify(probe, dst2, "reencode")
        check(problem is None, f"verify() passes ({problem})")
        o = convert.probe(dst2)
        check(o.get("vcodec") == "h264" and o.get("pix_fmt") == "yuv420p",
              f"H.264 yuv420p out ({o.get('vcodec')} {o.get('pix_fmt')})")
        n = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                            "-count_packets", "-show_entries", "stream=nb_read_packets",
                            "-of", "csv=p=0", str(dst2)], capture_output=True, text=True)
        check(n.stdout.strip() == "120", f"all 120 frames ({n.stdout.strip()})")

    print("\nFAIL (%d)" % len(FAIL) if FAIL else "\nPASS")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
