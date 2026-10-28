"""Saved AVBase pages: titles, cast, stage names, profile and work relations.

The pages here are built in the shape of the real ones (checked on
2026-10-25: 森田花梨's talent page, AKO-599's work page) — `__NEXT_DATA__`
JSON inside a saved HTML file:

  * a work's title is its own product's, not a remaster's or compilation's
    (UFD-061's work title is 「【4Kリマスター版】…」; `244kufd00061` is not it)
  * the relation verdict: parent older → original; newer and uncut → full
    compilation; newer otherwise → digest; a date missing → no verdict
  * a talent page names its parents by number only; a later work page adds
    the date, and the stored verdict follows
  * the import fills only what is empty: a title from a wiki or a hand, a
    date, a cast list already there stay; stage names are compared folded
    (淺 ＝ 浅); a profile field already filled is not overwritten

Run:  python tests/test_avbase.py      (from backend/)
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import avbase, catalog, javdb, media     # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def page(next_data: dict) -> bytes:
    return ('<!DOCTYPE html><html><head><link rel="canonical" href="https://www.avbase.net/">'
            '</head><body><div id="__next"></div><script id="__NEXT_DATA__" '
            'type="application/json">' + json.dumps(next_data, ensure_ascii=False)
            + '</script></body></html>').encode("utf-8")


def product(source, pid, title, date):
    return {"source": source, "product_id": pid, "title": title, "date": date}


UFD = {"prefix": "dreamticket", "work_id": "UFD-061",
       "title": "【4Kリマスター版】働く美女と性交 浅井花梨",
       "min_date": "Thu Aug 04 2016 09:00:00 GMT+0900 (Japan Standard Time)",
       "casts": [{"actor": {"name": "浅井花梨"}, "note": ""}],
       "products": [product("fanza", "244kufd00061", "【4Kリマスター版】働く美女と性交 浅井花梨",
                            "Fri Jan 05 2024 00:00:00 GMT+0900"),
                    product("fanza", "24ufd00061", "働く美女と性交 浅井花梨",
                            "Thu Aug 04 2016 00:00:00 GMT+0900")]}
AKO = {"prefix": "", "work_id": "AKO-599", "title": "YURIさん 2発目",
       "min_date": "Sat Mar 07 2026 09:00:00 GMT+0900 (Japan Standard Time)",
       "casts": [{"actor": {"name": "浅井花梨"}, "note": ""}],
       "products": [product("mgstage", "210AKO-599", "YURIさん 2発目",
                            "Sat Mar 07 2026 09:00:00 GMT+0900"),
                    product("fanza", "ako599", "YURIさん 2発目",
                            "Sat Mar 07 2026 00:00:00 GMT+0900")]}
WORK_PAGE = page({"page": "/works/[canonical_id]", "query": {"canonical_id": "AKO-599"},
                  "props": {"pageProps": {"work": AKO, "parents": [UFD], "children": [],
                                          "originals": [], "adaptations": []}}})


def talent_work(w, parents=(), children=()):
    return {**{k: v for k, v in w.items() if k != "casts"},
            "actors": [{"name": c["actor"]["name"]} for c in w["casts"]],
            "relworks": {"parents": [{"prefix": "", "work_id": p} for p in parents],
                         "children": [{"prefix": "", "work_id": c} for c in children]}}


TALENT_PAGE = page({
    "page": "/talents/[name]", "query": {"name": "森田花梨", "page": "1"},
    "props": {"pageProps": {
        "talent": {"primary": {"name": "森田花梨", "ruby": "もりたかりん"},
                   "actors": [{"name": "森田花梨"}, {"name": "浅井花梨"}, {"name": "浅川かな"}],
                   "meta": {"basic_info": {"birthday": "1997-01-10", "height": "150",
                                           "bust": "88", "waist": "60", "hip": "89",
                                           "cup": "F", "prefectures": "新潟県"}}},
        "works": [talent_work(AKO, parents=["UFD-061"])],
        "total": 431, "page": 1}}})


def card(prefix, wid, date, title, cast, parents=(), children=()):
    """One work card as the page renders it (2026-10-25 markup)."""
    strip = lambda cls, ids: (
        f'<div class="flex flex-wrap h-14 gap-0 bg-muted overflow-hidden grow {cls}">'
        + "".join(f'<a href="https://www.avbase.net/works/{i}"><img alt="{i}"></a>' for i in ids)
        + '</div>')
    pre = f'<span class="text-gray-400">{prefix}:</span>' if prefix else ""
    return ('<div class="bg-background border border-light rounded-lg overflow-hidden h-full">'
            f'<div dir="rtl">{pre}<span class="font-bold text-gray-500">{wid}</span></div>'
            f'<a class="block font-bold" href="https://www.avbase.net/works/date/{date}">x</a>'
            # the real class list varies by title length (line-clamp-3 / -5)
            f'<a data-slot="button" class="m-1 py-1 px-2 line-clamp-{5 if len(title) > 8 else 3}" '
            f'href="https://www.avbase.net/works/{wid}">{title}</a>'
            '<div class="flex bg-muted pl-2">'
            + (strip("", parents) if parents else "")
            + (strip("flex-row-reverse", children) if children else "")
            + '</div></div></div>'
            + "".join(f'<a href="https://www.avbase.net/talents/{c}">{c}</a>' for c in cast)
            + '</div>')


# Saved from the talent page, merged by the user's bookmarklet — while its
# JSON is still the *work* page the tab first loaded (the real file's case).
SCREEN_PAGE = (
    '<!-- saved from url=(0080)https://www.avbase.net/talents/%E6%B7%B1%E7%94%B0'
    '%E7%B5%90%E6%A2%A8?cast_size=single -->\n<html><head><title>AKO-599: stale</title>'
    '<style>.x{}</style></head><body>'
    '<a>新着作品</a><a>作品コメント</a><div>森田花梨</div><div>もりたかりん</div>'
    '<div>森田花梨</div><div>もりたかりん</div><div>浅井花梨</div><div>あさいかりん</div>'
    '<div>浅川かな</div><div>あさかわかな</div><div>(イメージビデオ)</div>'
    '<div>かりんまん</div><button>フォロー</button><span>31</span>'
    '<h3>基本情報</h3><span>編集</span><dt>生年月日</dt><dd>1997-01-10</dd>'
    '<dt>出身地</dt><dd>新潟県</dd><dt>身長</dt><dd>150 cm</dd>'
    '<dt>サイズ</dt><dd>B88(F) W60 H89</dd><dt>趣味</dt><dd>空手</dd>'
    '<p>最終更新: 2026/08/13</p><span>単体</span><span>431</span>'
    '<div class="grid gap-4 sm:grid-cols-2 gap-4">'
    + card("", "AKO-599", "2026-03-07", "YURIさん 2発目", ["浅井花梨"], parents=["UFD-061"])
    + card("dreamticket", "UFD-061", "2016-08-04", "働く美女と性交 浅井花梨", ["浅井花梨"],
           children=["AKO-385", "AKO-599"])
    + card("", "IENFA-8601", "2026-02-18", "池袋で見つけた…", ["森田花梨"], parents=["IENF-086"])
    + card("", "AKO-599", "2026-03-07", "YURIさん 2発目", ["浅井花梨"], parents=["UFD-061"])
    + '</div>'
).encode("utf-8") + WORK_PAGE


def test_parse():
    print("讀頁面")
    p = avbase.parse(WORK_PAGE)
    check(p["kind"] == "work" and p["works"][0]["id"] == "AKO-599", "作品頁")
    ufd = next(w for w in p["works"] if w["id"] == "UFD-061")
    check(ufd["title"] == "働く美女と性交 浅井花梨",
          f"片名取它自己的商品，不是 4K 重製版（{ufd['title']}）")
    check(ufd["date"] == "2016-08-04", "發行日也是自己商品的")
    r = p["relations"]
    check(len(r) == 1 and (r[0]["parent"], r[0]["child"], r[0]["kind"])
          == ("UFD-061", "AKO-599", "original"), "UFD-061（2016）⊃ AKO-599（2026）：原片")

    t = avbase.parse(TALENT_PAGE)
    check(t["kind"] == "talent" and t["name"] == "森田花梨"
          and t["aliases"] == ["浅井花梨", "浅川かな"], "演員頁：本名與其他藝名")
    check(t["profile"] == {"dob": "1997-01-10", "height": "150 cm",
                           "measurements": "88-60-89", "cup": "F", "birthplace": "新潟縣"},
          f"個資換成既有格式：{t['profile']}")
    check(t["relations"][0]["parent_date"] == "" and t["relations"][0]["kind"] == "unknown",
          "演員頁只有原片番號、沒有日期：不判斷")

    print("\n合併過的演員頁（讀畫面，不讀過時的 JSON）")
    sp = avbase.parse(SCREEN_PAGE)
    check(sp["kind"] == "talent" and sp["name"] == "森田花梨",
          "看存檔來源判斷是演員頁，不信過時的作品頁 JSON")
    check(sp["aliases"] == ["浅井花梨", "浅川かな", "かりんまん"],
          f"藝名：讀音跳過、括號註記跳過，かりんまん（本身是假名）算藝名（{sp['aliases']}）")
    check(sp["profile"] == {"dob": "1997-01-10", "height": "150 cm",
                            "measurements": "88-60-89", "cup": "F", "birthplace": "新潟縣"},
          f"側欄個資（{sp['profile']}）")
    check(len(sp["works"]) == 3 and sp["total"] == 431, "重複的卡片只算一次；總數取「単体 431」")
    check(all(w["title"] for w in sp["works"]),
          "每張卡的片名都讀到（line-clamp-3 與 -5 兩種寫法）")
    rel = {(r["parent"], r["child"]): r for r in sp["relations"]}
    check(set(rel) == {("UFD-061", "AKO-599"), ("UFD-061", "AKO-385"), ("IENF-086", "IENFA-8601")},
          f"一般排列＝原片、靠右排列＝被剪出來的片；同一組只算一次（{sorted(rel)}）")
    check(rel[("UFD-061", "AKO-599")]["kind"] == "original",
          "兩張卡都在檔裡：日期齊全，判斷為原片")
    check(rel[("IENF-086", "IENFA-8601")]["kind"] == "unknown",
          "原片不在清單裡（多人作品）：沒有日期，不判斷")

    print("\n收錄判斷")
    check(avbase.judge("2016-08-04", "2026-03-07") == "original", "原片在前 → 原片")
    check(avbase.judge("2024-01-01", "2020-01-01", "ノーカット完全収録 福袋") == "uncut",
          "總集編在後、寫明ノーカット → 完整收錄")
    check(avbase.judge("2024-01-01", "2020-01-01", "BEST 55人5時間") == "digest",
          "總集編在後、其他 → 精選，原片比較完整")
    check(avbase.judge("", "2020-01-01") == "unknown" and
          avbase.judge("2020-01-01", "2020-01-01") == "unknown", "缺日期或同一天 → 不判斷")

    for bad in (b"<html>nothing</html>",
                page({"page": "/", "props": {"pageProps": {}}})):
        try:
            avbase.parse(bad)
            check(False, "不是作品頁或演員頁要拒絕")
        except ValueError:
            check(True, "不是作品頁或演員頁：拒絕")


def test_import(tmp: Path):
    print("\n匯入：只補空的")
    from fastapi.testclient import TestClient
    from app import auth, db, main, settings as app_settings
    from app.core import searchindex

    lib = tmp / "JAV"
    lead = lib / "森田花梨"
    lead.mkdir(parents=True)
    for n in ("UFD-061.mp4", "AKO-599.mp4"):
        (lead / n).write_bytes(b"v")
    (lead / "person_info.json").write_text(json.dumps(
        {"aliases": ["淺井花梨"], "profile": {"height": "151 cm"}}, ensure_ascii=False),
        encoding="utf-8")
    saved = (db.DB_PATH, javdb.DB_PATH, searchindex._FILE, searchindex._cache,
             app_settings.archive_roots_for, app_settings.inbox_paths_for,
             main.is_allowed)
    try:
        db.DB_PATH = tmp / "vam.db"
        javdb.DB_PATH = tmp / "javdb.sqlite3"
        searchindex._FILE = tmp / "search_index.json"
        searchindex._cache = None
        db.init_db()
        app_settings.archive_roots_for = lambda user=None: [lib]
        app_settings.inbox_paths_for = lambda user=None: []
        main.is_allowed = lambda p, u=None: True
        main.app.dependency_overrides[auth.current_user] = lambda: {"id": 1, "role": "admin"}
        javdb.set_title("AKO-599", ja="wiki 的日文")
        javdb.set_title("UFD-061", en="Working Beauty")
        c = TestClient(main.app)

        def preview(raw, name):
            r = c.post("/api/wiki/preview", files=[("files", (name, raw, "text/html"))])
            return r.json()["pages"][0]

        w = preview(WORK_PAGE, "AKO-599.html")
        check(w.get("site") == "avbase.net" and w["kind"] == "work", f"認得是 AVBase 作品頁（{w.get('error')}）")
        check(w["titles"] == {"UFD-061": "働く美女と性交 浅井花梨"},
              "只提供沒有日文的：AKO-599 已有 wiki 的日文，不動")
        check(w["replacing"] == {"UFD-061": "Working Beauty"}, "說明會取代英文")
        check(set(w["released"]) == {"UFD-061", "AKO-599"}, "沒有發行日的都補")
        check(w["rel_counts"] == {"total": 1, "hers": 1, "hers_judged": 1, "both": 1, "redundant": 1},
              f"兩部你都有，可以只留原片（{w['rel_counts']}）")

        t = preview(TALENT_PAGE, "talent.html")
        check(t["matches"][0]["name"] == "森田花梨" and t["matches"][0]["score"] == 1,
              "演員頁：番號重疊對到森田花梨")
        check(t["profile"] == {"dob": "1997-01-10", "measurements": "88-60-89", "cup": "F",
                               "birthplace": "新潟縣"} and t["profile_existing"] == {"height": "151 cm"},
              "已填的身高不會被覆蓋")

        # the talent page first (no dates), the work page after (with dates)
        body = {"site": "avbase.net", "folder": str(lead), "titles": t["titles"],
                "released": t["released"], "cast": t["cast"], "aliases": t["aliases"],
                "profile": t["profile"], "relations": t["relations"],
                "source": t["url"], "works": t["works"], "catalog": True,
                "page_name": t["name"]}
        r = c.post("/api/wiki/apply", json=body).json()["written"]
        check(r["aliases"] == 1 and r["added_aliases"] == ["浅川かな"],
              f"藝名：浅井花梨＝淺井花梨（已有），只加浅川かな（{r.get('added_aliases')}）")
        info = json.loads((lead / "person_info.json").read_text(encoding="utf-8"))
        check(info["profile"]["height"] == "151 cm" and info["profile"]["cup"] == "F",
              "個資：只補空的")
        check(avbase.relations()[0]["kind"] == "unknown", "只有演員頁時，關係還不能判斷")

        r = c.post("/api/wiki/apply", json={
            "site": "avbase.net", "titles": w["titles"], "released": w["released"],
            "cast": w["cast"], "relations": w["relations"], "source": w["url"],
            "works": w["works"], "catalog": True}).json()["written"]
        rel = avbase.relations()
        check(len(rel) == 1 and rel[0]["kind"] == "original" and rel[0]["parent_date"] == "2016-08-04",
              "作品頁補上日期之後，判斷更新成「原片」")
        check(javdb.title_row("UFD-061")["ja"] == "働く美女と性交 浅井花梨"
              and javdb.title_row("AKO-599")["ja"] == "wiki 的日文", "片名：補空的，wiki 的不換")
        check(javdb.title_row("UFD-061")["released"] == "2016-08-04", "發行日寫入")
        check(not javdb._connect().execute(
                  "SELECT ja FROM movie_title WHERE video_id = 'UFD-061'").fetchone()["ja"],
              "AVBase 的片名只進作品目錄，不寫 movie_title（讀的時候才墊在下面）")

        javdb.set_title("UFD-061", ja="之後手動匯入的")
        check(javdb.title_row("UFD-061")["ja"] == "之後手動匯入的"
              and javdb.titles()["UFD-061"] == "之後手動匯入的",
              "之後才有的日文（DMM、NFO）蓋得過作品目錄")
        javdb.set_title("UFD-061", manual="手打")
        check(javdb.titles()["UFD-061"] == "手打", "手打的永遠最優先")

        print("\n共用作品目錄")
        before = c.get(f"/api/videos?root={lead}").json()["wiki"]
        s = preview(SCREEN_PAGE, "merged.html")
        c.post("/api/wiki/apply", json={
            "site": "avbase.net", "folder": str(lead), "works": s["works"], "catalog": True,
            "page_name": s["name"], "source": s["url"], "relations": s["relations"]})
        check(javdb.titles().get("IENFA-8601") == "池袋で見つけた…",
              "庫裡沒有的作品也存了片名")
        (lead / "IENFA-8601.mp4").write_bytes(b"x")
        searchindex.invalidate()
        vids = {v["video_id"]: v for v in c.get(f"/api/videos?root={lead}").json()["videos"]}
        check(vids["IENFA-8601"]["title"] == "池袋で見つけた…"
              and vids["IENFA-8601"]["released"] == "2026-02-18",
              "之後才下載的片，片名與發行日自動帶入")
        (lead / "IENFA-8601.mp4").unlink()
        after = c.get(f"/api/videos?root={lead}").json()["wiki"]
        check(len(before["ids"]) == 1 and len(after["ids"]) == 3,
              f"AVBase 的成員只累加，不會被下一個檔蓋掉（{before['ids']} → {after['ids']}）")

        javdb.set_wiki_profile(
            main.videoflags.key(lead), "森田花梨", "https://wiki/x",
            {"total": 2, "no_id": 1, "sections": []}, ["AKO-599"],
            works=[{"i": "AKO-599", "t": "wiki 版", "d": "2026-03-07", "s": "単体作品"},
                   {"i": "IENFA-8601", "t": "wiki 版池袋", "d": "", "s": "配信作品"},
                   {"i": "", "t": "番號不明的舊片", "d": "2015-01-01", "s": "単体作品"}])
        wp = c.get(f"/api/videos?root={lead}").json()["wiki"]
        secs = {x["name"]: x["count"] for x in wp["sections"]}
        check([p["source"] for p in wp["pages"]] == ["wiki", "avbase"],
              f"兩個來源合成一份，wiki 排前面（{wp['pages']}）")
        check(wp["total"] == 4 and wp["no_id"] == 1,
              f"聯集：AKO-599、IENFA-8601、UFD-061 ＋ 無番號 1 部（{wp['total']}）")
        check(secs == {"単体作品": 2, "配信作品": 1, "AVBase:単体": 1},
              f"兩邊都有的歸 wiki 的區段，只有 AVBase 的另成一區（{secs}）")
        check(javdb.titles()["IENFA-8601"] == "wiki 版池袋", "兩邊片名不同，以 wiki 為準")

        avbase.save_relations([{"parent": "UFD-061", "child": "IENFA-8601",
                                "parent_date": "2016-08-04", "child_date": "2026-02-18"}])
        miss = {w["video_id"]: w for w in
                c.get(f"/api/wiki/missing?root={lead}").json()["works"]}
        check(set(miss) == {"IENFA-8601", ""}, f"尚未收入：沒有的才列（{sorted(miss)}）")
        check(miss["IENFA-8601"]["covered_by"] == "UFD-061",
              "已經有原片的，標出是哪一部")

        other = lib / "改名後"
        javdb.move_wiki_profile(main.videoflags.key(lead), main.videoflags.key(other))
        check(catalog.summary(main.videoflags.key(other))["total"] == 4
              and catalog.summary(main.videoflags.key(lead)) is None,
              "角色資料夾改名，收錄資料跟著走")
    finally:
        main.app.dependency_overrides.clear()
        (db.DB_PATH, javdb.DB_PATH, searchindex._FILE, searchindex._cache,
         app_settings.archive_roots_for, app_settings.inbox_paths_for,
         main.is_allowed) = saved


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    test_parse()
    d = Path(tempfile.mkdtemp())
    try:
        test_import(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
