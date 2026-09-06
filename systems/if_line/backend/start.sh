#!/usr/bin/env bash
set -euo pipefail

BACKEND_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$BACKEND_DIR"

PYTHON_BIN="${PYTHON_BIN:-$BACKEND_DIR/venv/bin/python}"
ENV_FILE="${ENV_FILE:-$BACKEND_DIR/.env}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "backend virtualenv Python not found: $PYTHON_BIN" >&2
  exit 1
fi

UVICORN_ARGS=(
  app.main:app
  --host "${HOST:-0.0.0.0}"
  --port "${PORT:-60002}"
  --workers "${WORKERS:-1}"
)

if [[ -f "$ENV_FILE" ]]; then
  UVICORN_ARGS+=(--env-file "$ENV_FILE")
fi

exec "$PYTHON_BIN" -m uvicorn "${UVICORN_ARGS[@]}"
