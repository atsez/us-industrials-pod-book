#!/bin/zsh
# Weekly Yahoo consensus snapshot. Called by launchd (see README).
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
mkdir -p data/logs
echo "--- $(date '+%Y-%m-%d %H:%M') ---" >> data/logs/consensus.log
PODBOOK_ROOT="$REPO" "$REPO/.venv/bin/python" -m podbook log-consensus >> data/logs/consensus.log 2>&1
