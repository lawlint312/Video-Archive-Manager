"""The loading screen can say which root folder the server is waiting on.

On 2026-11-18 opening VAM sat on 「載入中…」 for over a minute while a NAS
woke up: every request resolves the configured roots first, so all of them
waited on it and nothing said why.

  * resolving the roots and listing characters report which folder they are
    on, and how many came before it
  * the folder waited on longest is the one reported
  * nothing is reported once they are done
  * the endpoint shows the path to the admin only
  * the endpoint is `async` — the thread pool is exactly what is full when
    this is needed

Never touches a real folder: `Path.resolve` is replaced for the test.

Run:  python tests/test_rootprobe.py      (from backend/)
"""
import _tmpdata  # noqa: F401  (first: never the real data/ or cache/)
import asyncio
import inspect
import pathlib
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def wait_for(cond, secs=3.0):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def main_test():
    from app import settings, main, auth
    from app.core import rootprobe

    gate = threading.Event()
    real_resolve = pathlib.Path.resolve

    def fake_resolve(self, strict=False):
        if self.name == "Sleepy":
            gate.wait(5)
        return self

    pathlib.Path.resolve = fake_resolve
    saved_user = auth.current_user
    try:
        roots = [r"X:\A", r"X:\B", r"\\nas\share\Sleepy", r"X:\D"]
        out = []
        th = threading.Thread(target=lambda: out.append(settings._resolve_list(roots)))
        th.start()
        check(wait_for(lambda: (rootprobe.snapshot() or {}).get("current", "").endswith("Sleepy")),
              "回報正在等的資料夾")
        time.sleep(0.15)
        s = rootprobe.snapshot()
        check(s["kind"] == "resolve" and s["done"] == 2 and s["total"] == 4,
              f"前面兩個已經好了、共四個（{s}）")
        check(s["waited"] >= 0.1, "記得等了多久")

        # a second, quick round must not hide the stuck one
        with rootprobe.track("list", 3) as r:
            r.at(r"X:\quick")
            check(rootprobe.snapshot()["current"].endswith("Sleepy"),
                  "回報的是等最久的那一個")

        check(inspect.iscoroutinefunction(main.roots_progress),
              "端點是 async（不佔執行緒池）")
        auth.current_user = lambda req: {"id": 2, "role": "user"}
        got = asyncio.run(main.roots_progress(None))["progress"]
        check(got["current"] is None and got["total"] == 4,
              "非管理員：只看到進度，看不到路徑")
        auth.current_user = lambda req: {"id": 1, "role": "admin"}
        got = asyncio.run(main.roots_progress(None))["progress"]
        check(got["current"].endswith("Sleepy"), "管理員看得到路徑")

        gate.set()
        th.join(5)
        check(out and len(out[0]) == 4, "全部解析完")
        check(rootprobe.snapshot() is None, "做完了就沒有進度")
        check(asyncio.run(main.roots_progress(None))["progress"] is None,
              "端點也回 None")
    finally:
        gate.set()
        pathlib.Path.resolve = real_resolve
        auth.current_user = saved_user


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main_test()
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
