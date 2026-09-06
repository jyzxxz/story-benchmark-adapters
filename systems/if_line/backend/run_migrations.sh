#!/usr/bin/env bash
set -euo pipefail

BACKEND_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$BACKEND_DIR"

PYTHON_BIN="${PYTHON_BIN:-$BACKEND_DIR/venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "backend virtualenv Python not found: $PYTHON_BIN" >&2
  exit 1
fi

exec "$PYTHON_BIN" -m alembic upgrade head

