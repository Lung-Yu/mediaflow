#!/usr/bin/env bash
# Start pipeline watcher in foreground (useful for debugging).
# For background daemon mode use: scripts/ctl.sh start watcher
set -e
cd "$(dirname "$0")/.."

[[ -f venv/bin/activate ]] || { python3 -m venv venv; }
source venv/bin/activate
pip install -q -r requirements.txt

echo "Starting mediaflow pipeline watcher (foreground)..."
python -m pipeline.watcher
