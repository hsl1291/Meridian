"""The memo, checked without a browser.

Every bug fixed here was invisible on screen and only showed up on paper, so the
temptation is to verify by rendering. That would put a headless browser in the
dependency list of a tool that otherwise needs four packages, and the memo does
not need one: it is static HTML that whatever browser opens it prints.

So the geometry is asserted as arithmetic over the stylesheet instead. The page
box is a known size, the margins are declared, and the fixed elements either fit
inside what is left or they do not -- which is a calculation, not a rendering.
"""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from backend.prospect.memo import CSS, gather, render  # noqa: E402
from memo_fixture import KEY, memo_db  # noqa: E402

LETTER_H = 11.0
LETTER_W = 8.5


def inches(v: str) -> float:
    m = re.fullmatch(r"([\d.]+)(in|pt|px)?", v.strip())
    if not m:
        raise ValueError(v)
    n, unit = float(m.group(1)), (m.group(2) or "in")
    return {"in": n, "pt": n / 72, "px": n / 96}[unit]


def rules(selector: str, css: str = CSS) -> dict:
    """Declarations for one selector, last-wins, ignoring @media blocks."""
    out = {}
    base = re.sub(r"@media[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}", "", css, flags=re.S)
    for body in re.findall(re.escape(selector) + r"\s*\{([^}]*)\}", base):
        for decl in body.split(";"):
            if ":" in decl:
                k, v = decl.split(":", 1)
                out[k.strip()] = v.strip()
    return out


def print_rules(selector: str) -> dict:
    block = re.search(r"@media print\s*\{(.*)\n\}", CSS, re.S)
    assert block, "no @media print block"
    return rules(selector, block.group(1))


def page_margins() -> tuple[float, float, float]:
    m = re.search(r"@page\s*\{[^}]*margin:\s*([^;]+);", CSS)
    parts = [inches(p) for p in m.group(1).split()]
    top, side, bottom = (parts + parts * 3)[:3] if len(parts) == 3 else (
        parts[0], parts[1 % len(parts)], parts[0])
    return top, side, bottom


# ── the four print bugs ────────────────────────────────────────────────────

def test_the_side_inset_is_applied_once_not_twice():
    """@page insets each side, and .pad used to add the same again inside it --
    leaving about 5.7in of live width on Letter for tables built for more."""
    _, side, _ = page_margins()
    assert side > 0
    assert inches(print_rules(".pad").get("padding", "0").split()[-1]) == 0, \
        ".pad still adds a second inset inside the @page margin"

    live = LETTER_W - 2 * side
    assert live >= 7.0, f"only {live:.2f}in of live width"


def test_the_cover_fits_the_live_height_of_a_page():
    """It was min-height 9.6in against 9.4in available, so it overflowed and
    page-break-after then added a second break: every memo had a blank page 2."""
    top, _, bottom = page_margins()
    live = LETTER_H - top - bottom
    cover = inches(rules(".cover")["min-height"])
    assert cover <= live, f"cover is {cover}in against {live}in of live height"


def test_tables_repeat_their_header_where_they_continue():
    assert rules("thead").get("display") == "table-header-group"


def test_break_avoidance_is_not_on_containers_taller_than_a_sheet():
    """`section` can exceed a page, where page-break-inside:avoid either does
    nothing or opens a large blank gap ahead of it. It belongs on rows and on
    small blocks."""
    assert "page-break-inside" not in rules("section")
    assert "break-inside" not in rules("section")
    assert rules("tr").get("break-inside") == "avoid"
    for small in (".callout", ".gap", ".kv div"):
        assert rules(small).get("break-inside") == "avoid", small


def test_the_stat_grid_has_a_fixed_track_count():
    """repeat(auto-fill, minmax(1.6in, 1fr)) stretched a leftover cell across the
    whole final row whenever the count did not divide evenly."""
    cols = rules(".kv")["grid-template-columns"]
    assert "auto-fill" not in cols and "auto-fit" not in cols, cols
    assert re.fullmatch(r"repeat\(\d+,\s*1fr\)", cols), cols


def test_widow_and_orphan_control_is_set():
    body = rules("body")
    assert int(body.get("orphans", 0)) >= 2 and int(body.get("widows", 0)) >= 2


# ── the document itself ────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def memo():
    return render(gather(KEY, memo_db()))


def test_the_memo_renders_from_the_roll(memo):
    assert "Seaside Towers Condominium" in memo
    assert "1200 OCEAN DR" in memo


def test_no_table_has_a_header_that_cannot_repeat(memo):
    """The invariant is not that every table has a header -- the Sources list is
    two unlabelled columns and does not need one. It is that a table with column
    labels carries them in a <thead>, which is what repeats across a page break.
    A <th> sitting loose in <tbody> would render identically and vanish on
    page two."""
    tables = re.findall(r"<table>(.*?)</table>", memo, re.S)
    assert len(tables) >= 4
    for t in tables:
        body = t[t.index("<tbody>"):] if "<tbody>" in t else t
        assert "<th" not in body, "column labels outside <thead> will not repeat"
    assert sum("<thead>" in t for t in tables) >= 4


def test_bulk_deeds_are_called_out_rather_than_averaged_in(memo):
    """The fixture carries one six-folio instrument. The memo must say so where
    the per-unit figures are, not let $2.94M read as six $2.94M sales."""
    assert "convey" in memo and "units together" in memo


def test_what_it_could_not_confirm_is_printed_at_the_top(memo):
    head = memo[:memo.index("Sources")] if "Sources" in memo else memo
    assert "could not confirm" in head


# ── nearby comps ────────────────────────────────────────────────────────────

def test_gathering_a_building_with_a_nearby_comp_does_not_raise():
    """gather() used an undefined `this_year` inside the per-comp sale-median
    loop -- invisible in the module-scoped `memo` fixture above because that
    building has no OTHER target row within range, so the comps list is
    always empty and the loop body never runs. A second target close enough
    to land in the radius, with at least one priced unit, exercises it."""
    from datetime import date

    from memo_fixture import memo_db

    con = memo_db()
    other = "0132071"
    con.execute(
        "INSERT INTO target (group_key, condo_name, addr_primary, city, units_nal, "
        "act_yr_blt, lon, lat) VALUES (?,?,?,?,?,?,?,?)",
        (other, "Nearby Towers Condominium", "1210 OCEAN DR", "MIAMI BEACH", 20,
         1980, -80.1305, 25.7895))  # a few hundred feet from the main fixture building
    for i in range(3):
        con.execute(
            "INSERT INTO nal_condo_unit (folio, group_key, owner_norm, owner_addr_norm, "
            "tot_lvg_area, sale_prc1, sale_yr1, is_entity, is_absentee, county) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (f"{other}{i:06d}", other, f"OWNER {i}", f"{i} ELM ST",
             900, 380_000 + i * 10_000, date.today().year - 1, 0, 0, "DADE"))
    con.commit()

    doc = gather(KEY, con)  # must not raise NameError
    comps = [c for c in doc["comps"] if c["group_key"] == other]
    assert comps, "the nearby target did not land in the comp radius"
    assert comps[0]["sale_n"] == 3
    assert comps[0]["sale_median"] == pytest.approx(390_000)


# ── the server-side chart ──────────────────────────────────────────────────

def test_the_sparkline_no_longer_lies_about_its_slopes():
    """It set preserveAspectRatio="none", which stretches the axes independently
    — on a chart whose entire job is showing a slope."""
    from backend.prospect.market_dd import _sparkline
    svg = _sparkline([100, 120, 115, 140, 160])
    assert 'preserveAspectRatio="none"' not in svg
    assert 'preserveAspectRatio="xMidYMid meet"' in svg


def test_the_sparkline_carries_no_literal_colour():
    from backend.prospect.market_dd import _sparkline
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", _sparkline([1, 2, 3]))


def test_the_sparkline_is_described_for_a_screen_reader():
    from backend.prospect.market_dd import _sparkline
    svg = _sparkline([100, 160])
    assert 'role="img"' in svg and "aria-label" in svg and "up" in svg


def test_a_series_too_short_to_plot_says_so():
    from backend.prospect.market_dd import _sparkline
    assert "Not enough history" in _sparkline([5])
    assert "Not enough history" in _sparkline([])


def test_a_flat_series_does_not_divide_by_zero():
    from backend.prospect.market_dd import _sparkline
    assert "NaN" not in _sparkline([7, 7, 7])
