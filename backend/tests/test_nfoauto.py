"""NFOs are read in by themselves, and the import finds what is on disk now.

On 2026-11-16 two videos downloaded with their NFOs showed no title. An NFO
was only ever read when someone ran the import in 角色查詢 — and even that
would have found nothing: it took its video list from the search index,
built a minute before the two were moved into a `V` subfolder outside VAM,
so it looked for their NFOs where they had been.

  * the search index notes each video's NFO and its mtime on the walk it
    already does; a new, rewritten or moved one is handed to `on_nfo`
  * an index from before this counts as having seen no NFO (read all once)
  * the automatic read fills only what is empty, like the import — and gives
    an inbox video no co-stars (the NFO names the person herself)
  * the import walks the chosen characters' folders instead of the index

Builds its own folders and databases in a temporary directory.

Run:  python tests/test_nfoauto.py      (from backend/)
"""
import _tmpdata  # noqa: F401  (first: never the real data/ or cache/)
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


# what the downloader wrote beside the two videos (title and date only)
NFO = """<?xml version="1.0" encoding="utf-8" standalone="yes"?>
<movie>
  <title>{title}</title>
  <url>https://x.com/someone/status/1</url>
  <premiered>2024-10-25</premiered>
  <year>2024</year>
  <studio>X (Twitter)</studio>
  <actor>
    <name>{actor}</name>
    <type>Actor</type>
  </actor>
</movie>
"""


def put(video: Path, title=None, actor="Lead"):
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"\0")
    if title is not None:
        video.with_suffix(".nfo").write_text(NFO.format(title=title, actor=actor),
                                             encoding="utf-8")


def test_index(tmp: Path):
    print("\n索引記下 NFO：新的、改過的、搬走的才交出去")
    from app.core import searchindex, javdb, filetitle, costar
    saved = (javdb.titles, filetitle.all_titles, costar.all_names)
    # the walk reads the title tables: never the real ones
    javdb.titles = lambda: {}
    filetitle.all_titles = lambda: {}
    costar.all_names = lambda: {}
    try:
        _test_index(tmp)
    finally:
        javdb.titles, filetitle.all_titles, costar.all_names = saved


def _test_index(tmp: Path):
    from app.core import searchindex
    lib, box = tmp / "Lib", tmp / "Inbox"
    put(lib / "Lead" / "a.mp4", "片名 A")
    put(lib / "Lead" / "b.mp4")                      # no NFO
    put(box / "c.mp4", "片名 C")
    d1 = searchindex._compute([lib], [box])
    check(set(d1["nfo"]) == {str(lib / "Lead" / "a.mp4"), str(box / "c.mp4")},
          "只記有 NFO 的影片")
    check(d1["nfo"][str(lib / "Lead" / "a.mp4")][1] == "Lead"
          and d1["nfo"][str(box / "c.mp4")][1] == "", "連同角色（待分類是空的）")
    check("nfo" not in searchindex._public(d1), "不送給瀏覽器")

    first = {str(p) for p, _c in searchindex.nfo_changes(None, d1)}
    check(len(first) == 2, "第一次（或升級前的索引）：全部讀一次")
    old_format = {k: v for k, v in d1.items() if k != "nfo"}
    check(len(searchindex.nfo_changes(old_format, d1)) == 2,
          "升級前的索引沒有 NFO 清單：當作都沒看過")
    check(searchindex.nfo_changes(d1, searchindex._compute([lib], [box])) == [],
          "沒變：什麼都不交")

    nfo = lib / "Lead" / "a.nfo"
    st = nfo.stat()
    os.utime(nfo, (st.st_atime, st.st_mtime + 10))
    (lib / "Lead" / "V").mkdir()
    for ext in (".mp4", ".nfo"):                     # moved outside VAM
        (box / f"c{ext}").rename(lib / "Lead" / "V" / f"c{ext}")
    d2 = searchindex._compute([lib], [box])
    got = sorted((p.name, c) for p, c in searchindex.nfo_changes(d1, d2))
    check(got == [("a.mp4", "Lead"), ("c.mp4", "Lead")],
          f"改過的 NFO、搬進 V 的影片都交出去（{got}）")

    # an index written before NFOs were noted is rebuilt now, not at the TTL
    saved_file, saved_cache = searchindex._FILE, searchindex._cache
    try:
        searchindex._FILE = tmp / "search_index.json"
        searchindex._cache = {**old_format, "at": time.time()}
        check(searchindex.get([lib], [box])["stale"],
              "升級前的索引（沒有 NFO 清單）就算還新，也馬上重建")
        # wait for the whole refresh, its disk write included: restoring
        # `_FILE` before that would let it write the real index file
        for _ in range(200):
            if not searchindex._refreshing:
                break
            time.sleep(0.05)
        check(not searchindex._refreshing, "重建跑完（含寫檔）才還原路徑")
        check("nfo" in (searchindex._cache or {}), "重建後有 NFO 清單")
    finally:
        searchindex._FILE, searchindex._cache = saved_file, saved_cache

    heard = []
    searchindex.on_nfo, saved = (lambda found, roots: heard.append(found)), searchindex.on_nfo
    try:
        searchindex._announce(d1, d2, [lib])
        for _ in range(50):
            if heard:
                break
            time.sleep(0.02)
    finally:
        searchindex.on_nfo = saved
    check(heard and len(heard[0]) == 2, "on_nfo 在另一條執行緒被叫到")


def test_auto_and_scope(tmp: Path):
    print("\n自動讀入：只補空的；待分類的不標共演；匯入看的是現在的資料夾")
    from app import db, main, settings as app_settings
    from app.core import javdb, filetitle, costar, searchindex

    lib, box = tmp / "Lib", tmp / "Inbox"
    lead = lib / "Lead"
    put(lead / "a.mp4", "人體彩繪 假裝是衣服")
    put(lead / "b.mp4", "NFO 的片名")
    put(lead / "Other.mp4")
    (lib / "Other").mkdir()
    put(box / "c.mp4", "待分類的片名", actor="Lead")

    saved = (db.DB_PATH, javdb.DB_PATH, searchindex._FILE, searchindex._cache,
             app_settings.archive_roots_for, app_settings.inbox_paths_for)
    try:
        db.DB_PATH = tmp / "vam.db"
        javdb.DB_PATH = tmp / "javdb.sqlite3"
        searchindex._FILE = tmp / "search_index.json"
        searchindex._cache = None
        db.init_db()
        app_settings.archive_roots_for = lambda user=None: [lib]
        app_settings.inbox_paths_for = lambda user=None: [box]
        filetitle.set_title(lead / "b.mp4", "手打的片名")

        found = searchindex.nfo_changes(None, searchindex._compute([lib], [box]))
        main._auto_nfo(found, [lib])
        check(filetitle.get(lead / "a.mp4") == "人體彩繪 假裝是衣服", "片名帶進來了")
        check(filetitle.all_released().get(
                  __import__("app.core.videoflags", fromlist=["key"]).key(lead / "a.mp4"))
              == "2024-10-25", "發行日也帶進來")
        check(filetitle.get(lead / "b.mp4") == "手打的片名", "手打的片名沒被換掉")
        check(filetitle.get(box / "c.mp4") == "待分類的片名", "待分類的影片也有片名")
        check(costar.for_video(box / "c.mp4") == [],
              "待分類的影片不標共演（NFO 裡是她本人）")

        # moved into a subfolder after the index was built: the import must
        # still find it
        (lead / "V").mkdir()
        for ext in (".mp4", ".nfo"):
            (lead / f"a{ext}").rename(lead / "V" / f"a{ext}")
        scope = main._nfo_scope(["Lead"], {"id": 1, "role": "admin"})
        check(lead / "V" / "a.mp4" in {p for p, _c in scope},
              "匯入直接看資料夾：搬進 V 的影片找得到")
        check(all(c == "Lead" for _p, c in scope) and len(scope) == 3,
              f"只有選到的角色（{len(scope)} 支）")
    finally:
        (db.DB_PATH, javdb.DB_PATH, searchindex._FILE, searchindex._cache,
         app_settings.archive_roots_for, app_settings.inbox_paths_for) = saved


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for fn in (test_index, test_auto_and_scope):
        d = Path(tempfile.mkdtemp(prefix="vam-nfoauto-"))
        try:
            fn(d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
