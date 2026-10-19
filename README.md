# pageviews-on-a-laptop

Load Wikimedia's hourly pageview dumps on one ordinary laptop, and prove that every row is accounted for.

**Machine:** Intel Core i5-10310U (4 cores, 8 threads), 31.6 GB RAM, no GPU, Windows 11, Python 3.12, DuckDB 1.5.6. Every number below was measured on this machine. Anything estimated says so.

## Why this matters

Loading billions of rows is a solved problem. Knowing that none of them went missing along the way is the part people skip. Here, every input line ends up in one of two places, the good file or a quarantine file with a reason, and a check re-reads the files on disk to show that rows in equal rows loaded plus rows quarantined. If they don't, the loader deletes its output and marks the file failed instead of leaving a plausible partial result.

The limit to know about: this is a single machine, so it will not scale past what fits on one disk. One month (about 36 GB compressed, estimated from the first day) is the realistic ceiling here.

## Result so far: one day

2024-01-01, all 24 hourly files, from a cold start:

| | |
|---|---|
| Files | 24 of 24 |
| Compressed size | 1,213,999,694 bytes |
| Rows in | 143,351,375 |
| Rows loaded | 143,351,375 |
| Rows quarantined | 0 |
| Download | 174 s (2 connections) |
| Load (count, classify, write Parquet) | 137 s, about 1.05 million rows/s |
| Parquet on disk | 1,338,033,466 bytes (not yet optimized, see below) |
| Second run, same day | 2 s, nothing changed |
| Reconcile | OK: every row is accounted for |

A first query over the day: row count and total views in 0.30 s; the five most-viewed English pages in 3.35 s. These were not run cold, and the file cache was likely warm.

Not measured yet: peak memory, CPU use during load, behavior under failure (kill mid-load, truncated or duplicated files). Those are the next milestone.

## Run it

```
uv venv --python 3.12 .venv
uv pip install --python .venv/Scripts/python.exe -e ".[dev]"
.venv/Scripts/pvl run --day 2024-01-01
```

`pvl run` plans, downloads, loads and reconciles. The steps also run on their own: `pvl plan`, `pvl download`, `pvl load`, `pvl reconcile`. Data goes to `./data` (ignored by git). Re-running is safe and cheap.

## How it works

- **Plan:** read Wikimedia's directory listing and record each hourly file in a SQLite manifest.
- **Download:** two connections at most, a User-Agent that says who and why, resume from a `.part` file, and verify size and gzip integrity before the file gets its final name.
- **Load:** DuckDB splits each line and writes good rows and quarantined rows to separate Parquet files. A line count made without DuckDB checks the result.
- **Reconcile:** re-read the Parquet files, compare with the manifest, report missing hours.

Choices and what they cost are in [DECISIONS.md](DECISIONS.md), including two bugs the tests caught.

## Data

Wikimedia pageviews, CC0. Page titles and counts only; no personal data.

## Tests

```
.venv/Scripts/python -m pytest
```

15 tests: parsing, loading (bad lines, blank lines, a line with the delimiter byte), download resume, a corrupted download, a truncated download, reconcile (missing hours, a deleted file), and idempotent re-runs.
