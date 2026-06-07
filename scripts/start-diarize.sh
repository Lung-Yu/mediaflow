#!/usr/bin/env bash
# Start local speaker diarization service on :9003.
# No HuggingFace token needed — speechbrain models are Apache 2.0.
# Creates venv-diarize/ on first run (~200 MB model download on first request).
set -euo pipefail
cd "$(dirname "$0")/.."

VENV="venv-diarize"
if [[ ! -d "$VENV" ]]; then
    echo "Creating $VENV and installing dependencies..."
    python3 -m venv "$VENV"
    source "$VENV/bin/activate"
    pip install --quiet -r diarize/requirements.txt
else
    source "$VENV/bin/activate"
fi

echo "Starting diarization service on :9003 ..."
echo "  First request downloads ECAPA-TDNN model (~200 MB, cached in ~/.cache/speechbrain/)"
exec uvicorn diarize.service:app --host 0.0.0.0 --port 9003
