"""In-place updates from GitHub.

The risk in a self-updater is not the download — it is overwriting something the
user cannot get back. So most of these are about what it must NOT touch.
"""
import json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import updater  # noqa: E402


def fake_repo(tmp_path, files):
    """A GitHub-shaped zip: one top-level directory, then the tree."""
    src = tmp_path / "repo-main"
    for rel, body in files.items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    blob = tmp_path / "repo.zip"
    with zipfile.ZipFile(blob, "w") as zf:
        for p in src.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(tmp_path))
    return blob.read_bytes()


@pytest.fixture
def app(tmp_path, monkeypatch):
    """An installed copy, with the updater pointed at it."""
    root = tmp_path / "app"
    (root / "backend" / "prospect").mkdir(parents=True)
    (root / "data").mkdir()
    (root / ".venv").mkdir()
    (root / "backend" / "app.py").write_text("old code\n", encoding="utf-8")
    (root / "data" / "prospect.db").write_text("IRREPLACEABLE\n", encoding="utf-8")
    (root / ".venv" / "marker").write_text("env\n", encoding="utf-8")
    (root / "backend" / "prospect" / "config.json").write_text(
        json.dumps({"memo": {"firm": "Harold Holdings"}, "score_weights": {"age": 0.99}}),
        encoding="utf-8")
    monkeypatch.setattr(updater, "APP_ROOT", root)
    monkeypatch.setattr(updater, "STAMP", root / ".version")
    return root


def serve(monkeypatch, blob, sha="newsha", local=None):
    monkeypatch.setattr(updater, "_get", lambda url, timeout=30: (
        json.dumps({"sha": sha, "commit": {"message": "a change",
                                           "author": {"date": "2026-01-01T00:00:00Z"}}}).encode()
        if "api.github.com" in url else blob))
    monkeypatch.setattr(updater, "installed", lambda: ({"sha": local} if local else {}))


# ── what it must never touch ───────────────────────────────────────────────

def test_user_data_is_never_replaced(app, monkeypatch):
    """The databases, declarations and memos are the only things here that cost
    somebody real work to produce."""
    blob = fake_repo(app.parent, {"backend/app.py": "new code\n",
                                  "data/prospect.db": "SHIPPED PLACEHOLDER\n"})
    serve(monkeypatch, blob, local="oldsha")
    r = updater.apply()
    assert r["updated"]
    assert (app / "data" / "prospect.db").read_text() == "IRREPLACEABLE\n"
    assert r["protected_skipped"] >= 1


def test_the_environment_is_never_replaced(app, monkeypatch):
    blob = fake_repo(app.parent, {".venv/marker": "clobbered\n",
                                  "backend/app.py": "new code\n"})
    serve(monkeypatch, blob, local="oldsha")
    updater.apply()
    assert (app / ".venv" / "marker").read_text() == "env\n"


def test_application_code_is_replaced(app, monkeypatch):
    blob = fake_repo(app.parent, {"backend/app.py": "new code\n"})
    serve(monkeypatch, blob, local="oldsha")
    r = updater.apply()
    assert (app / "backend" / "app.py").read_text() == "new code\n"
    assert "backend/app.py" in r["changed"]


# ── config is the user's ───────────────────────────────────────────────────

def test_config_gains_new_keys_and_keeps_every_value_set(app, monkeypatch):
    """Score weights, the firm name on a memo cover and the county table are all
    edited by hand. An update that overwrites them is an update nobody runs
    twice."""
    blob = fake_repo(app.parent, {"backend/prospect/config.json": json.dumps(
        {"memo": {"firm": "", "confidentiality": "NEW"},
         "score_weights": {"age": 0.25}, "counties": {"DADE": {}}})})
    serve(monkeypatch, blob, local="oldsha")
    updater.apply()
    cfg = json.loads((app / "backend" / "prospect" / "config.json").read_text())
    assert cfg["memo"]["firm"] == "Harold Holdings", "a set value must survive"
    assert cfg["score_weights"]["age"] == 0.99, "a tuned weight must survive"
    assert cfg["memo"]["confidentiality"] == "NEW", "a new key must arrive"
    assert "counties" in cfg


def test_an_unparseable_config_is_left_completely_alone(app, monkeypatch):
    """A half-merged config is worse than a stale one."""
    (app / "backend" / "prospect" / "config.json").write_text("{ broken", encoding="utf-8")
    blob = fake_repo(app.parent, {"backend/prospect/config.json": '{"a": 1}'})
    serve(monkeypatch, blob, local="oldsha")
    updater.apply()
    assert (app / "backend" / "prospect" / "config.json").read_text() == "{ broken"


# ── recoverability ─────────────────────────────────────────────────────────

def test_every_replaced_file_is_backed_up_first(app, monkeypatch):
    """This is a tool with no uninstaller. A bad update has to be a folder move
    away from undone."""
    blob = fake_repo(app.parent, {"backend/app.py": "new code\n"})
    serve(monkeypatch, blob, local="oldsha")
    r = updater.apply()
    backup = Path(r["backup"])
    assert (backup / "backend" / "app.py").read_text() == "old code\n"


def test_a_corrupt_download_changes_nothing(app, monkeypatch):
    serve(monkeypatch, b"this is not a zip", local="oldsha")
    r = updater.apply()
    assert r["ok"] is False and "unpack" in r["error"].lower()
    assert (app / "backend" / "app.py").read_text() == "old code\n"


def test_no_staging_directory_is_left_behind(app, monkeypatch):
    blob = fake_repo(app.parent, {"backend/app.py": "new code\n"})
    serve(monkeypatch, blob, local="oldsha")
    updater.apply()
    assert not list((app / ".backup").glob("staging-*"))


# ── deciding whether to update at all ──────────────────────────────────────

def test_an_unchanged_file_is_not_rewritten_or_backed_up(app, monkeypatch):
    blob = fake_repo(app.parent, {"backend/app.py": "old code\n"})
    serve(monkeypatch, blob, local="oldsha")
    r = updater.apply()
    assert r["changed_count"] == 0
    assert r["backup"] is None


def test_a_matching_sha_short_circuits(app, monkeypatch):
    blob = fake_repo(app.parent, {"backend/app.py": "new code\n"})
    serve(monkeypatch, blob, sha="same", local="same")
    r = updater.apply()
    assert r["updated"] is False and r["reason"] == "already up to date"
    assert (app / "backend" / "app.py").read_text() == "old code\n"


def test_a_copy_with_no_stamp_is_unknown_not_out_of_date(app, monkeypatch):
    """A fresh clone has no stamp. Telling someone their brand-new download is
    stale is the fastest way to make them distrust the button."""
    serve(monkeypatch, b"", sha="abc", local=None)
    st = updater.check()
    assert st["unknown_local"] is True
    assert st["behind"] is False


def test_a_dry_run_reports_without_touching_anything(app, monkeypatch):
    blob = fake_repo(app.parent, {"backend/app.py": "new code\n"})
    serve(monkeypatch, blob, local="oldsha")
    r = updater.apply(dry_run=True)
    assert r["changed_count"] == 1
    assert (app / "backend" / "app.py").read_text() == "old code\n"
    assert not (app / ".version").exists()


def test_an_unreachable_github_is_reported_not_raised(app, monkeypatch):
    def boom(url, timeout=30):
        raise OSError("no network")
    monkeypatch.setattr(updater, "_get", boom)
    r = updater.check()
    assert r["ok"] is False and "Could not reach GitHub" in r["error"]


# ── no PowerShell, no shelling out ─────────────────────────────────────────

def test_the_updater_shells_out_to_nothing():
    """It must stay stdlib-and-in-process. Matching on INVOCATIONS, not the word:
    the module's own docstring promises no PowerShell, and a test that fails on
    the promise is a test that punishes documenting the decision."""
    import re
    src = (ROOT / "backend" / "updater.py").read_text()
    for banned in (r"\bimport subprocess\b", r"\bos\.system\b", r"shell\s*=\s*True",
                   r"powershell(\.exe)?\s+[-/]", r"\bpwsh\b"):
        assert not re.search(banned, src, re.I), banned


def test_the_launchers_do_not_use_powershell():
    """The user asked for this explicitly, and a .bat that quietly calls
    powershell to do the real work would satisfy the letter and not the point."""
    import re
    for name in ("start.bat", "update.bat", "start.sh", "update.sh", "start.py"):
        src = (ROOT / name).read_text()
        for banned in (r"powershell(\.exe)?\s+[-/]", r"\bpwsh\b", r"Invoke-WebRequest",
                       r"Start-Process"):
            assert not re.search(banned, src, re.I), f"{name}: {banned}"


def test_the_launchers_need_nothing_but_python():
    """`py` then `python`, and a real message when neither is there — not a
    traceback, and not silence."""
    for name in ("start.bat", "update.bat"):
        src = (ROOT / name).read_text()
        assert "where py" in src and "where python" in src, name
        assert "python.org/downloads" in src, f"{name} must say where to get Python"
        assert "pause" in src, f"{name} must not close the window on an error"
