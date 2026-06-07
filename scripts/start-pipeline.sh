#!/usr/bin/env bash
# Start the pipeline watcher (host-native, requires Whisper + Ollama running)
set -e
cd "$(dirname "$0")/.."

if [ ! -f venv/bin/activate ]; then
  echo "Creating venv..."
  python3 -m venv venv
fi

source venv/bin/activate
pip install -q -r requirements.txt

echo "Starting mediaflow pipeline watcher..."
python -m pipeline.watcher
