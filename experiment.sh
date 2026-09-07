#!/usr/bin/env bash
# Thin root alias. Keep one authoritative setup/experiment workflow in tools/.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$ROOT/tools/experiment.sh" "$@"
