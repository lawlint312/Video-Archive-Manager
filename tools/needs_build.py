"""Is `frontend/dist` older than the sources that produce it?

Exit 1 when a build is needed, 0 when it is not. Called by run-server.bat so
that starting the service can never serve a stale frontend — that check is the
whole reason the Vite dev server could be dropped from everyday use.

The comparison is against `dist/index.html`, not against the whole of `dist/`,
and that is a measured decision rather than a simplification. Vite copies
`public/` through **preserving the source mtime**:

    06:02:28  dist/assets/index-C8aX4-nm.js     <- compiled just now
    18:41:59  dist/brand-lockup.png             <- copied, keeps public/'s date
    06:02:28  dist/index.html

so "the oldest file in dist/" is a date from whenever those images were last
touched, and a build check written that way reports "stale" every single time —
which would mean a pointless rebuild on every start. `index.html` is rewritten
by every build and is the file the server actually serves.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONT = ROOT / "frontend"
ENTRY = FRONT / "dist" / "index.html"

# Everything a build reads. `node_modules` is deliberately absent: it is
# enormous, and npm rewrites timestamps in it for reasons unrelated to our code.
SOURCES = [FRONT / "src", FRONT / "public", FRONT / "index.html",
           FRONT / "package.json", FRONT / "vite.config.js"]


def newest(paths) -> float:
    best = 0.0
    for p in paths:
        if p.is_dir():
            for f in p.rglob("*"):
                if f.is_file():
                    best = max(best, f.stat().st_mtime)
        elif p.is_file():
            best = max(best, p.stat().st_mtime)
    return best


if not ENTRY.is_file():
    print("dist/index.html 不存在 —— 需要建置")
    sys.exit(1)

built = ENTRY.stat().st_mtime
src = newest(SOURCES)
if src > built:
    print("前端原始碼比 dist/ 新 —— 需要建置")
    sys.exit(1)
print("dist/ 是最新的")
sys.exit(0)
