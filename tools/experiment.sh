#!/usr/bin/env bash
# Run with bash; no environment file is executed as shell code.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
MODE="${1:-help}"
if [[ $# -gt 0 ]]; then shift; fi
case "$MODE" in
  setup)
    if [[ "$(uname -s)" != Linux ]]; then
      echo 'Use Ubuntu 24.04 or WSL2 Ubuntu 24.04 for native generation.' >&2; exit 2
    fi
    bash tools/bootstrap_linux.sh --system all "$@"
    source work/activate.sh
    if [[ ! -f work/config/batch.local.json ]]; then python3 tools/configure_batch.py; fi
    python3 tools/setup_experiment.py
    python3 tools/doctor.py --system all
    ;;
  configure)
    exec work/envs/common/bin/python tools/setup_experiment.py "$@"
    ;;
  preview)
    exec python3 tools/eval30.py "$@"
    ;;
  quick|genres|pilot|full)
    exec python3 tools/experiment.py --preset "$MODE" --run "$@"
    ;;
  prepare)
    exec python3 tools/experiment.py "$@"
    ;;
  preflight|resume|verify)
    exec python3 tools/experiment.py "--$MODE" "$@"
    ;;
  test)
    exec python3 -m unittest discover -s tests -p 'test_eval30*.py' -v
    ;;
  help|-h|--help)
    cat <<'EOF'
One-command story experiments (Ubuntu/WSL2). No command silently buys model calls.
  bash tools/experiment.sh setup                         # install + local credential wizard; no models
  bash tools/experiment.sh preview                       # export all 30 full prompts; no config/key needed
  bash tools/experiment.sh quick --allow-pilot            # 1 case x 1 repeat x 3 systems; asks RUN
  bash tools/experiment.sh genres --allow-pilot           # 6 genres x 1 repeat x 3 systems; asks RUN
  bash tools/experiment.sh pilot --allow-pilot            # 30 cases x 1 repeat x 3 systems; asks RUN
  bash tools/experiment.sh full --allow-pilot             # 30 cases x 3 repeats x 3 systems; asks RUN
  bash tools/experiment.sh resume --out work/experiments/NAME   # queued-only; asks RUN
  bash tools/experiment.sh verify --out work/experiments/NAME   # no credentials/models
Use --yes only to explicitly authorize paid calls without a terminal confirmation.
Content review: tools/approve_eval30.py. Complete guide: docs/EXPERIMENTS.md.
EOF
    ;;
  *) echo "Unknown mode: $MODE. Use --help." >&2; exit 2 ;;
esac
