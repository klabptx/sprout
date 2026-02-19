#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   scripts/run_compare.sh <file.2020>           # single file
#   scripts/run_compare.sh /path/to/dir/          # all *.2020 in directory

ARG="${1:?Usage: $0 <file.2020 | directory>}"

STITCH_BIN="${STITCH_BIN:-/Users/karl.labarbara/ptx/stitch/.build/arm64/stitch}"
STITCH_PORT="${STITCH_PORT:-8888}"
STITCH_DATA_DIR="${STITCH_DATA_DIR:-/tmp/stitch}"
ORG_CODE="${ORG_CODE:-dev}"
SHIM_PORT="${SHIM_PORT:-9000}"
PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"
OUTPUT_DIR="${OUTPUT_DIR:-artifacts/compare_results}"
OUTPUT_PREFIX="${OUTPUT_PREFIX:-compare}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
export PYTHONPATH="${PROJECT_ROOT}${PYTHONPATH:+:$PYTHONPATH}"

export STITCH_LOCAL_BASE_URL="http://localhost:${STITCH_PORT}"
export TAILOR_SHIM_PORT="$SHIM_PORT"
export COMPARE_OUTPUT_DIR="$OUTPUT_DIR"

# Build file list
if [[ -d "$ARG" ]]; then
  shopt -s nullglob
  files=( "$ARG"/*.2020 )
  shopt -u nullglob
  if [[ ${#files[@]} -eq 0 ]]; then
    echo "No .2020 files found in $ARG"
    exit 1
  fi
elif [[ -f "$ARG" ]]; then
  files=( "$ARG" )
else
  echo "Not a file or directory: $ARG"
  exit 1
fi

# Bail out early if ports are already in use
if lsof -ti :"${STITCH_PORT}" >/dev/null 2>&1; then
  echo "Error: port ${STITCH_PORT} already in use (stitch?). Kill it first."
  exit 1
fi
if lsof -ti :"${SHIM_PORT}" >/dev/null 2>&1; then
  echo "Error: port ${SHIM_PORT} already in use (shim?). Kill it first."
  exit 1
fi

STITCH_PID=""
cleanup() {
  if [[ -n "$STITCH_PID" ]]; then
    kill "$STITCH_PID" 2>/dev/null || true
    wait "$STITCH_PID" 2>/dev/null || true
  fi
  kill "$SHIM_PID" 2>/dev/null || true
  wait "$SHIM_PID" 2>/dev/null || true
}
trap cleanup EXIT

# Start shim once for the entire batch
"$PYTHON_BIN" scripts/tailor_shim.py &
SHIM_PID=$!

shim_ready=false
for i in {1..10}; do
  if ! kill -0 "$SHIM_PID" 2>/dev/null; then
    echo "Shim exited early"
    exit 1
  fi
  if curl -s -o /dev/null "http://localhost:${SHIM_PORT}/" 2>/dev/null; then
    shim_ready=true
    break
  fi
  sleep 0.5
done
if [[ "$shim_ready" != "true" ]]; then
  echo "Shim did not become ready on port ${SHIM_PORT}"
  exit 1
fi

echo "Processing ${#files[@]} file(s)"

passed=0
failed=0
failed_files=()

for file in "${files[@]}"; do
  echo ""
  echo "=== $(basename "$file") ==="

  # Kill previous stitch and wait for port to be fully released
  if [[ -n "$STITCH_PID" ]]; then
    kill "$STITCH_PID" 2>/dev/null || true
    wait "$STITCH_PID" 2>/dev/null || true
    STITCH_PID=""
    for _w in {1..60}; do
      if ! lsof -ti :"${STITCH_PORT}" >/dev/null 2>&1; then
        break
      fi
      sleep 0.5
    done
    if lsof -ti :"${STITCH_PORT}" >/dev/null 2>&1; then
      echo "Port ${STITCH_PORT} still in use after 30s, skipping $file"
      ((failed++))
      failed_files+=( "$file" )
      continue
    fi
  fi

  # Start stitch for this file
  "$STITCH_BIN" --port "$STITCH_PORT" --data-dir "$STITCH_DATA_DIR" fs --file "$file" &
  STITCH_PID=$!

  # Wait for stitch (up to 30s)
  stitch_ready=false
  for i in {1..60}; do
    if ! kill -0 "$STITCH_PID" 2>/dev/null; then
      echo "Stitch exited early for $file"
      STITCH_PID=""
      break
    fi
    if curl -s "http://localhost:${STITCH_PORT}/status" >/dev/null 2>&1; then
      stitch_ready=true
      break
    fi
    sleep 0.5
  done

  status=0
  if [[ "$stitch_ready" == "true" ]]; then
    stream_id="$(basename "$file" .2020)"
    export TAILOR_STREAM_URL="http://localhost:${SHIM_PORT}/tailor/${ORG_CODE}/streams/${stream_id}"
    export COMPARE_OUTPUT_PREFIX="${OUTPUT_PREFIX}"

    set +e
    "$PYTHON_BIN" scripts/compare_event_records.py
    status=$?
    set -e
  else
    echo "Stitch not ready, skipping $file"
    status=1
  fi

  if [[ $status -eq 0 ]]; then
    ((passed++))
  else
    ((failed++))
    failed_files+=( "$file" )
  fi
done

echo ""
echo "=== Done: ${passed} passed, ${failed} failed out of ${#files[@]} ==="
if [[ ${#failed_files[@]} -gt 0 ]]; then
  for f in "${failed_files[@]}"; do
    echo "  FAILED: $f"
  done
fi
