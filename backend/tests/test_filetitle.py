"""Path-keyed titles, and the move handling that keeps them attached.

The move/forget paths are the whole risk here. Getting them wrong is silent —
the title just is not there any more the next time you look — so each one is
tested rather than trusted, the same way `videoflags` is.
"""
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _fresh(tmp):
    """Point the database at a scratch file before importing anything.

    CLAUDE.md rule, and it has been broken twice: any test that writes must
    redirect DB_PATH first or it edits the real library.
    """
    os.environ["VAM_DATA_DIR"] = str(tmp)
    for m in [k for k in list(sys.modules) if k.startswith("app")]:
        del sys.modules[m]
    from app import db
    db.init_db()
    from app.core import filetitle
    return filetitle


def test_set_get_clear(tmp_path):
    ft = _fresh(tmp_path)
    p = tmp_path / "a" / "movie.mp4"
    assert ft.get(p) == ""
    assert ft.set_title(p, "  紅蓮螺巖  ") == "紅蓮螺巖"     # trimmed
    assert ft.get(p) == "紅蓮螺巖"
    assert ft.set_title(p, "") == ""
    assert ft.get(p) == "", "清空應該刪掉整列，而不是存一個空字串"


def test_case_insensitive_key_on_windows(tmp_path):
    ft = _fresh(tmp_path)
    ft.set_title(tmp_path / "Dir" / "Film.mp4", "片名")
    other = ft.get(tmp_path / "dir" / "film.mp4")
    if os.name == "nt":
        assert other == "片名", "Windows 上大小寫不同應視為同一個檔"
    else:
        assert other == ""


def test_move_carries_the_title(tmp_path):
    ft = _fresh(tmp_path)
    src, dst = tmp_path / "in" / "x.mp4", tmp_path / "out" / "y.mp4"
    ft.set_title(src, "搬家前")
    ft.move(src, dst)
    assert ft.get(dst) == "搬家前"
    assert ft.get(src) == ""


def test_move_onto_an_existing_title_replaces_it(tmp_path):
    ft = _fresh(tmp_path)
    src, dst = tmp_path / "a.mp4", tmp_path / "b.mp4"
    ft.set_title(src, "來源")
    ft.set_title(dst, "會被取代")
    ft.move(src, dst)
    assert ft.get(dst) == "來源"


def test_move_tree_rewrites_the_prefix(tmp_path):
    ft = _fresh(tmp_path)
    old, new = tmp_path / "Ann", tmp_path / "Anna"
    ft.set_title(old / "1.mp4", "一")
    ft.set_title(old / "sub" / "2.mp4", "二")
    outside = tmp_path / "Annette" / "3.mp4"
    ft.set_title(outside, "不該被動到")

    assert ft.move_tree(old, new) == 2
    assert ft.get(new / "1.mp4") == "一"
    assert ft.get(new / "sub" / "2.mp4") == "二"
    assert ft.get(outside) == "不該被動到", \
        "前綴比對沒帶分隔符號的話，Ann 會連 Annette 一起改掉"


def test_move_tree_survives_underscore_and_percent(tmp_path):
    """LIKE 的萬用字元。資料夾名稱裡真的會有 _ 和 %。"""
    ft = _fresh(tmp_path)
    old = tmp_path / "a_b%c"
    ft.set_title(old / "v.mp4", "有")
    decoy = tmp_path / "aXbYc" / "v.mp4"
    ft.set_title(decoy, "不該被動到")
    assert ft.move_tree(old, tmp_path / "plain") == 1
    assert ft.get(tmp_path / "plain" / "v.mp4") == "有"
    assert ft.get(decoy) == "不該被動到"


def test_forget(tmp_path):
    ft = _fresh(tmp_path)
    p = tmp_path / "gone.mp4"
    ft.set_title(p, "刪檔前")
    ft.forget(p)
    assert ft.get(p) == "", \
        "留著的話，新檔案落在同一個路徑會直接繼承舊片名"


def test_all_titles_is_keyed_the_same_way(tmp_path):
    """Bulk display looks rows up by `videoflags.key(path)` — if `all_titles`
    returned raw paths instead, every lookup would silently miss."""
    ft = _fresh(tmp_path)
    from app.core import videoflags
    p = tmp_path / "Mixed" / "Case.mp4"
    ft.set_title(p, "片名")
    assert ft.all_titles().get(videoflags.key(p)) == "片名"


if __name__ == "__main__":
    import tempfile
    import traceback
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    bad = 0
    for name, fn in fns:
        with tempfile.TemporaryDirectory() as d:
            try:
                fn(Path(d))
                print("ok   %s" % name)
            except Exception:
                bad += 1
                print("FAIL %s" % name)
                traceback.print_exc()
    print("\n%d 通過 / %d 失敗" % (len(fns) - bad, bad))
    sys.exit(1 if bad else 0)
