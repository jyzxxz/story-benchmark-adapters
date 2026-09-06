#!/usr/bin/env bash
# 自动化二创 Agent —— tmux 守护 wrapper。
#
# 用法:
#   bash scripts/auto_creator_cron.sh once [pace] [theme]   # 跑一篇就退
#   bash scripts/auto_creator_cron.sh loop [interval_hours] # 每 N 小时跑一篇, 直至 kill
#
# 不写系统 crontab, 先用 tmux 守护观察稳定性;
# 后续若要沉底为正式 cron, 走 .claude/skills/execution-cron-builder。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BACKEND_DIR="${REPO_ROOT}/backend"
SELF_ABS="${SCRIPT_DIR}/$(basename "${BASH_SOURCE[0]}")"
SESSION_NAME="auto_creator_cron"

cd "${BACKEND_DIR}"

# 激活 venv (若存在)
if [[ -f "${BACKEND_DIR}/venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "${BACKEND_DIR}/venv/bin/activate"
fi

run_once() {
  local pace="${1:-medium}"
  local theme="${2:-}"
  local args=(--pace "${pace}")
  if [[ -n "${theme}" ]]; then
    args+=(--theme "${theme}")
  fi
  echo "[$(date -u +%FT%TZ)] starting auto-creator run pace=${pace} theme='${theme}'"
  if python -m app.scripts.run_auto_creator "${args[@]}"; then
    echo "[$(date -u +%FT%TZ)] run finished OK"
  else
    echo "[$(date -u +%FT%TZ)] run FAILED (exit $?)" >&2
  fi
}

case "${1:-loop}" in
  once)
    run_once "${2:-medium}" "${3:-}"
    ;;
  loop)
    INTERVAL_HOURS="${2:-6}"
    # 支持小数小时 (如 0.333 = 20 min), 用 awk 算秒数避免整数除零
    if ! INTERVAL_SECONDS=$(awk -v h="${INTERVAL_HOURS}" 'BEGIN{ s = h * 3600; if (s < 60) s = 60; printf "%d", s }'); then
      echo "invalid interval: ${INTERVAL_HOURS}" >&2
      exit 1
    fi
    if tmux has-session -t "${SESSION_NAME}" 2>/dev/null; then
      echo "tmux session '${SESSION_NAME}' already exists. attach: tmux attach -t ${SESSION_NAME}"
      exit 0
    fi
    tmux new-session -d -s "${SESSION_NAME}" \
      "while true; do bash \"${SELF_ABS}\" once medium \"\" || true; echo \"[$(date -u +%FT%TZ)] sleeping ${INTERVAL_HOURS}h (${INTERVAL_SECONDS}s)...\"; sleep ${INTERVAL_SECONDS}; done"
    echo "tmux session '${SESSION_NAME}' started. interval=${INTERVAL_HOURS}h (${INTERVAL_SECONDS}s)"
    echo "  attach:  tmux attach -t ${SESSION_NAME}"
    echo "  stop:    tmux kill-session -t ${SESSION_NAME}"
    ;;
  stop)
    tmux kill-session -t "${SESSION_NAME}" 2>/dev/null || true
    echo "stopped."
    ;;
  *)
    echo "Usage: $0 {once [pace] [theme] | loop [interval_hours] | stop}" >&2
    exit 1
    ;;
esac
