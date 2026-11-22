"""Which VAM this server is running — shown at the bottom of the account menu.

`VERSION` (repo root) is the one place the version number lives: three parts,
`X.Y.Z` — a new feature moves Y, a fix moves Z (the owner's rule, 2026-11-21).
It is changed only when a release is made (`tools/publish_public.py release`
refuses a version that does not match it).

Between releases the private repo runs code that is newer than that number, so
when the project is a git checkout this also records, once at start-up:

  * the commit the server started from,
  * how many commits it is past that release,
  * whether tracked files had uncommitted changes (the owner tests before
    committing, so "is the server running my change?" is a real question),
  * when the server started — the answer to "was it restarted?".

Read once at import: that is the code this process actually loaded. Without
git (a downloaded zip), only the number is shown.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
STARTED = time.time()


def _git(root: Path, *args: str) -> Optional[str]:
    try:
        r = subprocess.run(["git", *args], cwd=root, capture_output=True,
                           text=True, encoding="utf-8", timeout=5,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def read(root: Path = None) -> dict:
    """Version facts for the checkout at `root` (default: this project)."""
    root = Path(root or ROOT)
    try:
        version = (root / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        version = ""
    out = {"version": version, "commit": "", "date": "", "ahead": 0,
           "dirty": False, "started": STARTED}
    if not (root / ".git").exists():
        return out
    head = _git(root, "rev-parse", "--short", "HEAD")
    if not head:
        return out
    out["commit"] = head
    out["date"] = _git(root, "log", "-1", "--format=%cs") or ""
    # Counted from the release's tag (`release` tags the private commit it
    # published, the public repo has its own); without one, from the last
    # change to VERSION.
    base = (_git(root, "rev-parse", "-q", "--verify", f"refs/tags/v{version}^{{commit}}")
            if version else None) or _git(root, "log", "-1", "--format=%H", "--", "VERSION")
    if base:
        n = _git(root, "rev-list", "--count", f"{base}..HEAD")
        out["ahead"] = int(n) if n and n.isdigit() else 0
    status = _git(root, "status", "--porcelain", "--untracked-files=no")
    out["dirty"] = bool(status)
    return out


INFO = read()
