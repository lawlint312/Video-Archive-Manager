"""Sidecar subtitles, converted to WebVTT.

The interesting failure is not "no subtitle appears" — that one is obvious the
moment you press play. It is the two silent ones:

  * a UTF-16 file read as UTF-8 gives a screen of replacement characters and
    raises nothing (17 of this library's 31 files are UTF-16, in the same
    folder as 14 UTF-8 ones); and
  * ASS karaoke is many Dialogue lines for one sung line — one per syllable,
    each starting later and all ending together — so a faithful conversion put
    twenty copies of the same line on screen at once. They are NOT identical,
    which an earlier version of the dedup assumed; that is why the syllable
    case has its own test.

Run:  python tests/test_subtitles.py        (from backend/)
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import media, subtitles                 # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def _vid(p: Path):
    p.write_bytes(bytes(16))
    return p


ASS_HEAD = """[Script Info]
ScriptType: v4.00+

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ass(lines):
    return ASS_HEAD + "\n".join(lines) + "\n"


# ── subtitle_for ─────────────────────────────────────────────────────────────

def test_finds_sidecar(tmp: Path):
    print("\n同名、同目錄")
    v = _vid(tmp / "ep01.mp4")
    want = tmp / "ep01.ass"
    want.write_text(_ass([]), encoding="utf-8")
    check(media.subtitle_for(v) == want, "ep01.mp4 → ep01.ass")


def test_extension_priority(tmp: Path):
    print("\n同時有好幾種格式時，留表達力最強的")
    v = _vid(tmp / "ep01.mp4")
    (tmp / "ep01.srt").write_text("1\n", encoding="utf-8")
    (tmp / "ep01.vtt").write_text("WEBVTT\n", encoding="utf-8")
    ass = tmp / "ep01.ass"
    ass.write_text(_ass([]), encoding="utf-8")
    picked = {media.subtitle_for(v) for _ in range(5)}
    check(picked == {ass}, "五次都挑到 .ass，不會每次重整換一個")


def test_no_sidecar(tmp: Path):
    print("\n沒有就回 None（端點回 404，播放器不畫 track）")
    v = _vid(tmp / "ep01.mp4")
    check(media.subtitle_for(v) is None, "旁邊什麼都沒有")
    (tmp / "ep01.ass").write_bytes(b"")
    check(media.subtitle_for(v) is None, "0 位元組的不算")


def test_never_leaves_the_directory(tmp: Path):
    print("\n只看同目錄")
    sub = tmp / "sub"
    sub.mkdir()
    v = _vid(sub / "ep01.mp4")
    (tmp / "ep01.ass").write_text(_ass([]), encoding="utf-8")
    check(media.subtitle_for(v) is None, "上層的同名字幕不算")


# ── _dedup ───────────────────────────────────────────────────────────────────

def test_dedup_collapses_karaoke(_tmp):
    print("\n卡拉OK的重複疊字（時間完全相同的那種）")
    vtt = "WEBVTT\n\n" + "\n\n".join(
        ["00:15.410 --> 00:20.890\n君は聴こえる?"] * 4
        + ["00:15.410 --> 00:20.890\n我的聲音 你能聽見嗎?"] * 4)
    out = subtitles._dedup(vtt)
    check(out.count("君は聴こえる?") == 1, "日文那行只剩一次")
    check(out.count("我的聲音 你能聽見嗎?") == 1, "中文那行只剩一次")
    check(out.startswith("WEBVTT"), "檔頭留著")


def test_dedup_collapses_syllable_karaoke(_tmp):
    print("\n卡拉OK的逐字計時 —— 開始時間全都不同")
    # THE case an exact-match rule misses entirely, and the one that actually
    # dominates: one cue per sung syllable, each starting later, all ending
    # together. Measured on the real files, this is 2,068 of 15,331 cues and it
    # put twenty copies of one line on screen at once.
    starts = ["00:23.470", "00:23.780", "00:24.000", "00:24.300", "00:24.400",
              "00:24.500", "00:24.700", "00:25.000", "00:25.100", "00:25.200",
              "00:25.400"]
    vtt = "WEBVTT\n\n" + "\n\n".join(
        f"{s} --> 00:26.700\nだからカッコつけないで" for s in starts)
    out = subtitles._dedup(vtt)
    cues = [b for b in out.split("\n\n") if "-->" in b]
    check(len(cues) == 1, f"十一份變成一份（實得 {len(cues)}）")
    check(cues[0].startswith("00:23.470"),
          "留下的是最早開始的那一條 —— 整句話從頭到尾都看得到")


def test_dedup_keeps_same_text_at_another_end(_tmp):
    print("\n同一句話但結束時間不同 → 是兩次不同的台詞")
    vtt = ("WEBVTT\n\n"
           "00:01.000 --> 00:02.000\n是\n\n"
           "00:09.000 --> 00:11.000\n是")
    out = subtitles._dedup(vtt)
    check(out.count("是") == 2, "兩次都要留")


def test_dedup_keeps_real_bilingual_overlap(_tmp):
    print("\n真正的雙語字幕不能被砍掉")
    # This is the whole reason the match is exact rather than by timestamp:
    # two DIFFERENT cues sharing a timestamp are a bilingual subtitle.
    vtt = ("WEBVTT\n\n"
           "00:15.410 --> 00:20.890\n日本語\n\n"
           "00:15.410 --> 00:20.890\n中文\n\n"
           "00:15.410 --> 00:20.890\n日本語")
    out = subtitles._dedup(vtt)
    check("日本語" in out and "中文" in out, "兩行都在")
    check(out.count("日本語") == 1, "重複的那一份被砍掉")
    check(len([b for b in out.split("\n\n") if "-->" in b]) == 2,
          "同一時間點剩兩條 —— 日文 + 中文，正是雙語該有的樣子")


def test_dedup_keeps_repeated_line_at_another_time(_tmp):
    print("\n同一句話在不同時間再說一次，兩次都要留")
    vtt = ("WEBVTT\n\n"
           "00:01.000 --> 00:02.000\n是\n\n"
           "00:09.000 --> 00:10.000\n是")
    out = subtitles._dedup(vtt)
    check(out.count("是") == 2, "時間不同就是不同的 cue")


def test_dedup_leaves_headers_alone(_tmp):
    print("\nNOTE / STYLE 區塊不參與去重")
    vtt = "WEBVTT\n\nNOTE x\n\nNOTE x\n\n00:01.000 --> 00:02.000\na"
    out = subtitles._dedup(vtt)
    check(out.count("NOTE x") == 2, "沒有 --> 的區塊原封不動")


def test_dedup_survives_crlf(_tmp):
    print("\nCRLF 換行")
    vtt = "WEBVTT\r\n\r\n00:01.000 --> 00:02.000\r\na\r\n\r\n00:01.000 --> 00:02.000\r\na"
    out = subtitles._dedup(vtt)
    check(len([b for b in out.split("\n\n") if "-->" in b]) == 1,
          "\\r\\n 也要認得出是同一個 cue")


# ── _merge_simultaneous ──────────────────────────────────────────────────────

def test_merge_bilingual(_tmp):
    print("\n時間完全相同的兩條併成一條兩行")
    vtt = ("WEBVTT\n\n"
           "00:70.090 --> 00:79.040\n答えはそう\n\n"
           "00:70.090 --> 00:79.040\n答案 就在這裡")
    out = subtitles._merge_simultaneous(vtt)
    cues = [b for b in out.split("\n\n") if "-->" in b]
    check(len(cues) == 1, f"變成一條（實得 {len(cues)}）")
    body = cues[0].split("\n")[1:]
    check(len(body) == 2, "一條裡面兩行")
    # The browser draws the FIRST cue at the bottom and stacks later ones above,
    # so the translation was already the top line. Joining in document order
    # would silently swap the two languages.
    check(body == ["答案 就在這裡", "答えはそう"],
          f"順序要反過來才跟合併前的畫面一樣（實得 {body}）")


def test_merge_near_identical(_tmp):
    print("\n結束時間差 40 毫秒的也要併 —— 這是整個片尾的形狀")
    # Verbatim from episode 25's ending theme. An exact-match rule merged the
    # first few lines of an episode and nothing here, which is how this shipped
    # broken once.
    vtt = ("WEBVTT\n\n"
           "00:23:20.490 --> 00:23:22.670\nだからカッコつけないで\n\n"
           "00:23:20.490 --> 00:23:22.630\n所以不要逞強了")
    out = subtitles._merge_simultaneous(vtt)
    cues = [b for b in out.split("\n\n") if "-->" in b]
    check(len(cues) == 1, f"併成一條（實得 {len(cues)}）")
    check(cues[0].split("\n")[1:] == ["所以不要逞強了", "だからカッコつけないで"],
          "中文在上，跟合併前畫面一致")
    # the union, so neither line is cut short
    check(cues[0].startswith("00:23:20.490 --> 00:23:22.670"),
          f"時間取聯集（實得 {cues[0].split(chr(10))[0]}）")


def test_merge_threshold(_tmp):
    print("\n門檻兩側")
    # 90% of the longer cue → one line in two languages
    vtt = ("WEBVTT\n\n"
           "00:00:10.000 --> 00:00:20.000\nA\n\n"
           "00:00:10.000 --> 00:00:19.000\nB")
    check(len([b for b in subtitles._merge_simultaneous(vtt).split("\n\n")
               if "-->" in b]) == 1, "重疊 90% → 併")
    # 45% → two different people talking over each other
    vtt = ("WEBVTT\n\n"
           "00:00:10.000 --> 00:00:20.000\nA\n\n"
           "00:00:10.000 --> 00:00:14.500\nB")
    check(len([b for b in subtitles._merge_simultaneous(vtt).split("\n\n")
               if "-->" in b]) == 2, "重疊 45% → 不併")
    check(0.80 < subtitles.MERGE_RATIO <= 0.85,
          f"門檻要落在量到的空隙 0.80–0.85 裡（現在是 {subtitles.MERGE_RATIO}）")


def test_merge_untouched_cue_keeps_its_stamp(_tmp):
    print("\n沒被併到的 cue，時間戳一個位元組都不能動")
    vtt = "WEBVTT\n\n00:01.000 --> 00:02.000\n一個人"
    out = subtitles._merge_simultaneous(vtt)
    check("00:01.000 --> 00:02.000" in out,
          "原樣保留，不重新格式化 13,000 個不需要動的 cue")


def test_merge_leaves_different_timing_alone(_tmp):
    print("\n只是重疊、時間不同的不能併")
    # Merging these would change when a line appears on screen.
    vtt = ("WEBVTT\n\n"
           "00:15.210 --> 00:20.370\n過ぎた季節を\n\n"
           "00:15.210 --> 00:17.530\n已逝的季節\n\n"
           "00:17.920 --> 00:20.370\n無暇去歎息")
    out = subtitles._merge_simultaneous(vtt)
    cues = [b for b in out.split("\n\n") if "-->" in b]
    check(len(cues) == 3, f"三條還是三條（實得 {len(cues)}）")


def test_merge_respects_cue_settings(_tmp):
    print("\n位置設定不同的不能併")
    # Same times but placed differently — they are two different slots.
    vtt = ("WEBVTT\n\n"
           "00:01.000 --> 00:02.000 line:0\n上面的招牌\n\n"
           "00:01.000 --> 00:02.000\n下面的對白")
    out = subtitles._merge_simultaneous(vtt)
    cues = [b for b in out.split("\n\n") if "-->" in b]
    check(len(cues) == 2, f"兩條都留著（實得 {len(cues)}）")


def test_merge_keeps_header(_tmp):
    print("\n檔頭與 NOTE 不受影響")
    vtt = "WEBVTT\n\nNOTE 這是註解\n\n00:01.000 --> 00:02.000\na"
    out = subtitles._merge_simultaneous(vtt)
    check(out.startswith("WEBVTT"), "檔頭在最前面")
    check("NOTE 這是註解" in out, "NOTE 留著")


def test_merge_three_way(_tmp):
    print("\n三條同時間也併得起來")
    vtt = ("WEBVTT\n\n"
           "00:01.000 --> 00:05.000\n一\n\n"
           "00:01.000 --> 00:05.000\n二\n\n"
           "00:01.000 --> 00:05.000\n三")
    out = subtitles._merge_simultaneous(vtt)
    cues = [b for b in out.split("\n\n") if "-->" in b]
    check(len(cues) == 1, "一條")
    check(cues[0].split("\n")[1:] == ["三", "二", "一"], "由上到下是反的文件順序")


# ── to_vtt (real ffmpeg) ─────────────────────────────────────────────────────

def _have_ffmpeg():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=10,
                       creationflags=media.SUBPROCESS_FLAGS)
        return True
    except Exception:
        return False


def test_converts_utf8(tmp: Path):
    print("\nUTF-8 的 .ass")
    if not _have_ffmpeg():
        check(True, "（沒有 ffmpeg，略過）")
        return
    p = tmp / "a.ass"
    p.write_text(_ass([
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,こんにちは",
        "Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,你好",
    ]), encoding="utf-8")
    out = subtitles.to_vtt(p)
    check(out is not None and out.startswith("WEBVTT"), "轉得出 WebVTT")
    check("こんにちは" in out and "你好" in out, "日文與中文都沒有亂碼")


def test_converts_utf16(tmp: Path):
    print("\nUTF-16 的 .ass —— 這個庫裡 31 個檔有 17 個是")
    if not _have_ffmpeg():
        check(True, "（沒有 ffmpeg，略過）")
        return
    p = tmp / "b.ass"
    # utf-16 with a BOM, exactly what the real files look like
    p.write_bytes(_ass([
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,過ぎた季節を",
    ]).encode("utf-16"))
    check(p.read_bytes()[:2] == b"\xff\xfe", "（前置條件：檔案真的是 UTF-16 LE）")
    out = subtitles.to_vtt(p)
    check(out is not None, "轉得出來")
    check("過ぎた季節を" in out,
          "沒有變成亂碼 —— 用 UTF-8 讀會靜靜壞掉，不會丟例外")


def test_strips_override_tags(tmp: Path):
    print("\nASS 的覆寫標籤不會出現在字幕上")
    if not _have_ffmpeg():
        check(True, "（沒有 ffmpeg，略過）")
        return
    p = tmp / "c.ass"
    p.write_text(_ass([
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,"
        "{\\pos(190,270)\\fs40\\1c&HFFFFFF&}招牌上的字",
    ]), encoding="utf-8")
    out = subtitles.to_vtt(p)
    check(out is not None and "招牌上的字" in out, "文字留著")
    check("\\pos" not in out and "1c&H" not in out, "標籤沒有跑到畫面上")


def test_karaoke_end_to_end(tmp: Path):
    print("\n真的走一遍：四層卡拉OK進去，出來剩一條")
    if not _have_ffmpeg():
        check(True, "（沒有 ffmpeg，略過）")
        return
    p = tmp / "d.ass"
    # both shapes at once: four identical layers, and the syllable timing that
    # gives each copy a different start
    body = "{\\K100}君{\\K100}は{\\K100}聴こえる"
    lines = [f"Dialogue: 0,0:00:15.41,0:00:20.89,Default,,0,0,0,,{body}"] * 4
    lines += [f"Dialogue: 0,0:00:1{n}.00,0:00:20.89,Default,,0,0,0,,{body}"
              for n in (6, 7, 8)]
    p.write_text(_ass(lines), encoding="utf-8")
    out = subtitles.to_vtt(p)
    cues = [b for b in out.split("\n\n") if "-->" in b]
    check(len(cues) == 1, f"七份（四份同時間 + 三份逐字）變成 1 條（實得 {len(cues)}）")


def test_unreadable(tmp: Path):
    print("\n壞掉的檔案回 None，不丟例外")
    if not _have_ffmpeg():
        check(True, "（沒有 ffmpeg，略過）")
        return
    p = tmp / "e.ass"
    p.write_bytes(b"this is not a subtitle at all")
    check(subtitles.to_vtt(p) is None, "端點會據此回 404")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for fn in (test_finds_sidecar, test_extension_priority, test_no_sidecar,
               test_never_leaves_the_directory,
               test_dedup_collapses_karaoke,
               test_dedup_collapses_syllable_karaoke,
               test_dedup_keeps_same_text_at_another_end,
               test_dedup_keeps_real_bilingual_overlap,
               test_dedup_keeps_repeated_line_at_another_time,
               test_dedup_leaves_headers_alone, test_dedup_survives_crlf,
               test_merge_bilingual, test_merge_near_identical,
               test_merge_threshold,
               test_merge_untouched_cue_keeps_its_stamp,
               test_merge_leaves_different_timing_alone,
               test_merge_respects_cue_settings, test_merge_keeps_header,
               test_merge_three_way,
               test_converts_utf8, test_converts_utf16,
               test_strips_override_tags, test_karaoke_end_to_end,
               test_unreadable):
        d = Path(tempfile.mkdtemp())
        try:
            fn(d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
