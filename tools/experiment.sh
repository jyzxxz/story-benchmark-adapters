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
    exec python3 tools/open_eval30.py "$@"
    ;;
  playback)
    exec python3 tools/export_playback.py "$@"
    ;;
  preview-legacy)
    exec python3 tools/eval30.py --legacy-actions "$@"
    ;;
  list)
    exec python3 tools/open_eval30.py --list "$@"
    ;;
  run)
    exec python3 tools/experiment.py --preset pilot --run "$@"
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
One-command action-open story experiments (Ubuntu/WSL2).
Same task and rubric, no prescribed key actions. All bundled tasks remain pilot.
  bash experiment.sh setup                              # install/configure; no models
  bash experiment.sh preview                            # full open prompts, no keys
  bash experiment.sh preview-legacy --out work/legacy    # historical prescribed-action suite
  bash experiment.sh list                               # case IDs and titles
  bash experiment.sh quick --allow-pilot                # 1 case x 1 repeat x 3 systems; asks RUN
  bash experiment.sh run --allow-pilot --count 12 --concurrency 2
                                                       # 12 attempts/system, 36 total; sequential systems
  bash experiment.sh run --allow-pilot --genres SCI-FI --count 7 --systems if_line
  bash experiment.sh run --allow-pilot --case-ids CAMPUS-01 SCI-FI-01 --repeat 2
  bash experiment.sh genres --allow-pilot               # one case of each genre
  bash experiment.sh prepare --allow-pilot              # freeze inputs; no paid calls
  bash experiment.sh resume --out work/experiments/NAME # queued-only; asks RUN
  bash experiment.sh verify --out work/experiments/NAME # no credentials/models
  bash experiment.sh playback --input work/experiments/NAME --out work/review/NAME
                                                       # export offline folder/ZIP; no models
--count is per-system TOTAL attempts; --repeat is repetitions PER CASE. Do not combine.
--concurrency controls root jobs within the currently active system, not HTTP requests.
Use --yes only to explicitly authorize paid calls. No adapter total cost/token/time cap.
Guide: docs/OPEN_ACTION_EXPERIMENTS.md. Offline playback: docs/PLAYBACK_REVIEW.md.
After generation, REVIEW_DELIVERY.txt identifies the whole-batch ZIP to send.
Recipients extract it and open 打开故事.html; no installation or API key needed.
Human review: tools/approve_eval30.py.
EOF
    ;;
  *) echo "Unknown mode: $MODE. Use --help." >&2; exit 2 ;;
esac
