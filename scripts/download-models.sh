#!/usr/bin/env bash
# Download ML model files used by the pipeline.
# Safe to re-run — skips files that already exist.

set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p models

BASE="https://raw.githubusercontent.com/GregorR/rnnoise-models/master"

_fetch() {
    local url="$1" dest="$2"
    if [[ -f "$dest" ]]; then
        printf '  already exists: %s\n' "$dest"
        return
    fi
    printf '  downloading %s ...\n' "$(basename "$dest")"
    curl -fsSL "$url" -o "$dest"
    printf '  ✓  %s\n' "$(basename "$dest")"
}

echo "=== RNNoise models (FFmpeg arnndn) ==="
_fetch "${BASE}/beguiling-drafter-2018-08-30/bd.rnnn"      models/bd.rnnn
_fetch "${BASE}/leavened-quisling-2018-08-31/lq.rnnn"      models/lq.rnnn

echo ""
echo "Done. Enable in config.yaml:"
echo "  preprocessing:"
echo "    rnnoise_model: models/bd.rnnn   # general noise"
echo "    # rnnoise_model: models/lq.rnnn # low-quality mic / far-field"
