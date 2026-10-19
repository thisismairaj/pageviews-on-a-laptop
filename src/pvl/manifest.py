"""SQLite manifest: one row per hourly file. The single source of truth for what was done."""
import sqlite3
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
    status         TEXT NOT NULL DEFAULT 'planned',  -- planned | downloaded | loaded | failed
    sha256         TEXT,
    rows_in        INTEGER,   -- counted independently of the loader
    rows_loaded    INTEGER,
    rows_quarantined INTEGER,
    error          TEXT
);
"""


def connect(data_dir: Path) -> sqlite3.Connection:
    data_dir.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(data_dir / "manifest.sqlite")
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
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
