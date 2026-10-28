"""The person-scan cache — the part that must be right without a GPU.

The detector itself is a model; what this file protects is everything around
it, because the failure modes there are silent:

  * a stale verdict served after the photo was replaced,
  * a verdict cached under one confidence threshold reused under another,
  * a folder-wide purge that quietly deletes the wrong rows because the
    archive root is called `Social Media_亞洲` and `_` is a LIKE wildcard.

Run:  python tests/test_personscan.py        (from backend/)
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import personscan as ps                # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def test_cache_roundtrip(tmp: Path):
    print("\n快取的基本進出")
    c = ps.Cache(tmp / "person.sqlite3")
    p = Path(r"X:\pics\a.jpg")
    check(c.get(p, 100, 1000.0) is None, "沒有紀錄時回 None")
    c.put(p, 100, 1000.0, 0)
    c.flush()
    check(c.get(p, 100, 1000.0) == 0, "存 0 個人，讀回來是 0（不是 None）")
    c.put(Path(r"X:\pics\b.jpg"), 200, 1000.0, 3)
    c.flush()
    check(c.get(Path(r"X:\pics\b.jpg"), 200, 1000.0) == 3, "存 3 個人")
    c.close()


def test_zero_is_not_missing(tmp: Path):
    """`0 people` is the answer this feature is built on, and it is falsy."""
    print("\n「0 個人」不能被當成「沒有紀錄」")
    c = ps.Cache(tmp / "z.sqlite3")
    p = Path(r"X:\pics\scenery.jpg")
    c.put(p, 50, 1.0, 0)
    c.flush()
    got = c.get(p, 50, 1.0)
    check(got is not None, "查得到")
    check(got == 0, f"而且值是 0（拿到 {got!r}）")
    # the whole feature would silently rescan every scenery photo forever if
    # this were written as `if not cached:`
    check(not (got is None), "用 `is None` 判斷，不是用真假值")
    c.close()


def test_replaced_photo_rescans(tmp: Path):
    print("\n照片被換掉就要重算")
    c = ps.Cache(tmp / "r.sqlite3")
    p = Path(r"X:\pics\c.jpg")
    c.put(p, 100, 1000.0, 1)
    c.flush()
    check(c.get(p, 101, 1000.0) is None, "大小不同 → 重算")
    check(c.get(p, 100, 2000.0) is None, "mtime 差很多 → 重算")
    # SMB reports a slightly different precision after a copy, so a sub-second
    # difference must NOT invalidate — that would defeat the cache on the NAS
    check(c.get(p, 100, 1000.4) == 1, "mtime 差不到 1 秒 → 仍算命中")
    c.close()


def test_threshold_is_part_of_the_key(tmp: Path):
    """A verdict is only meaningful at the confidence it was produced with."""
    print("\n信心門檻改了，舊答案就不能用")
    db = tmp / "t.sqlite3"
    c = ps.Cache(db)
    p = Path(r"X:\pics\d.jpg")
    c.put(p, 100, 1000.0, 0)
    c.close()

    c2 = ps.Cache(db, 0.45)
    check(c2.get(p, 100, 1000.0) is None, "門檻 0.35 存的，用 0.45 讀不到")
    c2.close()
    c3 = ps.Cache(db, ps.DEFAULT_CONF)
    check(c3.get(p, 100, 1000.0) == 0, "改回原門檻又命中")
    c3.close()


def test_forget_tree_escapes_wildcards(tmp: Path):
    print("\n整個資料夾的清除要逸出 LIKE 萬用字元")
    c = ps.Cache(tmp / "f.sqlite3")
    # the real archive root that broke this in videoflags
    keep = Path(r"N:\Social MediaX亞洲\someone\pic.jpg")
    doomed = Path(r"N:\Social Media_亞洲\someone\pic.jpg")
    for p in (keep, doomed):
        c.put(p, 10, 1.0, 1)
    c.flush()
    c.forget_tree(Path(r"N:\Social Media_亞洲"))
    check(c.get(doomed, 10, 1.0) is None, "指定的資料夾被清掉了")
    check(c.get(keep, 10, 1.0) == 1,
          "`_` 沒有被當成萬用字元，隔壁那個名字幾乎一樣的資料夾沒被波及")
    c.close()


def test_forget_one(tmp: Path):
    print("\n刪掉單一檔案的紀錄")
    c = ps.Cache(tmp / "o.sqlite3")
    a, b = Path(r"X:\a.jpg"), Path(r"X:\b.jpg")
    c.put(a, 1, 1.0, 0)
    c.put(b, 1, 1.0, 2)
    c.flush()
    c.forget(a)
    check(c.get(a, 1, 1.0) is None, "指定的沒了")
    check(c.get(b, 1, 1.0) == 2, "別人沒事")
    c.close()


def test_probe_reports_missing_model(tmp: Path):
    print("\n模型不在時要說清楚，而不是等到掃描才爆")
    p = ps.probe(tmp)
    check(p["model"] is False, "回報找不到模型")
    check(str(ps.model_path(tmp)) in p["reason"] or not p["deps"],
          f"訊息裡帶著它找過的路徑或缺套件原因：{p['reason']!r}")
    check(ps.available(tmp) is False, "available() 是 False")


def test_scan_uses_cache_and_can_stop(tmp: Path):
    """No model needed: `count_people` is stubbed, so this checks the loop."""
    print("\n掃描迴圈：會用快取、會回報進度、可以中途停止")
    photos = [(Path(rf"X:\p{i}.jpg"), 100 + i, 1000.0) for i in range(10)]
    calls = []

    real_model, real_count, real_dev = ps.get_model, ps.count_people, ps.device_for
    ps.get_model = lambda d: "fake"
    ps.device_for = lambda: "cpu"
    # every third photo has nobody in it
    ps.count_people = lambda m, p, d, c=ps.DEFAULT_CONF: (
        calls.append(p), 0 if len(calls) % 3 == 0 else 1)[1]

    class Prog:
        def __init__(self, stop_after=None):
            self.msgs, self.stop_after = [], stop_after
        def __call__(self, m):
            self.msgs.append(m)
        @property
        def cancelled(self):
            return self.stop_after is not None and len(calls) >= self.stop_after

    db = tmp / "s.sqlite3"
    try:
        pr = Prog()
        r1 = ps.scan(photos, tmp, db, pr)
        check(r1["scanned"] == 10 and r1["cached"] == 0, "第一趟全部實算")
        check(len(r1["no_person"]) == 3,
              f"找到 3 張沒有人物的（{len(r1['no_person'])}）")
        check(bool(pr.msgs) and pr.msgs[-1]["total"] == 10, "有回報進度與總數")

        calls.clear()
        r2 = ps.scan(photos, tmp, db, Prog())
        check(r2["cached"] == 10 and r2["scanned"] == 0, "第二趟全部命中快取")
        check(not calls, "而且一次模型都沒呼叫")
        check(r2["no_person"] == r1["no_person"], "結論一樣")

        # cancelling mid-run on fresh files
        fresh = [(Path(rf"Y:\q{i}.jpg"), 1, 1.0) for i in range(30)]
        calls.clear()
        r3 = ps.scan(fresh, tmp, db, Prog(stop_after=5))
        check(r3["cancelled"] is True, "回報已取消")
        check(r3["scanned"] < 30, f"沒有跑完全部（做了 {r3['scanned']} 張）")
    finally:
        ps.get_model, ps.count_people, ps.device_for = \
            real_model, real_count, real_dev


def test_unreadable_photo_counts_as_failed(tmp: Path):
    """A broken file must not be reported as "no person" — that is a delete
    suggestion for a photo nobody could even open."""
    print("\n讀不到的檔案算失敗，不能算成「沒有人物」")
    real_model, real_count, real_dev = ps.get_model, ps.count_people, ps.device_for
    ps.get_model = lambda d: "fake"
    ps.device_for = lambda: "cpu"
    ps.count_people = lambda m, p, d, c=ps.DEFAULT_CONF: None
    try:
        r = ps.scan([(Path(r"X:\broken.jpg"), 1, 1.0)], tmp,
                    tmp / "b.sqlite3", None)
        check(r["failed"] == 1, "算進 failed")
        check(r["no_person"] == [], "沒有出現在待刪清單裡")
    finally:
        ps.get_model, ps.count_people, ps.device_for = \
            real_model, real_count, real_dev


def test_conf_is_clamped(tmp: Path):
    """A hand-made request must not be able to ask for a reckless threshold.

    Measured: at 0.80 the detector flags 19% of a folder as person-free, and
    almost all of those photos do contain a person — just at confidence 0.7
    rather than 0.9. Handing that to a batch delete is the whole risk.
    """
    print("\n門檻要被夾在量過的安全範圍內")
    check(ps.clamp_conf(0.35) == 0.35, "正常值原樣通過")
    check(ps.clamp_conf(0.95) == ps.CONF_MAX, f"太高夾到 {ps.CONF_MAX}")
    check(ps.clamp_conf(0.01) == ps.CONF_MIN, f"太低夾到 {ps.CONF_MIN}")
    check(ps.clamp_conf(None) == ps.DEFAULT_CONF, "None 用預設")
    check(ps.clamp_conf("abc") == ps.DEFAULT_CONF, "垃圾字串用預設")
    check(ps.CONF_MIN < ps.DEFAULT_CONF < ps.CONF_MAX, "預設值在範圍內")


def test_last_conf_finds_the_threshold_used(tmp: Path):
    """The grid must read verdicts at the threshold they were written with.

    Otherwise a folder scanned at 0.45 reports as entirely unscanned when the
    slider happens to sit at 0.35 — the answers exist, filed under another key.
    """
    print("\n查得出一個資料夾上次用的門檻")
    db = tmp / "lc.sqlite3"
    folder = Path(r"N:\Social Media_亞洲\someone")
    c = ps.Cache(db, 0.45)
    for i in range(5):
        c.put(folder / f"{i}.jpg", 10, 1.0, i % 2)
    c.close()
    probe = ps.Cache(db)
    check(probe.last_conf(folder) == 0.45, "回報 0.45")
    check(probe.last_conf(Path(r"N:\Social Media_亞洲\nobody")) is None,
          "沒掃過的資料夾回 None")
    # the LIKE-escaping trap again: `Social Media_亞洲` contains a wildcard
    check(probe.last_conf(Path(r"N:\Social MediaX亞洲\someone")) is None,
          "`_` 沒有被當成萬用字元")
    probe.close()


def test_scan_honours_the_threshold(tmp: Path):
    print("\n掃描會把門檻傳給模型，也寫進結果")
    real_model, real_count, real_dev = ps.get_model, ps.count_people, ps.device_for
    seen = []
    ps.get_model = lambda d: "fake"
    ps.device_for = lambda: "cpu"
    ps.count_people = lambda m, p, d, c=ps.DEFAULT_CONF: (seen.append(c), 0)[1]
    try:
        one = [(Path("X:") / "one.jpg", 1, 1.0)]
        two = [(Path("X:") / "two.jpg", 1, 1.0)]
        r = ps.scan(one, tmp, tmp / "t.sqlite3", None, conf=0.5)
        check(seen == [0.5], f"模型收到 0.5（實際 {seen}）")
        check(r["conf"] == 0.5, "結果帶著用過的門檻")
        r2 = ps.scan(two, tmp, tmp / "t.sqlite3", None, conf=0.99)
        check(r2["conf"] == ps.CONF_MAX, f"超出範圍的先被夾住（{r2['conf']}）")
    finally:
        ps.get_model = real_model
        ps.count_people = real_count
        ps.device_for = real_dev


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for fn in (test_cache_roundtrip, test_zero_is_not_missing,
               test_replaced_photo_rescans, test_threshold_is_part_of_the_key,
               test_forget_tree_escapes_wildcards, test_forget_one,
               test_probe_reports_missing_model,
               test_scan_uses_cache_and_can_stop,
               test_unreadable_photo_counts_as_failed,
               test_conf_is_clamped, test_last_conf_finds_the_threshold_used,
               test_scan_honours_the_threshold):
        d = Path(tempfile.mkdtemp())
        try:
            fn(d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
