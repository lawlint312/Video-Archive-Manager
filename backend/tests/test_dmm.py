"""Japanese titles from DMM's own catalogue (core/dmm.py).

DMM's answers are canned here — shaped like the real ones checked on
2026-10-25 (STARS-786 is `1stars00786`, released 2023-03-22T15:00:00Z) — so
the rules are tested without asking DMM anything:

  * the guessed id when it is right; a search when it is not
  * a search hit counts only with the same letters *and* number
    (`ssis00570` is not SSIS-057, `gerk00116` is not ERK-116)
  * the detail must name this number, or it is another film
  * an AI remaster is not taken over the original
  * the release day is Japan's, not UTC's
  * FC2 / HEYZO / dated amateur numbers are not asked at all
  * in the title scan: DMM's Japanese is stored and shown, javdatabase is not
    asked for those; a miss falls back to javdatabase and is remembered for a
    week; a title typed by hand stays the one shown; DMM being down is not
    fatal and stops further DMM requests for that run

Run:  python tests/test_dmm.py      (from backend/)
"""
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import dmm, javdb           # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def detail(cid, maker_id, title, when="2023-03-22T15:00:00Z", cast=("星川優子",)):
    return {"id": cid, "title": title, "makerContentId": maker_id,
            "makerReleasedAt": when, "actresses": [{"name": n} for n in cast],
            "maker": {"name": "SODクリエイト"}}


class FakeDMM:
    """`dmm._post`, answering from a table: {cid: detail} and {query: [cids]}."""

    def __init__(self, details, searches):
        self.details, self.searches, self.calls = details, searches, []

    def __call__(self, query, variables):
        self.calls.append(variables)
        if "ppvContent" in query:
            return {"ppvContent": self.details.get(variables["id"])}
        ids = self.searches.get(variables["queryWord"], [])
        return {"legacySearchPPV": {"result": {"contents": [{"id": i} for i in ids]}}}


def with_fake(fake, fn):
    real = dmm._post
    dmm._post = fake
    try:
        return fn()
    finally:
        dmm._post = real


def test_rules():
    print("找商品 id 的規則")
    fake = FakeDMM({"1stars00786": detail("1stars00786", "STARS-786", "しゃぶるの大好き…")},
                   {"STARS 786": ["stars00787", "1stars00786"]})
    h = with_fake(fake, lambda: dmm.lookup("STARS-786"))
    check(h and h["cid"] == "1stars00786", "猜錯（stars00786）就搜尋，找到帶前綴的 1stars00786")
    check(h and h["released"] == "2023-03-23", "發行日是日本的那一天（UTC 15:00 = 隔天）")
    check(h and h["actresses"] == ["星川優子"], "演員")

    fake = FakeDMM({"ssis00570": detail("ssis00570", "SSIS-570", "別的片"),
                    "ssis00057": detail("ssis00057", "SSIS-057", "對的片")},
                   {"SSIS 057": ["ssis00570", "ssis00579", "ssis00057"]})
    h = with_fake(fake, lambda: dmm.lookup("SSIS-057"))
    check(h and h["title"] == "對的片", "猜中就不搜尋")
    check(len(fake.calls) == 1, f"只問了一次（{len(fake.calls)}）")

    fake = FakeDMM({"gerk00116": detail("gerk00116", "GERK-116", "別的片")},
                   {"ERK 116": ["gerk00116"]})
    check(with_fake(fake, lambda: dmm.lookup("ERK-116")) is None,
          "gerk00116 不是 ERK-116（字母段要整段對上）")

    fake = FakeDMM({"48midv00012ai": detail("48midv00012ai", "MIDV-012", "AI 重製版"),
                    "1midv00012": detail("1midv00012", "MIDV-012", "原版")},
                   {"MIDV 012": ["48midv00012ai", "1midv00012"]})
    h = with_fake(fake, lambda: dmm.lookup("MIDV-012"))
    check(h and h["title"] == "原版", "原版排在 AI 重製版前面")

    fake = FakeDMM({"abc00123": detail("abc00123", "ABCD-123", "另一部")}, {})
    check(with_fake(fake, lambda: dmm.lookup("ABC-123")) is None,
          "商品裡寫的番號不是這部，就不是這部")

    fake = FakeDMM({}, {})
    for vid in ("FC2-PPV-1234567", "HEYZO-1380", "080124-001", ""):
        check(not dmm.askable(vid) and with_fake(fake, lambda: dmm.lookup(vid)) is None,
              f"{vid or '空字串'}：不問 DMM")
    check(fake.calls == [], "一個請求都沒發")
    check(dmm.jst_date("2021-05-06T15:00:00Z") == "2021-05-07" and dmm.jst_date("壞的") == "",
          "日期換算")


def test_title_scan(tmp: Path):
    print("\n片名查詢：先 DMM、查不到才 javdatabase")
    from fastapi.testclient import TestClient
    from app import auth, db, main, settings as app_settings
    from app.core import searchindex

    lib = tmp / "JAV" / "星川優子"
    lib.mkdir(parents=True)
    for n in ("STARS-786.mp4", "SDMU-066.mp4", "ABP-100.mp4", "MIDV-012.mp4"):
        (lib / n).write_bytes(b"v")
    saved = (db.DB_PATH, javdb.DB_PATH, javdb._fetch, dmm._post, searchindex._FILE,
             searchindex._cache, app_settings.archive_roots_for,
             app_settings.inbox_paths_for)
    asked_javdatabase = []

    def fake_javdatabase(url):
        asked_javdatabase.append(url)
        return None                           # 404: no English page either

    try:
        db.DB_PATH = tmp / "vam.db"
        javdb.DB_PATH = tmp / "javdb.sqlite3"
        searchindex._FILE = tmp / "search_index.json"
        searchindex._cache = None
        db.init_db()
        app_settings.archive_roots_for = lambda user=None: [tmp / "JAV"]
        app_settings.inbox_paths_for = lambda user=None: []
        javdb._fetch = fake_javdatabase
        javdb.set_title("MIDV-012", manual="手打的")
        javdb.set_title("ABP-100", ja="wiki 的日文")
        dmm._post = FakeDMM(
            {"1stars00786": detail("1stars00786", "STARS-786", "しゃぶるの大好き…"),
             "midv00012": detail("midv00012", "MIDV-012", "DMM 的日文", cast=())},
            {"STARS 786": ["1stars00786"]})
        main.app.dependency_overrides[auth.current_user] = lambda: {"id": 1, "role": "admin"}
        c = TestClient(main.app)

        def scan():
            jid = c.post("/api/movietitle/scan", json={"names": ["星川優子"]}).json()["job_id"]
            for _ in range(200):
                j = c.get(f"/api/dedup/job/{jid}").json()
                if j["status"] != "running":
                    return j
                time.sleep(0.05)
            return j

        j = scan()
        r = j["result"]
        by = {f["id"]: f for f in r["found"]}
        check(by["STARS-786"]["source"] == "dmm"
              and javdb.title_row("STARS-786")["ja"] == "しゃぶるの大好き…",
              "STARS-786：DMM 的日文存進日文欄，顯示來源 dmm")
        check(javdb.title_row("STARS-786")["released"] == "2023-03-23", "發行日一起補上")
        check(not any("stars-786" in u for u in asked_javdatabase),
              "有日文的就不再問 javdatabase")
        check("SDMU-066" in [m["id"] for m in r["missing"]]
              and any("sdmu-066" in u for u in asked_javdatabase),
              "DMM 沒有的退回 javdatabase")
        check(by["MIDV-012"]["title"] == "手打的" and by["MIDV-012"]["source"] == "manual"
              and javdb.title_row("MIDV-012")["ja"] == "DMM 的日文",
              "手打的片名照樣顯示；DMM 的日文存著當後備")
        check(by["ABP-100"]["source"] == "ja"
              and not any(v.get("id") == "abp00100" for v in dmm._post.calls),
              "已經有日文（wiki）的不問 DMM")
        check(r["dmm_new"] == 2 and not r["dmm_error"], f"DMM 補了 2 部（{r['dmm_new']}）")

        before = len(dmm._post.calls)
        scan()
        check(not any(v.get("queryWord") == "SDMU 066" or v.get("id") == "sdmu00066"
                      for v in dmm._post.calls[before:]),
              "一週內查過沒有的不再問")

        def down(query, variables):
            raise RuntimeError("無法連線到 DMM：timed out")
        dmm._post = down
        javdb.DB_PATH = tmp / "javdb2.sqlite3"
        r = scan()["result"]
        check(r["dmm_error"].startswith("無法連線") and len(r["missing"]) == 4
              and j["status"] == "done", "DMM 連不上：不中斷，全部退回 javdatabase")
    finally:
        main.app.dependency_overrides.clear()
        (db.DB_PATH, javdb.DB_PATH, javdb._fetch, dmm._post, searchindex._FILE,
         searchindex._cache, app_settings.archive_roots_for,
         app_settings.inbox_paths_for) = saved


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    test_rules()
    d = Path(tempfile.mkdtemp())
    try:
        test_title_scan(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
