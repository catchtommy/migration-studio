#!/usr/bin/env bash
# Starts the backend (:8000) and the UI (:5173). Ctrl+C stops both.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
export PATH="$ROOT/.tools/node/bin:$PATH"

(cd "$ROOT/backend" && .venv/bin/uvicorn app.main:app --port 8000 --reload) &
(cd "$ROOT/frontend" && npx vite --port 5173 --strictPort) &
trap 'kill 0' EXIT
wait
