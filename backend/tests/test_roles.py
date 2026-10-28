"""Permission groups: an admin decides which features each group may use.

The assertions worth having:
* the day this ships nothing changes — `editor` and `viewer` start out with
  exactly what they could do before;
* a group only gets what is ticked, and the refusal happens at the endpoint,
  not just by hiding a button;
* two-factor follows the permissions (any ticked box means it is required),
  so a brand-new group is covered the moment it is saved;
* the ways to break the admin's own access are closed.

Run:  python tests/test_roles.py        (from backend/)
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_TD = tempfile.mkdtemp(prefix="roles_")
os.environ["VAM_DATA_DIR"] = os.path.join(_TD, "data")
os.environ["VAM_CACHE_DIR"] = os.path.join(_TD, "cache")

from fastapi.testclient import TestClient                 # noqa: E402

from app import auth, db, main, roles                     # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def login(name, pw, ip):
    c = TestClient(main.app, client=(ip, 5555))
    r = c.post("/api/auth/login", json={"username": name, "password": pw})
    assert r.status_code == 200, r.text
    return c


def test_builtin_groups_keep_old_behaviour():
    print("\n上線當天什麼都不變")
    db.init_db()
    check(set(roles.perms_for("editor")) == set(roles.PERM_KEYS),
          "「可編輯」一開始就是原本能做的全部")
    check(roles.perms_for("viewer") == [], "「唯讀」一開始什麼都不能改")
    db.init_db()
    check(len([r for r in roles.all_roles() if r["builtin"]]) == 2,
          "重複啟動不會長出第二份內建組")


def test_custom_group_is_enforced_at_the_endpoint():
    print("\n自訂權限組：只有勾選的做得到，而且是伺服器擋的")
    auth.TWOFA_ENFORCED = False          # this test is about permissions
    auth.create_user("boss", "Str0ngPass98", "admin")
    admin = login("boss", "Str0ngPass98", "192.168.1.20")

    r = admin.post("/api/roles", json={"name": "整理員",
                                       "perms": ["archive", "rename_merge", "dedup",
                                                 "not_a_real_permission"]})
    check(r.status_code == 200, f"管理員可以新增權限組（{r.status_code}）")
    key = r.json()["key"]
    check(r.json()["perms"] == ["archive", "rename_merge", "dedup"],
          "不存在的權限名稱會被丟掉，不會存進去")

    uid = auth.create_user("sorter", "Str0ngPass97", "viewer")
    r = admin.patch(f"/api/users/{uid}", json={"role": key})
    check(r.status_code == 200, "可以把帳號放進新的權限組")
    sorter = login("sorter", "Str0ngPass97", "192.168.1.31")
    me = sorter.get("/api/auth/me").json()
    check(me["role_name"] == "整理員" and "dedup" in me["perms"],
          f"帳號看得到自己的組名與權限：{me['role_name']} {me['perms']}")
    check(me["can_edit"], "有任何一項權限就能切換到編輯模式")

    check(sorter.get("/api/dedup/report").status_code != 403, "有勾「去重」→ 看得到重複報告")
    r = sorter.post("/api/media/delete", json={"path": "C:/nope.mp4"})
    check(r.status_code == 403 and "刪除檔案" in r.text,
          f"沒勾「刪除檔案」→ 刪除被伺服器擋下，而且說得出缺哪一項（{r.text[:40]}）")
    r = sorter.post("/api/convert/scan", json={"roots": []})
    check(r.status_code == 403 and "轉檔" in r.text, "沒勾「轉檔」→ 轉檔被擋")
    check(sorter.get("/api/jobs").status_code == 200,
          "大家共用的「背景工作清單」有任何一項權限就能看")
    check(sorter.get("/api/users").status_code == 403,
          "帳號管理永遠只有管理員 —— 不在可勾選的清單裡")

    r = admin.patch(f"/api/roles/{key}", json={"perms": ["archive"]})
    check(r.json()["perms"] == ["archive"], "取消勾選後")
    check(sorter.get("/api/dedup/report").status_code == 403,
          "下一個請求就生效，不用重新登入")

    viewer_id = auth.create_user("reader", "Str0ngPass96", "viewer")
    reader = login("reader", "Str0ngPass96", "192.168.1.44")
    check(reader.post("/api/roles", json={"name": "偷偷", "perms": ["delete"]}
                      ).status_code == 403,
          "非管理員不能新增或修改權限組")
    check(not reader.get("/api/auth/me").json()["can_edit"], "唯讀帳號沒有編輯模式")
    auth.TWOFA_ENFORCED = True
    return key, admin, viewer_id


def test_twofa_follows_permissions(key):
    print("\n兩步驟驗證：唯讀不強制，其他一律強制")
    check(not auth.twofa_required({"role": "viewer"}), "唯讀不強制")
    check(auth.twofa_required({"role": "editor"}), "可編輯強制")
    check(auth.twofa_required({"role": key}), "自訂的組只要勾了任何一項就強制")
    check(auth.twofa_required({"role": "admin"}), "管理員強制")
    empty = roles.create("看看就好", [])
    check(not auth.twofa_required({"role": empty["key"]}),
          "什麼都沒勾的自訂組等同唯讀，不強制")


def test_guards(key, admin, viewer_id):
    print("\n不能把事情弄壞的那幾條")
    auth.TWOFA_ENFORCED = False
    r = admin.delete("/api/roles/viewer")
    check(r.status_code == 400, "內建的權限組不能刪")
    r = admin.delete(f"/api/roles/{key}")
    check(r.status_code == 400 and "帳號" in r.text,
          f"還有人在用的權限組不能刪，而且說得出原因（{r.json()['detail']}）")
    check(admin.patch("/api/roles/admin", json={"perms": []}).status_code == 400,
          "管理員不是一個可以改的權限組")
    check(admin.post("/api/roles", json={"name": "管理員"}).status_code == 400,
          "不能建一個也叫「管理員」的組來混淆")
    r = admin.patch(f"/api/users/{viewer_id}", json={"role": "no_such_group"})
    check(r.status_code == 400, "不能把帳號指到不存在的權限組")
    lst = admin.get("/api/roles").json()
    check(lst["roles"][0]["key"] == "admin" and lst["roles"][0]["fixed"],
          "清單裡管理員排第一，標明是固定的")
    check(len(lst["catalogue"]) == len(roles.PERMS), "權限目錄完整回傳給畫面用")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    test_builtin_groups_keep_old_behaviour()
    key, admin, viewer_id = test_custom_group_is_enforced_at_the_endpoint()
    test_twofa_follows_permissions(key)
    test_guards(key, admin, viewer_id)
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
