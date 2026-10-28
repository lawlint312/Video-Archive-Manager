"""NFO files (Kodi / Jellyfin / Emby): read into VAM, written out of it.

The sample has the shape of the files already in the library — Jellyfin wrote
them in 2020: UTF-8 with a BOM, CRLF, `standalone="yes"`, the plot, artwork
paths, stream details, an actor with a Jellyfin thumbnail under an earlier
stage name.

  * reading fills only what VAM does not know — never over a typed title
  * a webcam name the number detection misreads is not keyed by that number
  * writing merges: only VAM's fields change, the rest of the file (and its
    BOM, declaration, line endings) stays; an alias is not added twice
  * an unchanged file is not rewritten; a broken one is not touched
  * every write is backed up, and undo restores exactly — except a file
    changed since, which is newer work and is left alone
  * archiving one video takes its .nfo, artwork and subtitles along

Run:  python tests/test_nfo.py      (from backend/)
"""
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import nfo, media           # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


JELLYFIN = (
    '\ufeff<?xml version="1.0" encoding="utf-8" standalone="yes"?>\r\n'
    '<movie>\r\n'
    '  <plot>可愛い仕草とは裏腹に…</plot>\r\n'
    '  <lockdata>false</lockdata>\r\n'
    '  <title>Re：エロ淫乱メイドコスプレイヤー かりん</title>\r\n'
    '  <premiered>2020-11-27</premiered>\r\n'
    '  <releasedate>2020-11-27</releasedate>\r\n'
    '  <studio>TMA</studio>\r\n'
    '  <art>\r\n'
    '    <poster>K:\\JAV\\日高優奈\\TMA\\28ID-042-poster.png</poster>\r\n'
    '  </art>\r\n'
    '  <actor>\r\n'
    '    <name>日高優奈</name>\r\n'
    '    <type>Actor</type>\r\n'
    '    <thumb>C:\\ProgramData\\Jellyfin\\Server\\metadata\\People\\日\\日高優奈\\folder.jpg</thumb>\r\n'
    '  </actor>\r\n'
    '  <actor>\r\n'
    '    <name>星川優子</name>\r\n'
    '  </actor>\r\n'
    '  <actor>\r\n'
    '    <name>知らない人</name>\r\n'
    '  </actor>\r\n'
    '  <fileinfo>\r\n'
    '    <streamdetails>\r\n'
    '      <video><codec>hevc</codec><width>1920</width></video>\r\n'
    '    </streamdetails>\r\n'
    '  </fileinfo>\r\n'
    '  <num>28ID-042</num>\r\n'
    '</movie>\r\n').encode("utf-8")


def test_format():
    print("讀與合併（只碰 VAM 的欄位）")
    n = nfo.read(JELLYFIN)
    check(n["title"].startswith("Re：エロ") and n["premiered"] == "2020-11-27"
          and n["actors"] == ["日高優奈", "星川優子", "知らない人"] and n["num"] == "28ID-042",
          "讀得到片名、發行日、演員、番號")
    check(nfo.title_lang(n["title"]) == "ja" and nfo.title_lang("某個片名") == "zh"
          and nfo.title_lang("Rich Sperm") == "en", "片名語言：假名→日、漢字→中、其他→英")

    alias = lambda a, b: {a, b} == {"日高優奈", "森田花梨"}
    data, changed = nfo.render(JELLYFIN, {
        "title": "Re：エロ淫乱メイドコスプレイヤー かりん",
        "originaltitle": "Re：エロ淫乱メイドコスプレイヤー かりん",
        "released": "2020-11-27", "num": "WRONG-1",
        "actors": ["森田花梨", "星川優子", "新しい人"]}, alias)
    check(changed == ["originaltitle", "year", "actor"],
          f"只動到真的不同的欄位：{changed}")
    out = data.decode("utf-8")
    check(data.startswith(b"\xef\xbb\xbf<?xml") and 'standalone="yes"' in out,
          "BOM 與 XML 宣告照舊")
    check("\r\n" in out and "\n" not in out.replace("\r\n", ""), "換行仍是 CRLF")
    for keep in ("<plot>可愛い仕草とは裏腹に…</plot>", "<lockdata>false</lockdata>",
                 "28ID-042-poster.png", "Jellyfin\\Server\\metadata", "<codec>hevc</codec>",
                 "<studio>TMA</studio>"):
        check(keep in out, f"保留：{keep[:30]}")
    check(out.count("<name>") == 4 and "新しい人" in out and "森田花梨" not in out,
          "森田花梨＝日高優奈（別名）不重複加；新的人加上")
    check("<num>28ID-042</num>" in out and "WRONG-1" not in out, "既有的番號不改")
    again, ch2 = nfo.render(data, {
        "title": "Re：エロ淫乱メイドコスプレイヤー かりん",
        "originaltitle": "Re：エロ淫乱メイドコスプレイヤー かりん",
        "released": "2020-11-27", "actors": ["森田花梨", "新しい人"]}, alias)
    check(again is None and ch2 == [], "沒有變化就不重寫")

    data, changed = nfo.render(None, {"title": "某個片名", "released": "2019",
                                      "actors": ["A"]})
    out = data.decode("utf-8")
    check(out.startswith("<?xml") and "<year>2019</year>" in out
          and "<premiered>" not in out and out.index("<title>") < out.index("<actor>"),
          "新檔：只有年份就只寫 year；欄位在演員前面")
    for bad, why in ((b"<movie><title>x</movie>", "壞掉的 XML"),
                     (b"<tvshow><title>x</title></tvshow>", "不是電影")):
        try:
            nfo.render(bad, {"title": "y"})
            check(False, f"{why}：應該拒絕")
        except ValueError:
            check(True, f"{why}：拒絕寫入")


def test_companions(tmp: Path):
    print("\n單檔歸檔帶走 NFO、圖、字幕")
    a, b = tmp / "inbox", tmp / "lib"
    a.mkdir(); b.mkdir()
    for n in ("V-1.mp4", "V-1.nfo", "V-1-poster.jpg", "V-1-fanart.png", "V-1.ass",
              "V-1.txt", "V-10.mp4", "V-10.nfo", "V-1-poster.jpg.bak"):
        (a / n).write_bytes(b"x")
    got = sorted(p.name for p in media.companions(a / "V-1.mp4"))
    check(got == ["V-1-fanart.png", "V-1-poster.jpg", "V-1.ass", "V-1.nfo"],
          f"只認得自己的：{got}")
    (b / "V-1_99-fanart.png").write_bytes(b"already")
    moved = media.move_companions(a / "V-1.mp4", b / "V-1_99.mp4")
    check(sorted(Path(m).name for m in moved) == ["V-1_99-poster.jpg", "V-1_99.ass",
                                                   "V-1_99.nfo"],
          "跟著新的檔名改名")
    check((b / "V-1_99-fanart.png").read_bytes() == b"already"
          and (a / "V-1-fanart.png").exists(), "目的地已有的不覆蓋")
    check((a / "V-10.nfo").exists(), "別的影片的不動")


def test_end_to_end(tmp: Path):
    print("\n端對端：匯入 → 寫出 → 復原")
    from fastapi.testclient import TestClient
    from app import auth, db, main, settings as app_settings
    from app.core import javdb, searchindex, filetitle, costar

    saved = (db.DB_PATH, javdb.DB_PATH, main.NFO_BACKUPS, searchindex._FILE,
             searchindex._cache, app_settings.archive_roots_for,
             app_settings.inbox_paths_for, app_settings.path_gate)
    lib = tmp / "JAV"
    lead = lib / "森田花梨"
    other = lib / "星川優子"
    cam = lib / "guoguo-77"
    for d in (lead, other, cam):
        d.mkdir(parents=True)
    (lead / "person_info.json").write_text(json.dumps({"aliases": ["日高優奈"]},
                                                       ensure_ascii=False), encoding="utf-8")
    for n in ("STARS-786.mp4", "SSIS-001.mp4", "clipA.mp4"):
        (lead / n).write_bytes(b"video")
    # a number the detection knows (28ID-042 itself is not one it reads)
    (lead / "STARS-786.nfo").write_bytes(JELLYFIN.replace(b"28ID-042", b"STARS-786"))
    (lead / "clipA.nfo").write_bytes(
        '<?xml version="1.0" encoding="utf-8"?>\n<movie>\n  <title>某個片名</title>\n'
        '  <premiered>2021-05-01</premiered>\n</movie>\n'.encode("utf-8"))
    (cam / "guoguo-77-20260727.mp4").write_bytes(b"video")
    (cam / "guoguo-77-20260727.nfo").write_bytes(
        b"<movie><title>Live show</title></movie>")
    (lead / "SSIS-001.nfo.bak").write_bytes(b"not ours")

    try:
        db.DB_PATH = tmp / "vam.db"
        javdb.DB_PATH = tmp / "javdb.sqlite3"
        main.NFO_BACKUPS = tmp / "nfo_backups"
        searchindex._FILE = tmp / "search_index.json"
        searchindex._cache = None
        db.init_db()
        app_settings.archive_roots_for = lambda user=None: [lib]
        app_settings.inbox_paths_for = lambda user=None: []
        app_settings.path_gate = lambda user=None: (lambda p: True)
        admin = {"id": 1, "role": "admin"}
        main.app.dependency_overrides[auth.current_user] = lambda: dict(admin)
        javdb.set_title("SSIS-001", manual="手打的片名")
        c = TestClient(main.app)

        def run(url, body):
            jid = c.post(url, json=body).json()["job_id"]
            for _ in range(200):
                j = c.get(f"/api/jobs/{jid}").json()
                if j["status"] != "running":
                    return j
                time.sleep(0.05)
            return j

        names = ["森田花梨", "guoguo-77"]
        j = run("/api/nfo/scan", {"names": names, "mode": "import"})
        check(j["status"] == "done", f"匯入預覽跑完（{j.get('error')}）")
        r = j["result"]
        rows = {Path(x["path"]).name: x for x in r["rows"]}
        f42 = {f["field"]: f for f in rows["STARS-786.mp4"]["fills"]}
        check(f42["title"]["lang"] == "ja" and f42["released"]["value"] == "2020-11-27",
              "STARS-786：日文片名、發行日（番號寫在檔裡，就以番號為 key）")
        check([f["value"] for f in rows["STARS-786.mp4"]["fills"] if f["field"] == "costar"]
              == ["星川優子"], "共演：星川優子；日高優奈是主角的別名，不算")
        check({f["field"] for f in rows["clipA.mp4"]["fills"]} == {"title", "released"},
              "沒番號的片：片名、發行日走路徑")
        check(rows["guoguo-77-20260727.mp4"]["vid"] == "",
              "webcam 的名字誤判成番號，不以番號為 key")
        check([u["name"] for u in r["unmatched"]] == ["知らない人"], "對不到的演員列出來")
        check(r["found"] == 3, "找到 3 個 NFO（.nfo.bak 不算）")

        j = run("/api/nfo/apply", {"names": names, "mode": "import"})
        a = j["result"]
        check((a["titles"], a["released"], a["costars"]) == (3, 2, 1), f"寫入：{a}")
        check(javdb.title_row("STARS-786")["ja"].startswith("Re：")
              and filetitle.get(lead / "clipA.mp4") == "某個片名", "片名進了 VAM")
        check(javdb.title_row("SSIS-001")["manual"] == "手打的片名", "手打的片名沒被動")
        check(len(costar.for_video(lead / "STARS-786.mp4")) == 1, "共演標上了")
        again = run("/api/nfo/scan", {"names": names, "mode": "import"})["result"]
        check(again["rows"] == [], "再預覽一次：沒有要補的了")

        before = (lead / "STARS-786.nfo").read_bytes()
        j = run("/api/nfo/scan", {"names": ["森田花梨"], "mode": "export"})
        r = j["result"]
        st = {Path(i["path"]).name: i for i in r["items"]}
        check(st["SSIS-001.mp4"]["status"] == "new" and st["STARS-786.mp4"]["status"] == "changed",
              f"寫出預覽：新建 SSIS-001、更新 STARS-786（{r['counts']}）")
        check("data" not in st["STARS-786.mp4"], "預覽不把檔案內容送給瀏覽器")
        check((lead / "STARS-786.nfo").read_bytes() == before, "預覽不寫檔")

        j = run("/api/nfo/apply", {"names": ["森田花梨"], "mode": "export"})
        w = j["result"]
        check(w["created"] == 1 and w["errors"] == [], f"寫出：{w}")
        new = (lead / "SSIS-001.nfo").read_text(encoding="utf-8")
        check("<title>手打的片名</title>" in new and "<name>森田花梨</name>" in new,
              "新檔用 VAM 的片名與演員")
        out = (lead / "STARS-786.nfo").read_bytes()
        check(out.startswith(b"\xef\xbb\xbf") and b"<plot>" in out and b"fileinfo" in out,
              "更新的檔留住 BOM、簡介、串流資訊")
        check(not list(lead.glob("*.vamtmp")), "沒有留下暫存檔")
        again = run("/api/nfo/scan", {"names": ["森田花梨"], "mode": "export"})["result"]
        check(again["counts"]["new"] == 0 and again["counts"]["changed"] == 0,
              "再預覽：全部沒變")

        check(c.get("/api/nfo/last").json()["count"] == w["created"] + w["updated"],
              "最後一批可以復原")
        u = c.post("/api/nfo/undo").json()
        check((lead / "STARS-786.nfo").read_bytes() == before, "復原：原檔一個位元組都不差")
        check(not (lead / "SSIS-001.nfo").exists() and u["removed"] == 1, "復原：新建的拿掉")
        check(c.get("/api/nfo/last").json()["batch"] is None, "復原過的不能再復原")

        run("/api/nfo/apply", {"names": ["森田花梨"], "mode": "export"})
        (lead / "SSIS-001.nfo").write_text("<movie><title>Jellyfin 改過</title></movie>",
                                           encoding="utf-8")
        u = c.post("/api/nfo/undo").json()
        check((lead / "SSIS-001.nfo").read_text(encoding="utf-8").startswith("<movie><title>Jellyfin")
              and len(u["skipped"]) == 1, "之後被改過的檔不復原，列出來")
        check((lead / "SSIS-001.nfo.bak").read_bytes() == b"not ours", "別的檔沒被碰")
    finally:
        main.app.dependency_overrides.clear()
        (db.DB_PATH, javdb.DB_PATH, main.NFO_BACKUPS, searchindex._FILE,
         searchindex._cache, app_settings.archive_roots_for,
         app_settings.inbox_paths_for, app_settings.path_gate) = saved


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    test_format()
    for fn in (test_companions, test_end_to_end):
        d = Path(tempfile.mkdtemp())
        try:
            fn(d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
