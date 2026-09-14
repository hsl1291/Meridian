"""The check that would have caught the merge regression: everything imports."""
import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BACKEND = ["backend.app", "backend.site_screen", "backend.shared_data",
           "backend.florida_lookups", "backend.prospect.routes",
           "backend.prospect.db", "backend.prospect.declaration",
           "backend.prospect.capacity", "backend.prospect.memo",
           "backend.prospect.market_dd", "backend.prospect.multiplier",
           "backend.prospect.norm", "backend.prospect.trends"]


@pytest.mark.parametrize("mod", BACKEND)
def test_backend_modules_import(mod):
    importlib.import_module(mod)


SCRIPTS = sorted(p.name for p in (ROOT / "scripts" / "prospect").glob("*.py"))


@pytest.mark.parametrize("script", SCRIPTS)
def test_prospect_scripts_parse(script):
    """Syntax only. Always expected to pass -- it is the weaker half of the pair
    below, and exists so a genuine syntax error is distinguishable from the
    import breakage."""
    path = ROOT / "scripts" / "prospect" / script
    compile(path.read_text(), str(path), "exec")


@pytest.mark.xfail(reason="Phase 0.1: the merge moved these into scripts/prospect/ "
                          "without their path wiring -- ROOT resolves to scripts/ "
                          "and backend.db is now backend.prospect.db",
                   strict=True)
@pytest.mark.parametrize("script", SCRIPTS)
def test_prospect_scripts_import(script):
    """Runs each script's module-level code in a subprocess. Expected to fail
    today; delete the xfail when Phase 0.1 lands and this becomes the check that
    keeps the README's first-run sequence honest."""
    import subprocess
    path = ROOT / "scripts" / "prospect" / script
    r = subprocess.run(
        [sys.executable, "-c",
         "import importlib.util,sys;"
         f"spec=importlib.util.spec_from_file_location('m', r'{path}');"
         "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)"],
        capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr.strip().splitlines()[-1] if r.stderr else "failed"


def test_declaration_selftest_passes():
    from backend.prospect.declaration import _selftest
    assert _selftest()
