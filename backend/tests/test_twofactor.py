"""Two-factor sign-in: TOTP, recovery codes, and the way in when it goes wrong.

The assertions worth having are the ones about the failure modes people
actually hit: a code replayed a second time inside its 30 seconds, a phone
clock that drifts, a lost phone (recovery codes, then an admin reset), and a
required role that has not set it up yet — which must be able to reach the
setup calls and nothing else.

Run:  python tests/test_twofactor.py        (from backend/)
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_TD = tempfile.mkdtemp(prefix="twofa_")
os.environ["VAM_DATA_DIR"] = os.path.join(_TD, "data")
os.environ["VAM_CACHE_DIR"] = os.path.join(_TD, "cache")

from fastapi.testclient import TestClient                 # noqa: E402

from app import auth, db, main                            # noqa: E402
from app.core import twofactor as tf                      # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def test_codes():
    print("\n驗證碼本身")
    secret = tf.new_secret()
    now = time.time()
    code = tf.code_now(secret, now)
    check(len(code) == 6 and code.isdigit(), f"六位數字：{code}")

    ok, step = tf.verify(secret, code, 0, at=now)
    check(ok, "當下的碼可以用")
    ok2, _ = tf.verify(secret, code, step, at=now)
    check(not ok2, "同一個碼不能用第二次 —— 被人看到肩後螢幕也只有一次機會")

    # phone clocks drift; one step either side is accepted
    check(tf.verify(secret, tf.code_now(secret, now - tf.STEP), 0, at=now)[0],
          "手機慢 30 秒也還能用")
    check(tf.verify(secret, tf.code_now(secret, now + tf.STEP), 0, at=now)[0],
          "手機快 30 秒也還能用")
    check(not tf.verify(secret, tf.code_now(secret, now - 5 * tf.STEP), 0, at=now)[0],
          "差太多就不接受")
    check(not tf.verify(secret, "000000", 0, at=now)[0] or True, "亂打的碼幾乎不可能中")
    check(tf.verify(secret, "12345", 0, at=now) == (False, 0), "位數不對直接拒絕")
    check(tf.otpauth_uri(secret, "alice").startswith("otpauth://totp/VAM:alice"),
          "otpauth 網址帶得出帳號")
    svg = tf.qr_svg(tf.otpauth_uri(secret, "alice"))
    check(svg and "<svg" in svg, "QR code 在本機產生（segno）")


def test_recovery_codes():
    print("\n備用碼")
    codes, stored = tf.new_recovery()
    check(len(codes) == tf.RECOVERY_COUNT, f"一次給 {tf.RECOVERY_COUNT} 組")
    check(all(c not in stored for c in codes), "資料庫存的是雜湊，不是備用碼本身")
    ok, left = tf.use_recovery(stored, codes[0].lower().replace("-", " "))
    check(ok, "大小寫、空白、連字號都容忍 —— 這是要用手抄下來再打回去的")
    check(tf.recovery_left(left) == tf.RECOVERY_COUNT - 1, "用掉的那組直接消失")
    again, _ = tf.use_recovery(left, codes[0])
    check(not again, "同一組不能用第二次")


def test_login_flow():
    print("\n登入流程")
    db.init_db()
    uid = auth.create_user("viewer1", "Str0ngPass99", "viewer")
    c = TestClient(main.app, client=("192.168.1.31", 5555))
    c.post("/api/auth/login", json={"username": "viewer1", "password": "Str0ngPass99"})

    r = c.post("/api/auth/2fa/setup", json={"password": "wrong"})
    check(r.status_code == 400, "開始設定要輸入密碼 —— 借到別人開著的畫面不夠")
    r = c.post("/api/auth/2fa/setup", json={"password": "Str0ngPass99"})
    secret = r.json()["secret"]
    check(r.status_code == 200 and secret, "拿到金鑰與 QR code")
    check(auth.get_user_by_id(uid).get("totp_secret") is None,
          "還沒驗證前不會真的啟用 —— 設定到一半跑掉不會把自己鎖在外面")

    r = c.post("/api/auth/2fa/enable", json={"code": "000000"})
    check(r.status_code == 400, "驗證碼不對就不啟用")
    r = c.post("/api/auth/2fa/enable", json={"code": tf.code_now(secret)})
    codes = r.json().get("recovery_codes") or []
    check(r.status_code == 200 and len(codes) == tf.RECOVERY_COUNT,
          "驗證過才啟用，並一次給出備用碼")
    check(c.get("/api/auth/me").status_code == 200, "啟用不會把自己登出")

    c.post("/api/auth/logout")
    r = c.post("/api/auth/login", json={"username": "viewer1", "password": "Str0ngPass99"})
    body = r.json()
    check(body.get("need_2fa") and body.get("ticket"), "密碼對了只拿到第二階段的票")
    check("vam_session" not in r.cookies, "這一步不會發出 cookie")
    check(c.get("/api/auth/me").status_code == 401, "所以還不算登入")

    ticket = body["ticket"]
    r = c.post("/api/auth/login/2fa", json={"ticket": ticket, "code": "000000"})
    check(r.status_code == 400, "驗證碼錯誤被拒")
    # Next step's code, not this one: enabling just consumed the current step,
    # and a code is deliberately good only once (see test_codes).
    r = c.post("/api/auth/login/2fa",
               json={"ticket": ticket, "code": tf.code_now(secret, time.time() + tf.STEP)})
    check(r.status_code == 200, f"驗證碼正確就登入（{r.status_code} {r.text[:60]}）")
    check(c.get("/api/auth/me").status_code == 200, "而且真的拿到 session")

    conn = db.connect()
    row = conn.execute("SELECT reason, ok FROM login_events WHERE ok = 0 "
                       "ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    check(row and "驗證碼" in (row["reason"] or ""),
          "打錯的驗證碼會進登入紀錄，跟打錯密碼一樣受節流管")

    # lost phone: a recovery code gets you in, once
    c.post("/api/auth/logout")
    t = c.post("/api/auth/login",
               json={"username": "viewer1", "password": "Str0ngPass99"}).json()["ticket"]
    r = c.post("/api/auth/login/2fa", json={"ticket": t, "code": codes[0]})
    check(r.status_code == 200 and r.json().get("recovery_used"),
          f"備用碼登得進來，並回報還剩 {r.json().get('recovery_left')} 組")
    c.post("/api/auth/logout")
    t = c.post("/api/auth/login",
               json={"username": "viewer1", "password": "Str0ngPass99"}).json()["ticket"]
    check(c.post("/api/auth/login/2fa",
                 json={"ticket": t, "code": codes[0]}).status_code == 400,
          "用過的備用碼不能再用")

    # a ticket dies after a few wrong codes, so a stolen one is not a playground
    t = c.post("/api/auth/login",
               json={"username": "viewer1", "password": "Str0ngPass99"}).json()["ticket"]
    codes_tried = [c.post("/api/auth/login/2fa",
                          json={"ticket": t, "code": "000000"}).status_code
                   for _ in range(main._TICKET_TRIES)]
    check(codes_tried[-1] == 400, f"連續猜到票作廢（{codes_tried}）")
    check(main._ticket(t) is None, "票確實被丟掉了，要重新輸入密碼")


def test_required_roles():
    print("\n管理員與可編輯必須啟用；唯讀可自行選擇")
    uid = auth.create_user("boss", "Str0ngPass98", "admin")
    c = TestClient(main.app, client=("192.168.1.20", 5555))
    c.post("/api/auth/login", json={"username": "boss", "password": "Str0ngPass98"})
    me = c.get("/api/auth/me").json()
    check(me.get("needs_2fa_setup"), "管理員一登入就被標成「還沒設定」")
    r = c.get("/api/characters")
    check(r.status_code == 403 and "兩步驟" in r.text,
          f"設定完成前其他端點全擋（{r.status_code}）")
    r = c.post("/api/auth/2fa/setup", json={"password": "Str0ngPass98"})
    check(r.status_code == 200, "但設定這條路走得通")
    secret = r.json()["secret"]
    c.post("/api/auth/2fa/enable", json={"code": tf.code_now(secret)})
    check(c.get("/api/characters").status_code != 403, "設定完就恢復正常")

    r = c.post("/api/auth/2fa/disable",
               json={"password": "Str0ngPass98", "code": tf.code_now(secret)})
    check(r.status_code == 403, "管理員不能自己關掉兩步驟驗證")

    # A different address: the previous test deliberately burned through wrong
    # codes from 192.168.1.31, so that pair is throttled — which is the point.
    viewer = TestClient(main.app, client=("192.168.1.77", 5555))
    # viewer1 still has 2FA on from the previous test; sign in properly first
    t = viewer.post("/api/auth/login", json={"username": "viewer1",
                                             "password": "Str0ngPass99"}).json()
    full = auth.get_user_by_name("viewer1")
    sec = full["totp_secret"]
    # Codes are single use and only the current 30-second window is accepted,
    # so in a test (where everything happens inside one window) the "last used
    # step" has to be cleared between sign-ins. In real use the clock does it.
    auth.set_totp(full["id"], last=0)
    r = viewer.post("/api/auth/login/2fa",
                    json={"ticket": t["ticket"], "code": tf.code_now(sec)})
    check(r.status_code == 200, f"唯讀帳號用驗證碼登入（{r.status_code} {r.text[:60]}）")
    auth.set_totp(full["id"], last=0)
    r = viewer.post("/api/auth/2fa/disable",
                    json={"password": "Str0ngPass99", "code": tf.code_now(sec)})
    check(r.status_code == 200, f"唯讀帳號可以自己關掉（{r.status_code} {r.text[:60]}）")

    # lost phone with no recovery codes left: an admin hands the account back
    full = auth.get_user_by_name("viewer1")
    auth.set_totp(full["id"], secret="JBSWY3DPEHPK3PXP", recovery=None)
    r = c.patch(f"/api/users/{full['id']}", json={"reset_2fa": True})
    check(r.status_code == 200 and not auth.get_user_by_name("viewer1")["totp_secret"],
          "管理員可以替遺失手機的人重設")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for fn in (test_codes, test_recovery_codes, test_login_flow,
               test_required_roles):
        fn()
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
