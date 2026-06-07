#!/usr/bin/env bash
# End-to-end pipeline smoke test.
# Copies test-speech.m4a into workspace/1_input/ and waits for outputs.
#
# Prerequisites (must all be running before this script):
#   bash scripts/start-services.sh        # Docker: redis + api + web
#   bash scripts/start-pipeline.sh        # in a separate terminal
#   Whisper service on localhost:9001
#   Ollama with qwen2.5:7b on localhost:11434
#
# Usage:
#   bash tests/run-pipeline-test.sh

set -e
cd "$(dirname "$0")/.."

STEM="test-speech"
INPUT_FILE="tests/fixtures/${STEM}.m4a"
OUTPUT_SRT="workspace/3_output/${STEM}.srt"
OUTPUT_MD="workspace/3_output/${STEM}_summary.md"
ARCHIVE_FILE="workspace/4_archive/${STEM}.m4a"
TIMEOUT=300   # 5 minutes max

# ── Preflight ────────────────────────────────────────────────────────────────
echo "=== mediaflow smoke test ==="
echo ""

if [ ! -f "$INPUT_FILE" ]; then
  echo "ERROR: $INPUT_FILE not found" && exit 1
fi

if ! curl -sf http://localhost:8080/health > /dev/null; then
  echo "ERROR: API not reachable at localhost:8080 — run: bash scripts/start-services.sh" && exit 1
fi

if ! curl -sf http://localhost:9001/transcribe_segments > /dev/null 2>&1; then
  echo "WARNING: Whisper service may not be running at localhost:9001"
  echo "  Pipeline will fail at transcription stage."
fi

# ── Clean up any previous test run ───────────────────────────────────────────
rm -f "workspace/1_input/${STEM}.m4a"
rm -f "workspace/1_input/${STEM}.m4a.failed"
rm -f "$OUTPUT_SRT" "$OUTPUT_MD"
rm -f "$ARCHIVE_FILE"

# ── Drop file into pipeline ───────────────────────────────────────────────────
echo "Dropping $INPUT_FILE into workspace/1_input/ ..."
cp "$INPUT_FILE" "workspace/1_input/${STEM}.m4a"
START=$(date +%s)

# ── Poll for completion ───────────────────────────────────────────────────────
echo "Waiting for pipeline (timeout: ${TIMEOUT}s) ..."
echo ""

while true; do
  ELAPSED=$(( $(date +%s) - START ))

  STATUS=$(curl -sf "http://localhost:8080/status/" 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
for t in d.get('completed', []) + d.get('recent', []):
    if t.get('stem') == '${STEM}':
        print('completed')
        sys.exit(0)
for t in d.get('failed', []):
    if t.get('stem') == '${STEM}':
        print('failed')
        sys.exit(0)
print('pending')
" 2>/dev/null || echo "pending")

  if [ "$STATUS" = "completed" ]; then
    echo "✓  Pipeline completed (${ELAPSED}s)"
    break
  elif [ "$STATUS" = "failed" ]; then
    echo "✗  Pipeline FAILED (${ELAPSED}s)"
    echo ""
    echo "Check logs: docker compose logs api"
    exit 1
  fi

  if [ "$ELAPSED" -ge "$TIMEOUT" ]; then
    echo "✗  Timed out after ${TIMEOUT}s — pipeline may still be running"
    echo ""
    echo "Check status: curl http://localhost:8080/status/"
    exit 1
  fi

  printf "   [%3ds] status=%s ...\r" "$ELAPSED" "$STATUS"
  sleep 5
done

# ── Verify outputs ────────────────────────────────────────────────────────────
echo ""
PASS=0
FAIL=0

check() {
  local label="$1" path="$2"
  if [ -f "$path" ]; then
    SIZE=$(wc -c < "$path" | tr -d ' ')
    echo "  ✓  $label ($SIZE bytes)"
    PASS=$((PASS + 1))
  else
    echo "  ✗  $label — MISSING: $path"
    FAIL=$((FAIL + 1))
  fi
}

check "SRT transcript"     "$OUTPUT_SRT"
check "Summary markdown"   "$OUTPUT_MD"
check "Summary JSON"       "workspace/3_output/${STEM}_summary.json"
check "Processed WAV"      "workspace/3_output/${STEM}_clean.wav"
check "Archived original"  "$ARCHIVE_FILE"

# SRT content sanity check
if [ -f "$OUTPUT_SRT" ]; then
  LINES=$(wc -l < "$OUTPUT_SRT" | tr -d ' ')
  if [ "$LINES" -gt 10 ]; then
    echo "  ✓  SRT has content ($LINES lines)"
    PASS=$((PASS + 1))
  else
    echo "  ✗  SRT looks empty ($LINES lines — expected > 10)"
    FAIL=$((FAIL + 1))
  fi
fi

echo ""
echo "=== Result: $PASS passed, $FAIL failed ==="

if [ "$FAIL" -gt 0 ]; then
  echo ""
  echo "Diagnostics:"
  echo "  API status:  curl http://localhost:8080/status/"
  echo "  API logs:    docker compose logs api"
  echo "  SRT content: cat $OUTPUT_SRT"
  exit 1
fi

echo ""
echo "View in browser: open http://localhost:3000"
