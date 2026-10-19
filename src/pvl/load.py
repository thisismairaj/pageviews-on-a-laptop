"""Load one hourly file into Parquet. Every input line ends up in exactly one of two places:
the good file or the quarantine file, with a reason. Nothing is dropped silently."""
import gzip
import os
from pathlib import Path

import duckdb

from . import manifest
from .download import sha256_of

CHUNK = 1 << 20


def count_lines(path: Path) -> int:
    """Count lines without DuckDB, so the loader is checked against something it did not produce."""
    n, last = 0, b"\n"
    with gzip.open(path, "rb") as g:
        while chunk := g.read(CHUNK):
            n += chunk.count(b"\n")
            last = chunk[-1:]
    return n + (0 if last == b"\n" else 1)


CLASSIFY_SQL = """
CREATE TEMP TABLE classified AS
WITH raw AS (
    SELECT line FROM read_csv(?, columns={'line': 'VARCHAR'}, delim='\x1f\x1e\x1d', quote='', escape='',
                              header=false, compression='gzip')
), parts AS (SELECT line, string_split(line, ' ') AS p FROM raw)
SELECT line,
       p[1] AS wiki, p[2] AS title,
       TRY_CAST(p[3] AS BIGINT) AS views, TRY_CAST(p[4] AS BIGINT) AS bytes,
       CASE WHEN len(p) <> 4 THEN 'wrong field count'
            WHEN TRY_CAST(p[3] AS BIGINT) IS NULL THEN 'views not an integer'
            WHEN TRY_CAST(p[4] AS BIGINT) IS NULL THEN 'bytes not an integer'
            WHEN TRY_CAST(p[3] AS BIGINT) < 0 THEN 'negative views'
            ELSE NULL END AS reason
FROM parts
"""


def _atomic_copy(con, query: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    con.execute(f"COPY ({query}) TO '{tmp.as_posix()}' (FORMAT parquet, COMPRESSION zstd)")
    os.replace(tmp, dest)


def _parquet_rows(path: Path) -> int:
    return duckdb.sql(f"SELECT count(*) FROM read_parquet('{path.as_posix()}')").fetchone()[0]


def load_file(raw: Path, good: Path, quarantine: Path) -> tuple[int, int, int]:
    """Returns (rows_in, rows_loaded, rows_quarantined), where loaded and quarantined are read
    back from the files on disk. Raises if they do not add up to rows_in; leaves no output then."""
    rows_in = count_lines(raw)
    con = duckdb.connect()
    try:
        con.execute(CLASSIFY_SQL, [raw.as_posix()])
        _atomic_copy(con, "SELECT wiki, title, views, bytes FROM classified WHERE reason IS NULL", good)
        _atomic_copy(con, "SELECT line, reason FROM classified WHERE reason IS NOT NULL", quarantine)
    finally:
        con.close()
    loaded, quarantined = _parquet_rows(good), _parquet_rows(quarantine)
    if rows_in != loaded + quarantined:
        good.unlink(missing_ok=True)
        quarantine.unlink(missing_ok=True)
        raise ValueError(f"rows in={rows_in} but loaded={loaded} + quarantined={quarantined}")
    return rows_in, loaded, quarantined


def run(con, raw_dir: Path, out_dir: Path) -> tuple[int, int]:
    ok = failed = 0
    for r in manifest.rows_with_status(con, "downloaded") + manifest.rows_with_status(con, "load_failed"):
        good = out_dir / "good" / f"day={r['day']}" / f"hour={r['hour']:02d}.parquet"
        quar = out_dir / "quarantine" / f"day={r['day']}" / f"hour={r['hour']:02d}.parquet"
        try:
            n_in, n_ok, n_bad = load_file(raw_dir / r["name"], good, quar)
            manifest.update(con, r["name"], status="loaded", rows_in=n_in, rows_loaded=n_ok,
                            rows_quarantined=n_bad, good_sha256=sha256_of(good),
                            quarantine_sha256=sha256_of(quar), error=None)
            ok += 1
        except Exception as e:
            manifest.update(con, r["name"], status="load_failed", error=f"load: {e}"[:300])
            failed += 1
    return ok, failed
