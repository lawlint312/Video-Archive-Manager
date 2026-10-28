"""SQLite storage for users and sessions."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .config import DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'viewer',   -- admin | editor | viewer
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

-- Permission groups (see roles.py). `users.role` holds `key`; `admin` is
-- deliberately not a row.
CREATE TABLE IF NOT EXISTS roles (
    id         INTEGER PRIMARY KEY,
    key        TEXT UNIQUE NOT NULL,
    name       TEXT NOT NULL,
    perms      TEXT NOT NULL DEFAULT '[]',   -- JSON list of permission keys
    builtin    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_settings (
    id   INTEGER PRIMARY KEY CHECK (id = 1),
    data TEXT NOT NULL
);

-- Every login attempt, successful or not. Two jobs in one table: the throttle
-- counts recent failures out of it, and it is the only record that would ever
-- show someone trying. Without it a password-guessing run leaves no trace at
-- all -- which was the state before this table existed.
CREATE TABLE IF NOT EXISTS login_events (
    id       INTEGER PRIMARY KEY,
    at       TEXT NOT NULL,
    username TEXT NOT NULL,      -- as typed: a wrong username is worth seeing
    ip       TEXT NOT NULL,
    ok       INTEGER NOT NULL,   -- 1 = signed in, 0 = rejected
    reason   TEXT                -- why it was rejected, for the admin view
);
CREATE INDEX IF NOT EXISTS idx_login_at ON login_events(at);

-- Things an admin should look at: a lock that just closed, somebody trying
-- account names that do not exist, a successful sign-in from an address that
-- account has never used. The last one is the valuable one -- a leaked
-- password produces *successful* logins, which no failure count will ever
-- show. `seen` is per admin (see alert_seen), this row is shared.
CREATE TABLE IF NOT EXISTS security_alerts (
    id       INTEGER PRIMARY KEY,
    at       TEXT NOT NULL,
    kind     TEXT NOT NULL,      -- new_ip | unknown_user | locked | spray | 2fa
    username TEXT NOT NULL DEFAULT '',
    ip       TEXT NOT NULL DEFAULT '',
    detail   TEXT
);
CREATE INDEX IF NOT EXISTS idx_alert_at ON security_alerts(at);

-- How far each admin has read. Per user, because "I have seen this" is not a
-- property of the event.
CREATE TABLE IF NOT EXISTS alert_seen (
    user_id INTEGER PRIMARY KEY,
    last_id INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_login_user_at ON login_events(username, at);

-- Per-user "watched" and "favourite" marks, keyed by path (normcase'd, see
-- core/videoflags.py -- which also explains why the key is a path and not a
-- catalogue number, and what that costs every time a file moves).
CREATE TABLE IF NOT EXISTS video_flags (
    user_id INTEGER NOT NULL,
    path    TEXT    NOT NULL,
    watched TEXT,
    fav     INTEGER NOT NULL DEFAULT 0,
    fav_at  TEXT,
    PRIMARY KEY (user_id, path)
);
CREATE INDEX IF NOT EXISTS idx_flags_fav ON video_flags(user_id, fav);

-- Hand-typed titles for the 95% of videos with no catalogue number, keyed by
-- the same normcase'd path as video_flags (see core/filetitle.py, which also
-- records why the filename was measured and rejected as a key). Titles for
-- videos that DO have a number live in javdb's movie_title instead, keyed by
-- the number -- do not merge the two, they have different identities.
-- The 相似 sort's threshold for one character folder, set in edit mode. Its
-- presence is also what makes the 相似 button appear in browse mode: a folder
-- nobody has tuned shows nothing rather than a sort with an arbitrary default.
-- Server-side rather than localStorage so the phone, another browser and a
-- viewer account all see the order the editor chose. Keyed by the normcase'd
-- folder path (see core/photosimilar.py for why a folder move must carry it).
CREATE TABLE IF NOT EXISTS photo_similar (
    folder     TEXT PRIMARY KEY,
    threshold  INTEGER NOT NULL,
    -- colour layer, whole percent; 0 = off, NULL = applied before it existed
    color_threshold INTEGER,
    applied_at TEXT
);

CREATE TABLE IF NOT EXISTS file_title (
    path  TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    -- Release date for a film with no catalogue number, same YYYY-MM-DD shape
    -- as movie_title.released -- but an IAFD import only knows the year, and
    -- stores just that rather than inventing a month and a day.
    released TEXT,
    -- typed by hand; wins over `released` and survives a re-import
    released_manual TEXT
);

-- Co-stars: a video filed under one character that another also appears in.
-- `video` is 'id:<catalogue number>' when the file has one (covers every copy
-- of the film, survives renames) and 'path:<normcase'd path>' otherwise, the
-- same two keys the titles use. `folder` is the co-star's character folder.
-- state 1 = confirmed, 0 = a suggestion the user turned down (so it is not
-- offered again). See core/costar.py.
CREATE TABLE IF NOT EXISTS costar (
    video  TEXT NOT NULL,
    folder TEXT NOT NULL,
    state  INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (video, folder)
);
CREATE INDEX IF NOT EXISTS costar_folder ON costar(folder);
-- Co-stars with no character folder: a name only (横田夏美 in RCTD-208).
-- Same video keys as above. Resolved through folder names and aliases each
-- time it is read, so once a folder exists for her — or the name becomes
-- someone's alias — every film marked with it links up by itself.
CREATE TABLE IF NOT EXISTS costar_name (
    video  TEXT NOT NULL,
    name   TEXT NOT NULL,
    at     REAL NOT NULL,
    PRIMARY KEY (video, name)
);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# Columns added after the first release. SQLite has no "ADD COLUMN IF NOT
# EXISTS", so each one is applied only when it is missing.
_MIGRATIONS = [
    # NULL = unrestricted; otherwise a JSON list of root paths this user may see
    ("users", "allowed_roots", "ALTER TABLE users ADD COLUMN allowed_roots TEXT"),
    # NULL/empty = reachable from anywhere; otherwise a JSON list of CIDRs this
    # account may sign in from. Same shape as allowed_roots on purpose.
    ("users", "allowed_ips", "ALTER TABLE users ADD COLUMN allowed_ips TEXT"),
    # `file_title` shipped without this. CREATE TABLE IF NOT EXISTS only builds
    # the new shape on a *new* database, so without a migration every existing
    # install would raise "no such column" on the first write -- and every test
    # would pass, because each one starts from an empty file. That exact trap
    # cost a release once already (javdb.wiki_profile.works).
    ("file_title", "released", "ALTER TABLE file_title ADD COLUMN released TEXT"),
    # And again for the hand-typed release date (2026-10-22).
    ("file_title", "released_manual",
     "ALTER TABLE file_title ADD COLUMN released_manual TEXT"),
    # Same trap, third time: photo_similar shipped on 2026-09-14 without the
    # colour layer, and the live database already has the table.
    ("photo_similar", "color_threshold",
     "ALTER TABLE photo_similar ADD COLUMN color_threshold INTEGER"),
    # 1 = the account exists but cannot sign in. Deleting a user throws away
    # their marks and history; `admin` in particular is a name worth keeping
    # out of circulation rather than freeing up again.
    ("users", "disabled", "ALTER TABLE users ADD COLUMN disabled INTEGER NOT NULL DEFAULT 0"),
    # Set at sign-in when the password turns out to be weak (the rules arrived
    # after these accounts did, and that is the only moment the plain password
    # exists). `pw_problem` is the message to show, so the user is not left
    # guessing what is wrong with a password the server just accepted.
    ("users", "must_change_pw",
     "ALTER TABLE users ADD COLUMN must_change_pw INTEGER NOT NULL DEFAULT 0"),
    ("users", "pw_problem", "ALTER TABLE users ADD COLUMN pw_problem TEXT"),
    # Two-factor. `totp_pending` holds a secret that has been shown but not yet
    # proven with a code — keeping it apart from `totp_secret` means a setup
    # someone abandoned halfway never locks them out of their own account.
    ("users", "totp_secret", "ALTER TABLE users ADD COLUMN totp_secret TEXT"),
    ("users", "totp_pending", "ALTER TABLE users ADD COLUMN totp_pending TEXT"),
    ("users", "totp_last", "ALTER TABLE users ADD COLUMN totp_last INTEGER NOT NULL DEFAULT 0"),
    ("users", "recovery_codes", "ALTER TABLE users ADD COLUMN recovery_codes TEXT"),
    # Where and when each session is being used, so its owner can recognise it
    # in the list and sign out the one they do not.
    ("sessions", "ip", "ALTER TABLE sessions ADD COLUMN ip TEXT"),
    ("sessions", "user_agent", "ALTER TABLE sessions ADD COLUMN user_agent TEXT"),
    ("sessions", "last_seen", "ALTER TABLE sessions ADD COLUMN last_seen TEXT"),
]


def init_db():
    conn = connect()
    try:
        conn.executescript(_SCHEMA)
        for table, column, ddl in _MIGRATIONS:
            cols = {r["name"] for r in
                    conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if column not in cols:
                conn.execute(ddl)
        conn.commit()
    finally:
        conn.close()
    # Here rather than only at startup: every test builds its database with
    # init_db(), and an `editor` account without its group row has no
    # permissions at all.
    from . import roles
    roles.seed()
