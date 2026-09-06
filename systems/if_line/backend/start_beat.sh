#!/usr/bin/env bash
set -euo pipefail

BACKEND_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$BACKEND_DIR"

PYTHON_BIN="${PYTHON_BIN:-$BACKEND_DIR/venv/bin/python}"
SCHEDULE_FILE="${CELERY_BEAT_SCHEDULE:-$BACKEND_DIR/.runtime/celerybeat-schedule}"
mkdir -p "$(dirname -- "$SCHEDULE_FILE")"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "backend virtualenv Python not found: $PYTHON_BIN" >&2
  exit 1
fi

exec "$PYTHON_BIN" -m celery \
  -A app.workers.celery_app:celery_app beat \
  --loglevel "${CELERY_LOG_LEVEL:-INFO}" \
  --schedule "$SCHEDULE_FILE"

