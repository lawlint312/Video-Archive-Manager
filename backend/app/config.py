"""App configuration. In dev, reads from env vars; in Docker, from mounted config."""
import os
from pathlib import Path

# Initial media roots used ONLY to seed the settings DB on first run.
# After that, roots are configured via the Settings UI (stored in the DB).
# Comma/`os.pathsep`-separated in the VAM_MEDIA_ROOTS env var.
_DEFAULT_TEST_ROOT = Path(__file__).resolve().parents[2] / "testdata"
INITIAL_MEDIA_ROOTS = [
    p for p in os.environ.get("VAM_MEDIA_ROOTS", str(_DEFAULT_TEST_ROOT)).split(os.pathsep)
    if p.strip()
]

# Where generated thumbnails are cached (persist this as a Docker volume).
CACHE_DIR = Path(os.environ.get(
    "VAM_CACHE_DIR",
    str(Path(__file__).resolve().parents[2] / "cache" / "thumbs"))).resolve()
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Persistent data (SQLite: users, sessions, metadata). Mount as a Docker volume.
DATA_DIR = Path(os.environ.get(
    "VAM_DATA_DIR",
    str(Path(__file__).resolve().parents[2] / "data"))).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "vam.db"

# First-run admin. If no users exist at startup, one is created from these;
# if the password is blank, a random one is generated and printed to the log.
INIT_ADMIN_USER = os.environ.get("VAM_ADMIN_USER", "admin")
INIT_ADMIN_PASSWORD = os.environ.get("VAM_ADMIN_PASSWORD", "")

# Session lifetime (days) and cookie name. 7, not the original 30: once the
# site was reachable from the internet (2026-09-15), a lost phone or a
# forgotten sign-in kept working for a month.
SESSION_DAYS = int(os.environ.get("VAM_SESSION_DAYS", "7"))
SESSION_COOKIE = "vam_session"
