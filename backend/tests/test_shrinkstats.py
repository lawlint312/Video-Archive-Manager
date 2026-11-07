"""The record of real shrink results that calibrates the panel's estimate.

Everything lives in a temp directory; the probe is faked, so no video is read.

Run:  python tests/test_shrinkstats.py      (from backend/)
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import shrinkstats            # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


INFO = {"mbps": 32.17, "width": 3840, "height": 2160, "duration": 2292.4,
        "size": 9_212_000_000, "fps": 59.94, "vcodec": "h264"}


def main(tmp: Path):
    print("\n記下一次壓縮")
    store = tmp / "results.json"
    shrinkstats.record(store, INFO, 20, 0, "h265", "hevc_nvenc", 5_744_000_000)
    rows = shrinkstats.rows(store)
    check(len(rows) == 1 and rows[0]["before"] == INFO["size"]
          and rows[0]["after"] == 5_744_000_000 and rows[0]["codec"] == "h265"
          and rows[0]["crf"] == 20, "大小、crf、編碼都記下")
    check(not any("path" in k or isinstance(v, str) and "\\" in v
                  for r in rows for k, v in r.items()), "不存路徑")
    pub = shrinkstats.public(rows)
    check(set(pub[0]) == set(shrinkstats._FIELDS) and "at" not in pub[0],
          "端點只給估算用得到的欄位")

    shrinkstats.record(store, {**INFO, "size": 0}, 20, 0, "h265", "x", 5)
    shrinkstats.record(store, INFO, 20, 0, "h265", "x", 0)
    check(len(shrinkstats.rows(store)) == 1, "沒有大小的不記")

    print("\n只留最新的 KEEP 筆")
    saved = shrinkstats.KEEP
    shrinkstats.KEEP = 5
    try:
        for n in range(8):
            shrinkstats.record(store, INFO, 26, 0, "h264", "libx264", 1000 + n)
        rows = shrinkstats.rows(store)
        check(len(rows) == 5 and rows[-1]["after"] == 1007, "舊的被擠掉")
    finally:
        shrinkstats.KEEP = saved

    print("\n壞掉的檔案當作空的")
    broken = tmp / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    check(shrinkstats.rows(broken) == [], "讀不懂 → []")
    broken.write_text('{"a": 1}', encoding="utf-8")
    check(shrinkstats.rows(broken) == [], "不是清單 → []")

    print("\n第一次從待決定清單補回以前的壓縮")
    a, b = tmp / "film.mp4", tmp / "film_h265_crf20.mp4"
    a.write_bytes(b"x" * 100)
    b.write_bytes(b"x" * 40)
    gone = tmp / "gone_crf26.mp4"
    pend = [
        {"original": str(a), "produced": str(b), "kind": "shrink", "crf": 20,
         "max_edge": 0, "codec": "h265", "encoder": "hevc_nvenc", "at": 2.0},
        {"original": str(a), "produced": str(gone), "kind": "shrink",
         "crf": 26, "at": 1.0},                                  # already deleted
        {"original": str(a), "produced": str(b), "kind": "repair", "at": 3.0},
        # recorded before codecs existed: x264
        {"original": str(a), "produced": str(b), "kind": "shrink", "crf": 23,
         "max_edge": 1920, "at": 0.5},
    ]
    probed = []

    def probe(p):
        probed.append(p)
        return {**INFO, "size": 100}

    fresh = tmp / "fresh.json"
    n = shrinkstats.backfill(fresh, pend, probe)
    rows = shrinkstats.rows(fresh)
    check(n == 2 and len(rows) == 2, f"補回 {n} 筆（另一邊已刪的與修復不算）")
    check(rows[0]["crf"] == 23 and rows[0]["codec"] == "h264"
          and rows[0]["max_edge"] == 1920, "依時間排序；舊紀錄沒有編碼 = H.264")
    check(rows[1]["after"] == 40 and rows[1]["before"] == 100, "大小取自磁碟")

    probed.clear()
    check(shrinkstats.backfill(fresh, pend, probe) == 0 and not probed,
          "第二次不補、也不探測（避免重複計算）")
    check(shrinkstats.backfill(store, pend, probe) == 0, "已經有紀錄的也不補")

    empty = tmp / "empty.json"
    shrinkstats.backfill(empty, [], probe)
    check(empty.exists() and shrinkstats.rows(empty) == [],
          "沒東西可補也會建檔，下次就不再翻待決定清單")
    check(json.loads(empty.read_text(encoding="utf-8")) == [], "內容是 []")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    d = Path(tempfile.mkdtemp())
    try:
        main(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
