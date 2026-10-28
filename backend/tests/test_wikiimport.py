"""Parsing a saved seesaawiki performer page.

This is the only route to Japanese film titles that works. `movie_title.ja`
has been empty since the column existed, because javlibrary sits behind a
Cloudflare challenge and DMM is a JS-only SPA that also geo-blocks -- both
recorded as dead ends. This wiki publishes the same data as plain HTML.

The fixture below is synthetic but every shape in it was taken from a real
saved page (美咲エリ, 83 releases), including the three that the first version
of the parser silently dropped. "Silently" is the word that matters: a missed
release is not an error, it is just a title that never appears, and nobody
would ever notice which ones were missing.

Run:  python tests/test_wikiimport.py      (from backend/)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The Windows console here is cp950 (Traditional Chinese), which cannot encode
# Japanese kana -- and this test prints parsed titles. Without this the test
# dies on its own output rather than on a real failure, which is a confusing
# way to be told nothing is wrong.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from app.core import wikiimport as W                   # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


# Shapes taken from the real page:
#  - the stage-name heading, with DMM links and kana readings
#  - a plain release with co-performers
#  - a release whose ※ note follows it
#  - a section heading sharing a chunk with the release under it
#  - a release carrying two catalogue numbers
#  - a sidebar entry that mentions an id but is not a release
#  - a NEC extended character (Ⅲ), which plain euc_jp cannot decode
PAGE = """<html><head>
<meta http-equiv="Content-Type" content="text/html; charset=EUC-JP">
<title>美咲エリ - 素人系総合 wiki</title>
<link rel="canonical" href="https://seesaawiki.jp/w/sougouwiki/d/%b4%f5%ba%e9%a5%a8%a5%de">
</head><body>
<div id="content_block_1" class="wiki-section-1"><div class="title-1"><h3 id="content_1">
<a href="https://www.dmm.co.jp/x" class="outlink">加納なつ希（かのうなつき）／NATSUKI（なつき）</a>／<a href="https://www.dmm.co.jp/y">美咲エリ（みさきえり）</a>／<a href="https://www.dmm.co.jp/z">北村ルイ</a>
<a title="部分編集" href="https://seesaawiki.jp/e/edit" class="title-1-part-edit part-edit"><img src="pen.gif"></a>
</h3></div>
<div id="content_block_1-body" class="wiki-section-body-1">
2018/04/13 NSPS-694<br>
<a href="https://www.dmm.co.jp/detail/cid=nsps694/" class="outlink">他人に吸い尽くされた母乳 夫以外の男に狂う！（ながえスタイル）</a>　<a href="https://seesaawiki.jp/label">(レーベル一覧)</a><br>
<a href="https://pics.dmm.co.jp/x.jpg"><img src="x.jpg" border="0"></a><br>
出演者：<a href="/a">美咲エリ</a>／<a href="/b">本田優香</a>／<a href="/c">椎木綾</a><br>
<br>
2015/07/04 OBT-024<br>
<a href="https://www.dmm.co.jp/detail/cid=obt024/">私、本名でセックスしてました。 美咲エリ（無名時代）</a>　<a href="/l">(レーベル一覧)</a><br>
※A子さん「HIKARI(19)」のDVD版<br>
<br>
2012/12/08 MVF-375（MADV-277）<br>
<a href="/d">トリプルレズビアン 6（クリスタル映像／MANIAC）</a><br>
出演者：<a href="/a">美咲エリ</a>／<a href="/e">夢野ねむ</a><br>
<br>
<div class="title-2">総集編作品</div>
2015/03/19 BOMN-122<br>
<a href="/f">母乳と潮が混ざり合う6本番 美咲エリ Best（ABC/妄想族／BoinBB）</a>　<a href="/l">(レーベル一覧)</a><br>
<br>
<div class="title-2">配信作品</div>
2015/04/17 AKO-205<br>
<a href="/g">A子さん HIKARI(19) T152 B90(G-65) W56 H86</a>　<a href="/l">(レーベル一覧)</a><br>
<br>
2010/08/06<br>
<a href="/h">VANILLA 25 小麦ちゃんのお尻 NATSUKI（プレステージ）</a><br>
<br>
<div class="title-2">写真集等</div>
2012/03/01<br>
<a href="/i">写真集 えり（三和出版）</a><br>
<br>
</div></div>
<div class="sidebar"><ul>
<li>闘神都市Ⅲ(投稿中)</li>
<li>Hunter 6(HUNTA-001〜200)</li>
</ul></div>
</body></html>"""


def test_request_contract():
    """The fields the server reads and the fields the client sends.

    Two of these went missing in one change and neither showed up as an error:
    the import reported success and the data simply was not there. A drift
    between the two lists is exactly that bug, so the lists are compared.
    """
    print("\n前後端對這個請求的欄位認知要一致")
    import re
    from app.main import WikiApplyBody                  # noqa: E402

    server = set(WikiApplyBody.model_fields)
    js = (Path(__file__).resolve().parent.parent.parent /
          "frontend" / "src" / "wikiapply.js")
    if not js.exists():
        print("  --   找不到前端檔案，跳過")
        return
    # Two source lists, not one: the wiki and IAFD branches send different
    # shapes on purpose, so the union is what the server must read and neither
    # branch alone would satisfy. Reading the literal arrays rather than
    # `FIELDS` (which is now computed from them) keeps this a text check.
    text = js.read_text(encoding="utf-8")
    client = set()
    for name in ("WIKI_FIELDS", "IAFD_FIELDS", "AVBASE_FIELDS"):
        m = re.search(r"export const %s = (\[.*?\])" % name, text, re.S)
        check(m is not None, f"前端有列出 {name}")
        if m:
            client |= set(re.findall(r"'([a-z_]+)'", m.group(1)))
    check(server == client,
          f"兩邊一致；伺服器多出 {sorted(server - client)}、"
          f"前端多出 {sorted(client - server)}")


def test_migration():
    """A column added to a table that already exists.

    This shipped broken and every test passed: they each build a fresh
    database, where `CREATE TABLE IF NOT EXISTS` naturally includes the new
    column. The user's database already had the table from an earlier import,
    kept its old shape, and every write returned 500.

    So the fixture here is the *old* schema, deliberately. A test that starts
    from the current schema cannot catch a missing migration -- it is testing
    the one case that was never in doubt.
    """
    print("\n舊資料庫加欄位（tests 都用新的，所以這條是特地反過來建的）")
    import sqlite3
    import tempfile as tf
    from app.core import javdb                          # noqa: E402

    with tf.TemporaryDirectory() as td:
        dbp = Path(td) / "old.sqlite3"
        # wiki_profile as it was before `works` existed
        conn = sqlite3.connect(str(dbp))
        conn.execute("""CREATE TABLE wiki_profile (
            folder TEXT PRIMARY KEY, page_name TEXT, url TEXT,
            total INTEGER NOT NULL, no_id INTEGER NOT NULL,
            sections TEXT, ids TEXT, at REAL NOT NULL)""")
        conn.commit()
        conn.close()

        real, javdb.DB_PATH = javdb.DB_PATH, dbp
        real_flag, javdb._migrated = javdb._migrated, False
        try:
            def cols():
                # closed explicitly: an open handle stops Windows deleting the
                # temp directory, and the test then fails on cleanup rather
                # than on anything it was checking
                c2 = sqlite3.connect(str(dbp))
                try:
                    return {r[1] for r in
                            c2.execute("PRAGMA table_info(wiki_profile)")}
                finally:
                    c2.close()
            check("works" not in cols(), "起點確實是舊的表（沒有 works 欄）")
            javdb.set_wiki_profile("k", "n", "u",
                                   {"total": 3, "no_id": 0, "sections": []},
                                   ["A-1"], works=[{"i": "A-1", "t": "t",
                                                    "d": "2020/01/01", "s": "x"}])
            check("works" in cols(), "連線時自動補上欄位")
            # If someone removes the migration entry, the line above fails and
            # so does the write below — which is the whole point of this test.
            check(len(javdb.wiki_works("k")) == 1, "而且真的寫得進去、讀得出來")
        finally:
            javdb.DB_PATH = real
            javdb._migrated = real_flag


def test_over_http(raw: bytes):
    """Upload, preview, confirm, write — the shape the dialog actually uses.

    The assertions worth having are the two that protect data rather than the
    ones that prove the parser works (covered above): that a preview writes
    nothing, and that applying aliases only ever adds.
    """
    print("\nHTTP：上傳 → 預覽 → 確認 → 寫入")
    import json
    import tempfile as tf
    from fastapi.testclient import TestClient      # noqa: E402
    from app import auth, db, main                 # noqa: E402
    from app.core import javdb, media, videoflags  # noqa: E402
    from app.config import DATA_DIR                # noqa: E402

    with tf.TemporaryDirectory() as td:
        tmp = Path(td)
        # a throwaway catalogue database — never the real one
        real_db, javdb.DB_PATH = javdb.DB_PATH, tmp / "javdb.sqlite3"
        # ...and a throwaway `vam.db` too. This used to run against the real
        # one: harmless while it only read, until `/api/videos` grew a query
        # for `file_title.released` and the live database had not been migrated
        # yet, so the test failed for a reason that had nothing to do with it.
        # CLAUDE.md's rule is every writing path gets redirected first; reading
        # the user's database is the same mistake one step earlier.
        real_main_db, db.DB_PATH = db.DB_PATH, tmp / "vam.db"
        db.init_db()
        folder = tmp / "美咲エリ"
        folder.mkdir()
        (folder / "NSPS-694.mp4").write_bytes(b"x")
        (folder / "OBT-024.mp4").write_bytes(b"x")

        real_allowed = main.is_allowed
        main.is_allowed = lambda p, u: True
        editor = {"id": 1, "role": "editor", "username": "e", "can_edit": True}
        main.app.dependency_overrides[auth.current_user] = lambda: editor
        main.app.dependency_overrides[auth.require_editor] = lambda: editor
        # Point the library at the temp folder for the duration.
        real_ids = main._ids_by_character
        real_chars = media.list_characters
        main._ids_by_character = lambda u: {"美咲エリ": {"NSPS-694", "OBT-024"}}
        media.list_characters = lambda roots: [{"name": "美咲エリ",
                                                "path": str(folder),
                                                "paths": [str(folder)]}]
        try:
            c = TestClient(main.app)
            r = c.post("/api/wiki/preview",
                       files={"files": ("page.html", raw, "text/html")})
            check(r.status_code == 200, f"預覽回得來（{r.status_code}）")
            page = r.json()["pages"][0]

            check(page["name"] == "美咲エリ", "認出這頁是誰的")
            m = page["matches"][0]
            check(m["score"] == 2,
                  f"番號重疊 2 個，比對到那個資料夾（{m['shared']}）")
            check(m["by_name"] is True, "名字也對得上（但那只是加分，不是證據）")

            check(set(page["titles"]) == {"NSPS-694", "OBT-024"},
                  f"只提供庫裡真的有的片名（{sorted(page['titles'])}）")
            check(page["titles_total"] > len(page["titles"]),
                  f"頁面上還有其他 {page['titles_total']} 部，不會一起塞進來")

            check(javdb.ja_title_count() == 0,
                  "預覽階段一個字都還沒寫進去")
            before = json.loads((folder / "person_info.json").read_text("utf-8")) \
                if (folder / "person_info.json").exists() else None
            check(before is None, "而且還沒碰 person_info.json")

            print("\n  確認之後才寫")
            r = c.post("/api/wiki/apply", json={
                "titles": page["titles"], "aliases": page["aliases"],
                "folder": str(folder), "cast": page["cast"],
                "source": page["url"], "summary": page["summary"],
                "all_ids": page["all_ids"], "page_name": page["name"],
                "works": page["works"], "released": page["released"]})
            res = r.json()
            check(res["titles"] == 2, f"寫進兩個日文片名（{res['titles']}）")
            check(javdb.ja_title_count() == 2, "資料庫裡真的有了")
            row = javdb.title_row("NSPS-694")
            check(row["ja"].startswith("他人に"), f"內容對（{row['ja']}）")
            check(javdb.best_title(row) == row["ja"],
                  "而且日文排在英文前面，直接變成顯示的片名")

            info = media.load_person_info(folder)
            check("加納なつ希" in info["aliases"], "別名寫進去了")
            check("美咲エリ" not in info["aliases"],
                  "資料夾自己的名字不算別名")

            print("\n  再匯入一次不會重複，也不會蓋掉手改的")
            info["aliases"].append("手動加的")
            media.save_person_info(folder, info)
            r = c.post("/api/wiki/apply", json={
                "titles": {}, "aliases": page["aliases"],
                "folder": str(folder)})
            check(r.json()["aliases"] == 0, "沒有新增任何重複的別名")
            after = media.load_person_info(folder)["aliases"]
            check("手動加的" in after, "手改的還在 —— 只增不覆蓋")
            check(len(after) == len(set(after)), "沒有重複項")

            print("\n  影片列表的對照數字必須加得起來")
            # The panel shows four numbers under the wiki total, and they have
            # to reconcile to the file count the user can see in the grid --
            # otherwise it commits the exact sin it exists to fix. The fourth,
            # 重複的檔案, is the one nobody thinks of: two copies of one film
            # are one work in the wiki column but two rows in the list.
            (folder / "NSPS-694 (2).mp4").write_bytes(b"x")   # a duplicate
            (folder / "no-number-here.mp4").write_bytes(b"x")  # unrecognisable
            (folder / "ZZZ-999.mp4").write_bytes(b"x")        # not on the page
            d = c.get(f"/api/videos?root={folder}").json()
            w = d.get("wiki")
            check(w is not None, "匯入過的角色才有這一塊")
            if w:
                total = w["have"] + w["extra"] + w["dupes"] + w["no_number"]
                check(total == w["files"],
                      f"{w['have']}+{w['extra']}+{w['dupes']}+{w['no_number']}"
                      f" = {total}，檔案數 {w['files']}")
                check(w["files"] == len(d["videos"]),
                      "而且那個檔案數就是畫面上那個數字")
                check(w["dupes"] == 1, f"認得出重複檔（{w['dupes']}）")
                check(w["extra"] == 1, f"認得出 wiki 沒收錄的（{w['extra']}）")
                check(w["no_number"] == 1, f"認得出沒有番號的（{w['no_number']}）")
                check(sum(x["have"] for x in w["sections"]) == w["have"],
                      "各區段的『資料夾』欄相加要等於総計那一欄")

                # Clicking a number filters the grid, so each number has to
                # equal the rows it produces -- a count that does not match
                # what appears is worse than no count.
                vids = d["videos"]
                tag = lambda st: sum(1 for v in vids if v["wiki_state"] == st)
                check(tag("extra") == w["extra"],
                      f"點『wiki 未收錄 {w['extra']}』會得到 {tag('extra')} 張卡")
                check(tag("none") == w["no_number"],
                      f"點『認不出番號 {w['no_number']}』會得到 {tag('none')} 張卡")
                dupe_rows = sum(1 for v in vids if v["wiki_dupe"])
                check(dupe_rows == w["dupe_files"],
                      f"重複那一類回報 {w['dupe_files']}、實際 {dupe_rows} 張卡")
                check(dupe_rows > w["dupes"],
                      "而且比表格上的數字多 —— 表格算多出來的份，"
                      "篩選給全部的份，兩個都是真的")
                # The table column counts works (so it can be compared with
                # the wiki column and still sum to the total); the filter
                # shows files. They differ by exactly the duplicates in that
                # section, and nowhere else. The chip reports the real card
                # count, so nothing on screen claims otherwise.
                for sec in w["sections"]:
                    rows = [v for v in vids if v["wiki_section"] == sec["name"]
                            and v["wiki_state"] == "have"]
                    dup_here = len(rows) - len({v["video_id"] for v in rows})
                    check(len(rows) - dup_here == sec["have"],
                          f"{sec['name']}：表格 {sec['have']} 部作品、"
                          f"篩出 {len(rows)} 個檔（其中 {dup_here} 個是重複檔）")

            print("\n  發行日")
            row = javdb.title_row("NSPS-694")
            check(row["released"] == "2018-04-13",
                  f"存成 YYYY-MM-DD，字串排序就是時間排序（{row['released']}）")
            d2 = c.get(f"/api/videos?root={folder}").json()
            got = {v["video_id"]: v.get("released") for v in d2["videos"]}
            check(got.get("NSPS-694") == "2018-04-13",
                  f"影片清單帶得出來（{got.get('NSPS-694')}）")
            check(got.get("ZZZ-999") == "",
                  "wiki 沒有的就是空字串，不是 None 也不是缺欄位")
            iso = [v for v in got.values() if v]
            check(all(len(x) == 10 and x[4] == "-" and x[7] == "-" for x in iso),
                  f"格式一致（{iso[:3]}）")

            print("\n  尚未收入：列得出番號與片名")
            # The one number in the panel that cannot filter the grid -- these
            # are films with no file to show. What the list has to hand over is
            # the catalogue number, so that is what the rows carry.
            r = c.get(f"/api/wiki/missing?root={folder}")
            miss = r.json()
            check(r.status_code == 200, f"端點回得來（{r.status_code}）")
            check(miss["stale"] is False, "有存作品清單，不是舊版匯入")
            ids = {w["video_id"] for w in miss["works"]}
            check("NSPS-694" not in ids and "OBT-024" not in ids,
                  "已經有的不會出現在缺少清單裡")
            check("BOMN-122" in ids, f"沒有的會出現（{sorted(ids)}）")
            check(all(w["title"] for w in miss["works"]),
                  "每一筆都有片名 —— 沒有片名的話這份清單沒有用")
            check(any(w["video_id"] == "" for w in miss["works"]),
                  "wiki 沒記番號的那幾部也列出來，番號欄留空")
            dates = [w["date"] for w in miss["works"]]
            check(dates == sorted(dates, reverse=True), "依日期新到舊")

            sec = c.get(f"/api/wiki/missing?root={folder}"
                        f"&section=配信作品").json()["works"]
            check(all(w["section"] == "配信作品" for w in sec),
                  "可以只看一個區段")
            check(len(sec) < len(miss["works"]), "而且比全部少")

            print("\n  舊版匯入（只存了數量）要說得出原因")
            javdb.set_wiki_profile(videoflags.key(folder), "x", "", 
                                   {"total": 5, "no_id": 0, "sections": []},
                                   ["AAA-1"], works=[])
            old_miss = c.get(f"/api/wiki/missing?root={folder}").json()
            check([w["video_id"] for w in old_miss["works"]] == ["AAA-1"]
                  and old_miss["stale"] is True,
                  "列得出番號、同時回報 stale，讓畫面能說『重新匯入一次就有片名了』"
                  f"而不是說『你都有了』（{old_miss}）")

            print("\n  舊字體／新字體算同一個名字")
            # The user reported this: they had typed 淺井花梨 (Traditional) and
            # the import added 浅井花梨 (shinjitai) beside it. Same name, two
            # code points, and plain equality let both in.
            info = media.load_person_info(folder)
            info["aliases"] = ["淺井花梨"]
            media.save_person_info(folder, info)
            r = c.post("/api/wiki/apply", json={
                "titles": {}, "aliases": ["浅井花梨", "浅井花莉"],
                "folder": str(folder)})
            res = r.json()
            check(res["duplicate_aliases"] == ["浅井花梨"],
                  f"浅井花梨 認得出是既有的 淺井花梨（{res['duplicate_aliases']}）")
            check(res["added_aliases"] == ["浅井花莉"],
                  f"但 浅井花莉（莉 不是 梨）是另一個名字，照樣加進去（"
                  f"{res['added_aliases']}）")
            al = media.load_person_info(folder)["aliases"]
            check(al.count("淺井花梨") == 1 and "浅井花梨" not in al,
                  f"存的還是使用者原本打的那個寫法（{al}）")

            print("\n  同一次匯入裡的重複也只進一個")
            info = media.load_person_info(folder)
            info["aliases"] = []
            media.save_person_info(folder, info)
            r = c.post("/api/wiki/apply", json={
                "titles": {}, "aliases": ["澤井", "沢井", " 澤井 "],
                "folder": str(folder)})
            check(r.json()["added_aliases"] == ["澤井"],
                  f"三種寫法只留第一個（{r.json()['added_aliases']}）")

            print("\n  折疊只處理「同一個字的兩種寫法」")
            f = media.fold_name
            check(f("淺") == f("浅") and f("澤") == f("沢"), "舊字體 → 新字體")
            check(f("Ｙｕｒｉ") == f("yuri"), "全形英數與大小寫")
            check(f(" 白 石 美 里 ") == f("白石美里"), "空白不算")
            check(f("浅井花莉") != f("浅井花梨"),
                  "莉 和 梨 是不同的字 —— wiki 自己就把它們列成兩個藝名")
            check(f("かりんまん") != f("karinman_"),
                  "假名和羅馬拼音不折疊，那是兩種寫法不是同一個字串")

            print("\n  共演者只進自己的表，不碰任何人的別名")
            check(javdb.wiki_cast("NSPS-694") == ["美咲エリ", "本田優香", "椎木綾"],
                  "查得到")
            check("本田優香" not in media.load_person_info(folder)["aliases"],
                  "合演的人沒有被塞進這位角色的別名 —— seed 就是這樣出事的")
        finally:
            main.is_allowed = real_allowed
            main._ids_by_character = real_ids
            media.list_characters = real_chars
            javdb.DB_PATH = real_db
            db.DB_PATH = real_main_db
            main.app.dependency_overrides.clear()


def main():
    raw = PAGE.encode("euc_jis_2004")

    print("解碼：宣告的 charset 是 EUC-JP，但那不完全是真的")
    check(W.decode(raw).find("闘神都市Ⅲ") > 0,
          "NEC 擴充字（Ⅲ）解得出來 —— 純 euc_jp 會在這裡整個爆掉")
    try:
        raw.decode("euc_jp")
        plain_ok = True
    except UnicodeDecodeError:
        plain_ok = False
    check(not plain_ok,
          "而且確認純 euc_jp 真的不行（不然這條測試沒有在測東西）")
    check(W.decode("こんにちは".encode("utf-8")) == "こんにちは",
          "UTF-8 的頁面也讀得動")

    d = W.parse(raw)

    print("\n這一頁是誰的")
    check(d["name"] == "美咲エリ", f"從 <title> 取名字（{d['name']}）")
    check("seesaawiki.jp" in d["url"], "來源網址留著")

    print("\n藝名（含假名讀音）")
    names = [a["name"] for a in d["aliases"]]
    check(names == ["加納なつ希", "NATSUKI", "美咲エリ", "北村ルイ"],
          f"四個藝名、順序不變（{names}）")
    check(d["aliases"][0]["kana"] == "かのうなつき", "讀音抓得到")
    check(d["aliases"][3]["kana"] == "", "沒有讀音的就留空，不要瞎猜")
    check(all("部分編集" not in n for n in names),
          "「部分編集」那個鉛筆連結沒有混進名字裡")

    print("\n作品")
    by = {w["video_id"]: w for w in d["works"]}
    check(len(d["works"]) == 7, f"七筆（{len(d['works'])}）")
    check(by["NSPS-694"]["title_ja"] ==
          "他人に吸い尽くされた母乳 夫以外の男に狂う！",
          f"片名切乾淨（{by['NSPS-694']['title_ja']}）")
    check(by["NSPS-694"]["label"] == "ながえスタイル", "廠牌拆出來")
    check(by["NSPS-694"]["performers"] == ["美咲エリ", "本田優香", "椎木綾"],
          "共演者拆得開")
    check(by["NSPS-694"]["date"] == "2018/04/13", "發行日")
    check("レーベル一覧" not in by["NSPS-694"]["title_ja"],
          "每一筆都有的導覽連結不能變成片名的一部分")
    check(by["OBT-024"]["note"].startswith("A子さん"), "※ 備註")

    print("\n這三種是第一版靜靜漏掉的")
    check("BOMN-122" in by,
          "區段標題（総集編作品）跟它底下第一筆擠在同一個 <br> 區塊裡")
    check("AKO-205" in by, "配信作品 區段同理")
    check(by["BOMN-122"]["title_ja"] == "母乳と潮が混ざり合う6本番 美咲エリ Best",
          "而且內容是對的，不是勉強塞進去的")
    check(by["MVF-375"]["alt_ids"] == ["MADV-277"],
          "一片兩個番號（出租版／販售版）")

    print("\n沒有番號的作品照樣算一筆")
    # Real releases the wiki simply never recorded a number for -- older titles,
    # mostly. Dropping them undercounted every page, and the count is the whole
    # point of the summary.
    noid = [w for w in d["works"] if not w["video_id"]]
    check(len(noid) == 2,
          f"解析出兩筆沒有番號的（含写真集）—— {[w['title_ja'][:10] for w in noid]}")
    check(noid[0]["title_ja"].startswith("VANILLA"), "片名還是抓得到")
    check(noid[0]["date"] == "2010/08/06", "日期也是")

    print("\n區段與統計")
    sm = W.summarise(d)
    names = {x["name"]: x["count"] for x in sm["sections"]}
    check(names.get("単体作品") == 3, f"沒有標題的第一段算 単体作品（{names}）")
    check(names.get("総集編作品") == 1 and names.get("配信作品") == 2,
          f"其餘照標題分（{names}）")
    check("写真集等" not in names,
          "写真集 不是影片 —— 它永遠不可能是庫裡的一個檔案")
    check(sm["total"] == 6, f"合計不含写真集（{sm['total']}）")
    check(sm["no_id"] == 1,
          f"只算影片區段裡沒有番號的（写真集 那筆本來就不算）—— {sm['no_id']}")
    check(sum(x["count"] for x in sm["sections"]) == sm["total"],
          "各區段加起來要等於合計 —— 不然畫面上的數字自己對不起來")

    print("\n每個區段各自帶著自己的番號（「資料夾」那一欄要靠它算）")
    ids = {x["name"]: x["ids"] for x in sm["sections"]}
    check("NSPS-694" in ids["単体作品"], "第一段的番號在第一段")
    check("BOMN-122" in ids["総集編作品"], "総集編的在総集編")
    check("MADV-277" in ids["単体作品"],
          "一片兩號時兩個都要在，庫裡的檔案可能用任一個命名")
    flat = [i for v in ids.values() for i in v]
    check(len(flat) == len(set(flat)),
          "同一個番號不能出現在兩個區段 —— 那會讓「資料夾」欄加起來超過総計")
    check(all(i for i in flat), "沒有空字串混進來")

    print("\n側邊欄提到的番號不是作品")
    check("HUNTA-001" not in by,
          "「Hunter 6(HUNTA-001〜200)」是選單，不能當成一部片")

    print("\n給 movie_title.ja 用的對照")
    t = W.titles_by_id(d)
    check(len(t) == 6, f"六個番號（五筆有番號的作品，其中一筆兩個號）—— 拿到 {len(t)}")
    check(t["MADV-277"] == t["MVF-375"],
          "兩個番號指向同一個片名 —— 庫裡的檔案可能用任一個命名")

    print("\n共演者對照（只提供，不自動寫進任何人的別名）")
    p = W.performers_by_id(d)
    check(p["NSPS-694"] == ["美咲エリ", "本田優香", "椎木綾"], "逐片列出")
    check("BOMN-122" not in p, "沒有共演者的就不列，不要留空清單")

    print("\n身分驗證：番號重疊才是證據，名字不是")
    m = W.match_score(d, ["NSPS-694", "obt-024", "ZZZ-999"])
    check(m["score"] == 2, f"重疊 2 個，而且不分大小寫（{m['shared']}）")
    check(W.match_score(d, [])["score"] == 0,
          "資料夾裡沒有任何可辨識番號時是 0 —— 那就該讓人確認")
    check(W.match_score(d, ["ZZZ-1"])["score"] == 0, "對不上就是 0")

    print("\n白名單：解析器只吐得出這幾個欄位")
    check(set(d) == {"name", "url", "aliases", "works"},
          f"頂層欄位固定（{sorted(d)}）")
    check(set(d["works"][0]) == {"date", "video_id", "alt_ids", "title_ja",
                                 "label", "section", "performers", "note"},
          f"每筆作品的欄位固定（{sorted(d['works'][0])}）")
    # The privacy boundary is structural: there is nowhere for a legal name,
    # an address or a contact detail to be returned, so a page carrying such a
    # section cannot leak it into person_info.json even by mistake.

    print("\n壞掉的輸入不能炸")
    for bad in (b"", b"<html></html>", b"\xff\xfe\x00", "沒有標記".encode("utf-8")):
        r = W.parse(bad)
        check(isinstance(r.get("works"), list), f"{bad[:12]!r} → 回傳空結構")

    real = Path.home() / "Downloads" / "素人系総合 wiki" / \
        "美咲エリ - 素人系総合 wiki.html"
    if real.exists():
        print("\n拿使用者真正下載的那一頁再跑一次")
        rd = W.parse(real.read_bytes())
        check(len(rd["works"]) >= 80, f"解析出 {len(rd['works'])} 筆作品")
        check(len(W.titles_by_id(rd)) >= 80,
              f"{len(W.titles_by_id(rd))} 個番號有日文片名")
        check(all(w["title_ja"] for w in rd["works"]), "每一筆都有片名")
        check(rd["name"] == "美咲エリ", "名字對")
    else:
        print("\n（找不到使用者那份下載檔，跳過真實頁面的驗證）")

    test_request_contract()
    test_migration()
    test_over_http(raw)

    print()
    if FAIL:
        print(f"{len(FAIL)} FAILED")
        for m in FAIL:
            print("  - " + m)
        sys.exit(1)
    print("all good")


if __name__ == "__main__":
    main()
