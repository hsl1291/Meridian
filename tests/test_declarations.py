"""Declaration review: the extractor's new findings, and the synthesis across a
building's documents.

The synthesis is the part that matters. `stage2 --extract` wrote straight into
target's stage-2 columns, so an amendment overwrote the original declaration's
findings in the same fields -- and that distinction is the entire screen. The 3d
DCA ruled against a developer whose Kaufman language was added BY AMENDMENT
after it held 183 of 192 units.
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.prospect.declaration import analyze, synthesise  # noqa: E402
from backend.prospect.docs import EMBEDDED_MIN_CHARS, OcrUnavailable, ocr_available  # noqa: E402

HEAD = ("DECLARATION OF CONDOMINIUM OF SEASIDE TOWERS, A CONDOMINIUM. "
        "Made pursuant to the Condominium Act, Chapter 718, Florida Statutes. ")
TERM = ("ARTICLE XX. TERMINATION. The condominium may be terminated upon the "
        "approval of eighty percent (80%) of the total voting interests. ")


def doc(**kw):
    base = {"doc_type": "original", "termination_threshold": None, "kaufman_present": 0,
            "rofr": 0, "leasehold": 0, "age_restricted": 0, "recorded_year": 1974}
    base.update(kw)
    return base


# ── the three new findings ─────────────────────────────────────────────────

def test_a_right_of_first_refusal_is_found():
    """It routes every purchase through the board, which throttles quiet
    assembly whatever the termination vote says."""
    f = analyze(HEAD + TERM + "No unit owner shall sell or transfer a unit without "
                "first granting the Association a right of first refusal to purchase.")
    assert f.rofr is True and f.rofr_snippet


def test_a_right_of_first_refusal_needs_a_transfer_context():
    """The bare phrase turns up in declarations that do not impose one on units."""
    f = analyze(HEAD + TERM + "The parties acknowledge the phrase right of first refusal "
                "carries its ordinary meaning under Florida common law generally.")
    assert f.rofr is False


def test_a_recreation_lease_is_found():
    """Leasehold land can survive termination, which makes the fee under the
    building not necessarily yours to redevelop."""
    f = analyze(HEAD + TERM + "The recreation lease between the Developer as lessor and "
                "the Association as lessee shall run for 99 years with rent payable monthly.")
    assert f.leasehold is True


def test_an_age_restriction_is_found():
    f = analyze(HEAD + TERM + "This condominium is operated as housing for older persons; "
                "no unit shall be occupied unless one resident is 55 years or older.")
    assert f.age_restricted is True


def test_an_ordinary_declaration_trips_none_of_them():
    f = analyze(HEAD + TERM + "Each unit owner shall pay assessments monthly and maintain "
                "the interior of the unit in good repair at the owner's expense.")
    assert (f.rofr, f.leasehold, f.age_restricted) == (False, False, False)


def test_the_original_findings_still_hold():
    f = analyze(HEAD + "as the same may be amended from time to time. " + TERM)
    assert f.termination_threshold == "80%"
    assert f.kaufman_original is True and f.kaufman_by_amendment is False


# ── synthesis ──────────────────────────────────────────────────────────────

def test_an_amendment_does_not_overwrite_the_original():
    """The exact fact pattern that lost: unanimous original with no Kaufman, then
    an amendment adding Kaufman and dropping the vote to 80%."""
    s = synthesise([
        doc(termination_threshold="100% (unanimous)", kaufman_present=0, recorded_year=1974),
        doc(doc_type="amendment", termination_threshold="80%", kaufman_present=1,
            recorded_year=2019),
    ])
    assert s["termination_threshold"] == "80%", "the amendment sets the operative vote"
    assert s["threshold_from"] == "amendment"
    assert s["kaufman_original"] == 0, "an amendment cannot make Kaufman original"
    assert s["kaufman_by_amendment"] == 1


def test_kaufman_in_the_original_survives_later_amendments():
    s = synthesise([
        doc(kaufman_present=1, termination_threshold="100% (unanimous)", recorded_year=1974),
        doc(doc_type="amendment", termination_threshold="80%", kaufman_present=1,
            recorded_year=2019),
    ])
    assert s["kaufman_original"] == 1
    assert s["kaufman_by_amendment"] == 0, "already original; the amendment adds nothing"


def test_the_latest_amendment_sets_the_vote():
    s = synthesise([
        doc(termination_threshold="100% (unanimous)", recorded_year=1974),
        doc(doc_type="amendment", termination_threshold="90%", recorded_year=2001),
        doc(doc_type="amendment", termination_threshold="80%", recorded_year=2019),
    ])
    assert s["termination_threshold"] == "80%"


def test_an_amendment_silent_on_the_vote_leaves_the_original_standing():
    s = synthesise([
        doc(termination_threshold="100% (unanimous)", recorded_year=1974),
        doc(doc_type="amendment", termination_threshold=None, rofr=1, recorded_year=2019),
    ])
    assert s["termination_threshold"] == "100% (unanimous)"
    assert s["threshold_from"] == "original"
    assert s["rofr"] == 1, "the amendment's own finding still counts"


def test_deal_killers_are_never_inferred_away():
    """An amendment that is silent about a recreation lease has not repealed it.
    Repeal has to be read by a human; silence is not evidence of removal."""
    s = synthesise([
        doc(leasehold=1, age_restricted=1, recorded_year=1974),
        doc(doc_type="amendment", leasehold=0, age_restricted=0, recorded_year=2019),
    ])
    assert s["leasehold"] == 1 and s["age_restricted"] == 1


def test_without_an_original_kaufman_is_unresolved_and_says_so():
    s = synthesise([doc(doc_type="amendment", termination_threshold="80%",
                        kaufman_present=1, recorded_year=2019)])
    assert s["kaufman_original"] is None, "unresolved, not false"
    assert any("ORIGINAL" in w for w in s["warnings"])


def test_an_unclassified_document_is_used_but_flagged():
    s = synthesise([doc(doc_type="unknown", termination_threshold="75%")])
    assert s["termination_threshold"] == "75%"
    assert any("could not be classified" in w for w in s["warnings"])


def test_no_documents_synthesise_to_nothing():
    assert synthesise([]) == {}


# ── text extraction ────────────────────────────────────────────────────────

def test_a_missing_ocr_engine_is_distinguishable_from_an_empty_document():
    """They call for completely different actions, so they are different
    exceptions -- reporting a scan as 'no terms found' would be a silent wrong
    answer on the documents this tool exists to read."""
    from backend.prospect.docs import ocr_text
    if ocr_available():
        pytest.skip("OCR is installed here")
    with pytest.raises(OcrUnavailable) as e:
        ocr_text(Path("nonexistent.pdf"))
    msg = str(e.value)
    assert "tesseract" in msg.lower() and "poppler" in msg.lower()
    assert "1965" in msg, "the message should say why this matters"


def test_the_embedded_text_threshold_is_low_enough_to_catch_a_stamp_only_scan():
    assert 100 <= EMBEDDED_MIN_CHARS <= 500


# ── a rebuild must not destroy reviewed documents ──────────────────────────

def test_a_rebuild_restores_declaration_findings():
    """build_targets drops and rewrites `target`, so without this a rebuild
    erases every stage-2 finding an analyst entered -- the most expensive data in
    the app, since each one cost somebody reading a recorded instrument."""
    import importlib.util
    import sqlite3

    spec = importlib.util.spec_from_file_location("bt2", ROOT / "scripts/prospect/build_targets.py")
    bt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bt)

    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""
      CREATE TABLE target (group_key TEXT PRIMARY KEY, termination_threshold TEXT,
        kaufman_original INTEGER, kaufman_by_amendment INTEGER, rofr INTEGER,
        leasehold INTEGER, age_restricted INTEGER, declaration_docs INTEGER);
      CREATE TABLE declaration_doc (id INTEGER PRIMARY KEY AUTOINCREMENT,
        group_key TEXT, source TEXT, doc_type TEXT, recorded_year INTEGER,
        termination_threshold TEXT, threshold_pct REAL, kaufman_present INTEGER,
        rofr INTEGER, leasehold INTEGER, age_restricted INTEGER);
    """)
    # A freshly rebuilt target row: every stage-2 column null.
    con.execute("INSERT INTO target (group_key) VALUES ('k')")
    con.execute("INSERT INTO declaration_doc (group_key, doc_type, recorded_year, "
                "termination_threshold, kaufman_present, rofr, leasehold, age_restricted) "
                "VALUES ('k','original',1974,'100% (unanimous)',1,1,0,0)")
    con.commit()

    assert bt.restore_declarations(con) == 1
    row = con.execute("SELECT * FROM target WHERE group_key='k'").fetchone()
    assert row["termination_threshold"] == "100% (unanimous)"
    assert row["kaufman_original"] == 1
    assert row["rofr"] == 1
    assert row["declaration_docs"] == 1


# ── shared paths ───────────────────────────────────────────────────────────

@pytest.mark.skipif(os.name == "nt", reason=(
    "tests the non-Windows branch; on real Windows, faking os.name makes pathlib "
    "build a PosixPath, which it refuses to instantiate there"))
def test_the_windows_fallback_is_not_used_off_windows(monkeypatch, tmp_path):
    """Path(r"C:\\Apps\\_shared") is absolute on Windows and RELATIVE everywhere
    else, so an unconfigured POSIX run created that name as a directory in the
    working directory and wrote a store into it."""
    import os

    from backend import shared_paths

    monkeypatch.delenv("APPS_SHARED", raising=False)
    monkeypatch.delenv("APPS_SHARED_DB", raising=False)
    # Faking os.name makes Path.home() take the POSIX branch, which needs HOME
    # -- unset on a real Windows runner. Point it somewhere with no legacy store.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(os, "name", "posix")
    root = shared_paths.shared_root()
    assert root.is_absolute()
    # Not `"C:" not in str(root)`: a checkout on drive C: is fine. The bug was
    # returning the Windows default itself.
    assert not str(root).startswith(shared_paths.WINDOWS_DEFAULT)


def test_an_explicit_override_always_wins(monkeypatch, tmp_path):
    from backend import shared_paths
    monkeypatch.setenv("APPS_SHARED", str(tmp_path))
    assert shared_paths.shared_root() == tmp_path
    monkeypatch.setenv("APPS_SHARED_DB", str(tmp_path / "x.db"))
    assert shared_paths.shared_db() == tmp_path / "x.db"


def test_the_resolver_exists_in_exactly_one_place():
    """It was copied into five modules, each with its own chance to drift."""
    import re
    # Assembled from parts so this file does not match its own source.
    pattern = re.compile(r"def " + "_shared" + r"_root\b")
    hits = []
    for f in ROOT.rglob("*.py"):
        if ".git" in f.parts or f.name == "shared_paths.py":
            continue
        if pattern.search(f.read_text(encoding="utf-8")):
            hits.append(str(f.relative_to(ROOT)))
    assert not hits, f"local copies of the resolver: {hits}"
