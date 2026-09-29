"""launch.py's git-clone update, against real git and a real remote.

FakeGit in test_launch.py proves the logic; this proves git actually does what
the logic assumes -- that a fast-forward pull catches a clone up, that a
conflicting local edit is refused and left alone, and that it never prompts."""
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
       "GIT_TERMINAL_PROMPT": "0"}


def git(cwd, *args):
    r = subprocess.run(["git", *args], cwd=cwd, env=ENV, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def commit(repo, name, text, msg):
    (Path(repo) / name).write_text(text, encoding="utf-8", newline="\n")
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", msg)


@pytest.fixture
def world(tmp_path):
    """remote <- author (pushes), install (a clone the launcher updates)."""
    remote, author, install = tmp_path / "remote.git", tmp_path / "author", tmp_path / "install"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    git(tmp_path, "clone", "-q", str(remote), str(author))
    git(author, "checkout", "-q", "-B", "main")
    commit(author, "app.txt", "v1\n", "v1")
    git(author, "push", "-q", "-u", "origin", "main")
    git(tmp_path, "clone", "-q", str(remote), str(install))

    spec = importlib.util.spec_from_file_location("launch_git", ROOT / "launch.py")
    launch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launch)
    launch.ROOT = install
    launch.LOG_DIR = install / "logs"
    launch.UPDATE_LOG = install / "logs" / "update.log"
    launch.GIT_STAMP = install / ".update-check"
    launch.VERSION_STAMP = install / ".version"
    launch.PYTHON = Path(sys.executable)
    return launch, author, install


def test_a_clone_catches_up_and_then_reports_nothing_more(world, monkeypatch):
    launch, author, install = world
    monkeypatch.delenv("MERIDIAN_AUTO_UPDATE", raising=False)
    monkeypatch.setattr(launch.subprocess, "run", (lambda real: (
        lambda cmd, *a, **k: subprocess.CompletedProcess(cmd, 0, "", "")
        if "start.py" in " ".join(map(str, cmd)) else real(cmd, *a, **k)))(subprocess.run))

    assert launch._is_git_checkout()
    assert launch._git_update() is False, "nothing new yet"

    commit(author, "app.txt", "v2\n", "v2")
    git(author, "push", "-q")
    assert launch._git_update() is True
    assert (install / "app.txt").read_text(encoding="utf-8") == "v2\n"
    assert launch._git_update() is False, "already current"
    assert "->" in launch.UPDATE_LOG.read_text(encoding="utf-8")


def test_a_conflicting_local_edit_is_refused_and_left_untouched(world):
    launch, author, install = world
    (install / "app.txt").write_text("my local edit\n", encoding="utf-8", newline="\n")
    commit(author, "app.txt", "v2\n", "v2")
    git(author, "push", "-q")

    assert launch._git_update() is False
    assert (install / "app.txt").read_text(encoding="utf-8") == "my local edit\n"
    assert "overwritten" in launch.UPDATE_LOG.read_text(encoding="utf-8")
    assert not launch.GIT_STAMP.exists(), "a refused pull is retried, not marked as checked"


def test_a_diverged_clone_is_not_merged(world):
    launch, author, install = world
    commit(install, "mine.txt", "local\n", "a local commit")
    commit(author, "app.txt", "v2\n", "v2")
    git(author, "push", "-q")
    before = git(install, "rev-parse", "HEAD")

    assert launch._git_update() is False
    assert git(install, "rev-parse", "HEAD") == before, "fast-forward only: no merge commit, no reset"


def test_the_installed_version_of_a_real_clone_is_its_head(world, monkeypatch):
    """The Version card reads HEAD straight from .git (no subprocess), both with
    loose refs and after `git pack-refs` moves them into packed-refs -- which git
    does on its own during gc, so a clone that worked yesterday must keep working."""
    launch, author, install = world
    import backend.updater as updater
    monkeypatch.setattr(updater, "APP_ROOT", install)
    monkeypatch.setattr(updater, "STAMP", install / ".version")
    head = git(install, "rev-parse", "HEAD")
    assert updater.installed()["sha"] == head

    git(install, "pack-refs", "--all")
    assert not (install / ".git" / "refs" / "heads" / "main").exists(), "refs are now packed"
    assert updater.installed()["sha"] == head
