#!/usr/bin/env bash
# Start Meridian. Nothing is installed outside this folder.
set -euo pipefail
cd "$(dirname "$0")"

for py in python3 python; do
  if command -v "$py" >/dev/null 2>&1; then exec "$py" start.py "$@"; fi
done

cat <<'MSG'

  Python 3 was not found on this machine.

  macOS:  brew install python
  Debian: sudo apt install python3 python3-venv
  Or:     https://www.python.org/downloads/

MSG
exit 1
