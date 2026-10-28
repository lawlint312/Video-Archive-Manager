"""Security alerts and the account's own device list.

The alert worth having is the one no failure count can produce: a *successful*
sign-in from an address that account has never used. A leaked password looks
exactly like the owner signing in, so that is the only signal there is.

Run:  python tests/test_alerts.py        (from backend/)
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_TD = tempfile.mkdtemp(prefix="alerts_")
os.environ["VAM_DATA_DIR"] = os.path.join(_TD, "data")
os.environ["VAM_CACHE_DIR"] = os.path.join(_TD, "cache")

from fastapi.testclient import TestClient                 # noqa: E402

from app import auth, db, main                            # noqa: E402
from app.core import alerts                               # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def kinds():
    conn = db.connect()
    try:
        return [r["kind"] for r in conn.execute(
            "SELECT kind FROM security_alerts ORDER BY id")]
    finally:
        conn.close()


def test_alerts():
    print("\n異常通知")
    db.init_db()
    auth.TWOFA_ENFORCED = False          # covered in test_twofactor.py
    auth.create_user("boss", "Str0ngPass98", "admin")
    auth.create_user("viewer1", "Str0ngPass99", "viewer")

    home = TestClient(main.app, client=("192.168.1.31", 5555))
    home.post("/api/auth/login", json={"username": "viewer1",
                                       "password": "Str0ngPass99"})
    check(alerts.NEW_IP in kinds(), "第一次從某個位址登入成功 → 通知")

    home.post("/api/auth/logout")
    home.post("/api/auth/login", json={"username": "viewer1",
                                       "password": "Str0ngPass99"})
    check(kinds().count(alerts.NEW_IP) == 1, "同一個位址再登入就不再通知")

    away = TestClient(main.app, client=("10.8.0.9", 5555))
    away.post("/api/auth/login", json={"username": "viewer1",
                                       "password": "Str0ngPass99"})
    check(kinds().count(alerts.NEW_IP) == 2,
          "換一個沒用過的位址登入成功 → 通知（密碼外洩時只會看到成功，不會有失敗）")

    stranger = TestClient(main.app, client=("192.168.1.99", 5555))
    stranger.post("/api/auth/login", json={"username": "nosuchuser",
                                           "password": "whatever"})
    check(alerts.UNKNOWN_USER in kinds(), "嘗試不存在的帳號 → 通知")
    for _ in range(6):
        stranger.post("/api/auth/login", json={"username": "nosuchuser",
                                               "password": "whatever"})
    check(kinds().count(alerts.UNKNOWN_USER) == 1,
          "一小時內同一組只通知一次 —— 否則真正重要的那筆會被洗掉")

    for i in range(6):
        stranger.post("/api/auth/login", json={"username": "viewer1",
                                               "password": "wrongpass"})
    check(alerts.LOCKED in kinds(), "連續失敗到開始鎖定 → 通知")

    admin = TestClient(main.app, client=("192.168.1.20", 5555))
    admin.post("/api/auth/login", json={"username": "boss",
                                        "password": "Str0ngPass98"})
    r = admin.get("/api/security/alerts").json()
    check(r["unseen"] == len(r["alerts"]), f"管理員看到 {r['unseen']} 筆未讀")
    check(all(a["label"] for a in r["alerts"]), "每一筆都有看得懂的說明文字")
    admin.post("/api/security/alerts/seen")
    check(admin.get("/api/security/alerts").json()["unseen"] == 0, "標記已讀之後歸零")

    viewer = TestClient(main.app, client=("192.168.1.31", 5555))
    viewer.post("/api/auth/login", json={"username": "viewer1",
                                         "password": "Str0ngPass99"})
    check(viewer.get("/api/security/alerts").status_code == 403,
          "一般帳號看不到這份清單")


def test_device_list():
    print("\n登入中的裝置")
    phone = TestClient(main.app, client=("192.168.1.55", 5555))
    phone.post("/api/auth/login", json={"username": "viewer1",
                                        "password": "Str0ngPass99"},
               headers={"user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) "
                                      "AppleWebKit/605 Version/17.0 Safari/605"})
    desktop = TestClient(main.app, client=("192.168.1.31", 5555))
    desktop.post("/api/auth/login", json={"username": "viewer1",
                                          "password": "Str0ngPass99"},
                 headers={"user-agent": "Mozilla/5.0 (Windows NT 10.0) "
                                        "AppleWebKit/537 Chrome/140 Safari/537"})

    rows = desktop.get("/api/auth/sessions").json()["sessions"]
    check(len(rows) >= 2, f"兩台裝置都列出來（{len(rows)} 筆）")
    check(sum(1 for r in rows if r["current"]) == 1, "只有一筆標成「這台」")
    devices = {r["device"] for r in rows}
    check(any("iPhone" in d for d in devices) and any("Windows" in d for d in devices),
          f"認得出是什麼裝置：{sorted(devices)}")
    check(all(r["ip"] for r in rows), "每一筆都記得住來源位址")

    lost = next(r for r in rows if not r["current"])
    check(desktop.delete(f"/api/auth/sessions/{lost['id']}").status_code == 200,
          "可以單獨把另一台登出")
    check(phone.get("/api/auth/me").status_code == 401, "那台真的被登出了")
    check(desktop.get("/api/auth/me").status_code == 200, "自己這台不受影響")

    # one account must not be able to revoke another's session
    other = TestClient(main.app, client=("192.168.1.20", 5555))
    other.post("/api/auth/login", json={"username": "boss", "password": "Str0ngPass98"})
    mine = [r for r in desktop.get("/api/auth/sessions").json()["sessions"]
            if r["current"]][0]
    check(other.delete(f"/api/auth/sessions/{mine['id']}").status_code == 404,
          "別人的登入動不了 —— 這個端點只作用在自己的帳號上")
    check(desktop.get("/api/auth/me").status_code == 200, "而且確實沒被登出")


def test_agent_names():
    print("\n裝置名稱")
    cases = {
        "Mozilla/5.0 (iPhone) Version/17 Safari/605": "iPhone",
        "Mozilla/5.0 (Windows NT 10.0) Chrome/140 Safari/537": "Windows",
        "Mozilla/5.0 (Macintosh) Firefox/130": "Mac",
        "Mozilla/5.0 (Windows NT 10.0) Chrome/140 Edg/140": "Edge",
        "": "未知裝置",
    }
    for ua, want in cases.items():
        got = alerts.describe_agent(ua)
        check(want in got, f"{want} ← {got}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for fn in (test_alerts, test_device_list, test_agent_names):
        fn()
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
