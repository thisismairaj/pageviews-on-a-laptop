import duckdb
import pytest
from conftest import make_gz

from pvl.load import count_lines, load_file

GOOD = ["en Main_Page 100 0", "en.m Python_(programming_language) 5 0", "de Berlin 7 0"]


def run(tmp_path, lines, trailing_newline=True):
    raw = tmp_path / "x.gz"
    make_gz(raw, lines, trailing_newline)
    return load_file(raw, tmp_path / "good.parquet", tmp_path / "quar.parquet")


def test_clean_file_loads_everything(tmp_path):
    assert run(tmp_path, GOOD) == (3, 3, 0)


def test_bad_lines_are_quarantined_with_a_reason_not_dropped(tmp_path):
    bad = ["en too few", "en Page notanumber 0", "en Page 5 notanumber", "en Page -3 0", "a b c d e"]
    assert run(tmp_path, GOOD + bad) == (8, 3, 5)
    q = (tmp_path / "quar.parquet").as_posix()
    reasons = {r[0] for r in duckdb.sql(f"select reason from read_parquet('{q}')").fetchall()}
    assert reasons == {"wrong field count", "views not an integer", "bytes not an integer", "negative views"}


def test_last_line_without_newline_is_counted(tmp_path):
    assert run(tmp_path, GOOD, trailing_newline=False) == (3, 3, 0)


def test_blank_line_in_the_middle_is_accounted_for(tmp_path):
    n_in, ok, q = run(tmp_path, GOOD[:1] + [""] + GOOD[1:])
    assert n_in == ok + q == 4


def test_line_containing_the_csv_delimiter_byte_is_accounted_for(tmp_path):
    n_in, ok, q = run(tmp_path, GOOD + ["en We\x1fird_Title 5 0"])
    assert n_in == ok + q == 4


def test_a_mismatch_raises_and_leaves_no_output(tmp_path, monkeypatch):
    import pvl.load as load

    monkeypatch.setattr(load, "count_lines", lambda p: 999)
    raw = tmp_path / "x.gz"
    make_gz(raw, GOOD)
    with pytest.raises(ValueError, match="rows in=999"):
        load.load_file(raw, tmp_path / "g.parquet", tmp_path / "q.parquet")
    assert not (tmp_path / "g.parquet").exists()
    assert not (tmp_path / "q.parquet").exists()


def test_count_lines_matches_gzip_content(tmp_path):
    raw = tmp_path / "x.gz"
    make_gz(raw, GOOD)
    assert count_lines(raw) == 3
