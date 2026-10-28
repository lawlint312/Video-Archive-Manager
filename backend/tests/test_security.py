"""Login hardening: password rules, throttling, audit trail, IP allowlists.

Written because the service turned out to be reachable from the internet over
plain HTTP with a 6-character password minimum and *no record of any login
attempt at all*. Measured first: argon2 costs 41 ms per verification here, so
one thread manages ~24 guesses a second — a 10-million common-password list
would have taken 57 hours, and nothing would have shown it happening.

The assertions that earn their keep are the ones about the two ways this kind
of control fails in practice: locking out the wrong person (a throttle keyed
only on username lets anyone freeze a real account), and locking out yourself
(an allowlist that omits the address you are sitting at).

Run:  python tests/test_security.py        (from backend/)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sqlite3                                          # noqa: E402

from app import security                                # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def memdb():
    """The login_events table alone, in memory — no real DB is touched."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE login_events (
        id INTEGER PRIMARY KEY, at TEXT NOT NULL, username TEXT NOT NULL,
        ip TEXT NOT NULL, ok INTEGER NOT NULL, reason TEXT)""")
    return conn


def test_password_policy():
    print("\n密碼規則：8 字元以上，含大小寫與數字")
    bad = {
        "Ab1": "太短",
        "abcdefg1": "沒有大寫",
        "ABCDEFG1": "沒有小寫",
        "Abcdefgh": "沒有數字",
        "": "空白",
    }
    for pw, why in bad.items():
        check(security.password_problem(pw) is not None, f"擋掉{why}：{pw!r}")
    # "Abcdefg1" satisfies every rule and is still on the common list — see
    # test_weak_password_check, which is where that behaviour belongs.
    for pw in ("Tr0ub4dor&3", "長密碼Abc12345", "Str0ngPass99"):
        check(security.password_problem(pw) is None, f"接受 {pw!r}")
    # the message has to say what is missing, or the user just guesses again
    msg = security.password_problem("abcdefgh")
    check("大寫" in msg and "數字" in msg, f"訊息說明缺什麼：{msg}")


def test_throttle():
    print("\n節流：連續失敗後要開始等待")
    conn = memdb()
    u, ip = "admin", "203.0.113.9"
    for i in range(security.FREE_TRIES):
        check(security.throttle_check(conn, u, ip) is None,
              f"第 {i+1} 次還放行（前 {security.FREE_TRIES} 次不擋）")
        security.record_attempt(conn, u, ip, False, "wrong")
    wait = security.throttle_check(conn, u, ip)
    check(wait and wait > 0, f"第 {security.FREE_TRIES+1} 次要等 {wait} 秒")

    print("\n延遲要隨失敗次數加長")
    steps = [security.lock_seconds(n) for n in range(security.FREE_TRIES,
                                                     security.FREE_TRIES + 5)]
    check(steps == sorted(steps) and steps[0] < steps[-1],
          f"逐步拉長：{steps}")
    check(security.lock_seconds(security.FREE_TRIES - 1) == 0,
          "還沒達到門檻就是 0")


def test_success_clears_the_slate():
    print("\n登入成功要清掉之前的失敗紀錄")
    conn = memdb()
    u, ip = "alice", "192.168.1.31"
    for _ in range(security.FREE_TRIES + 2):
        security.record_attempt(conn, u, ip, False, "wrong")
    check(security.throttle_check(conn, u, ip) is not None, "先被擋住")
    security.record_attempt(conn, u, ip, True)
    check(security.recent_failures(conn, u, ip) == 0,
          "成功之後計數歸零 —— 不然打錯幾次的人一整天都要被罰")
    check(security.throttle_check(conn, u, ip) is None, "而且立刻能再登入")


def test_blocked_retries_do_not_extend_the_lock():
    print("\n鎖定期間再按，不會把懲罰越加越長")
    conn = memdb()
    u, ip = "alice", "192.168.1.20"
    for _ in range(security.FREE_TRIES):
        security.record_attempt(conn, u, ip, False, "帳號或密碼錯誤")
    first = security.throttle_check(conn, u, ip)
    # the owner mistypes, sees the message, and clicks a few more times
    for _ in range(3):
        w = security.throttle_check(conn, u, ip)
        security.record_attempt(conn, u, ip, False, f"{security.BLOCKED}（還有 {w} 秒）")
    after = security.throttle_check(conn, u, ip)
    check(after is not None and after <= first,
          f"等待時間沒有變長：{first} 秒 → {after} 秒")
    # ...and they are still recorded, because the audit trail wants them
    n = conn.execute("SELECT COUNT(*) FROM login_events").fetchone()[0]
    check(n == security.FREE_TRIES + 3, f"但每一次都有留下紀錄（{n} 筆）")


def test_throttle_cannot_lock_a_victim_out():
    print("\n別人猜你的帳號，不能害你自己登不進來")
    conn = memdb()
    victim, attacker_ip, my_ip = "admin", "198.51.100.7", "192.168.1.20"
    for _ in range(20):
        security.record_attempt(conn, victim, attacker_ip, False, "wrong")
    check(security.throttle_check(conn, victim, attacker_ip) is not None,
          "攻擊者那一邊被鎖住")
    wait = security.throttle_check(conn, victim, my_ip)
    # Not None any more: 20 failures against one account now cost everyone a
    # short pause (see ACCOUNT_DELAY). The point is that it stays a pause.
    check(wait is None or wait <= security.ACCOUNT_DELAY + 1,
          f"你從自己的位址最多等 {security.ACCOUNT_DELAY} 秒（實際 {wait}）—— "
          "只看帳號而且鎖死的話，這就變成阻斷服務")


def test_same_microsecond_ordering():
    print("\n同一微秒內的多筆紀錄也要排得出先後")
    conn = memdb()
    u, ip = "admin", "10.0.0.9"
    # a script hammering the endpoint lands several rows on the same timestamp;
    # ordering by `at` then puts them in an arbitrary order and a success can
    # sort behind the failures it was supposed to clear
    for _ in range(7):
        security.record_attempt(conn, u, ip, False, "wrong")
    security.record_attempt(conn, u, ip, True)
    stamps = {r["at"] for r in conn.execute("SELECT at FROM login_events")}
    check(security.recent_failures(conn, u, ip) == 0,
          f"成功仍然排在最前面（這批共 {len(stamps)} 個不同的時間戳）")


def test_audit_records_everything():
    print("\n稽核：成功與失敗都要留下紀錄")
    conn = memdb()
    security.record_attempt(conn, "admin", "1.2.3.4", True)
    security.record_attempt(conn, "root", "5.6.7.8", False, "帳號或密碼錯誤")
    rows = conn.execute("SELECT * FROM login_events ORDER BY id").fetchall()
    check(len(rows) == 2, "兩筆都在")
    check(rows[1]["username"] == "root",
          "失敗時記下輸入的帳號 —— 猜不存在的帳號正是被攻擊的訊號")
    check(rows[1]["ip"] == "5.6.7.8" and rows[1]["reason"], "來源與原因都有")


def test_ip_allowlist():
    print("\nIP 白名單")
    check(security.ip_allowed("8.8.8.8", None), "空白＝不限制（跟資料夾範圍同一套慣例）")
    check(security.ip_allowed("8.8.8.8", []), "空清單也是不限制")
    allow = ["192.168.1.0/24", "203.0.113.5"]
    check(security.ip_allowed("192.168.1.31", allow), "網段內放行")
    check(security.ip_allowed("203.0.113.5", allow), "單一位址等同 /32")
    check(not security.ip_allowed("192.168.51.31", allow), "隔壁網段擋掉")
    check(not security.ip_allowed("unknown", allow),
          "來源解析不出來時擋掉，不要猜")
    import json
    check(security.ip_allowed("192.168.1.9", json.dumps(allow)),
          "資料庫存的是 JSON 字串，也要吃得下")

    print("\n白名單格式檢查")
    clean, problem = security.validate_allowlist(["192.168.1.0/24", " 10.0.0.1 "])
    check(problem is None and clean == ["192.168.1.0/24", "10.0.0.1/32"],
          f"正規化：{clean}")
    _, problem = security.validate_allowlist(["這不是IP"])
    check(problem and "這不是IP" in problem, f"說清楚哪一筆看不懂：{problem}")


def test_forwarded_header_is_not_trusted_by_default():
    print("\nX-Forwarded-For 預設不能信")

    class Req:
        def __init__(self, hdr, peer):
            self.headers = {"x-forwarded-for": hdr} if hdr else {}
            self.client = type("C", (), {"host": peer})()

    r = Req("192.168.1.31", "203.0.113.9")
    check(security.client_ip(r) == "203.0.113.9",
          "預設用真實連線來源 —— 否則任何人送一個標頭就能穿過白名單")
    check(security.client_ip(r, trust_proxy=True) == "203.0.113.9",
          "就算開了信任代理，直接從外網連進來的請求送的標頭也不採用")
    rp = Req("192.168.1.31", "127.0.0.1")
    check(security.client_ip(rp, trust_proxy=True) == "192.168.1.31",
          "只有來自本機代理（loopback）的請求才採用標頭")
    r2 = Req("10.0.0.1, 203.0.113.9", "127.0.0.1")
    check(security.client_ip(r2, trust_proxy=True) == "10.0.0.1",
          "多層代理時取最左邊的原始用戶端")
    check(security.client_ip(Req(None, None)) == "unknown", "沒有來源時不會爆掉")


def test_local_sources():
    print("\n哪些來源算內網或 VPN")
    for ip in ("127.0.0.1", "::1", "192.168.1.20", "10.8.0.2", "172.16.5.1",
               "100.100.1.1", "169.254.3.4", "fe80::1", "fd00::5",
               "::ffff:192.168.1.123"):
        check(security.is_local_source(ip), f"{ip} 放行")
    for ip in ("9.9.9.9", "8.8.8.8", "1.1.1.1", "2001:4860::8888",
               "::ffff:9.9.9.9"):
        check(not security.is_local_source(ip), f"{ip} 擋下")
    check(security.is_local_source("testclient"),
          "不是 IP 的來源（只存在於行程內的測試用戶端）放行")


def test_lan_only_middleware():
    print("\n全站只接受內網：真的打端點")
    import os, tempfile
    td = tempfile.mkdtemp(prefix="lanonly_")
    os.environ["VAM_DATA_DIR"] = os.path.join(td, "data")
    os.environ["VAM_CACHE_DIR"] = os.path.join(td, "cache")
    for m in [k for k in list(sys.modules) if k.startswith("app")]:
        del sys.modules[m]
    from fastapi.testclient import TestClient
    from app import db, main
    from app import settings as app_settings
    db.init_db()

    outside = TestClient(main.app, client=("9.9.9.9", 5555))
    r = outside.post("/api/auth/login", json={"username": "admin", "password": "x"})
    check(r.status_code == 403 and "內網" in r.text,
          f"外網連登入頁都拿不到（{r.status_code}）—— 能看到登入頁就能猜密碼")
    check(outside.get("/").status_code == 403, "外網連首頁也拿不到")

    home = TestClient(main.app, client=("192.168.1.123", 5555))
    check(home.post("/api/auth/login",
                    json={"username": "admin", "password": "x"}).status_code != 403,
          "內網照常可以走到登入")
    vpn = TestClient(main.app, client=("10.8.0.2", 5555))
    check(vpn.get("/").status_code != 403, "VPN 位址照常可以連")

    # A spoofed header from outside must not pass, even with trust_proxy on.
    app_settings.save({"trust_proxy": True})
    try:
        r = outside.get("/", headers={"X-Forwarded-For": "192.168.1.5"})
        check(r.status_code == 403, "外網偽造 X-Forwarded-For 仍然被擋")
        proxy = TestClient(main.app, client=("127.0.0.1", 5555))
        r = proxy.get("/", headers={"X-Forwarded-For": "9.9.9.9"})
        check(r.status_code == 403, "經由本機代理轉進來的外網請求照樣被擋")
        r = proxy.get("/", headers={"X-Forwarded-For": "192.168.1.123"})
        check(r.status_code != 403, "經由本機代理轉進來的內網請求放行")
    finally:
        app_settings.save({"trust_proxy": False})

    app_settings.save({"lan_only": False})
    try:
        check(outside.get("/").status_code != 403, "明確關掉 lan_only 時才對外開放")
    finally:
        app_settings.save({"lan_only": True})


def test_change_own_password():
    print("\n自己修改密碼：真的打端點")
    import os, tempfile
    td = tempfile.mkdtemp(prefix="ownpw_")
    os.environ["VAM_DATA_DIR"] = os.path.join(td, "data")
    os.environ["VAM_CACHE_DIR"] = os.path.join(td, "cache")
    for m in [k for k in list(sys.modules) if k.startswith("app")]:
        del sys.modules[m]
    from fastapi.testclient import TestClient
    from app import auth, db, main
    db.init_db()
    auth.create_user("viewer1", "OldPass123", "viewer")

    c = TestClient(main.app, client=("192.168.1.123", 5555))
    check(c.post("/api/auth/password", json={"current_password": "OldPass123",
                                             "new_password": "NewPass456"}
                 ).status_code == 401, "沒登入不能改")
    check(c.post("/api/auth/login", json={"username": "viewer1",
                                          "password": "OldPass123"}).status_code == 200,
          "唯讀帳號登入")
    other = TestClient(main.app, client=("192.168.1.77", 5555))
    other.post("/api/auth/login", json={"username": "viewer1", "password": "OldPass123"})
    check(other.get("/api/auth/me").status_code == 200, "另一台裝置也登入著")

    r = c.post("/api/auth/password", json={"current_password": "wrong",
                                           "new_password": "NewPass456"})
    check(r.status_code == 400 and "目前的密碼" in r.text, "目前密碼錯誤被拒")
    check(c.get("/api/auth/me").status_code == 200, "打錯目前密碼不會被登出")
    r = c.post("/api/auth/password", json={"current_password": "OldPass123",
                                           "new_password": "short"})
    check(r.status_code == 400 and "8" in r.json()["detail"], "新密碼不符規則被拒並說明原因")
    r = c.post("/api/auth/password", json={"current_password": "OldPass123",
                                           "new_password": "OldPass123"})
    check(r.status_code == 400, "新舊密碼相同被拒")

    r = c.post("/api/auth/password", json={"current_password": "OldPass123",
                                           "new_password": "NewPass456"})
    check(r.status_code == 200, "正確修改成功")
    check(c.get("/api/auth/me").status_code == 401, "改完這台被登出")
    check(other.get("/api/auth/me").status_code == 401, "改完其他裝置也被登出")
    check(c.post("/api/auth/login", json={"username": "viewer1",
                                          "password": "OldPass123"}).status_code == 401,
          "舊密碼不能再登入")
    check(c.post("/api/auth/login", json={"username": "viewer1",
                                          "password": "NewPass456"}).status_code == 200,
          "新密碼可以登入")

    # Guessing through this endpoint is throttled like the login form.
    codes = [c.post("/api/auth/password",
                    json={"current_password": f"guess{i}",
                          "new_password": "Another789"}).status_code
             for i in range(security.FREE_TRIES + 1)]
    check(codes[-1] == 429, f"連續猜目前密碼會被鎖（{codes}）")
    r = TestClient(main.app, client=("192.168.1.123", 5555)).post(
        "/api/auth/login", json={"username": "viewer1", "password": "NewPass456"})
    check(r.status_code == 429, "同一來源的登入也一起被鎖，不能換個入口繼續猜")
    check('value="admin"' not in c.get("/test").text, "舊的 /test 測試頁已移除")


def test_public_hardening():
    print("\n對外開放後的加強：API 文件頁與登入有效期")
    import os, tempfile
    from datetime import datetime, timedelta, timezone
    td = tempfile.mkdtemp(prefix="harden_")
    os.environ["VAM_DATA_DIR"] = os.path.join(td, "data")
    os.environ["VAM_CACHE_DIR"] = os.path.join(td, "cache")
    os.environ.pop("VAM_SESSION_DAYS", None)
    for m in [k for k in list(sys.modules) if k.startswith("app")]:
        del sys.modules[m]
    from fastapi.testclient import TestClient
    from app import auth, config, db, main
    db.init_db()

    c = TestClient(main.app, client=("192.168.1.123", 5555))
    for path in ("/docs", "/redoc", "/openapi.json"):
        r = c.get(path)
        check("swagger" not in r.text.lower() and '"openapi"' not in r.text,
              f"{path} 不再公開 API 清單（{r.status_code}）")

    check(config.SESSION_DAYS == 7, f"登入有效期預設 7 天（{config.SESSION_DAYS}）")
    uid = auth.create_user("u1", "OldPass123", "viewer")
    fresh = auth.create_session(uid)
    old = auth.create_session(uid)
    # An old session issued under the 30-day rule: expires_at still in the
    # future, created 10 days ago. The new limit must apply to it too.
    ten_days_ago = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    conn = db.connect()
    conn.execute("UPDATE sessions SET created_at = ?, expires_at = ? WHERE token_hash = ?",
                 (ten_days_ago,
                  (datetime.now(timezone.utc) + timedelta(days=20)).isoformat(),
                  auth._hash_token(old)))
    conn.commit()
    conn.close()
    check(auth.user_for_token(fresh) is not None, "剛登入的有效")
    check(auth.user_for_token(old) is None,
          "舊規則發出、已登入 10 天的也失效（不因 expires_at 還沒到而繼續有效）")
    r = c.post("/api/auth/login", json={"username": "u1", "password": "OldPass123"})
    cookie = r.headers.get("set-cookie", "")
    check(f"Max-Age={7 * 86400}" in cookie, "登入 cookie 的有效期也是 7 天")


def test_disabled_account():
    print("\n停用帳號：保留資料但不能登入")
    import os, tempfile
    td = tempfile.mkdtemp(prefix="disabled_")
    os.environ["VAM_DATA_DIR"] = os.path.join(td, "data")
    os.environ["VAM_CACHE_DIR"] = os.path.join(td, "cache")
    for m in [k for k in list(sys.modules) if k.startswith("app")]:
        del sys.modules[m]
    from fastapi.testclient import TestClient
    from app import auth, db, main
    db.init_db()
    # Two-factor is off for this test: it is about the disabled flag, and the
    # required-role gate has its own file (test_twofactor.py).
    auth.TWOFA_ENFORCED = False
    boss = auth.create_user("boss", "AdminPass123", "admin")
    other = auth.create_user("helper", "HelpPass123", "editor")

    admin = TestClient(main.app, client=("192.168.1.20", 5555))
    admin.post("/api/auth/login", json={"username": "boss", "password": "AdminPass123"})
    user = TestClient(main.app, client=("192.168.1.31", 5555))
    user.post("/api/auth/login", json={"username": "helper", "password": "HelpPass123"})
    check(user.get("/api/auth/me").status_code == 200, "停用前登入著")

    r = admin.patch(f"/api/users/{other}", json={"disabled": True})
    check(r.status_code == 200, f"管理員可以停用別人（{r.status_code}）")
    check(user.get("/api/auth/me").status_code == 401, "停用後現有登入立刻失效")
    r = user.post("/api/auth/login", json={"username": "helper", "password": "HelpPass123"})
    check(r.status_code == 403 and "停用" in r.text, "密碼正確也登不進來，而且說得出原因")
    check(auth.get_user_by_name("helper") is not None, "帳號本身還在，資料沒被刪掉")

    conn = db.connect()
    row = conn.execute("SELECT reason FROM login_events WHERE ok = 0 "
                       "ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    check(row and "停用" in (row["reason"] or ""), "被拒的原因有進登入紀錄")

    r = admin.patch(f"/api/users/{boss}", json={"disabled": True})
    check(r.status_code == 400 and "自己" in r.text, "不能停用自己")
    r = admin.patch(f"/api/users/{other}", json={"disabled": False})
    check(r.status_code == 200, "可以再啟用")
    check(user.post("/api/auth/login",
                    json={"username": "helper", "password": "HelpPass123"}
                    ).status_code == 200, "啟用後又能登入")

    # The last admin must not be able to vanish behind a disabled flag either.
    admin2 = auth.create_user("boss2", "AdminPass456", "admin")
    admin.patch(f"/api/users/{admin2}", json={"disabled": True})
    r = admin.patch(f"/api/users/{admin2}", json={"role": "viewer"})
    check(auth.count_admins() == 1,
          "已停用的管理員不算在「還剩幾個管理員」裡 —— 否則最後一個可用管理員會被降權")


def test_spraying_one_address_many_accounts():
    print("\n同一個位址猜遍多個帳號，也要被擋下來")
    conn = memdb()
    ip = "203.0.113.77"
    # five tries each against several accounts keeps every (account, ip) pair
    # under its own limit -- the pair rule alone never fires
    names = ["admin", "alice", "mike", "wang", "guest"]
    for name in names:
        for _ in range(security.FREE_TRIES - 1):
            security.record_attempt(conn, name, ip, False, "帳號或密碼錯誤")
    per_pair = [security.lock_seconds(security.recent_failures(conn, n, ip))
                for n in names]
    check(all(w == 0 for w in per_pair),
          f"每一組「帳號+位址」都還沒到自己的門檻（{per_pair}）")
    total = security.FREE_TRIES - 1
    check(total * len(names) >= security.IP_FREE_TRIES,
          f"但這個位址總共已經失敗 {total * len(names)} 次")
    wait, why = security.throttle_reason(conn, "someone_new", ip)
    check(wait and "位址" in why, f"改用位址計算就擋住了：{wait} 秒（{why}）")
    check(security.throttle_check(conn, "admin", "192.168.1.31") is None,
          "別的位址不受影響 —— 擋的是那個攻擊來源，不是那些帳號")


def test_one_account_from_many_addresses_is_slowed_not_locked():
    print("\n同一帳號被很多位址輪流猜：只放慢，不鎖死")
    conn = memdb()
    victim = "alice"
    for i in range(security.ACCOUNT_FREE_TRIES + 2):
        security.record_attempt(conn, victim, f"203.0.113.{i}", False, "帳號或密碼錯誤")
    wait, why = security.throttle_reason(conn, victim, "192.168.1.20")
    check(wait and "帳號" in why, f"本人這邊也會被要求稍候：{wait} 秒（{why}）")
    check(wait <= security.ACCOUNT_DELAY + 1,
          f"但只有 {security.ACCOUNT_DELAY} 秒等級，不是幾十分鐘 —— "
          "鎖死的話任何人都能把你關在門外")
    other = security.throttle_check(conn, "someone_else", "192.168.1.20")
    check(other is None, "別的帳號從這個位址照樣登得進來")


def test_weak_password_check():
    print("\n弱密碼：規則之外還要擋常見密碼")
    for pw in ("Password1", "Qwerty123", "Abcd1234", "P@ssw0rd", "Admin2026"):
        check(security.password_problem(pw) is not None, f"擋掉常見密碼 {pw!r}")
    check("包含帳號" in (security.password_problem("Alice123", "alice") or ""),
          "擋掉「密碼裡就是自己的帳號」")
    check(security.password_problem("Alice123") is None,
          "不知道帳號時就不管這條 —— 規則不能靠猜")
    for pw in ("Tr0ub4dor&3", "Str0ngPass99", "長密碼Abc12345"):
        check(security.password_problem(pw, "alice") is None, f"正常密碼照過 {pw!r}")


def test_weak_password_forces_a_change():
    print("\n登入時發現舊帳號用弱密碼：登得進去，但只能去改密碼")
    import os, tempfile
    td = tempfile.mkdtemp(prefix="weakpw_")
    os.environ["VAM_DATA_DIR"] = os.path.join(td, "data")
    os.environ["VAM_CACHE_DIR"] = os.path.join(td, "cache")
    for m in [k for k in list(sys.modules) if k.startswith("app")]:
        del sys.modules[m]
    from fastapi.testclient import TestClient
    from app import auth, db, main
    db.init_db()
    # created directly, the way an account from before the rules existed is
    auth.create_user("olduser", "Password1", "viewer")

    c = TestClient(main.app, client=("192.168.1.31", 5555))
    r = c.post("/api/auth/login", json={"username": "olduser", "password": "Password1"})
    check(r.status_code == 200, f"密碼是對的，登入仍然成功（{r.status_code}）")
    check(r.json().get("must_change_pw"), "但回應裡標明必須改密碼")

    r = c.get("/api/characters")
    check(r.status_code == 403 and "修改密碼" in r.text,
          f"其他端點一律擋下並說明原因（{r.status_code}）")
    check(c.get("/api/auth/me").status_code == 200, "但還讀得到自己")
    r = c.json() if False else c.get("/api/auth/me").json()
    check(r.get("pw_problem"), f"而且說得出哪裡不好：{r.get('pw_problem')}")

    r = c.post("/api/auth/password", json={"current_password": "Password1",
                                           "new_password": "Str0ngPass99"})
    check(r.status_code == 200, "改密碼這條路是通的")
    c.post("/api/auth/login", json={"username": "olduser", "password": "Str0ngPass99"})
    check(c.get("/api/characters").status_code != 403, "改完就恢復正常")
    check(not c.get("/api/auth/me").json().get("must_change_pw"), "旗標已清掉")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for fn in (test_password_policy, test_throttle, test_success_clears_the_slate,
               test_blocked_retries_do_not_extend_the_lock,
               test_throttle_cannot_lock_a_victim_out,
               test_same_microsecond_ordering, test_audit_records_everything,
               test_ip_allowlist, test_forwarded_header_is_not_trusted_by_default,
               test_local_sources, test_lan_only_middleware,
               test_change_own_password, test_public_hardening,
               test_disabled_account, test_spraying_one_address_many_accounts,
               test_one_account_from_many_addresses_is_slowed_not_locked,
               test_weak_password_check, test_weak_password_forces_a_change):
        fn()
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
