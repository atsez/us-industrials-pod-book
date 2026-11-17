#!/bin/zsh
# Install the newest us-industrials-pod-book zip from Downloads, test it, and rerun the pipeline.
# Usage: podbook-update            (after the one-time alias in the README)
set -euo pipefail

REPO="$HOME/code/us-industrials-pod-book"
ZIP=$(ls -t "$HOME"/Downloads/us-industrials-pod-book*.zip 2>/dev/null | head -1 || true)
if [[ -z "$ZIP" ]]; then
  echo "No us-industrials-pod-book zip found in Downloads. Download the latest one from Claude first."
  exit 1
fi
echo "Installing $ZIP"

TMP=$(mktemp -d)
unzip -q "$ZIP" -d "$TMP"
rsync -a "$TMP/us-industrials-pod-book/" "$REPO/"
rm -rf "$TMP"
# Clear old zips and unzipped copies so the next download keeps its plain name.
rm -rf "$HOME"/Downloads/us-industrials-pod-book*

cd "$REPO"
source .venv/bin/activate
pip install -q -e ".[dev]"
pytest -q
caffeinate -i podbook pipeline "$@"

open data/reports data/logs
echo ""
echo "Done. Send Claude the files it asked for from the two Finder windows."
