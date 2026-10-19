"""Polite, resumable, verified downloads. Two connections at most, identifying User-Agent."""
import gzip
import hashlib
import os
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import USER_AGENT, manifest

CHUNK = 1 << 20
MAX_CONNECTIONS = 2


def fetch_into(url: str, part: Path, expected: int) -> None:
    """Append to `part` until it is `expected` bytes long, resuming from what is already there."""
    offset = part.stat().st_size if part.exists() else 0
    if offset > expected:  # leftover that cannot be a prefix of the file: start over
        part.unlink()
        offset = 0
    if offset == expected:
        return
    headers = {"User-Agent": USER_AGENT}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as resp:
        if offset and resp.status != 206:  # server ignored Range: restart from zero
            offset = 0
        with open(part, "ab" if offset else "wb") as f:
            while chunk := resp.read(CHUNK):
                f.write(chunk)


def verify_gzip(path: Path) -> None:
    """Read to the end: gzip checks its own CRC32 and length trailer, so truncation or bit-rot raises."""
    with gzip.open(path, "rb") as g:
        while g.read(CHUNK):
            pass


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def download_one(url: str, expected: int, dest: Path) -> str:
    """Download, verify size and gzip integrity, then atomically move into place. Returns sha256."""
    part = dest.with_name(dest.name + ".part")
    fetch_into(url, part, expected)
    size = part.stat().st_size
    if size != expected:
        raise OSError(f"size {size} != expected {expected}")  # short file stays as .part to resume
    try:
        verify_gzip(part)
    except (OSError, EOFError, zlib.error) as e:  # complete length but corrupt: a resume cannot fix it
        part.unlink()
        raise OSError(f"gzip integrity check failed: {e}") from e
    digest = sha256_of(part)
    os.replace(part, dest)
    return digest


def run(con, raw_dir: Path) -> tuple[int, int]:
    """Download everything planned, failed, or marked downloaded but missing on disk."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    todo = []
    for status in ("planned", "failed", "downloaded"):
        for r in manifest.rows_with_status(con, status):
            if status != "downloaded" or not (raw_dir / r["name"]).exists():
                todo.append(r)
    ok = failed = 0
    with ThreadPoolExecutor(MAX_CONNECTIONS) as pool:
        futures = {
            pool.submit(download_one, r["url"], r["expected_bytes"], raw_dir / r["name"]): r
            for r in todo
        }
        for fut in as_completed(futures):
            name = futures[fut]["name"]
            try:
                manifest.update(con, name, status="downloaded", sha256=fut.result(), error=None)
                ok += 1
            except Exception as e:  # recorded, never swallowed: reconcile reports it
                manifest.update(con, name, status="failed", error=f"download: {e}"[:300])
                failed += 1
    return ok, failed
