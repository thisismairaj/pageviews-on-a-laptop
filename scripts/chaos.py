"""Failure-injection suite on real data.

    python scripts/chaos.py SRC_RAW_DIR [--out RESULTS.md]

SRC_RAW_DIR holds real hourly files (for example data/raw after milestone 1). Each scenario builds a
fresh data directory from three of them, breaks something on purpose, then checks what the loader
and the reconcile step did about it. Downloads come from a local throttled server so a kill lands
mid-transfer every time and Wikimedia is not hit again.
"""
import argparse
import gzip
import http.server
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from pvl import manifest
from pvl.listing import Listed

HOURS = (0, 1, 2)


def name_of(h: int) -> str:
    return f"pageviews-20240101-{h:02d}0000.gz"


class Throttled(http.server.BaseHTTPRequestHandler):
    root: Path = Path(".")
    sleep = 0.02  # per 256 KiB, roughly 12 MB/s

    def do_GET(self):
        f = self.root / self.path.lstrip("/")
        if not f.exists():
            return self.send_error(404)
        body = f.read_bytes()
        start = 0
        rng = self.headers.get("Range")
        self.send_response(206 if rng else 200)
        if rng:
            start = int(rng[6:].split("-")[0])
        body = body[start:]
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            for i in range(0, len(body), 1 << 18):
                self.wfile.write(body[i : i + (1 << 18)])
                time.sleep(self.sleep)
        except ConnectionError:  # the client was killed on purpose
            pass

    def log_message(self, *a):
        pass


class Lab:
    def __init__(self, src: Path):
        self.src = src
        self.tmp = Path(tempfile.mkdtemp(prefix="pvl-chaos-"))
        handler = type("H", (Throttled,), {"root": src})
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def fresh(self, hours=HOURS, copy_raw=False) -> Path:
        d = Path(tempfile.mkdtemp(dir=self.tmp))
        con = manifest.connect(d)
        manifest.plan(con, [
            Listed(name_of(h), "2024-01-01", h, (self.src / name_of(h)).stat().st_size, "x",
                   f"{self.base}/{name_of(h)}") for h in hours
        ])
        con.close()
        if copy_raw:
            (d / "raw").mkdir()
            for h in hours:
                shutil.copy(self.src / name_of(h), d / "raw" / name_of(h))
            con = manifest.connect(d)
            for h in hours:
                manifest.update(con, name_of(h), status="downloaded")
            con.close()
        return d

    def cmd(self, d: Path, *args):
        return [sys.executable, "-m", "pvl.cli", "--data", str(d), *args]

    def run(self, d: Path, *args) -> subprocess.CompletedProcess:
        return subprocess.run(self.cmd(d, *args), capture_output=True, text=True)

    def start(self, d: Path, *args) -> subprocess.Popen:
        return subprocess.Popen(self.cmd(d, *args), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def close(self):
        self.srv.shutdown()
        shutil.rmtree(self.tmp, ignore_errors=True)


def reconcile_out(lab, d, hours="0-2"):
    r = lab.run(d, "reconcile", "--expected-hours", hours)
    return r.returncode, [l for l in r.stdout.splitlines() if l.startswith("PROBLEM")]


def wait_for(cond, timeout=60.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


# ---- scenarios: each returns (passed, evidence) ----------------------------------------------

def s_kill_during_download(lab):
    d = lab.fresh()
    p = lab.start(d, "download")
    started = wait_for(lambda: any(f.stat().st_size > 5_000_000 for f in (d / "raw").glob("*.part")) if (d / "raw").exists() else False)
    p.kill()  # TerminateProcess: no cleanup code runs, like kill -9
    p.wait()
    finals = list((d / "raw").glob("*.gz"))
    parts = list((d / "raw").glob("*.part"))
    lab.run(d, "download")
    lab.run(d, "load")
    code, problems = reconcile_out(lab, d)
    same = all((d / "raw" / name_of(h)).read_bytes() == (lab.src / name_of(h)).read_bytes() for h in HOURS)
    ok = started and parts and code == 0 and same
    return ok, (f"killed mid-transfer: {len(parts)} partial file(s), {len(finals)} complete; "
                f"after rerun all {len(HOURS)} files byte-identical to source={same}, reconcile exit={code}")


def s_kill_during_load(lab):
    d = lab.fresh(copy_raw=True)
    p = lab.start(d, "load")
    started = wait_for(lambda: (d / "parquet").exists(), 60)
    time.sleep(1.5)
    p.kill()
    p.wait()
    mid_code, mid_problems = reconcile_out(lab, d)
    lab.run(d, "load")
    code, problems = reconcile_out(lab, d)
    leftovers = list((d / "parquet").rglob("*.tmp"))
    ok = started and mid_code == 1 and code == 0 and not leftovers
    return ok, (f"killed mid-load: reconcile exit={mid_code} ({len(mid_problems)} problem(s) reported, not hidden); "
                f"after rerun exit={code}, leftover .tmp files={len(leftovers)}")


def s_truncated_file(lab):
    d = lab.fresh(copy_raw=True)
    f = d / "raw" / name_of(1)
    f.write_bytes(f.read_bytes()[:-5000])
    r = lab.run(d, "load")
    code, problems = reconcile_out(lab, d)
    has_parquet = (d / "parquet" / "good" / "day=2024-01-01" / "hour=01.parquet").exists()
    ok = code == 1 and any(name_of(1) in p for p in problems) and not has_parquet and "load: 2 ok, 1 failed" in r.stdout
    return ok, f"{r.stdout.strip()} | reconcile exit={code}, hour 1 parquet exists={has_parquet}, problems={len(problems)}"


def s_bit_flip_in_raw(lab):
    d = lab.fresh(copy_raw=True)
    f = d / "raw" / name_of(2)
    b = bytearray(f.read_bytes())
    b[len(b) // 2] ^= 0xFF
    f.write_bytes(bytes(b))
    r = lab.run(d, "load")
    code, problems = reconcile_out(lab, d)
    ok = code == 1 and any(name_of(2) in p for p in problems) and "1 failed" in r.stdout
    return ok, f"{r.stdout.strip()} | reconcile exit={code}, problems={len(problems)}"


def s_duplicate_content(lab):
    d = lab.fresh(copy_raw=True)
    shutil.copy(d / "raw" / name_of(0), d / "raw" / name_of(1))  # hour 1 is really hour 0
    con = manifest.connect(d)
    import hashlib
    h = hashlib.sha256((d / "raw" / name_of(0)).read_bytes()).hexdigest()
    for n in (name_of(0), name_of(1)):
        manifest.update(con, n, sha256=h)
    con.close()
    lab.run(d, "load")
    code, problems = reconcile_out(lab, d)
    ok = code == 1 and any("duplicate content" in p for p in problems)
    return ok, f"reconcile exit={code}, problems={[p[:90] for p in problems]}"


def s_missing_hour(lab):
    d = lab.fresh(hours=(0, 2), copy_raw=True)
    lab.run(d, "load")
    code, problems = reconcile_out(lab, d, "0-2")
    ok = code == 1 and any("missing hours [1]" in p for p in problems)
    return ok, f"reconcile exit={code}, first problem: {problems[0][:100] if problems else None}"


def s_garbage_lines(lab):  # one hour only
    d = lab.fresh(hours=(0,), copy_raw=True)
    f = d / "raw" / name_of(0)
    text = gzip.decompress(f.read_bytes()).decode("utf-8")
    bad = ["en only_two", "en Page abc 0", "en Page 5 xyz", "en Page -9 0", "a b c d e", ""] * 100
    f.write_bytes(gzip.compress((text + "\n".join(bad) + "\n").encode("utf-8")))
    r = lab.run(d, "load")
    code, problems = reconcile_out(lab, d, "0-0")
    con = manifest.connect(d)
    row = con.execute("select rows_in, rows_loaded, rows_quarantined from files").fetchone()
    ok = code == 0 and row["rows_in"] == row["rows_loaded"] + row["rows_quarantined"] and row["rows_quarantined"] >= 500
    return ok, (f"600 injected lines (500 non-empty bad + 100 blank): in={row['rows_in']:,} loaded={row['rows_loaded']:,} "
                f"quarantined={row['rows_quarantined']} reconcile exit={code}")


def s_parquet_bit_rot(lab):
    d = lab.fresh(hours=(0,), copy_raw=True)
    lab.run(d, "load")
    pq = d / "parquet" / "good" / "day=2024-01-01" / "hour=00.parquet"
    b = bytearray(pq.read_bytes())
    for i in range(len(b) // 3, len(b) // 3 + 64):  # damage 64 bytes in the middle of the data pages
        b[i] ^= 0xFF
    pq.write_bytes(bytes(b))
    code, problems = reconcile_out(lab, d, "0-0")
    ok = code == 1 and any("hour" in p or "00" in p or "corrupt" in p or "differ" in p for p in problems)
    return ok, f"reconcile exit={code}, problems={[p[:90] for p in problems]}"


def s_concurrent_loads(lab):
    d = lab.fresh(copy_raw=True)
    a = lab.start(d, "load")
    time.sleep(1.0)  # let the first one take the lock
    b = lab.start(d, "load")
    a.wait()
    b.wait()
    code, problems = reconcile_out(lab, d)
    codes = sorted((a.returncode, b.returncode))
    ok = code == 0 and codes == [0, 3]
    return ok, (f"exit codes of the two loaders={codes} (3 = refused, another pvl is working); "
                f"reconcile exit={code}, problems={[p[:90] for p in problems]}")


SCENARIOS = [
    ("1. kill -9 during download", "resumes, final files byte-identical to source", s_kill_during_download),
    ("2. kill -9 during load", "half-done state is reported, rerun converges, no leftovers", s_kill_during_load),
    ("3. truncated raw file", "that hour fails loudly, others load, no partial output", s_truncated_file),
    ("4. flipped bit in a raw file", "that hour fails loudly, no partial output", s_bit_flip_in_raw),
    ("5. same content under two names", "reconcile flags the duplicate", s_duplicate_content),
    ("6. a missing hour", "reconcile names the missing hour", s_missing_hour),
    ("7. 600 garbage lines inside a valid file", "all quarantined with reasons, rows in = loaded + quarantined", s_garbage_lines),
    ("8. bit rot in a Parquet file after load", "reconcile reports it", s_parquet_bit_rot),
    ("9. two loaders at the same time", "the second one refuses to start; the first finishes; reconcile OK", s_concurrent_loads),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src", type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    lab = Lab(args.src)
    rows, failed = [], 0
    try:
        for title, expect, fn in SCENARIOS:
            t = time.time()
            try:
                ok, evidence = fn(lab)
            except Exception as e:  # a crashing scenario is a failed scenario, reported as such
                ok, evidence = False, f"scenario crashed: {type(e).__name__}: {e}"
            failed += 0 if ok else 1
            rows.append((title, expect, ok, evidence, time.time() - t))
            print(f"{'PASS' if ok else 'FAIL'}  {title} ({time.time() - t:.0f}s)\n      {evidence}", flush=True)
    finally:
        lab.close()
    if args.out:
        lines = ["# Failure-injection results", "",
                 "| Scenario | Expected | Result | Evidence |", "|---|---|---|---|"]
        for title, expect, ok, ev, secs in rows:
            lines.append(f"| {title} | {expect} | {'PASS' if ok else 'FAIL'} | {ev.replace('|', '/')} |")
        args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n{len(rows) - failed}/{len(rows)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
