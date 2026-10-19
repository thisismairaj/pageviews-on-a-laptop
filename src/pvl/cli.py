"""pvl plan | download | load | reconcile | run"""
import argparse
import sys
from pathlib import Path

from . import download, listing, load, manifest
from .reconcile import reconcile


def _plan(con, args) -> None:
    year, month, day = (int(x) for x in args.day.split("-"))
    files = [f for f in listing.fetch_month_listing(year, month) if f.day == args.day]
    added = manifest.plan(con, files)
    print(f"{args.day}: {len(files)} files listed, {added} new in manifest")


def _download(con, args) -> None:
    ok, failed = download.run(con, args.data / "raw")
    print(f"download: {ok} ok, {failed} failed")


def _load(con, args) -> None:
    ok, failed = load.run(con, args.data / "raw", args.data / "parquet")
    print(f"load: {ok} ok, {failed} failed")


def parse_hours(spec: str) -> list[int]:
    """'0-23' or '0-2,5' -> [0, 1, 2, 5]"""
    out: list[int] = []
    for part in spec.split(","):
        lo, _, hi = part.partition("-")
        out.extend(range(int(lo), int(hi or lo) + 1))
    return out


def _reconcile(con, args) -> int:
    problems, t = reconcile(con, args.data / "parquet", parse_hours(args.expected_hours))
    print(f"files={t['files']} loaded={t['loaded_files']} rows_in={t['rows_in']:,} "
          f"loaded={t['rows_loaded']:,} quarantined={t['rows_quarantined']:,}")
    for p in problems:
        print("PROBLEM:", p)
    print("OK: every row is accounted for" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pvl")
    ap.add_argument("--data", type=Path, default=Path("data"), help="data directory (default ./data)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "run"):
        p = sub.add_parser(name)
        p.add_argument("--day", required=True, help="YYYY-MM-DD")
    for name in ("download", "load"):
        sub.add_parser(name)
    rec = sub.add_parser("reconcile")
    rec.add_argument("--expected-hours", default="0-23", help="hours every day must have (default 0-23)")
    args = ap.parse_args(argv)

    if args.cmd in ("download", "load", "run"):
        try:
            with manifest.exclusive(args.data):
                return _dispatch(args)
        except manifest.Busy as e:
            print(f"pvl: {e}", file=sys.stderr)
            return 3
    return _dispatch(args)


def _dispatch(args) -> int:
    con = manifest.connect(args.data)
    if args.cmd == "plan":
        _plan(con, args)
    elif args.cmd == "download":
        _download(con, args)
    elif args.cmd == "load":
        _load(con, args)
    elif args.cmd == "reconcile":
        return _reconcile(con, args)
    elif args.cmd == "run":
        _plan(con, args)
        _download(con, args)
        _load(con, args)
        args.expected_hours = "0-23"
        return _reconcile(con, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
