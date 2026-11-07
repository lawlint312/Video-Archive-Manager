"""Face index: pick characters, stop it, and schedule it.

* the schedule rules (once / daily / weekdays, an end time that may fall the
  next morning, catching up only inside the window) — pure functions of a
  fixed clock
* the scheduler launches each occurrence once, waits while another index is
  running, and records why when it cannot start
* `index_folders` stops at the stop button and at the window's end, keeps
  what it did, and the next run does only the rest
* the endpoints: per-folder counts, a run on chosen folders, the history,
  schedules (validation, someone else's schedule is not yours)

No video is read: embeddings are faked, the databases are temp files.

Run:  python tests/test_faceschedule.py      (from backend/)
"""
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import schedule, faces           # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


D = datetime


def sched(**kw):
    s = {"repeat": "daily", "start": "01:00", "end": "07:00", "enabled": True,
         "date": None, "weekdays": [], "last_slot": None}
    s.update(kw)
    return s


def rules():
    print("\n排程規則")
    s = sched()
    check(schedule.slot_at(s, D(2026, 11, 8, 5, 0)) == (D(2026, 11, 8, 1), D(2026, 11, 8, 7)),
          "01:00–07:00，05:00 開機 → 還在時間窗內，補跑到 07:00")
    check(schedule.slot_at(s, D(2026, 11, 8, 8, 0)) is None, "08:00 開機 → 錯過，不補")
    check(schedule.next_start(s, D(2026, 11, 8, 8, 0)) == D(2026, 11, 9, 1),
          "下一次是明天 01:00")
    check(schedule.slot_at(s, D(2026, 11, 8, 7, 0)) is None, "結束時間那一刻就不算在內")

    night = sched(start="23:00", end="06:00")
    check(schedule.slot_at(night, D(2026, 11, 8, 2, 0)) == (D(2026, 11, 7, 23), D(2026, 11, 8, 6)),
          "23:00–06:00：結束時間在隔天早上")

    wk = sched(repeat="weekly", weekdays=[0, 2])          # Mon, Wed
    tue = D(2026, 11, 10, 3, 0)                          # a Tuesday
    check(tue.weekday() == 1 and schedule.slot_at(wk, tue) is None, "每週一、三：週二不跑")
    check(schedule.next_start(wk, tue) == D(2026, 11, 11, 1), "下一次是週三")

    open_daily = sched(end=None)
    check(schedule.slot_at(open_daily, D(2026, 11, 8, 23, 0)) == (D(2026, 11, 8, 1), D(2026, 11, 9, 1)),
          "沒有結束時間的重複排程：時間窗到下一次開始為止")

    once = sched(repeat="once", date="2026-11-08", end=None)
    check(schedule.slot_at(once, D(2026, 11, 12, 9, 0)) == (D(2026, 11, 8, 1), None),
          "單次、沒有結束時間：幾天後開機也補跑")
    once_end = sched(repeat="once", date="2026-11-08")
    check(schedule.state(once_end, D(2026, 11, 8, 9, 0)) == "missed", "單次、整個時間窗都沒開機 → 已錯過")
    check(schedule.state(once_end, D(2026, 11, 7, 9, 0)) == "scheduled", "還沒到 → 已排定")
    check(schedule.state(once_end, D(2026, 11, 8, 2, 0)) == "due", "時間窗內還沒開始 → 該跑了")
    started = dict(once_end, last_slot=D(2026, 11, 8, 1).timestamp())
    check(schedule.state(started, D(2026, 11, 8, 2, 0)) == "started", "開始過了就不會再跑一次")
    check(schedule.state(started, D(2026, 11, 8, 9, 0)) == "done", "跑過的單次 → 完成")
    check(schedule.state(dict(s, enabled=False), D(2026, 11, 8, 5)) == "off", "停用")

    print("\n新增時的檢查")
    now = D(2026, 11, 8, 12, 0)
    v = lambda **kw: schedule.validate(sched(**kw), now)
    check(v() is None, "每天 01:00–07:00 可以")
    check(v(repeat="hourly") and v(start="25:00") and v(start="1:00"), "格式不對的拒絕")
    check(v(end="01:00") == "結束時間不能和開始時間相同", "開始等於結束")
    check(v(repeat="once") == "請指定日期", "單次要有日期")
    check(v(repeat="once", date="2026-11-08") == "這個時間已經過了", "單次的時間窗已經過了")
    check(v(repeat="once", date="2026-11-08", start="11:00", end="13:00") is None,
          "還沒結束的單次（現在 12:00、11:00–13:00）可以，會馬上補跑")
    check(v(repeat="weekly") == "請至少選一天" and v(repeat="weekly", weekdays=[7]),
          "每週要選星期幾，而且要合理")


def store_and_tick(tmp: Path):
    print("\n排程器：每個時段只啟動一次")
    from fastapi import HTTPException
    from app import auth, db, main
    saved = (db.DB_PATH, dict(main._SCHEDULED), auth.get_user_by_id)
    db.DB_PATH = tmp / "vam.db"
    db.init_db()
    calls, busy = [], [False]

    def fake(s, slot, user):
        if busy[0]:
            raise HTTPException(status_code=409, detail="人臉索引已在執行中")
        calls.append((s["id"], slot))
        return f"job{len(calls)}"

    users = {1: {"id": 1, "role": "admin", "disabled": 0}}
    auth.get_user_by_id = lambda uid: dict(users[uid]) if uid in users else None
    main._SCHEDULED["face_index"] = ("person", fake)
    try:
        sid = schedule.add("face_index", 1, sched(), {"all": True})
        check(main.schedule_tick(D(2026, 11, 8, 0, 30)) == [], "還沒到")
        busy[0] = True
        check(main.schedule_tick(D(2026, 11, 8, 1, 0)) == [] and not calls,
              "已經有索引在跑 → 先不啟動")
        check(schedule.get(sid)["last_slot"] is None, "而且不算錯過")
        busy[0] = False
        check(main.schedule_tick(D(2026, 11, 8, 1, 1)) == ["job1"], "下一輪就啟動")
        check(calls[0][1] == (D(2026, 11, 8, 1), D(2026, 11, 8, 7)), "帶著 07:00 的結束時間")
        check(main.schedule_tick(D(2026, 11, 8, 3, 0)) == [], "同一個時段不會再啟動")
        check(main.schedule_tick(D(2026, 11, 9, 1, 0)) == ["job2"], "隔天再跑")
        g = schedule.get(sid)
        check(g["last_job"] == "job2" and g["last_slot"] == D(2026, 11, 9, 1).timestamp(),
              "記下啟動的工作")

        schedule.set_enabled(sid, False)
        check(main.schedule_tick(D(2026, 11, 10, 1, 0)) == [], "停用的不跑")

        gone = schedule.add("face_index", 99, sched(), {"all": True})
        main.schedule_tick(D(2026, 11, 10, 2, 0))
        r = schedule.get(gone)["last_result"]
        check(r and "帳號" in r["error"], "帳號不在了 → 不啟動，記下原因")
        check(schedule.get(gone)["last_slot"] is not None, "而且這個時段不再重試")

        users[2] = {"id": 2, "role": "viewer", "disabled": 0}
        nop = schedule.add("face_index", 2, sched(), {"all": True})
        main.schedule_tick(D(2026, 11, 10, 2, 0))
        check("權限" in schedule.get(nop)["last_result"]["error"], "沒有權限也一樣")
    finally:
        db.DB_PATH, sch, auth.get_user_by_id = saved
        main._SCHEDULED.clear(); main._SCHEDULED.update(sch)


class Stop:
    """A progress callback whose stop button is pressed after `after` calls."""
    def __init__(self, after=None):
        self.n, self.after, self.msgs = 0, after, []

    def __call__(self, msg):
        self.msgs.append(msg)
        self.n += 1

    @property
    def cancelled(self):
        return self.after is not None and self.n >= self.after


def indexing(tmp: Path):
    print("\n索引可以停、有時間上限，做完的保留")
    import numpy as np
    lib = tmp / "lib"
    a, b = lib / "Alice", lib / "Bob"
    for d, n in ((a, 4), (b, 2)):
        (d / "sub").mkdir(parents=True)
        for i in range(n):
            (d / ("sub" if i == 0 else "") / f"v{i}.mp4").write_bytes(b"x" * (i + 1))
    real = faces.extract_embeddings
    faces.extract_embeddings = lambda v: [] if v.name == "v1.mp4" else [np.ones(512, np.float32)]
    fdb = tmp / "faces.db"
    try:
        r = faces.index_folders(fdb, [("Alice", str(a)), ("Bob", str(b))],
                                progress=Stop(after=3))
        check(r["stopped"] == "cancelled" and r["processed"] == 3 and r["total"] == 6,
              f"按停止 → 停在第 3 部（{r['processed']}/{r['total']}）")
        r = faces.index_folders(fdb, [("Alice", str(a)), ("Bob", str(b))],
                                stop_at=time.time() - 1)
        check(r["stopped"] == "deadline" and r["processed"] == 0 and r["total"] == 3,
              "時間到了 → 一部都不開始，剩 3 部")
        p = Stop()
        r = faces.index_folders(fdb, [("Alice", str(a)), ("Bob", str(b))], progress=p)
        check(r["stopped"] is None and r["processed"] == 3, "下一次只做剩下的 3 部")
        check(r["no_face"] + r["indexed"] == r["processed"], "沒找到臉的也算處理過")
        check(p.msgs[0]["total"] == 3 and p.msgs[-1]["done"] == 2, "進度有總數")
        check(len(faces.processed_paths(fdb)) == 6, "6 部都記下")
        r = faces.index_folders(fdb, {"Alice": str(a)})
        check(r["total"] == 0, "舊的 {角色: 路徑} 寫法照樣可用，全部做過 → 0")
    finally:
        faces.extract_embeddings = real
    return lib


def api(tmp: Path, lib: Path):
    print("\n端點")
    import os
    from fastapi.testclient import TestClient
    from app import auth, db, main, jobs
    saved = (db.DB_PATH, main.FACE_DB, main._FACE_RUNS, main.faces.probe,
             main.app_settings.archive_roots_for, main.app_settings.inbox_paths_for,
             main.searchindex.get, faces.extract_embeddings, auth.get_user_by_id)
    db.DB_PATH = tmp / "vam2.db"
    db.init_db()
    main.FACE_DB = tmp / "faces2.db"
    main._FACE_RUNS = tmp / "runs.json"
    main.faces.probe = lambda: {"deps": True, "gpu": True, "reason": ""}
    main.app_settings.archive_roots_for = lambda u: [lib]
    main.app_settings.inbox_paths_for = lambda u: []

    def index(roots, inbox, scoped=False):
        dirs, rows = [], []
        for root in roots:
            for dp, _dn, fn in os.walk(root):
                vids = [f for f in fn if f.endswith(".mp4")]
                if vids:
                    dirs.append(dp)
                    rows += [[f, "", len(dirs) - 1, 0, "", ""] for f in vids]
        return {"dirs": dirs, "v": rows, "stale": False}
    main.searchindex.get = index
    import numpy as np
    faces.extract_embeddings = lambda v: [np.ones(512, np.float32)]
    me = {"id": 1, "role": "admin", "disabled": 0, "username": "admin"}
    other = {"id": 5, "role": "editor", "disabled": 0, "username": "bob"}
    who = [me]
    auth.get_user_by_id = lambda uid: {1: me, 5: other}.get(uid)
    main.app.dependency_overrides[auth.current_user] = lambda: dict(who[0])
    c = TestClient(main.app)
    try:
        f = c.get("/api/face/folders").json()
        al, bo = str(lib / "Alice"), str(lib / "Bob")
        check(f["folders"][al] == {"videos": 4, "indexed": 0, "pending": 4}
              and f["folders"][bo]["videos"] == 2, "每個角色的影片數（含子資料夾）")
        check(f["sec_per_video"] == main.FACE_SEC_DEFAULT and f["runs"] == [],
              "還沒跑過 → 預設每部 4 秒")

        r = c.post("/api/face/index", json={"paths": [bo, str(tmp / "nope")]}).json()
        j = None
        for _ in range(200):
            j = jobs.get(r["job_id"])
            if j["status"] != "running":
                break
            time.sleep(0.05)
        check(j["status"] == "done" and j["result"]["processed"] == 2
              and j["result"]["missing"] == 1, "只做選的 Bob；不存在的路徑略過並記下")
        f = c.get("/api/face/folders").json()
        check(f["folders"][bo]["pending"] == 0 and f["folders"][al]["pending"] == 4,
              "計數跟著更新")
        check(len(f["runs"]) == 1 and f["runs"][0]["trigger"] == "manual"
              and f["runs"][0]["processed"] == 2, "執行紀錄")

        print("\n排程端點")
        bad = c.post("/api/face/schedules", json={"repeat": "weekly", "start": "01:00"})
        check(bad.status_code == 400 and bad.json()["detail"] == "請至少選一天", "不合理的排程擋下")
        none = c.post("/api/face/schedules", json={"repeat": "daily", "start": "01:00",
                                                    "paths": []})
        check(none.status_code == 400, "沒選角色也沒選全部 → 擋下")
        out = c.post("/api/face/schedules", json={"repeat": "daily", "start": "01:00",
                                                   "end": "07:00", "paths": [str(tmp)]})
        check(out.status_code == 400, "範圍外的資料夾不能排")
        s = c.post("/api/face/schedules", json={"repeat": "daily", "start": "01:00",
                                                 "end": "07:00", "paths": [al]}).json()
        check(s["names"] == ["Alice"] and s["state"] in ("scheduled", "due")
              and s["next"], "新增：記下角色名稱，算出下一次")
        allx = c.post("/api/face/schedules", json={"repeat": "weekly", "weekdays": [5],
                                                    "start": "02:00", "all": True}).json()
        check(allx["all"] and allx["end"] is None, "全部角色、沒有結束時間")
        lst = c.get("/api/face/schedules").json()
        check(len(lst["schedules"]) == 2 and lst["now"], "清單帶著伺服器時間")
        check(c.patch(f"/api/face/schedules/{s['id']}", json={"enabled": False}).json()["state"] == "off",
              "可以停用")

        who[0] = other
        check(c.get("/api/face/schedules").json()["schedules"] == [], "別人看不到我的排程")
        check(c.delete(f"/api/face/schedules/{s['id']}").status_code == 403, "也刪不掉")
        who[0] = me
        check(c.delete(f"/api/face/schedules/{s['id']}").json()["ok"], "自己的可以刪")
        check(len(c.get("/api/face/schedules").json()["schedules"]) == 1, "剩一個")

        print("\n排程啟動真的索引，結果寫回排程")
        sid = allx["id"]
        now = datetime.now()
        schedule._set(sid, repeat="daily", start_hm=(now.replace(minute=0)).strftime("%H:%M"),
                      end_hm=None)
        started = main.schedule_tick(now)
        check(len(started) == 1, "時間窗內 → 啟動")
        for _ in range(200):
            if jobs.get(started[0])["status"] != "running":
                break
            time.sleep(0.05)
        g = schedule.get(sid)
        check(g["last_result"] and g["last_result"]["processed"] == 4
              and g["last_result"]["trigger"] == "schedule", "全部角色 → 做完 Alice 剩下的 4 部")
        check(c.get("/api/face/folders").json()["runs"][0]["trigger"] == "schedule",
              "執行紀錄標成排程")
        check(c.post("/api/jobs/x/cancel").status_code == 404 and "face_index" in main._CANCELLABLE,
              "人臉索引可以按停止")
    finally:
        main.app.dependency_overrides.clear()
        (db.DB_PATH, main.FACE_DB, main._FACE_RUNS, main.faces.probe,
         main.app_settings.archive_roots_for, main.app_settings.inbox_paths_for,
         main.searchindex.get, faces.extract_embeddings, auth.get_user_by_id) = saved


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    d = Path(tempfile.mkdtemp())
    try:
        rules()
        store_and_tick(d)
        lib = indexing(d)
        api(d, lib)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
