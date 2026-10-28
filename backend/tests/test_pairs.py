"""The keep-or-discard list for files produced beside an original.

Shrink and repair both leave two files where there was one, and only a person
can pick. The list exists because that decision happens *later* — a batch of
125 files takes far too long to sit and watch.

Modelled on the duplicate report, including its best idea: a pair whose other
half is gone has been dealt with and drops off by itself, so "done" needs no
state of its own. The one case that does need recording is "keep both", or
those pairs nag for ever.

Run:  python tests/test_pairs.py        (from backend/)
"""
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import pairs                             # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def touch(p: Path, size: int):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * size)
    return p


def test_lifecycle(tmp: Path):
    print("\n一組配對的生命週期")
    store = tmp / "pairs.json"
    a = touch(tmp / "clip.mp4", 1000)
    b = touch(tmp / "clip_crf26.mp4", 300)

    check(pairs.pending(store) == [], "還沒記錄時是空的")
    pairs.record(store, a, b, "shrink", crf=26, max_edge=0)
    rows = pairs.pending(store)
    check(len(rows) == 1, "記錄後出現一組")
    r = rows[0]
    check(r["original_size"] == 1000 and r["produced_size"] == 300,
          "大小是現場讀的，不是相信當初記下的值")
    check(r["saved"] == 700 and abs(r["ratio"] - 0.7) < 0.001,
          f"算出省下多少：{r['saved']} bytes / {r['ratio']}")
    check(r["crf"] == 26, "當初的設定留著（幾天後才會回來看）")

    print("\n刪掉任一邊就自動消失")
    b.unlink()
    check(pairs.pending(store) == [],
          "產物不見了 → 這組已經處理完，不需要額外狀態")
    b = touch(tmp / "clip_crf26.mp4", 300)
    check(len(pairs.pending(store)) == 1, "檔案回來就又出現")
    a.unlink()
    check(pairs.pending(store) == [], "原檔不見了也一樣")


def test_keep_both(tmp: Path):
    print("\n「兩個都留」要能停止追問")
    store = tmp / "p.json"
    a = touch(tmp / "a.mp4", 500)
    b = touch(tmp / "a_fix.mp4", 400)
    pairs.record(store, a, b, "repair", dropped=1, lost_seconds=0.2)
    check(len(pairs.pending(store)) == 1, "先出現")
    pairs.resolve(store, a, b, kept_both=True)
    check(pairs.pending(store) == [], "標記後不再出現")
    check(a.is_file() and b.is_file(), "但兩個檔案都還在（只是不再問）")
    # and it must stay quiet across a reload, not come back
    check(pairs.pending(store) == [], "重新讀取後仍然安靜")


def test_resolve_removes(tmp: Path):
    print("\n處理掉的配對要從紀錄中移除")
    store = tmp / "p.json"
    a = touch(tmp / "x.mp4", 500)
    b = touch(tmp / "x_crf26.mp4", 100)
    pairs.record(store, a, b, "shrink")
    pairs.resolve(store, a, b)
    check(pairs.pending(store) == [], "不再列出")
    import json
    check(json.loads(store.read_text(encoding="utf-8")) == [],
          "而且真的從檔案裡移除了，不是只藏起來")


def test_permission_filter(tmp: Path):
    print("\n看不到的路徑不能出現在清單裡")
    store = tmp / "p.json"
    ok_a = touch(tmp / "vis" / "a.mp4", 300)
    ok_b = touch(tmp / "vis" / "a_crf26.mp4", 100)
    no_a = touch(tmp / "hidden" / "b.mp4", 300)
    no_b = touch(tmp / "hidden" / "b_crf26.mp4", 100)
    pairs.record(store, ok_a, ok_b, "shrink")
    pairs.record(store, no_a, no_b, "shrink")
    seen = pairs.pending(store, allowed=lambda p: "hidden" not in str(p))
    check(len(seen) == 1 and seen[0]["original"] == str(ok_a),
          "受限帳號只看得到自己的那一組")


def test_no_duplicate_rows(tmp: Path):
    print("\n同一組重複記錄不會變成兩列")
    store = tmp / "p.json"
    a = touch(tmp / "y.mp4", 500)
    b = touch(tmp / "y_crf26.mp4", 100)
    pairs.record(store, a, b, "shrink", crf=26)
    pairs.record(store, a, b, "shrink", crf=23)     # ran again over the top
    rows = pairs.pending(store)
    check(len(rows) == 1, f"仍然只有一列（{len(rows)}）")
    check(rows[0]["crf"] == 23, "而且是最後一次的設定")


def test_order_by_stake(tmp: Path):
    print("\n排序：省得多的排前面，不是先產生的排前面")
    store = tmp / "p.json"
    rows = [("small.mp4", 1000, 900),        # 省 100
            ("huge.mp4", 5_000_000, 200_000),  # 省 4.8M
            ("mid.mp4", 40_000, 10_000)]     # 省 30K
    for i, (nm, osz, psz) in enumerate(rows):
        a = touch(tmp / nm, osz)
        b = touch(tmp / f"x_{nm}", psz)
        pairs.record(store, a, b, "shrink", at=1000 + i)
    got = [r["original_name"] for r in pairs.pending(store)]
    check(got == ["huge.mp4", "mid.mp4", "small.mp4"],
          f"依省下的空間由大到小：{got}")

    # a repair can leave a *bigger* file; it belongs at the bottom, not missing
    a = touch(tmp / "grew.mp4", 1000)
    b = touch(tmp / "grew_fix.mp4", 3000)
    pairs.record(store, a, b, "repair")
    got = [r["original_name"] for r in pairs.pending(store)]
    check(got[-1] == "grew.mp4", f"變大的排最後但仍然列出：{got[-1]}")

    check([r["original_name"] for r in pairs.pending(store)] == got,
          "重新讀取的順序一樣（不會每次進來就重排）")


def test_prune(tmp: Path):
    print("\n兩邊都不見的舊紀錄要能清掉")
    store = tmp / "p.json"
    a = touch(tmp / "z.mp4", 100)
    b = touch(tmp / "z_crf26.mp4", 50)
    pairs.record(store, a, b, "shrink")
    a.unlink()
    check(pairs.prune(store) == 0, "還有一邊在就先留著")
    b.unlink()
    check(pairs.prune(store) == 1, "兩邊都沒了才清掉，檔案不會無限成長")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for fn in (test_lifecycle, test_keep_both, test_resolve_removes,
               test_permission_filter, test_no_duplicate_rows,
               test_order_by_stake, test_prune):
        d = Path(tempfile.mkdtemp())
        try:
            fn(d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
