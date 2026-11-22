"""A request cannot make the server open an SMB connection to a host it names.

Found 2026-11-19 while auditing dependencies. On Windows, resolving a
`\\host\share` path connects to that host over SMB and offers the account's
NTLM credentials, even when the answer is then "not found" or "not allowed":

  * the built frontend is served by Starlette's StaticFiles, which (before
    1.1.0, GHSA-wqp7-x3pw-xc5r) resolves the request path — and
    `/%5C%5Chost%5Cshare` is a UNC path. No login needed.
  * `path_gate` resolved every path a client sent before comparing it with
    the roots. Any logged-in account.

And the password/2FA gates read `request.url.path`, which Starlette before
1.0.1 rebuilt from the Host header (GHSA-86qp-5c8j-p5mr).

Nothing here connects anywhere: `realpath` / `Path.resolve` are spies that
refuse a UNC path.

Run:  python tests/test_uncguard.py      (from backend/)
"""
import _tmpdata  # noqa: F401  (first: never the real data/ or cache/)
import os
import pathlib
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def is_unc(p) -> bool:
    s = str(p).replace("/", "\\")
    return s.startswith("\\\\")


def test_static():
    print("\n前端靜態檔：網址裡的 UNC 路徑不會被解析")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import starlette.staticfiles as sf
    from app.main import _AppFiles

    d = tempfile.mkdtemp(prefix="vam-unc-")
    Path(d, "index.html").write_text("<p>app</p>", encoding="utf-8")
    Path(d, "assets").mkdir()
    Path(d, "assets", "a-123.js").write_text("1", encoding="utf-8")
    seen = []
    real = os.path.realpath

    def spy(p, *a, **k):
        if is_unc(p):
            seen.append(str(p))
            return str(p)               # never let it reach the network
        return real(p, *a, **k)

    saved = sf.os.path.realpath
    sf.os.path.realpath = spy
    try:
        app = FastAPI()
        app.mount("/", _AppFiles(directory=d, html=True))
        c = TestClient(app)
        for u in ("/%5C%5Cvam-test.invalid%5Cshare%5Cx",
                  "/%5c%5cvam-test.invalid/share",
                  "/x%5C..%5C..%5C%5C%5Cvam-test.invalid%5Cs",
                  "/C:%5CWindows%5Cwin.ini",
                  "/C:/Windows/win.ini"):
            seen.clear()
            r = c.get(u)
            check(r.status_code == 404 and not seen,
                  f"{u} → {r.status_code}，沒有碰到 UNC（{seen}）")
        check(c.get("/").status_code == 200, "首頁照常")
        r = c.get("/assets/a-123.js")
        check(r.status_code == 200 and "immutable" in r.headers.get("cache-control", ""),
              "assets 照常、快取標頭照舊")
    finally:
        sf.os.path.realpath = saved


def test_gate():
    print("\n路徑檢查：不在根目錄所在的磁碟或共用上，就不解析")
    from app import settings

    resolved = []
    real = pathlib.Path.resolve

    def spy(self, strict=False):
        resolved.append(str(self))
        if is_unc(self) and "nas" not in str(self).lower():
            raise AssertionError(f"resolved a foreign UNC path: {self}")
        return self                     # roots here are fake: keep them as is

    saved = (settings.roots_for, settings.get_cached)
    pathlib.Path.resolve = spy
    try:
        settings.roots_for = lambda user=None: [Path(r"\\nas\share\JAV"), Path(r"D:\Media")]
        settings.get_cached = lambda ttl=2.0: {
            "archive_roots": [r"\\NAS\share\JAV", r"Z:\Social"],
            "inbox_paths": [r"D:\Media"]}
        ok = settings.path_gate({"id": 1})
        for p, want in ((r"\\attacker.invalid\x\a.mp4", False),
                        ("//attacker.invalid/x/a.mp4", False),
                        (r"\\?\UNC\attacker.invalid\x\a.mp4", False),
                        (r"\\.\pipe\x", False),
                        (r"E:\Other\a.mp4", False),
                        ("relative\\a.mp4", False),
                        (r"\\NAS\SHARE\JAV\a\b.mp4", True),
                        (r"d:\media\x.mp4", True)):
            resolved.clear()
            got = ok(Path(p))
            touched = bool(resolved)
            check(got == want and (touched == want),
                  f"{p} → {got}（{'有' if touched else '沒有'}解析）")
        check(settings._volume(r"Z:\Social") in {"z:"}, "對應磁碟機（Z:）也算在根目錄的磁碟裡")
    finally:
        pathlib.Path.resolve = real
        settings.roots_for, settings.get_cached = saved


def test_auth_path():
    print("\n改密碼／兩步驟的閘門看路由用的路徑，不看 request.url")
    import inspect
    from app import auth
    src = inspect.getsource(auth.current_user)
    check("request.url.path" not in src.replace("`request.url.path`", ""),
          "current_user 不再用 request.url.path")
    check('request.scope.get("path"' in src, "改用 scope 裡的路徑")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    test_static()
    test_gate()
    test_auth_path()
    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)
