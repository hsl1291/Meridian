"""Zoning code -> colour family, and the storey cap a form-based code declares.

Miami is the app's home market and had the worst colour accuracy in it: the
generic letter rules read Civic Space as commercial, dropped the Miami 21
Districts into `other`, and flattened every T6 tier onto one colour.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app import _zone_category, _zone_stories  # noqa: E402

MIAMI = "City of Miami"


def test_miami21_transects():
    assert _zone_category("T3-R", MIAMI) == "residential"
    assert _zone_category("T4-L", MIAMI) == "residential"
    assert _zone_category("T5-O", MIAMI) == "mixed"
    assert _zone_category("T6-8-O", MIAMI) == "downtown"
    assert _zone_category("T6-80-O", MIAMI) == "downtown"


def test_miami21_codes_that_collide_with_generic_rules():
    # CS is Civic Space. The generic `^(C|B|...)` rule called it commercial.
    assert _zone_category("CS", MIAMI) == "open"
    # CI is Civic Institutional, not retail.
    assert _zone_category("CI", MIAMI) == "special"
    assert _zone_category("CI-HD", MIAMI) == "special"
    # D1 Work Place / D2 Industrial / D3 Marine all fell through to `other`.
    for code in ("D1", "D2", "D3"):
        assert _zone_category(code, MIAMI) == "industrial", code


def test_miami_dade_county_codes():
    assert _zone_category("EU-M", "Miami-Dade County") == "residential"
    assert _zone_category("GU", "Miami-Dade County") == "special"
    assert _zone_category("RU-4M", "Miami-Dade County") == "residential"
    assert _zone_category("BU-1A", "Miami-Dade County") == "commercial"


def test_local_vocabulary_does_not_leak_to_other_cities():
    """CS is Civic Space in Miami and Commercial Service elsewhere. Without a
    municipality the generic rules must still apply, or every wired city inherits
    Miami's dictionary."""
    assert _zone_category("CS") == "commercial"
    assert _zone_category("CS", "City of Tampa") == "commercial"
    assert _zone_category("D2", "City of Portland") == "other"


def test_generic_families_unchanged():
    assert _zone_category("RS-1") == "residential"
    assert _zone_category("CD-3") == "commercial"
    assert _zone_category("MXE") == "mixed"
    assert _zone_category("I-1") == "industrial"
    assert _zone_category("OS") == "open"
    assert _zone_category("PUD") == "special"
    assert _zone_category("") == "other"
    assert _zone_category(None) == "other"


def test_storey_cap_comes_from_the_code_or_not_at_all():
    assert _zone_stories("T3-R") == 2
    assert _zone_stories("T4-L") == 3
    assert _zone_stories("T5-O") == 5
    assert _zone_stories("T6-8-O") == 8
    assert _zone_stories("T6-24A-O") == 24
    assert _zone_stories("T6-80-O") == 80
    # Codes that declare no height must report none rather than a guess.
    for code in ("D1", "CS", "RS-1", "BU-1A", "", None):
        assert _zone_stories(code) is None, code
