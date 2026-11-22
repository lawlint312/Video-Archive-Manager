"""The version the account menu shows (app/version.py, /api/auth/me).

Built in a throwaway git repo, never this project's:
  * no git (a downloaded zip): the number only
  * at the release tag: +0
  * commits after it: counted from the tag
  * no tag: counted from the last change to VERSION
  * uncommitted changes to tracked files: dirty; untracked files: not
  * /api/auth/me carries it

Run:  python tests/test_version.py      (from backend/)
"""
import _tmpdata  # noqa: F401  (first: never the real data/ or cache/)
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def git(root, *a):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                    "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *a],
                   cwd=root, check=True, capture_output=True)


def main():
    from app import version

    print("no git")
    tmp = Path(tempfile.mkdtemp())
    (tmp / "VERSION").write_text("1.4.0\n", encoding="utf-8")
    v = version.read(tmp)
    check(v["version"] == "1.4.0" and v["commit"] == "" and v["ahead"] == 0
          and v["dirty"] is False, f"number only ({v})")
    check(version.read(Path(tempfile.mkdtemp()))["version"] == "", "no VERSION file → empty")

    print("\ngit, tagged")
    git(tmp, "init", "-q")
    (tmp / "a.py").write_text("1", encoding="utf-8")
    git(tmp, "add", ".")
    git(tmp, "commit", "-q", "-m", "release")
    git(tmp, "tag", "-a", "v1.4.0", "-m", "r")
    v = version.read(tmp)
    check(v["commit"] and v["ahead"] == 0 and not v["dirty"] and len(v["date"]) == 10,
          f"at the tag: +0 ({v})")
    for i in range(3):
        (tmp / "a.py").write_text(str(i + 2), encoding="utf-8")
        git(tmp, "commit", "-q", "-am", f"c{i}")
    v = version.read(tmp)
    check(v["ahead"] == 3, f"three commits later: +3 ({v['ahead']})")
    (tmp / "new.txt").write_text("x", encoding="utf-8")
    check(not version.read(tmp)["dirty"], "an untracked file is not 'uncommitted changes'")
    (tmp / "a.py").write_text("edited", encoding="utf-8")
    check(version.read(tmp)["dirty"], "an edited tracked file is")

    print("\ngit, no tag for this number")
    (tmp / "VERSION").write_text("1.5.0\n", encoding="utf-8")
    git(tmp, "commit", "-q", "-am", "bump")
    (tmp / "a.py").write_text("after", encoding="utf-8")
    git(tmp, "commit", "-q", "-am", "after bump")
    v = version.read(tmp)
    check(v["version"] == "1.5.0" and v["ahead"] == 1,
          f"counted from the VERSION change ({v['ahead']})")

    print("\nthis project")
    real = version.INFO
    check(real["version"].count(".") == 2, f"VERSION is X.Y.Z ({real['version']})")
    check(isinstance(real["started"], float), "start time recorded")

    print("\n/api/auth/me")
    from app import main as m, auth, db, jobs
    scratch = Path(tempfile.mkdtemp())
    db.DB_PATH = scratch / "vam.db"                     # never the real data/
    saved = (jobs.REGISTRY, jobs.NOTICES)
    jobs.REGISTRY = scratch / "jobs_running.json"
    jobs.NOTICES = scratch / "jobs_interrupted.json"
    m.app.dependency_overrides[auth.current_user] = lambda: {
        "id": 1, "username": "t", "role": "admin", "allowed_roots": None}
    try:
        from fastapi.testclient import TestClient
        # no `with`: the startup events (job recovery) are not this test's business
        c = TestClient(m.app)
        j = c.get("/api/auth/me").json()
        check(j.get("server", {}).get("version") == real["version"],
              f"carries the version ({j.get('server')})")
        check(j.get("username") == "t", "and still the user")
    finally:
        m.app.dependency_overrides.clear()
        jobs.REGISTRY, jobs.NOTICES = saved

    print("\nFAIL (%d)" % len(FAIL) if FAIL else "\nPASS")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
