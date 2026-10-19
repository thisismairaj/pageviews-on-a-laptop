import pytest
from conftest import make_gz

from pvl.download import download_one

LINES = [f"en Page_{i} {i} 0" for i in range(2000)]


def test_resume_continues_a_partial_file_and_verifies(tmp_path, http_server):
    base, files = http_server
    data = make_gz(tmp_path / "src.gz", LINES)
    files["/f.gz"] = data
    dest = tmp_path / "out" / "f.gz"
    dest.parent.mkdir()
    (dest.parent / "f.gz.part").write_bytes(data[: len(data) // 2])  # a crash left half a file
    digest = download_one(f"{base}/f.gz", len(data), dest)
    assert dest.read_bytes() == data and len(digest) == 64
    assert not (dest.parent / "f.gz.part").exists()


def test_corrupt_download_is_rejected_and_never_reaches_the_final_name(tmp_path, http_server):
    base, files = http_server
    data = bytearray(make_gz(tmp_path / "src.gz", LINES))
    data[len(data) // 2] ^= 0xFF  # same length, flipped bits
    files["/f.gz"] = bytes(data)
    dest = tmp_path / "out" / "f.gz"
    dest.parent.mkdir()
    with pytest.raises(OSError, match="integrity"):
        download_one(f"{base}/f.gz", len(data), dest)
    assert not dest.exists()
    assert not (dest.parent / "f.gz.part").exists()


def test_short_file_is_an_error_and_keeps_the_partial_for_resume(tmp_path, http_server):
    base, files = http_server
    data = make_gz(tmp_path / "src.gz", LINES)
    files["/f.gz"] = data[:-100]  # server cuts the file short
    dest = tmp_path / "out" / "f.gz"
    dest.parent.mkdir()
    with pytest.raises(OSError, match="size"):
        download_one(f"{base}/f.gz", len(data), dest)
    assert not dest.exists()
    assert (dest.parent / "f.gz.part").exists()
