#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 0 ]]; then
  echo "用法: backend/docs/apifox/apifox_doc.sh" >&2
  exit 2
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="$SCRIPT_DIR/../../venv/bin/python"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

"$PYTHON_BIN" "$SCRIPT_DIR/split_openapi.py" build \
  --source "$SCRIPT_DIR/source" \
  --output "$SCRIPT_DIR/ifline_product.openapi.json"

"$PYTHON_BIN" "$SCRIPT_DIR/split_openapi.py" catalog \
  --source "$SCRIPT_DIR/source" \
  --output "$SCRIPT_DIR/source/catalog.json"
