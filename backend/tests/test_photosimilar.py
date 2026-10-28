"""The 相似 sort: grouping rules, and the endpoints driven for real.

Two halves, for the reason `test_iafd_apply.py` gives: the grouping can be
right while the wiring is wrong, and only a request finds out.

* Grouping, on hand-built hashes so the distances are exact: the threshold
  decides pairs, and the chain cap stops A~B~C from merging when A and C are
  far apart — the failure that produced a 48-photo group of unrelated sittings
  at threshold 80 on real data.
* Endpoints, on generated images in a temp folder: before a scan nothing is
  hashed, the scan fills the cache, the groups come back, applying stores the
  threshold, `/api/profile` reports it (that is what shows the button in browse
  mode), a viewer cannot scan or apply, and renaming the character carries the
  setting.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ok = fail = 0


def check(cond, what):
    global ok, fail
    if cond:
        ok += 1
        print("  ok   " + what)
    else:
        fail += 1
        print("  FAIL " + what)


# ── grouping on exact hashes ────────────────────────────────────────────────

def _flip(h: bytes, bits) -> bytes:
    b = bytearray(h)
    for i in bits:
        b[i // 8] ^= 1 << (i % 8)
    return bytes(b)


def grouping():
    from app.core import photosimilar
    import random

    rng = random.Random(1)
    base = bytes(rng.getrandbits(8) for _ in range(32))

    # A, B = A with 30 bits flipped, D unrelated
    a = base
    b = _flip(a, range(0, 30))
    d = bytes(rng.getrandbits(8) for _ in range(32))
    gs = photosimilar.groups([a, b, d], 70)
    check(gs == [[0, 1]], f"距離 30 的兩張成組、不相干的那張不入組（{gs}）")

    check(photosimilar.groups([a, b, d], 25) == [],
          "門檻低於兩張的距離就不成組")

    # Chain: A~B at 60, B~C at 60, but A~C at 120. At threshold 70 the cap is
    # 80, so only one of the two links may be used.
    b2 = _flip(a, range(0, 60))
    c2 = _flip(b2, range(60, 120))
    gs = photosimilar.groups([a, b2, c2], 70)
    check(len(gs) == 1 and len(gs[0]) == 2,
          f"A~B、B~C 都在門檻內但 A~C 太遠時不會串成三張（{gs}）")

    # Same shape, all within the cap: A~B 20, B~C 20, A~C 40 → one group of 3.
    b3 = _flip(a, range(0, 20))
    c3 = _flip(b3, range(20, 40))
    gs = photosimilar.groups([a, b3, c3], 70)
    check(gs == [[0, 1, 2]], f"組內都夠近時正常串成一組（{gs}）")

    # ── layer 2: colour ──
    import numpy as np

    def sig(weights):
        h = np.zeros(photosimilar.COLOR_BINS, dtype=np.uint16)
        for k, v in weights.items():
            h[k] = v
        return h.tobytes()

    far1 = bytes(rng.getrandbits(8) for _ in range(32))
    far2 = bytes(rng.getrandbits(8) for _ in range(32))
    far3 = bytes(rng.getrandbits(8) for _ in range(32))
    # Three photos layer 1 cannot link (random hashes ~128 apart): two share a
    # palette (a pose change of one outfit), the third does not.
    outfit = sig({5: 3000, 40: 900, 100: 196})
    pose2 = sig({5: 2900, 40: 1000, 100: 196})
    other = sig({150: 3000, 200: 1096})
    gs = photosimilar.groups([far1, far2, far3], 70, None, 0)
    check(gs == [], "只用構圖時，換姿勢的同造型連不起來")
    gs = photosimilar.groups([far1, far2, far3], 70, [outfit, pose2, other], 93)
    check(gs == [[0, 1]], f"打開顏色後同造型併在一起、別的造型不進來（{gs}）")
    gs = photosimilar.groups([far1, far2, far3], 70, [outfit, pose2, other], 0)
    check(gs == [], "顏色門檻 0 等於關閉")
    gs = photosimilar.groups([far1, far2, far3], 70, [outfit, None, other], 93)
    check(gs == [], "沒有顏色特徵的照片不參與顏色層")

    # Colour chain: A~B and B~C clear the link threshold, A~C falls below the
    # cross floor (link - slack) → only one merge.
    a_ = sig({0: 4000, 1: 96})
    b_ = sig({0: 3200, 1: 896})
    c_ = sig({0: 2000, 1: 2096})
    X = np.array([np.frombuffer(v, dtype=np.uint16) for v in (a_, b_, c_)], dtype=np.float32)
    X = np.sqrt(X / X.sum(axis=1, keepdims=True))
    ab, bc, ac = float(X[0] @ X[1]), float(X[1] @ X[2]), float(X[0] @ X[2])
    gs = photosimilar.groups([far1, far2, far3], 70, [a_, b_, c_], 93)
    if ab >= 0.93 and bc >= 0.93 and ac < 0.90:
        check(len(gs) == 1 and len(gs[0]) == 2,
              f"顏色串連：兩兩相鄰夠像但頭尾太遠時不串成三張（{ab:.3f}/{bc:.3f}/{ac:.3f} → {gs}）")
    else:
        check(False, f"顏色串連測試素材不成立（{ab:.3f}/{bc:.3f}/{ac:.3f}）")

    check(photosimilar.clamp_color(0) == 0, "顏色 0 保持關閉")
    check(photosimilar.clamp_color(50) == photosimilar.COLOR_MIN, "顏色下限會被夾住")
    check(photosimilar.clamp_color(120) == photosimilar.COLOR_MAX, "顏色上限會被夾住")

    check(photosimilar.clamp(500) == photosimilar.MAX_THRESHOLD, "門檻上限會被夾住")
    check(photosimilar.clamp(1) == photosimilar.MIN_THRESHOLD, "門檻下限會被夾住")
    check(photosimilar.clamp("x") == photosimilar.DEFAULT_THRESHOLD, "亂填的門檻退回預設")


# ── endpoints, for real ─────────────────────────────────────────────────────

def _image(path: Path, seed: int, shift: int = 0, tint=(1.0, 1.0, 1.0)):
    """A smooth random picture; `shift` nudges it so the dHash stays close,
    `tint` gives it a palette. Grey pictures all share one palette, and the
    colour layer then — correctly — calls them all the same outfit."""
    import numpy as np
    from PIL import Image
    rng = np.random.default_rng(seed)
    small = (rng.random((6, 6)) * 255).astype("uint8")
    im = Image.fromarray(small, "L").resize((320, 320), Image.BICUBIC)
    if shift:
        im = im.transform(im.size, Image.AFFINE, (1, 0, shift, 0, 1, 0))
    a = np.asarray(im, dtype=np.float32)[:, :, None] * np.array(tint, dtype=np.float32)
    Image.fromarray(a.clip(0, 255).astype("uint8"), "RGB").save(path, quality=92)


def endpoints():
    td = tempfile.mkdtemp(prefix="photosimilar_")
    try:
        tmp = Path(td)
        os.environ["VAM_DATA_DIR"] = str(tmp / "data")
        os.environ["VAM_CACHE_DIR"] = str(tmp / "cache")
        for m in [k for k in list(sys.modules) if k.startswith("app")]:
            del sys.modules[m]

        from fastapi.testclient import TestClient
        from app import auth, db, main

        # The live database got photo_similar on 2026-09-14 without the colour
        # column. Build exactly that shape first — a test that starts from the
        # current schema can never catch a missing migration.
        import sqlite3
        db.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        old = sqlite3.connect(str(db.DB_PATH))
        old.execute("CREATE TABLE photo_similar (folder TEXT PRIMARY KEY, "
                    "threshold INTEGER NOT NULL, applied_at TEXT)")
        old.execute("INSERT INTO photo_similar VALUES ('x:\legacy', 65, NULL)")
        old.commit()
        old.close()
        db.init_db()
        from app.core import photosimilar as ps
        check(ps.saved("x:\legacy") == {"threshold": 65, "color": None},
              "舊資料表遷移後讀得到，且舊資料的顏色是 None 不是 0")
        folder = tmp / "Someone"
        (folder / "P").mkdir(parents=True)
        red, blue = (1.0, 0.35, 0.3), (0.3, 0.45, 1.0)
        _image(folder / "P" / "burst_1.jpg", seed=7, tint=red)
        _image(folder / "P" / "burst_2.jpg", seed=7, shift=3, tint=red)
        _image(folder / "P" / "other.jpg", seed=99, tint=blue)
        # same red outfit, a different "pose": framing unrelated, palette shared
        _image(folder / "P" / "pose.jpg", seed=1234, tint=red)
        for i, n in enumerate(("burst_1.jpg", "burst_2.jpg", "other.jpg", "pose.jpg")):
            t = time.time() - 100 + i
            os.utime(folder / "P" / n, (t, t))

        real_allowed = main.is_allowed
        main.is_allowed = lambda p, u: True
        editor = {"id": 1, "role": "editor", "username": "e", "can_edit": True}
        viewer = {"id": 2, "role": "viewer", "username": "v", "can_edit": False}
        who = {"u": editor}
        main.app.dependency_overrides[auth.current_user] = lambda: who["u"]
        try:
            c = TestClient(main.app)
            q = f"folder={folder}"

            r = c.get(f"/api/profile?{q}")
            prof = r.json()
            check(r.status_code == 200 and prof.get("similar_threshold") is None,
                  "沒套用過時 /api/profile 回報 None（一般模式因此不顯示按鈕）")
            check(all("mtime" in m and "size" in m for m in prof["media"]),
                  "照片清單帶著修改時間與大小（排序要用）")

            s0 = c.get(f"/api/profile/similar?{q}").json()
            check(s0["cached"] == 0 and s0["missing"] == 4,
                  f"掃描前不讀任何照片：已快取 {s0['cached']}、缺 {s0['missing']}")

            who["u"] = viewer
            r = c.post("/api/profile/similar/scan", json={"folder": str(folder)})
            check(r.status_code == 403, f"一般帳號不能開始掃描（{r.status_code}）")
            r = c.post("/api/profile/similar/apply",
                       json={"folder": str(folder), "threshold": 60})
            check(r.status_code == 403, f"一般帳號不能套用門檻（{r.status_code}）")
            who["u"] = editor

            jid = c.post("/api/profile/similar/scan",
                         json={"folder": str(folder)}).json()["job_id"]
            for _ in range(100):
                j = c.get(f"/api/profile/similar/job/{jid}").json()
                if j["status"] != "running":
                    break
                time.sleep(0.05)
            check(j["status"] == "done" and j["result"]["hashed"] == 4,
                  f"掃描完成並算出 4 張（{j['status']}，{j.get('result')}）")

            s1 = c.get(f"/api/profile/similar?{q}&threshold=70&color=0").json()
            g = s1["groups"]
            p1, p2, po, pp = (str(folder / "P" / n) for n in
                              ("burst_1.jpg", "burst_2.jpg", "other.jpg", "pose.jpg"))
            check(s1["missing"] == 0, "掃描後沒有缺的")
            check(p1 in g and p2 in g and g[p1] == g[p2],
                  f"兩張連拍分在同一組（{g}）")
            check(po not in g, "不相干的那張不在任何組裡")
            check(pp not in g, "只看構圖時，換了姿勢的同色照片不會被拉進來")

            s2 = c.get(f"/api/profile/similar?{q}&threshold=70&color=93").json()
            g2 = s2["groups"]
            check(pp in g2 and p1 in g2 and g2[pp] == g2[p1],
                  f"打開顏色後，同色的換姿勢照片併進連拍那組（{sorted(g2.values())}）")
            check(po not in g2, "打開顏色後，別的配色仍然不進來")

            r = c.post("/api/profile/similar/apply",
                       json={"folder": str(folder), "threshold": 72, "color": 94})
            check(r.status_code == 200 and r.json() == {"threshold": 72, "color": 94},
                  f"套用門檻與顏色（{r.text[:60]}）")
            sv = c.get(f"/api/profile/similar?{q}").json()
            check(sv["color"] == 94 and sv["saved"] == {"threshold": 72, "color": 94},
                  "不帶參數查詢時用已存的顏色門檻")
            check(c.get(f"/api/profile?{q}").json()["similar_threshold"] == 72,
                  "/api/profile 回報已套用的門檻")
            check(c.get(f"/api/profile/similar?{q}").json()["threshold"] == 72,
                  "不帶門檻查詢時用已存的門檻")

            # Renaming the character must carry the setting, or the button
            # quietly disappears in browse mode.
            r = c.post("/api/character/rename",
                       json={"path": str(folder), "new_name": "Renamed"})
            moved = tmp / "Renamed"
            check(r.status_code == 200 and moved.is_dir(),
                  f"角色改名成功（{r.status_code} {r.text[:60]}）")
            check(c.get(f"/api/profile?folder={moved}").json()
                  .get("similar_threshold") == 72,
                  "改名後門檻跟著資料夾走")
        finally:
            main.is_allowed = real_allowed
            main.app.dependency_overrides.clear()
    finally:
        import shutil
        shutil.rmtree(td, ignore_errors=True)


if __name__ == "__main__":
    print("相似分組規則")
    grouping()
    print("\n相似排序端點")
    endpoints()
    print("\n%d 通過 / %d 失敗" % (ok, fail))
    sys.exit(1 if fail else 0)
