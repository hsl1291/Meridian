r"""Where the shared store lives.

The national market tables and the Miami-Dade parcel/owner roll are far larger
than everything else Meridian holds, so they live in ONE database outside the
app folder, shared with anything else that wants them.

This resolution existed in FIVE copies -- app.py, shared_data.py, prospect/db.py,
prospect/capacity.py and scripts/fetch_layers.py -- each with its own docstring
and its own chance to drift.

It also had a bug that only appeared off Windows. `Path(r"C:\Apps\_shared")` is
an absolute path on Windows and a RELATIVE one everywhere else, so an
unconfigured run on macOS or Linux created a directory literally named
``C:\Apps\_shared`` in the working directory and wrote a store into it, rather
than reporting that the store was not configured. That is why the fallback is now
platform-aware.
"""
from __future__ import annotations

import os
from pathlib import Path

WINDOWS_DEFAULT = r"C:\Apps\_shared"


APP_ROOT = Path(__file__).resolve().parent.parent


def shared_root() -> Path:
    """Locate the shared store.

    The default is INSIDE the app folder, at ``data/_shared``, so downloading
    this repository and running it from wherever it landed works with no paths to
    edit and nothing to create outside it. That is the normal case now: the
    store lived outside the app because Sitefolio and Prospect both read it, and
    those are this app.

    The two external locations are still honoured where they already exist, so an
    existing install keeps its store instead of silently starting an empty one
    beside the code:

      1. ``APPS_SHARED``                      explicit always wins
      2. a ``_shared`` folder beside the app  the shareable layout
      3. ``C:\Apps\_shared`` on Windows       the original install, if present
      4. ``data/_shared`` in the app folder   the default
    """
    env = os.environ.get("APPS_SHARED")
    if env:
        return Path(env)
    sibling = APP_ROOT.parent / "_shared"
    if sibling.is_dir():
        return sibling
    # Only when it is actually there. Off Windows this string is a RELATIVE path,
    # which is how an unconfigured run used to create a directory literally named
    # C:\Apps\_shared in whatever folder the app started in.
    if os.name == "nt":
        legacy = Path(WINDOWS_DEFAULT)
        if legacy.is_dir():
            return legacy
    # The name before this app was Meridian. Only if it exists -- otherwise a
    # rename would orphan a store somebody already built.
    legacy_home = Path.home() / ".groundwork" / "_shared"
    if legacy_home.is_dir():
        return legacy_home
    return APP_ROOT / "data" / "_shared"


def shared_db() -> Path:
    return Path(os.environ.get("APPS_SHARED_DB") or shared_root() / "shared.db")


def shared_layers() -> Path:
    return Path(os.environ.get("APPS_SHARED_LAYERS") or shared_root() / "layers")
