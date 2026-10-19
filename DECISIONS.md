# Decisions log

Newest at the bottom. Each entry says what was decided, why, and what it costs.

## Dataset: Wikimedia hourly pageviews

CC0 licence, reachable from here, no personal data (page titles and counts only). Checked
alternatives first: NYC taxi trips returned HTTP 403 on every download from this network
(headers worked, bodies did not, on both the CloudFront host and the S3 mirror), and GH Archive
events carry user logins, so neither fit. Cost: the download speed here is about 2 MB/s, so
download time, not compute, sets the pace.

## Hardware stated up front

Intel Core i5-10310U (4 cores, 8 threads), 31.6 GB RAM, no GPU, Windows 11. Every number in this
repo was measured on that machine. Estimates are labelled as estimates.

## Manifest in SQLite

One row per hourly file with status and counts. SQLite is in the standard library, transactional,
and good enough for thousands of rows. A manifest in DuckDB or Parquet would add nothing here.

## No official checksums, so three checks instead

Wikimedia's directory listing publishes file sizes but no md5 or sha files that I could find. So a
download is accepted only if (1) its size equals the listed size, (2) gzip reads to the end,
which verifies gzip's own CRC32 and length trailer, and (3) it then gets a sha256 recorded in the
manifest. The sha256 detects later changes, not tampering at the source.

## Polite downloads

Two connections at most, a User-Agent that says who and why, resume from a `.part` file with an
HTTP Range request, and the final name only appears after verification (atomic rename).

## The invariant: rows in = rows loaded + rows quarantined

"Rows in" is counted by plain Python reading the gzip, not by the loader, so the loader is checked
against something it did not produce. "Loaded" and "quarantined" are read back from the Parquet
files on disk. If they do not add up, the loader deletes its output and marks the file failed
instead of leaving a plausible-looking partial result.

## Quarantine, not drop

A line that does not have four fields, or whose counts are not integers, or whose views are
negative goes to a quarantine Parquet file with the raw line and a reason. Nothing is discarded
silently.

## Python 3.12 in a project venv

The machine's global Python is 3.14, which already prints compatibility warnings from other
packages. This project pins 3.12 with uv and never installs into the global interpreter.

## What the tests found (the part worth writing down)

1. **One-byte delimiter collision.** To read each line as a single value, the first loader used
   a one-byte delimiter (0x1F). A test with that byte inside a title made DuckDB refuse the whole
   file. Failing loudly was right, but one odd byte should not cost an hour of data, so the loader
   now uses a three-byte sequence. Known limit: a line containing that exact sequence still fails
   the file, loudly.
2. **An exception I did not catch.** My gzip integrity check caught `OSError` and `EOFError`, but
   a flipped bit inside the stream raises `zlib.error`, which is neither. The corrupt file escaped
   with its `.part` left behind and no clear message. A test that flips one bit caught it before
   any real file did.

## Milestone 2: break it on purpose

`scripts/chaos.py` builds a fresh data directory from three real hourly files, breaks one thing,
and checks what the loader and the reconcile step did. Downloads come from a local throttled
server so a kill lands mid-transfer every time and Wikimedia is not hit again. "kill -9" on
Windows is `Popen.kill()`, which calls TerminateProcess: no cleanup code runs in the victim.

### What failed, in the order I found it

1. **The first run said 5 of 9 passed, and most of the failures were my harness.** The check that
   every day has 24 hours reported 21 missing hours, because the test only loaded 3. I added
   `reconcile --expected-hours` instead of weakening the check.
2. **Bit rot inside a Parquet file passed reconcile.** I damaged 64 bytes in the middle of a good
   file and `reconcile` still said every row was accounted for. The row count it read comes from
   the file's footer, which was intact. Fix: record a sha256 of each Parquet file when it is
   written and re-check it. Cost, measured on the one-day run: about 9 s extra on the 146 s load,
   and a 4 s reconcile over 1.3 GB. A row count proves the shape, a checksum proves the content.
3. **Two loaders at once passed once and failed once.** The pass was luck. On the next run one
   loader hit Windows "Access is denied" renaming a file the other had open, and the hour was
   marked failed. A second bug was hiding behind it: only download failures were retried, so a
   failed load stayed failed forever. Fixes: an exclusive lock per data directory, held as a SQLite
   lock on its own file so the operating system releases it even when the holder is killed (a PID
   lock file would go stale after a kill), and a separate `load_failed` status that the next load
   retries. The second loader now refuses with exit code 3.

### What this does not cover

Disk full, power loss during a rename (it relies on NTFS replacing a file atomically), a network
partition mid-listing, and a source that silently serves different bytes under the same name. The
checksums detect change after the fact; they cannot tell you the source was right in the first
place.
