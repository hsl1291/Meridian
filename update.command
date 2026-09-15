#!/usr/bin/env bash
# Update Meridian from GitHub, then start it.
set -euo pipefail
cd "$(dirname "$0")"
for py in python3 python; do
  if command -v "$py" >/dev/null 2>&1; then exec "$py" start.py --update "$@"; fi
done
echo "Python 3 was not found. See https://www.python.org/downloads/"
exit 1
