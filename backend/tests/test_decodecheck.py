"""Full-decode check — finding videos whose picture data is damaged.

The damaged file here is damaged for real: an ordinary h264 MP4 with a run
of bytes in the middle of `mdat` overwritten, which is the shape of the
library's broken files (container intact, ffprobe happy, frames garbage).

  * a clean file reports 0, a damaged one reports decoder errors
  * the null muxer's timestamp warnings are not counted as damage
  * the scan flags a damaged .mp4 even when MP4s are not otherwise included
    (that is exactly the file every other check passes)
  * an unchanged file is not decoded twice; a changed one is
  * the stop flag ends the pass, and what was found is still reported
  * corrupt *audio* packets are counted apart from picture damage, and a
    file with only those is offered the audio fix, which really removes them

Run:  python tests/test_decodecheck.py        (from backend/)
"""
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import convert, decodecheck            # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def clip(dst: Path, seconds=12) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", "-f", "lavfi",
                    "-i", f"testsrc2=size=640x360:rate=25:duration={seconds}",
                    "-c:v", "libx264", "-preset", "ultrafast", "-g", "50",
                    "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dst)],
                   capture_output=True, check=True)
    return dst


def damage(src: Path, dst: Path) -> Path:
    """Overwrite 64 KB in the middle of the file: container stays readable."""
    data = bytearray(src.read_bytes())
    mid = len(data) // 2
    data[mid:mid + 65536] = bytes((i * 131 + 7) % 256 for i in range(65536))
    dst.write_bytes(bytes(data))
    return dst


def av_clip(dst: Path, seconds=12) -> Path:
    """Picture and an AAC sine tone."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y",
                    "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=25:duration={seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(dst)],
                   capture_output=True, check=True)
    return dst


def damage_audio(src: Path, dst: Path, n=8) -> Path:
    """Overwrite n AAC packets in the middle, located from ffprobe's packet
    offsets: the picture and the container stay intact — the shape of the
    MTALL-120 file (0 video errors, 79 corrupt audio packets)."""
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0",
                          "-show_entries", "packet=pos,size", "-of", "json", str(src)],
                         capture_output=True, check=True).stdout
    pk = json.loads(out)["packets"]
    data = bytearray(src.read_bytes())
    mid = len(pk) // 2
    for p in pk[mid:mid + n]:
        pos, size = int(p["pos"]), int(p["size"])
        data[pos:pos + size] = bytes((i * 97 + 13) % 256 for i in range(size))
    dst.write_bytes(bytes(data))
    return dst


def test_counts(tmp: Path):
    print("\n乾淨的是 0，弄壞的有解碼錯誤")
    good = clip(tmp / "good.mp4")
    bad = damage(good, tmp / "bad.mp4")
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "csv=p=0", str(bad)], capture_output=True, text=True)
    check(probe.returncode == 0 and float(probe.stdout) > 10,
          "弄壞的檔案 ffprobe 照樣讀得到 —— 其他檢查就是這樣被騙過的")
    check(decodecheck.decode(good) == 0, "乾淨的影片 0 行")
    n = decodecheck.decode(bad)
    check(n is not None and n > 0, f"弄壞的影片有解碼錯誤（{n} 行）")


def test_ignores_muxer_warnings():
    print("\n時間標記警告不算損壞")
    s = ("[null @ 000001c4a3ae0f00] Application provided invalid, non monotonically "
         "increasing dts to muxer in stream 0: 13949 >= 13949\n") * 30
    check(decodecheck.count_errors(s) == 0, "30 行 null 封裝器的警告 → 0")
    s2 = s + "[h264 @ 0000026991b8ff40] Invalid NAL unit size (1088755311 > 3492).\n"
    check(decodecheck.count_errors(s2) == 1, "混一行解碼錯誤 → 1")


def test_repeated_muxer_warnings():
    print("\n「Last message repeated」跟著前一行：接在封裝器警告後面不算損壞")
    # what a real ffmpeg prints for zenmeimei\V\hd (4).mp4 — 3 of these
    # lines and no decoder complaint, which used to read as 3 errors
    w = ("[null @ 0000019689f64b40] Application provided invalid, non "
         "monotonically increasing dts to muxer in stream 0: 236 >= {}\n"
         "    Last message repeated 248 times\n")
    s = "".join(w.format(n) for n in (234, 235, 236))
    check(decodecheck.split_errors(s) == (0, 0), f"3 段封裝器警告＋重複 → 0：{decodecheck.split_errors(s)}")
    s2 = ("[h264 @ 1] error while decoding MB 86 43, bytestream -5\n"
          "    Last message repeated 3 times\n"
          "[aac @ 2] Number of bands (49) exceeds limit (44).\n"
          "    Last message repeated 5 times\n") + s
    check(decodecheck.split_errors(s2) == (2, 2),
          f"接在真的錯誤後面：算進同一類（影像 2、聲音 2）：{decodecheck.split_errors(s2)}")


def test_region_ends():
    print("\n損壞處是一段範圍：結束在畫面回來的地方")
    T = lambda t: f"out_time_us={int(round(t * 1e6))}\n"
    E = "[h264 @ 1] Invalid NAL unit size (1 > 2).\n"

    def run(a, b, step=0.5):
        # reports as dense as a real decode gives, from a to b
        out, t = "", a
        while t <= b + 1e-9:
            out += T(t)
            t += step
        return out

    # progress stands still at 28 while errors pour in, then jumps to 127
    s = run(0, 28) + E + T(28.0) + E + T(28.0) + E + run(127, 140)
    g = decodecheck.regions_of(s, duration=140)
    check(g and g[0]["start"] == 28.0 and g[0]["end"] == 127.0,
          f"進度停住、錯誤湧出、再跳走：範圍到畫面回來 → {g}")
    # a fast decode: each time reported once, far apart
    s2 = T(0.0) + E + T(9.0) + T(12.0)
    check(decodecheck.regions_of(s2, duration=12) is None, "回報太稀疏：不採用（交給逐格定位）")
    s2b = E + T(12.0)          # a 12 s clip: the first report is already the end
    check(decodecheck.regions_of(s2b, duration=12) is None,
          "錯誤在第一次回報之前、而那次已經很遠：一樣不採用")
    # frame by frame: three frames come out after the errors, then a hole
    s3 = run(0, 27.9, 1 / 30) + E + T(28.0) + T(28.03) + T(28.07) + run(127.27, 140, 1 / 30)
    g = decodecheck.regions_of(s3, exact=True, duration=140)
    check(g and abs(g[0]["end"] - 127.27) < 0.01,
          f"逐格：錯誤後幾格就斷了，空洞算進範圍 → {g}")
    # frames never come back
    s4 = run(0, 17, 1 / 30) + E + T(18.3) + T(18.4)
    g = decodecheck.regions_of(s4, exact=True, duration=265)
    check(g and g[0]["end"] == 265, f"之後再也沒有畫面：延到片尾 → {g}")
    # an ordinary one-second glitch stays about a second
    s5 = run(0, 85.5, 1 / 30) + E + T(85.6) + E + run(86.0, 225, 1 / 30)
    g = decodecheck.regions_of(s5, exact=True, duration=225)
    check(g and g[0]["end"] - g[0]["start"] < 1.5, f"一般的小段損壞不被拉長 → {g}")


def test_cache_v2(tmp: Path):
    print("\n舊快取裡報過損壞的紀錄會作廢重查，乾淨的保留")
    import sqlite3
    db = tmp / "dc.sqlite3"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE checks (path TEXT PRIMARY KEY, size INTEGER, mtime REAL, "
              "errors INTEGER, at REAL, audio INTEGER)")
    c.execute("INSERT INTO checks VALUES ('a.mp4', 1, 1.0, 3, 0, 0)")
    c.execute("INSERT INTO checks VALUES ('b.mp4', 1, 1.0, 0, 0, 0)")
    c.commit(); c.close()
    cache = decodecheck.Cache(db)
    check(cache.get(Path("a.mp4"), 1, 1.0) is None, "報過 3 行錯誤的：作廢，下次重新解碼")
    check(cache.get(Path("b.mp4"), 1, 1.0) == 0, "乾淨的：保留")
    cache.put(Path("a.mp4"), 1, 1.0, decodecheck.Counts(0, 0))
    cache.close()
    cache = decodecheck.Cache(db)
    check(cache.get(Path("a.mp4"), 1, 1.0) == 0, "只作廢一次：重查後的結果不會再被刪")
    cache.close()


def test_split_audio_lines():
    print("\n聲音的錯誤分開算（看 ffmpeg 每行開頭的元件標籤）")
    s = ("[aac @ 00000269ae6161c0] Number of bands (49) exceeds limit (44).\n"
         "[aist#0:1/aac @ 00000269b084f040] [dec:aac @ 00000269ae616040] Error "
         "submitting packet to decoder: Invalid data found when processing input\n"
         "[h264 @ 0000019867f74000] error while decoding MB 86 43, bytestream -5\n"
         "[vist#0:0/h264 @ 0000019867f74000] [dec:h264 @ 1] Error submitting packet\n"
         "[null @ 000001c4a3ae0f00] non monotonically increasing dts\n")
    check(decodecheck.split_errors(s) == (2, 2), f"影像 2、聲音 2、封裝器警告不算：{decodecheck.split_errors(s)}")
    check(decodecheck.count_errors(s) == 4, "合計照舊")
    n = decodecheck.Counts(0, 79)
    check(not n and n.audio == 79 and int(n) == 0,
          "影像 0、聲音 79：當成整數是 0（畫面沒壞），聲音數字另外帶著")


def test_audio_damage(tmp: Path):
    print("\n只有聲音壞：畫面 0 行、聲音有錯；修聲音之後真的沒了")
    good = av_clip(tmp / "av.mp4")
    bad = damage_audio(good, tmp / "badaudio.mp4")
    n0, n1 = decodecheck.decode(good), decodecheck.decode(bad)
    check(int(n0) == 0 and n0.audio == 0, f"乾淨的影音都是 0（{int(n0)}／{n0.audio}）")
    check(int(n1) == 0 and n1.audio > 0, f"畫面 0 行、聲音 {n1.audio} 行")
    check(decodecheck.audio_errors(bad) == n1.audio, "只解聲音的那條路算出一樣的數字")
    check(decodecheck.audio_errors(clip(tmp / "silent.mp4", 2)) == 0,
          "沒有聲音的影片 → 0，不是錯誤")
    check(convert.audio_needs_fix(bad) > 0 and convert.audio_needs_fix(good) == 0,
          "換殼前的檢查：壞的要修、乾淨的不用")

    info = convert.probe(bad)
    out = tmp / "fixed.mp4"
    convert.run_one(bad, out, "audiofix", info)
    check(convert.verify(info, out, "audiofix") is None, "修好的檔案通過驗證")
    check(decodecheck.audio_errors(out) == 0, "輸出的聲音 0 行錯誤")
    check(convert.probe(out)["vcodec"] == "h264", "影像照搬（仍是 h264）")
    # verify must catch an "audio fix" that fixed nothing
    shutil.copy(bad, tmp / "unfixed.mp4")
    why = convert.verify(info, tmp / "unfixed.mp4", "audiofix")
    check(why and "聲音" in why, f"沒修好的會被驗證擋下：{why}")


def test_noisy_convert_does_not_hang(tmp: Path):
    """run_one used to read stderr only after stdout ended; ffmpeg with more
    errors than the pipe holds then blocked forever (MTALL-120: stuck at 62%,
    0 CPU). Run in a thread so the old code fails here instead of hanging."""
    print("\n錯誤訊息很多的轉檔不會卡死（ffmpeg 的錯誤輸出要邊跑邊讀）")
    import threading
    good = av_clip(tmp / "long.mp4", 40)
    bad = damage_audio(good, tmp / "noisy.mp4", n=600)
    info = convert.probe(bad)
    out = tmp / "noisy_fixed.mp4"
    box = {}

    def go():
        try:
            convert.run_one(bad, out, "audiofix", info)
            box["ok"] = True
        except Exception as e:
            box["err"] = e

    t = threading.Thread(target=go, daemon=True)
    t.start()
    t.join(120)
    check(not t.is_alive(), "120 秒內跑完（舊寫法會永遠卡住）")
    check(box.get("ok") and decodecheck.audio_errors(out) == 0,
          f"而且真的修好了：{box}")


def _packets(path: Path) -> list:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "packet=dts_time,pos,size,flags",
                          "-of", "json", str(path)], capture_output=True, check=True).stdout
    return json.loads(out)["packets"]


def gapped_clip(tmp: Path, extra_damage=False) -> Path:
    """The MTALL-120 shape, built for real: a recording with two dropouts
    (packets removed, so the video DTS jumps), each resuming on a keyframe,
    and the last frame before each dropout overwritten. With `extra_damage`
    a frame in the middle of a GOP is broken too — damage the fix cannot
    reach, which the verify has to catch."""
    base = tmp / "rec_base.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25:duration=12",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=12",
                    "-c:v", "libx264", "-preset", "ultrafast", "-g", "25", "-bf", "0",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", str(base)],
                   capture_output=True, check=True)
    gapped = tmp / "rec_gapped.mp4"
    drop = "+".join(f"between(n\\,{a}\\,{b})" for a, b in ((60, 74), (160, 174)))
    subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", "-i", str(base),
                    "-c", "copy", "-bsf:v", f"noise=drop={drop}", str(gapped)],
                   capture_output=True, check=True)
    pk = _packets(gapped)
    data = bytearray(gapped.read_bytes())
    gaps = [i for i in range(len(pk) - 1)
            if float(pk[i + 1]["dts_time"]) - float(pk[i]["dts_time"]) > 0.5]
    hit = list(gaps) + ([110] if extra_damage else [])
    for i in hit:
        pos, size = int(pk[i]["pos"]), int(pk[i]["size"])
        data[pos + 4:pos + size] = bytes((j * 131 + 7) % 256 for j in range(size - 4))
    out = tmp / ("rec_broken2.mp4" if extra_damage else "rec_broken.mp4")
    out.write_bytes(bytes(data))
    return out


def test_gapfix(tmp: Path):
    print("\n錄影斷訊前的殘缺畫格：判為可修復，丟掉那幾格之後完整解碼 0 錯誤")
    bad = gapped_clip(tmp)
    n = decodecheck.decode(bad)
    check(n and n > 0, f"素材真的有畫面錯誤（{int(n or 0)} 行）")
    plan = convert.gap_plan(bad)
    check(plan and plan["gaps"] == 2 and len(plan["drop"]) == 2,
          f"找到 2 處斷訊、各丟 1 格：{plan}")

    lib = tmp / "lib"
    lib.mkdir()
    shutil.copy(bad, lib / "rec.mp4")
    rows = convert.scan([lib], check_decode=True)
    r = {Path(x["path"]).name: x for x in rows}.get("rec.mp4", {})
    check(r.get("action") == "gapfix" and r.get("in_place") is True,
          f"掃描列為修復斷訊、就地取代：{r.get('action')}｜{r.get('reason')}")

    info = {**convert.probe(bad), "drop": plan["drop"], "fix_audio": False}
    out = tmp / "fixed.mp4"
    convert.run_one(bad, out, "gapfix", info)
    check(convert.verify(info, out, "gapfix") is None, "修好的通過驗證（含完整解碼）")
    before, after = len(_packets(bad)), len(_packets(out))
    check(after == before - 2, f"只少了 2 格（{before} → {after}）")

    print("\n斷訊以外也壞了：修完驗證不過，原檔保留")
    bad2 = gapped_clip(tmp, extra_damage=True)
    plan2 = convert.gap_plan(bad2)
    info2 = {**convert.probe(bad2), "drop": plan2["drop"], "fix_audio": False}
    out2 = tmp / "fixed2.mp4"
    convert.run_one(bad2, out2, "gapfix", info2)
    why = convert.verify(info2, out2, "gapfix")
    check(why and "仍有畫面錯誤" in why, f"驗證擋下：{why}")

    print("\n沒有斷訊的畫面損壞照舊是「損壞」")
    plain = damage(clip(tmp / "plain.mp4"), tmp / "plain_bad.mp4")
    check(convert.gap_plan(plain) is None, "沒有斷訊 → 不適用")


def test_audio_scan_and_migration(tmp: Path):
    print("\n掃描：只有聲音壞的 .mp4 列為「修復聲音」；舊快取表會補欄位並重新檢查")
    lib = tmp / "lib"
    good = av_clip(lib / "good.mp4")
    damage_audio(good, lib / "badaudio.mp4")
    db = tmp / "dc.sqlite3"
    # an old-shape table, as the real data/decode_check.sqlite3 was before
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE checks (path TEXT PRIMARY KEY, size INTEGER, "
                 "mtime REAL, errors INTEGER, at REAL)")
    st = (lib / "badaudio.mp4").stat()
    conn.execute("INSERT INTO checks VALUES (?, ?, ?, 0, 0)",
                 (str(lib / "badaudio.mp4"), st.st_size, st.st_mtime))
    conn.commit()
    conn.close()

    rows = convert.scan([lib], check_decode=True, decode_cache=db)
    by = {Path(r["path"]).name: r for r in rows}
    r = by.get("badaudio.mp4", {})
    check(r.get("action") == "audiofix" and r.get("audio_errors", 0) > 0,
          f"舊快取說「0 行」也要重新檢查，並列為修復聲音：{r.get('action')} / {r.get('audio_errors')}")
    check(r.get("in_place") is True, "就地取代（檔名不變）")
    check("good.mp4" not in by, "乾淨的不列出")
    check("audiofix" in convert.RUNNABLE, "可以執行")

    ts = lib / "rec.ts"
    subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", "-i",
                    str(lib / "badaudio.mp4"), "-c", "copy", "-f", "mpegts", str(ts)],
                   capture_output=True, check=True)
    rows = convert.scan([lib], check_decode=True, decode_cache=db)
    t = {Path(r["path"]).name: r for r in rows}.get("rec.ts", {})
    check(t.get("action") == "remux" and "一併重新編碼聲音" in t.get("reason", ""),
          f".ts 照樣換殼，註明會一併修聲音：{t.get('action')}｜{t.get('reason')}")
    check(convert.audio_needs_fix(ts) > 0, "換殼前的檢查認得出這支要修")


def test_levels():
    print("\n三級分界（使用者選的）")
    check(decodecheck.level(0) == "" and decodecheck.level(None) == "", "0／未知 → 不標")
    check(decodecheck.level(122) == "輕微", "122 → 輕微")
    check(decodecheck.level(1_000) == "中等" and decodecheck.level(10_000) == "中等",
          "1,000 與 10,000 → 中等")
    check(decodecheck.level(10_001) == "嚴重", "10,001 → 嚴重")


def test_scan_and_cache(tmp: Path):
    print("\n掃描：不勾檢查 MP4 也會抓到壞的 .mp4；沒變的檔不重解；變了會重解")
    lib = tmp / "lib"
    good = clip(lib / "good.mp4")
    bad = damage(good, lib / "bad.mp4")
    db = tmp / "dc.sqlite3"

    rows = convert.scan([lib], include_mp4=False, check_decode=True, decode_cache=db)
    by = {Path(r["path"]).name: r for r in rows}
    check(set(by) == {"bad.mp4"}, f"只列出壞掉的那支：{sorted(by)}")
    r = by.get("bad.mp4", {})
    check(r.get("action") == "damaged" and r.get("decode_errors", 0) > 0,
          f"標成 damaged 並帶錯誤數：{r.get('action')} / {r.get('decode_errors')}")
    check(r.get("damage") in ("輕微", "中等", "嚴重") and "畫面損壞" in r.get("reason", ""),
          f"有分級與說明：{r.get('damage')}｜{r.get('reason')}")
    check(r.get("duration", 0) > 10, "帶片長，畫面上要顯示")

    calls = []
    real = decodecheck.decode
    decodecheck.decode = lambda p, gpu=False, stop=None: (
        calls.append(p.name), real(p, gpu, stop))[1]
    try:
        convert.scan([lib], check_decode=True, decode_cache=db)
        check(calls == [], f"第二次掃描沒有重新解碼：{calls}")
        damage(bad, bad)                     # rewrite → size/mtime differ
        bad.write_bytes(bad.read_bytes() + b"\0")
        convert.scan([lib], check_decode=True, decode_cache=db)
        check(calls == ["bad.mp4"], f"檔案變了才重解，而且只有那一支：{calls}")
    finally:
        decodecheck.decode = real

    rows = convert.scan([lib], check_decode=False)
    check(all(r.get("action") != "damaged" for r in rows), "沒勾選時完全不做這件事")


def test_stop(tmp: Path):
    print("\n喊停：不再開新的檔案，已找到的照樣回報")
    lib = tmp / "lib"
    good = clip(lib / "a_good.mp4", 4)
    damage(good, lib / "b_bad.mp4")
    for i in range(6):
        shutil.copy(good, lib / f"c_{i}.mp4")

    class Prog:
        cancelled = False
        def __init__(self): self.n = 0
        def __call__(self, msg):
            if msg.startswith("完整解碼檢查"):
                self.n += 1
                if self.n >= 2:
                    Prog.cancelled = True

    calls = []
    real = decodecheck.decode
    decodecheck.decode = lambda p, gpu=False, stop=None: (
        calls.append(p.name), real(p, gpu, stop))[1]
    convert.DECODE_WORKERS, saved = 1, convert.DECODE_WORKERS
    try:
        rows = convert.scan([lib], progress=Prog(), check_decode=True)
    finally:
        decodecheck.decode = real
        convert.DECODE_WORKERS = saved
    check(len(calls) == 2, f"8 支只解了 2 支就停：{calls}")
    check(any(r.get("action") == "damaged" for r in rows),
          "停之前找到的那支壞檔照樣列出來")


def test_stop_mid_file(tmp: Path):
    """Stopping ends the decode in hand, not after it.

    The stop button used to wait for every file being decoded to finish: two
    4-hour films sharing the GPU kept the scan at 「停止中」 for over a quarter
    of an hour. A stopped file is not an answer — nothing is remembered.
    """
    print("\n喊停：正在解的那支當場結束，不等它解完")
    big = tmp / "long.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", "-f", "lavfi",
                    "-i", "testsrc2=size=1920x1080:rate=25:duration=400",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    str(big)], capture_output=True, check=True)
    t0 = time.monotonic()
    decodecheck.decode(big)
    whole = time.monotonic() - t0

    t0 = time.monotonic()
    stop = lambda: time.monotonic() - t0 > 0.5
    stopped = False
    try:
        decodecheck.decode(big, stop=stop)
    except decodecheck.Stopped:
        stopped = True
    took = time.monotonic() - t0
    check(stopped and took < 2.0 and took < whole / 2,
          f"喊停後 {took:.1f} 秒結束（整支要 {whole:.1f} 秒）")

    cache = decodecheck.Cache(tmp / "dc.sqlite3")
    t0 = time.monotonic()
    n = decodecheck.check(big, cache, stop=stop)
    st = big.stat()
    check(n is None and cache.get(big, st.st_size, st.st_mtime) is None,
          "停下的那支沒有答案，也不會被記成「沒壞」")
    cache.close()


def test_long_files_alone(tmp: Path):
    """Short files four at a time, long ones (and unknown lengths) alone.

    Measured: on 10-minute clips decoding four at once gained nothing — each
    file just took four times as long — while on 30-second clips it saved
    about a quarter. The user's rule follows from that.
    """
    print("\n同時解幾部：短片 4 部一起，長片一次一部")
    lib = tmp / "lib"
    good = clip(lib / "short_0.mp4", 2)
    for name in ["short_1", "short_2", "short_3", "short_4", "short_5",
                 "long_0", "long_1", "unknown_0"]:
        shutil.copy(good, lib / f"{name}.mp4")
    length = {"short": 30.0, "long": 3 * 3600.0, "unknown": 0.0}
    running, seen, lock = [], [], threading.Lock()

    def fake(p, gpu=False, stop=None):
        with lock:
            running.append(p.stem)
            seen.append(list(running))
        time.sleep(0.15)
        with lock:
            running.remove(p.stem)
        return decodecheck.Counts(0, 0, [])

    real_d, real_len = decodecheck.decode, convert._duration
    decodecheck.decode = fake
    convert._duration = lambda p: length[p.stem.split("_")[0]]
    try:
        convert.scan([lib], check_decode=True)
    finally:
        decodecheck.decode, convert._duration = real_d, real_len
    check(max(len(r) for r in seen if all(x.startswith("short") for x in r)) > 1,
          "短片會同時解好幾支")
    alone = [r for r in seen if any(not x.startswith("short") for x in r)]
    check(alone and all(len(r) == 1 for r in alone),
          f"長片與長度不明的，解的時候只有它一支：{alone}")


def test_gpu_allowlist(tmp: Path):
    """Only what the RTX 2070's NVDEC takes goes to the GPU: a refusal does
    not fail fast, and its complaints would be counted as damage."""
    print("\nGPU 白名單：只有 NVDEC 吃得下的才送 GPU")
    ok = clip(tmp / "h264.mp4", 2)
    check(decodecheck.gpu_ok(ok), "h264 / yuv420p → GPU")
    p444 = tmp / "h264_444.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", "-f", "lavfi",
                    "-i", "testsrc2=size=320x180:rate=10:duration=2", "-c:v", "libx264",
                    "-preset", "ultrafast", "-pix_fmt", "yuv444p", str(p444)],
                   capture_output=True, check=True)
    check(not decodecheck.gpu_ok(p444), "h264 / yuv444p → CPU（NVDEC 不吃）")
    av1 = tmp / "av1.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", "-f", "lavfi",
                    "-i", "testsrc2=size=320x180:rate=10:duration=2", "-c:v", "libaom-av1",
                    "-cpu-used", "8", "-crf", "50", "-pix_fmt", "yuv420p", str(av1)],
                   capture_output=True)
    if av1.exists():
        check(not decodecheck.gpu_ok(av1), "AV1 → CPU（這張卡沒有 AV1 解碼器）")


def test_gpu_fallback():
    """A refusal is redone on the CPU; damage on the GPU is not."""
    print("\nGPU 拒絕才退回 CPU；結束碼非零但有解碼錯誤 = 損壞，不重跑")

    class R:
        def __init__(self, rc, err): self.returncode, self.stderr = rc, err

    real_run, real_ok = decodecheck._run, decodecheck.gpu_ok
    calls = []

    def fake(gpu_reply):
        def _run(path, gpu, stop=None):
            calls.append("gpu" if gpu else "cpu")
            return gpu_reply if gpu else R(0, "")
        return _run

    decodecheck.gpu_ok = lambda p: True
    try:
        decodecheck._run = fake(R(1, "[h264 @ 1] hwaccel initialisation returned error\n"))
        n = decodecheck.decode(Path("x.mp4"), gpu=True)
        check(calls == ["gpu", "cpu"] and n == 0,
              f"GPU 回報硬體拒絕 → 改用 CPU，答案取 CPU 的：{calls} / {n}")

        calls.clear()
        dmg = "[h264 @ 1] Invalid NAL unit size (1 > 2).\n" * 5
        decodecheck._run = fake(R(69, dmg))
        n = decodecheck.decode(Path("x.mp4"), gpu=True)
        check(calls == ["gpu"] and n == 5,
              f"結束碼 69 但有解碼錯誤 → 那是損壞（CPU 也回 69），不重跑：{calls} / {n}")

        calls.clear()
        decodecheck._run = fake(R(1, ""))
        decodecheck.decode(Path("x.mp4"), gpu=True)
        check(calls == ["gpu", "cpu"], f"失敗卻沒有任何解碼錯誤 → 改用 CPU：{calls}")

        calls.clear()
        decodecheck.gpu_ok = lambda p: False
        decodecheck._run = fake(R(0, ""))
        decodecheck.decode(Path("x.mp4"), gpu=True)
        check(calls == ["cpu"], f"不在白名單 → 根本不試 GPU：{calls}")
    finally:
        decodecheck._run, decodecheck.gpu_ok = real_run, real_ok


def test_gpu_real(tmp: Path):
    """On a machine with NVDEC: the GPU finds the same damage."""
    if shutil.which("nvidia-smi") is None:
        print("\n（沒有 NVIDIA 顯示卡，略過 GPU 實測）")
        return
    print("\nGPU 實測：一樣抓得到損壞")
    good = clip(tmp / "good.mp4")
    bad = damage(good, tmp / "bad.mp4")
    g0, g1 = decodecheck.decode(good, gpu=True), decodecheck.decode(bad, gpu=True)
    c1 = decodecheck.decode(bad)
    check(g0 == 0, f"乾淨的 GPU 也是 0（{g0}）")
    check(g1 and c1 and abs(g1 - c1) <= max(2, c1 // 20),
          f"壞的 GPU {g1} 行，CPU {c1} 行，幾乎相同")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    test_ignores_muxer_warnings()
    test_levels()
    test_split_audio_lines()
    test_repeated_muxer_warnings()
    test_region_ends()
    test_gpu_fallback()
    for fn in (test_counts, test_scan_and_cache, test_stop, test_stop_mid_file,
               test_long_files_alone,
               test_gpu_allowlist,
               test_gpu_real, test_audio_damage, test_noisy_convert_does_not_hang,
               test_gapfix, test_cache_v2,
               test_audio_scan_and_migration):
        d = Path(tempfile.mkdtemp())
        try:
            fn(d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
