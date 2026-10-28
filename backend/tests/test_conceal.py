"""Re-encoding a damaged video with the decoder's concealment (`conceal`).

The damaged moment still looks damaged; what changes is that the new file has
no broken packets left, so the browser plays and seeks it instead of freezing
on the first one. Measured on three damaged files in yaokaixin77: 0 errors
after, SSIM 0.979-0.995 against the source. A fourth, with minutes of picture
that cannot be decoded at all, came out 18 s long out of 265 — the verify has
to refuse that rather than call it a repair.

  * `locate` finds where the damage is (the pending list jumps there)
  * the re-encode decodes clean and keeps the length
  * a result that lost most of the film is refused, with a reason
  * over HTTP: the original stays, the output is `_fix.mp4`, and the pair
    goes to the pending list carrying the damaged stretches

Run:  python tests/test_conceal.py      (from backend/)
"""
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import convert, decodecheck                  # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def damaged_clip(tmp: Path) -> Path:
    """12 s with picture and sound; 64 KB in the middle overwritten, so the
    container reads fine and the decoder trips (same as test_decodecheck)."""
    good = tmp / "good.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=12",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=12",
                    "-c:v", "libx264", "-preset", "ultrafast", "-g", "50",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart",
                    str(good)], capture_output=True, check=True)
    data = bytearray(good.read_bytes())
    mid = len(data) // 2
    data[mid:mid + 65536] = bytes((i * 131 + 7) % 256 for i in range(65536))
    bad = tmp / "bad.mp4"
    bad.write_bytes(bytes(data))
    good.unlink()
    return bad


def test_conceal(tmp: Path):
    print("\n遮蔽損壞：找得到損壞處、修完 0 錯誤、長度不變")
    bad = damaged_clip(tmp)
    n0 = decodecheck.decode(bad)
    check(n0 and n0 > 0, f"素材真的壞了（{int(n0 or 0)} 行）")
    regions = decodecheck.locate(bad)
    check(regions and all(1 < g["start"] <= g["end"] < 11 for g in regions),
          f"損壞處在片子中段：{regions}")
    info = convert.probe(bad)
    dst = tmp / "bad_fix.mp4"
    convert.run_one(bad, dst, "conceal", info)
    problem = convert.verify(info, dst, "conceal")
    check(problem is None, f"驗證通過：{problem}")
    n = decodecheck.decode(dst)
    check(n == 0 and n.audio == 0, f"新檔完整解碼 0／0：{int(n)}／{n.audio}")
    check(abs(convert.probe(dst)["duration"] - info["duration"]) < 1, "長度不變")
    cmd = convert._build_cmd(bad, dst, "conceal", info)
    check("libx264" in cmd and str(convert.CONCEAL_CRF) in cmd, f"用 crf {convert.CONCEAL_CRF}")
    for act in ("conceal", "audiofix", "reencode", "remux_audio"):
        c2 = convert._build_cmd(bad, dst, act, info)
        check("-drop_changed:a" in c2 and c2.index("-drop_changed:a") < c2.index("-i"),
              f"{act}：聲音壞到參數突變時丟掉那一格，不讓整個轉檔中止")
    check("-drop_changed:a" not in convert._build_cmd(bad, dst, "remux", info),
          "純換殼不解碼聲音，不需要")

    print("\n修完只剩一小段：不算修好，說明原因")
    short = tmp / "short.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(dst), "-t", "3",
                    "-c", "copy", str(short)], capture_output=True, check=True)
    why = convert.verify(info, short, "conceal")
    check(why and "損壞太嚴重" in why, f"拒絕並說明：{why}")


def test_locate_cached(tmp: Path):
    print("\n播放前就能定位：結果記在檢查快取裡，第二次不用再解碼")
    bad = damaged_clip(tmp)
    db = tmp / "dc.sqlite3"
    cache = decodecheck.Cache(db)
    st = bad.stat()
    cache.put(bad, st.st_size, st.st_mtime, decodecheck.decode(bad))   # as the scan does
    cache.close()
    first = decodecheck.locate_cached(bad, db)
    check(first and 1 < first[0]["start"] < 11, f"定位到片子中段：{first}")
    real = decodecheck.locate
    decodecheck.locate = lambda p, limit=50: (_ for _ in ()).throw(AssertionError("又解碼了"))
    try:
        again = decodecheck.locate_cached(bad, db)
    finally:
        decodecheck.locate = real
    check(again == first, "第二次直接讀快取，答案一樣")
    cache = decodecheck.Cache(db)
    check(cache.get(bad, st.st_size, st.st_mtime) is not None, "原本的檢查結果還在")
    cache.close()


def test_scan_records_regions(tmp: Path):
    print("\n掃描時就記下損壞處：之後按 ▶ 不用再解碼")
    bad = damaged_clip(tmp)
    db = tmp / "dc.sqlite3"
    cache = decodecheck.Cache(db)
    n = decodecheck.check(bad, cache)
    cache.close()
    check(n and n.regions and 1 < n.regions[0]["start"] < 11,
          f"短片（進度回報太稀疏）也定位得到：{n and n.regions}")
    real = decodecheck.locate
    decodecheck.locate = lambda p, limit=50: (_ for _ in ()).throw(AssertionError("又解碼了"))
    try:
        got = decodecheck.locate_cached(bad, db)
    finally:
        decodecheck.locate = real
    check(got == n.regions, "按 ▶ 時直接讀掃描存下的位置")


def test_over_http(tmp: Path):
    print("\nHTTP：原檔保留、輸出 _fix.mp4、進待決定清單並帶著損壞處")
    from fastapi.testclient import TestClient            # noqa: E402
    from app import auth, db, jobs, main                 # noqa: E402
    from app.core import pairs                           # noqa: E402
    db.DB_PATH = tmp / "vam.db"
    db.init_db()
    bad = damaged_clip(tmp)
    saved = (main.is_allowed, main._PAIRS_PATH, main.DECODE_CHECK_DB)
    main.is_allowed = lambda p, u: True
    main._PAIRS_PATH = tmp / "pairs.json"
    # never the real data/ — reads included (CLAUDE.md)
    main.DECODE_CHECK_DB = tmp / "decode_check.sqlite3"
    main.app.dependency_overrides[auth.current_user] = lambda: {
        "id": 1, "role": "admin", "username": "a"}
    try:
        c = TestClient(main.app)
        r = c.post("/api/convert/run", json={"items": [
            {"path": str(bad), "action": "conceal"}], "crf": 20, "gpu": False})
        check(r.status_code == 200, f"開始：{r.status_code} {r.text[:120]}")
        jid = r.json().get("job_id")
        for _ in range(240):
            j = jobs.get(jid)
            if j and j["status"] != "running":
                break
            time.sleep(0.5)
        res = (j or {}).get("result") or {}
        check(len(res.get("converted", [])) == 1, f"完成 1 個：{res}")
        out = bad.with_name("bad_fix.mp4")
        check(bad.exists() and out.exists(), "原檔還在、新檔是 bad_fix.mp4")
        r = c.get("/api/convert/locate", params={"path": str(bad)})
        check(r.status_code == 200 and r.json()["regions"],
              f"播放前的定位端點：{r.status_code} {r.json()}")
        pend = pairs.pending(main._PAIRS_PATH)
        check(len(pend) == 1 and pend[0]["kind"] == "conceal" and pend[0].get("regions"),
              f"進待決定清單、帶著損壞處：{pend and pend[0].get('regions')}")
    finally:
        main.is_allowed, main._PAIRS_PATH, main.DECODE_CHECK_DB = saved
        main.app.dependency_overrides.clear()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for fn in (test_conceal, test_locate_cached, test_scan_records_regions,
               test_over_http):
        d = Path(tempfile.mkdtemp())
        try:
            fn(d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
