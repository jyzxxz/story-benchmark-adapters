#!/usr/bin/env bash
# 文本/语音/编译 worker：消费 text、audio、compile、maintenance 及默认队列。
# 与 start_worker_image.sh 配对使用，生图任务在独立的 worker 组中运行，互不阻塞。
#
# 并发约束（启动时软校验）：
#   (生图组进程数 + 文本组进程数 + FastAPI) × (DATABASE_POOL_SIZE + DATABASE_MAX_OVERFLOW)
#   不得超过 PostgreSQL max_connections（默认 200）。
set -euo pipefail

BACKEND_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$BACKEND_DIR"

# systemd EnvironmentFile deliberately does not expand ${VAR} references.
# Source the same shell-compatible file so aliases such as
# AI_IMAGE_API_KEY=${DASHSCOPE_API_KEY} reach provider clients as real values.
if [[ -f "$BACKEND_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$BACKEND_DIR/.env"
  set +a
fi

PYTHON_BIN="${PYTHON_BIN:-$BACKEND_DIR/venv/bin/python}"
CONCURRENCY="${TEXT_WORKER_CONCURRENCY:-4}"
# Keep the default queue as a safety net for messages produced by an older
# deployment while all current task names are explicitly routed.
QUEUES="${TEXT_WORKER_QUEUES:-text,audio,compile,maintenance,celery}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "backend virtualenv Python not found: $PYTHON_BIN" >&2
  exit 1
fi

# 连接数软校验：与 start_worker_image.sh 相同的公式。
DB_POOL_SIZE="${DATABASE_POOL_SIZE:-20}"
DB_MAX_OVERFLOW="${DATABASE_MAX_OVERFLOW:-20}"
PG_MAX_CONNECTIONS="${PG_MAX_CONNECTIONS:-200}"
IMAGE_CONCURRENCY_GROUP="${CELERY_CONCURRENCY:-5}"
TOTAL_PROCESSES=$(( IMAGE_CONCURRENCY_GROUP + CONCURRENCY + 1 ))
TOTAL_DB_CONNECTIONS=$(( TOTAL_PROCESSES * (DB_POOL_SIZE + DB_MAX_OVERFLOW) ))
if (( TOTAL_DB_CONNECTIONS > PG_MAX_CONNECTIONS )); then
  echo "WARNING: 预估 PG 连接数 ${TOTAL_PROCESSES}进程 × ${DB_POOL_SIZE}+${DB_MAX_OVERFLOW} = ${TOTAL_DB_CONNECTIONS} 可能超过 max_connections=${PG_MAX_CONNECTIONS}，请调小连接池。" >&2
fi

exec "$PYTHON_BIN" -m celery \
  -A app.workers.celery_app:celery_app worker \
  --loglevel "${CELERY_LOG_LEVEL:-INFO}" \
  --queues "$QUEUES" \
  --concurrency "$CONCURRENCY" \
  --prefetch-multiplier 1
