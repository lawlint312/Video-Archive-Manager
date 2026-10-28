"""An `.mp4` a browser cannot play, and the scan that could not see it.

The user reported one file that would not play: `EVDV-54319.mp4`, 99 minutes,
852x480. Its video stream is `mpeg4` -- MPEG-4 Part 2, the Xvid era -- which no
browser decodes. The container was fine; the extension was simply not evidence
about the codec inside.

The tool whose entire job is "make this playable" could not see it, twice over:

1. `scan()` skipped every `.mp4` by extension.
2. `target_for()` on an `.mp4` returns the source path itself, so the
   "a file of that name already exists" guard would have skipped it anyway.

A sampled survey of 300 MP4s in the real library found 95.7% h264 and 4.3%
hevc, and not one mpeg4 -- so this is a rare file, and the check is opt-in
because almost all of that probing finds nothing. Rare is not the same as
unreachable, though, which is what it was.

Every fixture here is generated with ffmpeg; no real file is touched.

Run:  python tests/test_convert_mp4.py      (from backend/)
"""
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import convert                          # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def make(path: Path, vcodec: str, extra=()):
    """A two-second clip in `vcodec`, written to `path`."""
    cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-y",
           "-f", "lavfi", "-i", "testsrc=size=320x240:rate=15:duration=2",
           "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
           "-c:v", vcodec, *extra, "-c:a", "aac", "-shortest", str(path)]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        print("     (ffmpeg 失敗，跳過：" +
              r.stderr.decode("utf-8", "replace").strip().splitlines()[-1][:90] + ")")
        return False
    return True


def main():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        # the shape of the reported file: mpeg4 video inside an .mp4 container
        bad = root / "EVDV-54319.mp4"
        ok = root / "fine.mp4"
        ts = root / "wrapped.ts"
        if not make(bad, "mpeg4"):
            print("這台機器的 ffmpeg 沒有 mpeg4 編碼器，測不了")
            return
        # `-pix_fmt yuv420p` is not decoration: libx264 on `testsrc` defaults
        # to yuv444p, which really is unplayable, and the first version of this
        # fixture was therefore a *correct* failure the scan reported. Left as
        # a note because the same mistake makes any h264 fixture wrong.
        make(ok, "libx264", ("-pix_fmt", "yuv420p"))
        make(ts, "libx264", ("-pix_fmt", "yuv420p"))

        print("\nplan_for 本來就判斷得出來 —— 問題不在它")
        p = convert.plan_for(bad)
        check(p["vcodec"] == "mpeg4", f"認出編碼是 mpeg4（{p['vcodec']}）")
        check(p["action"] == "reencode",
              f"而且知道要重編碼（{p['action']}：{p['reason']}）")

        print("\n預設掃描仍然不看 .mp4（那是刻意的，而且要保持）")
        rows = convert.scan([root])
        names = {Path(r["path"]).name for r in rows}
        check("EVDV-54319.mp4" not in names,
              "預設不含 .mp4 —— 全庫 19,503 個檔的探測不該白跑")
        check("wrapped.ts" in names, "非 .mp4 照樣列出來")

        print("\n勾了「同時檢查 .mp4」才看得到它 —— 這是原本的破口")
        rows = convert.scan([root], include_mp4=True)
        by = {Path(r["path"]).name: r for r in rows}
        check("EVDV-54319.mp4" in by, "播不動的 .mp4 出現在清單裡了")
        row = by.get("EVDV-54319.mp4", {})
        check(row.get("action") == "reencode",
              f"而且是可以動手的動作，不是 skip（{row.get('action')}）")
        check(row.get("in_place") is True,
              "標成就地取代 —— 它的目標檔名就是它自己")

        print("\n正常的 .mp4 不能被拖進來")
        # This is the assertion that stops the option becoming a re-encode of
        # the whole library: 95.7% of the MP4s are already fine, and `plan_for`
        # would call every one of them a "remux".
        check(by.get("fine.mp4", {}).get("action") == "skip",
              f"h264 的 .mp4 是 skip（{by.get('fine.mp4', {}).get('action')}）")
        check("已經" in by.get("fine.mp4", {}).get("reason", ""),
              f"理由講得清楚（{by.get('fine.mp4', {}).get('reason')}）")

        print("\n暫存檔名不能撞到來源")
        stage = convert.staging_for(bad)
        check(stage != bad, "暫存檔跟原檔不同名")
        check(stage.parent == bad.parent,
              "而且在同一個資料夾 —— os.replace 要同一個磁碟區才是原子的")
        check(stage.suffix == ".mp4", "副檔名仍是 .mp4，ffmpeg 才知道要寫什麼容器")

        print("\n真的轉一次，並驗證")
        info = convert.probe(bad)
        convert.run_one(bad, stage, "reencode", info, crf=28)
        problem = convert.verify(info, stage, "reencode")
        check(problem is None, f"驗證通過（{problem}）")
        check(convert.probe(stage)["vcodec"] == "h264", "輸出真的是 h264")

        print("\n驗證要擋得住「重編碼卻沒有真的換編碼」")
        # A re-encode whose output is still mpeg4 has achieved nothing, and the
        # old verify() would have passed it: its codec check only ran for
        # remuxes, where a *changed* codec is the failure.
        still_bad = root / "still.mp4"
        make(still_bad, "mpeg4")
        problem = convert.verify(info, still_bad, "reencode")
        check(problem is not None and "mpeg4" in problem,
              f"輸出還是 mpeg4 時要擋下來（{problem}）")

        print("\n取代原檔")
        before = bad.stat().st_size
        convert.replace_original(bad, stage)
        check(not stage.exists(), "暫存檔沒有留下來")
        check(bad.exists(), "原本的檔名還在 —— 這正是就地取代的重點")
        check(convert.probe(bad)["vcodec"] == "h264", "而且內容換成 h264 了")
        check(bad.stat().st_size != before, "大小變了（內容確實換過）")

    print()
    if FAIL:
        print(f"{len(FAIL)} FAILED")
        for m in FAIL:
            print("  - " + m)
        sys.exit(1)
    print("all good")


if __name__ == "__main__":
    main()
