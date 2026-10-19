import pytest

from pvl import manifest


def test_second_holder_is_refused_and_lock_is_released_afterwards(tmp_path):
    with manifest.exclusive(tmp_path):
        with pytest.raises(manifest.Busy):
            with manifest.exclusive(tmp_path):
                pass
    with manifest.exclusive(tmp_path):  # released when the first holder finished
        pass


def test_lock_is_released_when_the_holder_dies(tmp_path):
    import subprocess
    import sys
    import time

    code = (
        "import sys, time; from pathlib import Path; from pvl import manifest\n"
        "with manifest.exclusive(Path(sys.argv[1])):\n"
        "    print('held', flush=True); time.sleep(60)\n"
    )
    p = subprocess.Popen([sys.executable, "-c", code, str(tmp_path)], stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "held"
    with pytest.raises(manifest.Busy):
        with manifest.exclusive(tmp_path):
            pass
    p.kill()  # no cleanup code runs
    p.wait()
    time.sleep(0.2)
    with manifest.exclusive(tmp_path):
        pass
