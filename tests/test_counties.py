"""Counties as configuration.

The FDOR NAL and the DBPR registry are STATEWIDE files with identical structure,
so a second county was never a code problem. What made it one was two constants:
`capacity.py` read Miami-Dade's zoning layer by name, and `site_coords` hardcoded
EPSG:2236 plus a Miami-Dade bounding box — which would have put Broward's
buildings in the sea or thrown them away entirely.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CFG = json.loads((ROOT / "backend" / "prospect" / "config.json").read_text(encoding="utf-8"))

spec = importlib.util.spec_from_file_location("bt_c", ROOT / "scripts/prospect/build_targets.py")
bt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bt)


def counties():
    return {k: v for k, v in CFG["counties"].items() if isinstance(v, dict)}


def test_the_active_county_resolves_from_its_display_name():
    """config.county is "Miami-Dade"; the key is "DADE"."""
    c = bt.county_config()
    assert c["dbpr_county"] == "Miami-Dade"
    assert c["state_plane"] == 2236


def test_every_county_carries_what_used_to_be_hardcoded():
    for key, c in counties().items():
        assert c.get("state_plane"), key
        assert len(c.get("bbox") or []) == 4, key
        assert "zoning_layer" in c, key
        assert c.get("fdor_county") and c.get("dbpr_county"), key


def test_each_bounding_box_is_ordered_and_in_florida():
    for key, c in counties().items():
        x0, y0, x1, y1 = c["bbox"]
        assert x0 < x1 and y0 < y1, f"{key} bbox is inverted"
        assert -88 < x0 < -79 and 24 < y0 < 32, f"{key} bbox is not in Florida"


def test_an_unknown_county_fails_loudly_and_says_where_to_add_it():
    with pytest.raises(SystemExit) as e:
        bt.county_config("ATLANTIS")
    msg = str(e.value)
    assert "config.counties" in msg
    assert "not new code" in msg


def test_a_county_with_no_zoning_layer_does_not_borrow_another_ones():
    """A confident envelope for the wrong jurisdiction is worse than no
    envelope, so an unwired county must resolve to nothing."""
    from backend.prospect import capacity
    assert capacity._load("") == []


def test_capacity_reads_the_configured_layer_not_a_constant():
    from backend.prospect import capacity
    src = (ROOT / "backend" / "prospect" / "capacity.py").read_text(encoding="utf-8")
    assert 'ZONING_LAYER' in src
    assert '_load("mdc_zoning")' not in src, "the layer name is configuration now"
    assert capacity.ZONING_LAYER == counties()["DADE"]["zoning_layer"]


def test_the_projection_and_bbox_are_not_literals_in_the_builder():
    src = (ROOT / "scripts" / "prospect" / "build_targets.py").read_text(encoding="utf-8")
    assert "Transformer.from_crs(2236" not in src, "EPSG comes from the county"
    assert "-81.0 < lon < -80.0" not in src, "the bbox comes from the county"


def test_palm_beach_is_declared_without_pretending_it_is_ready():
    """Listing a county whose zoning is not wired is fine; claiming capacity
    works there would not be."""
    pb = counties()["PALM BEACH"]
    assert pb["zoning_layer"] == ""
    assert not pb["nal_url"], "no roll URL until a year is chosen"
