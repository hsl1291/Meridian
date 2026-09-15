r"""Where the shared store lives.

The national market tables and the Miami-Dade parcel/owner roll are far larger
than everything else Groundwork holds, so they live in ONE database outside the
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


def shared_root() -> Path:
    """Locate the shared store. Checked in order, so a copied install works
    wherever it is unzipped without anyone editing a path:

      1. ``APPS_SHARED``                       explicit wins
      2. a ``_shared`` folder beside the app   the shareable layout
      3. the platform default                  the original install
    """
    env = os.environ.get("APPS_SHARED")
    if env:
        return Path(env)
    sibling = Path(__file__).resolve().parent.parent.parent / "_shared"
    if sibling.is_dir():
        return sibling
    if os.name == "nt":
        return Path(WINDOWS_DEFAULT)
    # Off Windows the Windows default is a relative path, so it must not be
    # used. A dot-directory under $HOME is at least somewhere a store could
    # legitimately live, and is never created by accident in whatever directory
    # the app happened to start in.
    return Path.home() / ".groundwork" / "_shared"


def shared_db() -> Path:
    return Path(os.environ.get("APPS_SHARED_DB") or shared_root() / "shared.db")


def shared_layers() -> Path:
    return Path(os.environ.get("APPS_SHARED_LAYERS") or shared_root() / "layers")
