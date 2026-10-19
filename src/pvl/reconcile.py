"""The proof: for every file and in total, rows in = rows loaded + rows quarantined.
Re-reads the Parquet files on disk instead of trusting the manifest, and reports missing hours."""
from pathlib import Path

import duckdb


def _rows(path: Path) -> int | None:
    if not path.exists():
        return None
    return duckdb.sql(f"SELECT count(*) FROM read_parquet('{path.as_posix()}')").fetchone()[0]


def reconcile(con, out_dir: Path) -> tuple[list[str], dict]:
    """Returns (problems, totals). An empty problems list means everything is accounted for."""
    problems: list[str] = []
    totals = {"files": 0, "loaded_files": 0, "rows_in": 0, "rows_loaded": 0, "rows_quarantined": 0}
    hours_by_day: dict[str, set[int]] = {}

    for r in con.execute("SELECT * FROM files ORDER BY name"):
        totals["files"] += 1
        hours_by_day.setdefault(r["day"], set()).add(r["hour"])
        if r["status"] != "loaded":
            problems.append(f"{r['name']}: status is {r['status']}" + (f" ({r['error']})" if r["error"] else ""))
            continue
        good = out_dir / "good" / f"day={r['day']}" / f"hour={r['hour']:02d}.parquet"
        quar = out_dir / "quarantine" / f"day={r['day']}" / f"hour={r['hour']:02d}.parquet"
        on_disk_good, on_disk_quar = _rows(good), _rows(quar)
        if on_disk_good is None or on_disk_quar is None:
            problems.append(f"{r['name']}: manifest says loaded but a Parquet file is missing")
            continue
        if (on_disk_good, on_disk_quar) != (r["rows_loaded"], r["rows_quarantined"]):
            problems.append(f"{r['name']}: Parquet rows {on_disk_good}+{on_disk_quar} differ from manifest "
                            f"{r['rows_loaded']}+{r['rows_quarantined']}")
        if r["rows_in"] != on_disk_good + on_disk_quar:
            problems.append(f"{r['name']}: rows in {r['rows_in']} != loaded {on_disk_good} + quarantined {on_disk_quar}")
        totals["loaded_files"] += 1
        totals["rows_in"] += r["rows_in"]
        totals["rows_loaded"] += on_disk_good
        totals["rows_quarantined"] += on_disk_quar

    for day, hours in sorted(hours_by_day.items()):
        missing = sorted(set(range(24)) - hours)
        if missing:
            problems.append(f"{day}: missing hours {missing}")
    if totals["rows_in"] != totals["rows_loaded"] + totals["rows_quarantined"]:
        problems.append("TOTAL: rows in != loaded + quarantined")
    return problems, totals
