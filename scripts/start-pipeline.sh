#!/usr/bin/env bash
# Start pipeline watcher in foreground (useful for debugging).
# For background daemon mode use: scripts/ctl.sh start watcher
set -e
cd "$(dirname "$0")/.."

PID_FILE="/tmp/mediaflow-watcher.pid"
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "Watcher already running (PID $OLD_PID). Stop it first: kill $OLD_PID"
        exit 1
    fi
fi
echo $$ > "$PID_FILE"
trap "rm -f $PID_FILE" EXIT

[[ -f venv/bin/activate ]] || { python3 -m venv venv; }
source venv/bin/activate
pip install -q -r requirements.txt

echo "Starting mediaflow pipeline watcher (foreground)..."
exec python -m pipeline.watcher
