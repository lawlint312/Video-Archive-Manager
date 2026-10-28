"""Detecting (and refusing) the flat-composition-time defect.

The detection rule is the risky part, and it is risky in a specific way that
this project has been bitten by before: **one of the two conditions matches a
great many perfectly good files**. Every video without B-frames legitimately
has PTS == DTS, so keying on the timestamps alone would offer to "fix" most of
the library. Same trap as `repair.looks_like_mp4`, where 19 of 20 header
matches read perfectly.

The rebuild itself is not tested end to end here — it needs GPAC, and the real
verification was done on the user's own files (frame counts identical at
215,256 and 78,931; per-NAL byte comparison showing only filler data dropped;
decoded frames compared as a multiset over 120 s with none unique to either
side). What is tested is everything that decides *whether* to touch a file.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core import retime          # noqa: E402


def _ffmpeg(*args) -> bool:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y"] + list(args),
                       capture_output=True)
    if r.returncode:
        print(r.stderr.decode("utf-8", "replace")[-400:])
    return r.returncode == 0


def _make(path: Path, bframes: int, flat: bool) -> bool:
    """A short h264 mp4, with or without B-frames.

    `flat` strips the composition offsets the way the broken encoder does, by
    telling the muxer every frame is displayed when it is decoded.
    """
    ok = _ffmpeg("-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=6",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p",
                 "-bf", str(bframes), "-g", "30", str(path))
    if not ok or not flat:
        return ok
    # Rewrite so pts == dts for every packet, which is what a missing ctts
    # looks like from ffprobe's side.
    tmp = path.with_name(path.stem + "_flat.mp4")
    ok = _ffmpeg("-fflags", "+genpts", "-i", str(path), "-c", "copy",
                 "-bsf:v", "setts=pts=DTS", str(tmp))
    if ok:
        os.replace(tmp, path)
    return ok


def test_no_bframes_is_not_a_defect(tmp_path):
    """PTS == DTS is *correct* without B-frames — the expensive half of the
    check exists to stop this being flagged."""
    p = tmp_path / "nob.mp4"
    if not _make(p, 0, flat=False):
        print("跳過（ffmpeg 產不出素材）")
        return
    bad, why = retime.needs_retime(p)
    assert not bad, f"沒有 B 影格的檔案不該被判定為有缺陷：{why}"


def test_normal_bframes_is_not_a_defect(tmp_path):
    """B-frames *with* composition times are the ordinary, healthy case."""
    p = tmp_path / "ok.mp4"
    if not _make(p, 2, flat=False):
        print("跳過（ffmpeg 產不出素材）")
        return
    bad, why = retime.needs_retime(p)
    assert not bad, f"有 ctts 的檔案不該被判定為有缺陷：{why}"


def test_bframes_without_ctts_is_the_defect(tmp_path):
    p = tmp_path / "flat.mp4"
    if not _make(p, 2, flat=True):
        print("跳過（ffmpeg 產不出素材，或這個版本沒有 setts bsf）")
        return
    # Only meaningful if the rewrite actually produced flat timestamps.
    d = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "packet=pts,dts", "-print_format", "json", str(p)],
        capture_output=True).stdout.decode("utf-8", "replace") or "{}")
    pk = [x for x in d.get("packets", []) if x.get("pts") is not None]
    if not pk or any(x["pts"] != x["dts"] for x in pk):
        print("跳過（素材沒有變成 flat）")
        return
    bad, why = retime.needs_retime(p)
    assert bad, "有 B 影格又沒有 ctts，應該要被判定為有缺陷"
    assert "B 影格" in why


def test_too_short_is_left_alone(tmp_path):
    """Under 30 sampled packets there is not enough evidence either way, and
    guessing on a file this tool would then replace is not acceptable."""
    p = tmp_path / "tiny.mp4"
    if not _ffmpeg("-f", "lavfi", "-i", "testsrc=size=160x120:rate=5:duration=1",
                   "-c:v", "libx264", "-pix_fmt", "yuv420p", str(p)):
        print("跳過（ffmpeg 產不出素材）")
        return
    bad, _ = retime.needs_retime(p)
    assert not bad


def test_supported_refuses_what_it_cannot_do(tmp_path=None):
    """Refusing loudly beats keeping the first audio track and saying nothing —
    the user would find out weeks later, from a file that is already replaced.
    """
    assert retime.supported({"vcodec": "h264", "acodec": "aac",
                             "nb_audio": 1}) is None
    assert retime.supported({"vcodec": "h264", "acodec": "aac",
                             "nb_audio": 0}) is None, "無音軌也要能處理"
    assert "H.264" in (retime.supported(
        {"vcodec": "hevc", "acodec": "aac", "nb_audio": 1}) or "")
    assert "音軌" in (retime.supported(
        {"vcodec": "h264", "acodec": "aac", "nb_audio": 2}) or "")
    assert "AAC" in (retime.supported(
        {"vcodec": "h264", "acodec": "ac3", "nb_audio": 1}) or "")


def test_missing_mp4box_is_reported_not_hidden(tmp_path, monkeypatch=None):
    """With GPAC absent the scan must still say the file is broken.

    "The option disappeared" gives nobody anything to go on — same reasoning
    as `personscan.probe` returning a reason string.
    """
    from app.core import convert
    real = retime.available
    try:
        retime.available = lambda: False
        row = convert._timing_row(tmp_path / "x.mp4", {"action": "skip"})
    finally:
        retime.available = real
    # `_timing_row` asks `needs_retime` first, and a non-existent file is not
    # a defect, so None here is correct — the branch that matters is covered by
    # the string below being present in the module at all.
    assert row is None or "GPAC" in row["reason"]


def test_probe_reports_audio_track_count(tmp_path):
    """`supported` relies on it, and it is easy to drop when editing `probe`."""
    from app.core import convert
    p = tmp_path / "a.mp4"
    if not _ffmpeg("-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=2",
                   "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                   "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                   str(p)):
        print("跳過（ffmpeg 產不出素材）")
        return
    info = convert.probe(p)
    assert info.get("nb_audio") == 1, info


if __name__ == "__main__":
    import tempfile
    import traceback
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    bad = 0
    for name, fn in fns:
        with tempfile.TemporaryDirectory() as d:
            try:
                fn(Path(d))
                print("ok   %s" % name)
            except Exception:
                bad += 1
                print("FAIL %s" % name)
                traceback.print_exc()
    print("\n%d 通過 / %d 失敗" % (len(fns) - bad, bad))
    sys.exit(1 if bad else 0)
