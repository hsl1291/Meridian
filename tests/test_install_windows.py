"""install.py's COM and schtasks plumbing, against the real Windows APIs.

Everything else in the installer is tested on any OS; this part can only be
tested where IShellLink exists, so it runs in the windows-latest CI job and
is skipped elsewhere. A wrong vtable slot doesn't raise a clean error -- it
calls a different method with the wrong arguments -- so the only honest
check is a round trip: write a shortcut, read it back, compare.
"""
import importlib.util
import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="IShellLink is Windows-only")

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def install():
    spec = importlib.util.spec_from_file_location("install_mod", ROOT / "install.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_shortcut_round_trip(install, tmp_path):
    lnk = tmp_path / "Test.lnk"
    work = tmp_path / "Old Meridian"
    work.mkdir()
    target = Path(sys.executable)
    install.create_shortcut(lnk, target, f'"{work / "launch.py"}" --server-only',
                            "test", None, working_dir=work)
    assert lnk.exists()

    got = install.read_shortcut(lnk)
    assert Path(got["working_dir"]) == work
    assert Path(got["target"]).resolve() == target.resolve()
    assert got["arguments"] == f'"{work / "launch.py"}" --server-only'


def test_an_old_shortcut_leads_back_to_its_folder(install, tmp_path):
    old = tmp_path / "old"
    (old / "data").mkdir(parents=True)
    lnk = tmp_path / "Groundwork.lnk"
    install.create_shortcut(lnk, Path(sys.executable), f'"{old / "launch.py"}"',
                            working_dir=old)
    roots = install.data_import.root_from_launch_ref(**install.read_shortcut(lnk))
    assert install.data_import.previous_installs(roots, ROOT) == [old]


def test_reading_a_missing_shortcut_is_harmless(install, tmp_path):
    assert install.read_shortcut(tmp_path / "nope.lnk") == {}


def test_task_command_for_a_missing_task_is_empty(install):
    assert install._task_command("Meridian test task that does not exist") == {}
