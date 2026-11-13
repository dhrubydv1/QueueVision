#!/usr/bin/env bash
# One-command demo: build the dashboard if needed, export accuracy data, start the API.
# Run from anywhere: scripts/demo.sh   (needs the Python venv deps and Node 22 + npm).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
  if [ -x ".venv/bin/python" ]; then PYTHON=".venv/bin/python"; else PYTHON="python3"; fi
fi

if [ ! -d dashboard/out ]; then
  echo "dashboard/out not found: building the dashboard (npm ci, npm run build)"
  (cd dashboard && npm ci && npm run build)
fi

# Missing evaluation files only make sections of the accuracy page empty; do not stop the demo.
"$PYTHON" scripts/export_accuracy.py || echo "export_accuracy.py failed; the Accuracy page will say the data is not generated" >&2

echo "Starting the API on http://127.0.0.1:8000 (Ctrl+C to stop)"
exec "$PYTHON" -m api
