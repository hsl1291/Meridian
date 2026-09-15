r"""Update Meridian in place from GitHub.

Downloads the current branch as a zip, unpacks it, and swaps the application
files -- no git required, so it works on a folder that came from *Download ZIP*
as well as one that was cloned. Stdlib only, and **no PowerShell**: this is
`urllib` and `zipfile`, nothing shelled out.

What it will not touch, ever:

    data/            your databases, declarations, memos, fetched layers
    .venv/  venv/    the environment
    logs/            whatever is in them

`backend/prospect/config.json` is treated as yours. Score weights, the firm name
on a memo cover and the county table are all edited by hand, so an update MERGES
new keys into the existing file instead of overwriting it -- you get anything a
new version added, and keep every value you set.

Every replaced file is copied to `.backup/<timestamp>/` first. An update that
goes wrong is then a folder move away from undone, which matters more than disk:
this is a tool with no uninstaller and no support line.

The swap happens only after the whole archive is downloaded and unpacked
successfully, so a connection that drops halfway leaves the running copy alone.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
STAMP = APP_ROOT / ".version"

DEFAULT_REPO = "hsl1291/Meridian"
DEFAULT_BRANCH = "main"

# Never replaced, never deleted. Anything a user created or the app wrote.
PROTECTED = {"data", ".venv", "venv", "logs", ".backup", ".git", ".env",
             "node_modules", "__pycache__"}
# Merged rather than overwritten, because these are edited by hand.
MERGED_JSON = {"backend/prospect/config.json"}

USER_AGENT = "Meridian-updater"


def _configured() -> dict:
    """`update` in config.json. Read fresh each time so editing it takes effect
    without a restart, and tolerant of a missing or broken file because the
    updater has to work on a copy that is already damaged."""
    try:
        cfg = json.loads((APP_ROOT / "backend" / "prospect" / "config.json")
                         .read_text(encoding="utf-8"))
        return cfg.get("update") or {}
    except (OSError, ValueError):
        return {}


def _repo() -> tuple[str, str]:
    """Environment wins, then config.json, then the built-in default.

    The branch matters more than it looks: pointing this at a branch you no
    longer merge to would roll an installed copy BACKWARD, which is why it is
    configuration rather than a constant and why the UI prints it.
    """
    cfg = _configured()
    # GROUNDWORK_* are the names this app used before it was called Meridian.
    # Still honoured: a rename that silently ignores a variable somebody already
    # set is a rename that costs them an afternoon.
    repo = (os.environ.get("MERIDIAN_REPO") or os.environ.get("GROUNDWORK_REPO")
            or cfg.get("repo") or DEFAULT_REPO)
    branch = (os.environ.get("MERIDIAN_BRANCH") or os.environ.get("GROUNDWORK_BRANCH")
              or cfg.get("branch") or DEFAULT_BRANCH)
    return repo, branch


def _get(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def installed() -> dict:
    if STAMP.exists():
        try:
            return json.loads(STAMP.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    return {}


def check() -> dict:
    """What is on GitHub versus what is in this folder.

    An unknown local version is reported as unknown rather than out of date: a
    fresh clone has no stamp, and telling someone their brand-new download needs
    updating is the fastest way to make them distrust the button.
    """
    repo, branch = _repo()
    url = f"https://api.github.com/repos/{repo}/commits/{branch}"
    try:
        head = json.loads(_get(url))
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"GitHub returned {e.code} for {repo}@{branch}.",
                "repo": repo, "branch": branch}
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {"ok": False, "error": f"Could not reach GitHub: {e}",
                "repo": repo, "branch": branch}

    remote_sha = head.get("sha", "")
    commit = head.get("commit", {})
    local = installed()
    local_sha = local.get("sha", "")
    return {
        "ok": True, "repo": repo, "branch": branch,
        "local_sha": local_sha or None,
        "remote_sha": remote_sha,
        "behind": bool(local_sha) and local_sha != remote_sha,
        "unknown_local": not local_sha,
        "latest_message": (commit.get("message") or "").split("\n")[0][:200],
        "latest_date": (commit.get("author") or {}).get("date"),
    }


def _merge_json(existing: Path, incoming: Path) -> bool:
    """Add keys the new version introduced; keep every value already set.

    Returns True when the file was written. A config that cannot be parsed is
    left completely alone -- a half-merged config is worse than a stale one.
    """
    try:
        cur = json.loads(existing.read_text(encoding="utf-8"))
        new = json.loads(incoming.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return False

    def merge(a: dict, b: dict) -> dict:
        out = dict(a)
        for k, v in b.items():
            if k not in out:
                out[k] = v
            elif isinstance(v, dict) and isinstance(out[k], dict):
                out[k] = merge(out[k], v)
        return out

    merged = merge(cur, new)
    if merged == cur:
        return False
    existing.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    return True


def apply(dry_run: bool = False) -> dict:
    """Download and swap. Returns what changed, or why nothing did."""
    repo, branch = _repo()
    status = check()
    if not status.get("ok"):
        return status
    if status["local_sha"] and not status["behind"]:
        return {"ok": True, "updated": False, "reason": "already up to date",
                **status}

    zip_url = f"https://github.com/{repo}/archive/refs/heads/{branch}.zip"
    try:
        blob = _get(zip_url, timeout=180)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
        return {"ok": False, "error": f"Download failed: {e}"}

    # Unpack entirely before touching anything. A connection that drops halfway
    # then leaves the running copy exactly as it was.
    staging = APP_ROOT / ".backup" / f"staging-{datetime.now():%Y%m%d-%H%M%S}"
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            bad = zf.testzip()
            if bad:
                return {"ok": False, "error": f"The downloaded archive is corrupt ({bad})."}
            staging.mkdir(parents=True, exist_ok=True)
            zf.extractall(staging)
    except (zipfile.BadZipFile, OSError) as e:
        shutil.rmtree(staging, ignore_errors=True)
        return {"ok": False, "error": f"Could not unpack the download: {e}"}

    roots = [p for p in staging.iterdir() if p.is_dir()]
    if len(roots) != 1:
        shutil.rmtree(staging, ignore_errors=True)
        return {"ok": False, "error": "Unexpected archive layout."}
    src = roots[0]

    backup = APP_ROOT / ".backup" / f"{datetime.now():%Y%m%d-%H%M%S}"
    changed, merged, skipped = [], [], []

    for item in sorted(src.rglob("*")):
        if not item.is_file():
            continue
        rel = item.relative_to(src)
        parts = rel.parts
        if parts[0] in PROTECTED:
            skipped.append(str(rel).replace("\\", "/"))
            continue
        dest = APP_ROOT / rel
        rel_posix = str(rel).replace("\\", "/")

        if rel_posix in MERGED_JSON and dest.exists():
            if not dry_run and _merge_json(dest, item):
                merged.append(rel_posix)
            elif dry_run:
                merged.append(rel_posix)
            continue

        if dest.exists() and dest.read_bytes() == item.read_bytes():
            continue
        changed.append(rel_posix)
        if dry_run:
            continue
        if dest.exists():
            b = backup / rel
            b.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dest, b)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, dest)

    if not dry_run:
        STAMP.write_text(json.dumps({
            "sha": status["remote_sha"], "branch": branch, "repo": repo,
            "applied": datetime.now().isoformat(timespec="seconds"),
        }, indent=2) + "\n", encoding="utf-8")
    shutil.rmtree(staging, ignore_errors=True)

    return {
        "ok": True, "updated": bool(changed or merged), "dry_run": dry_run,
        "changed": changed[:200], "changed_count": len(changed),
        "merged": merged, "protected_skipped": len(skipped),
        "backup": None if dry_run or not backup.exists() else str(backup),
        "from_sha": status["local_sha"], "to_sha": status["remote_sha"],
        "restart_required": bool(changed) and not dry_run,
        **{k: status[k] for k in ("repo", "branch", "latest_message", "latest_date")},
    }
