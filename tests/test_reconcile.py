from conftest import make_gz

from pvl import manifest
from pvl.listing import Listed
from pvl.load import run as load_run
from pvl.reconcile import reconcile


def build(tmp_path, hours):
    data = tmp_path / "data"
    con = manifest.connect(data)
    raw = data / "raw"
    raw.mkdir(parents=True)
    listed = []
    for h in hours:
        name = f"pageviews-20240101-{h:02d}0000.gz"
        make_gz(raw / name, ["en A 1 0", "en B 2 0", "bad line"])
        listed.append(Listed(name, "2024-01-01", h, 1, "x", "u"))
    manifest.plan(con, listed)
    for h in hours:
        manifest.update(con, f"pageviews-20240101-{h:02d}0000.gz", status="downloaded")
    assert load_run(con, raw, data / "parquet") == (len(list(hours)), 0)
    return con, data / "parquet"


def test_complete_day_reconciles(tmp_path):
    con, out = build(tmp_path, range(24))
    problems, t = reconcile(con, out)
    assert problems == []
    assert (t["rows_in"], t["rows_loaded"], t["rows_quarantined"]) == (72, 48, 24)


def test_missing_hours_are_reported(tmp_path):
    con, out = build(tmp_path, [h for h in range(24) if h not in (5, 17)])
    problems, _ = reconcile(con, out)
    assert any("missing hours [5, 17]" in p for p in problems)


def test_deleted_parquet_is_caught(tmp_path):
    con, out = build(tmp_path, range(24))
    (out / "good" / "day=2024-01-01" / "hour=03.parquet").unlink()
    problems, _ = reconcile(con, out)
    assert any("pageviews-20240101-030000.gz" in p for p in problems)


def test_load_is_idempotent(tmp_path):
    con, out = build(tmp_path, range(3))
    before = {p.name: p.stat().st_mtime_ns for p in (out / "good").rglob("*.parquet")}
    assert load_run(con, tmp_path / "data" / "raw", out) == (0, 0)  # nothing left to do
    assert before == {p.name: p.stat().st_mtime_ns for p in (out / "good").rglob("*.parquet")}
