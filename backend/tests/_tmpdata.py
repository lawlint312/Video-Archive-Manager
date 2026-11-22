"""Point the app's data and cache directories at a throwaway folder.

Every test file imports this first, before anything from `app`:

    import _tmpdata  # noqa: F401  (first: never the real data/ or cache/)

`app.config` reads VAM_DATA_DIR / VAM_CACHE_DIR once, at import, and every
database, report and cache path in the app hangs off those two. Left at their
defaults they are the repo's own data/ and cache/ — the user's real accounts,
lookups, logs and thumbnails. Tests have overwritten a dedup report, dirtied a
real database and deleted every server log that way, so this is not left to
each file to remember (tests/test_tmpdata.py checks that every one does).

Always overridden, never `setdefault`: a shell that already has VAM_DATA_DIR
set is the server's environment, which is exactly the one not to use. A test
that needs its own layout still sets the variables again after this import.
"""
import atexit
import os
import shutil
import tempfile

ROOT = tempfile.mkdtemp(prefix="vam_test_")
os.environ["VAM_DATA_DIR"] = os.path.join(ROOT, "data")
os.environ["VAM_CACHE_DIR"] = os.path.join(ROOT, "cache")

# Ignoring errors: on Windows a database the test left open cannot be deleted,
# and failing to tidy up a temp folder is not a test result.
atexit.register(shutil.rmtree, ROOT, ignore_errors=True)
