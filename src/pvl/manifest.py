"""SQLite manifest: one row per hourly file. The single source of truth for what was done."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .listing import Listed

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    name           TEXT PRIMARY KEY,
    day            TEXT NOT NULL,
    hour           INTEGER NOT NULL,
    url            TEXT NOT NULL,
    expected_bytes INTEGER NOT NULL,
    listing_mtime  TEXT,
    status         TEXT NOT NULL DEFAULT 'planned',  -- planned | downloaded | loaded | failed (download) | load_failed
    sha256         TEXT,
    rows_in        INTEGER,   -- counted independently of the loader
    rows_loaded    INTEGER,
    rows_quarantined INTEGER,
    good_sha256    TEXT,      -- of the Parquet file as written, re-checked by reconcile
    quarantine_sha256 TEXT,
    error          TEXT
);
"""


def connect(data_dir: Path) -> sqlite3.Connection:
    data_dir.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(data_dir / "manifest.sqlite")
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    have = {r["name"] for r in con.execute("PRAGMA table_info(files)")}
    for col in ("good_sha256", "quarantine_sha256"):  # databases created before checksums existed
        if col not in have:
            con.execute(f"ALTER TABLE files ADD COLUMN {col} TEXT")
    con.commit()
    return con


def plan(con: sqlite3.Connection, files: list[Listed]) -> int:
    """Add listed files. Never resets a file that already has a status (idempotent)."""
    added = 0
    for f in files:
        cur = con.execute(
            "INSERT OR IGNORE INTO files(name, day, hour, url, expected_bytes, listing_mtime) "
            "VALUES (?,?,?,?,?,?)",
            (f.name, f.day, f.hour, f.url, f.size, f.mtime),
        )
        added += cur.rowcount
    con.commit()
    return added


def rows_with_status(con: sqlite3.Connection, status: str) -> list[sqlite3.Row]:
    return con.execute("SELECT * FROM files WHERE status=? ORDER BY name", (status,)).fetchall()


def update(con: sqlite3.Connection, name: str, **cols) -> None:
    sets = ", ".join(f"{k}=?" for k in cols)
    con.execute(f"UPDATE files SET {sets} WHERE name=?", (*cols.values(), name))
    con.commit()


class Busy(Exception):
    """Another pvl process is already working in this data directory."""


@contextmanager
def exclusive(data_dir: Path):
    """Only one process may download or load into a data directory at a time.

    Held as an exclusive SQLite lock on a separate file, so the operating system releases it the
    moment the holder exits, including when it is killed. A lock file with a PID would go stale."""
    data_dir.mkdir(parents=True, exist_ok=True)
    lock = sqlite3.connect(data_dir / "lock.sqlite", timeout=0, isolation_level=None)
    try:
        try:
            lock.execute("BEGIN EXCLUSIVE")
        except sqlite3.OperationalError as e:
            raise Busy("another pvl process is working in this data directory") from e
        yield
    finally:
        lock.close()
