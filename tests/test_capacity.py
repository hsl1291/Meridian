"""Development capacity (capacity.analyze), especially the Live Local Act
scenario. _jurisdiction_ceiling had `acc[n] = d` where the loop variable
holding the municipality name was `m` -- `n` was never defined anywhere in
the function, so the FIRST zoning feature with a DENSITY value raised
NameError. Any request against a commercial/industrial/mixed-use parcel
(the land types the Act reaches) triggered it, which is not a rare or
malformed input -- it is the normal case /api/capacity exists to handle.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backend.prospect.capacity as cap  # noqa: E402


def _feature(munic, density=None, far=None, height=None, lutype="C", mixuse="NO",
             zone="T6-8", ring=((-1, -1), (1, -1), (1, 1), (-1, 1), (-1, -1))):
    props = {"MUNICNAME": munic, "GENRLLUTYPE": lutype, "MIXUSE": mixuse, "ZONE": zone}
    if density is not None:
        props["DENSITY"] = density
    if far is not None:
        props["FAR"] = far
    if height is not None:
        props["MAXHEIGHT"] = height
    geom = {"type": "Polygon", "coordinates": [[list(p) for p in ring]]}
    bbox = cap._bbox(geom["coordinates"])
    return (bbox, geom, props)


@pytest.fixture(autouse=True)
def _reset_module_caches():
    """These are module-level and memoized on first use -- without a reset
    each test would see whatever an earlier test already computed. _load and
    ZONING_LAYER are monkeypatched by individual tests and MUST be restored,
    or every test file that runs after this one in the same session would
    see this file's fake zoning layer instead of the real one."""
    orig_load, orig_layer = cap._load, cap.ZONING_LAYER
    cap._ceilings = None
    cap._cache.clear()
    cap._grids.clear()
    cap._height_cache.clear()
    yield
    cap._load, cap.ZONING_LAYER = orig_load, orig_layer
    cap._ceilings = None
    cap._cache.clear()
    cap._grids.clear()
    cap._height_cache.clear()


def test_jurisdiction_ceiling_does_not_crash_on_the_first_real_density_value():
    """The actual regression: this used to raise NameError unconditionally
    the first time any feature's density beat the running max of 0.0."""
    cap._load = lambda layer: [_feature("Testville", density=50)]
    assert cap._jurisdiction_ceiling("Testville") == {"max_density": 50.0}


def test_jurisdiction_ceiling_tracks_the_highest_density_per_municipality():
    cap._load = lambda layer: [
        _feature("Testville", density=20),
        _feature("Testville", density=80),   # higher -- must win
        _feature("Testville", density=45),   # lower -- must not overwrite the max
        _feature("Otherville", density=10),
    ]
    assert cap._jurisdiction_ceiling("Testville") == {"max_density": 80.0}
    assert cap._jurisdiction_ceiling("Otherville") == {"max_density": 10.0}


def test_jurisdiction_ceiling_is_unknown_for_an_unseen_municipality():
    cap._load = lambda layer: [_feature("Testville", density=50)]
    assert cap._jurisdiction_ceiling("Nowhereville") == {"max_density": None}


def test_analyze_resolves_a_live_local_eligible_parcel_without_raising():
    """End to end through analyze(): a commercial parcel at (0, 0), on a lot
    large enough to produce a non-trivial unit count, must return a Live
    Local scenario instead of crashing."""
    cap._load = lambda layer: [_feature(
        "Testville", density=36, far=2.0, height=8, lutype="C", zone="BU-2")]
    cap.ZONING_LAYER = "fake_layer"
    out = cap.analyze(0.0, 0.0, lot_sf=43_560)  # exactly 1 acre
    assert out["resolved"] is True
    ll = next(s for s in out["scenarios"] if s["path"].startswith("Live Local"))
    assert ll["units"] == 36
    assert ll.get("administrative") is True
