#!/usr/bin/env bash
# 生图专属 worker：只消费 image 队列。
# 与 start_worker_text.sh 配对使用，避免文本/TTS 任务与生图任务互相争抢 worker 进程。
#
# 并发约束（启动时校验，超限直接拒绝启动）：
#   总生图并发 = CELERY_CONCURRENCY × IMAGE_CONCURRENCY 不得超出
#   图片供应商单 Key 并发上限（当前中转站给本 Key 的上限为 15）。
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
CONCURRENCY="${CELERY_CONCURRENCY:-5}"
IMAGE_CONCURRENCY="${IMAGE_CONCURRENCY:-2}"
# 图片供应商单 Key 并发上限。多 Key 部署（AI_IMAGE_API_KEYS）时按 Key 数上调。
IMAGE_PROVIDER_MAX_CONCURRENCY="${IMAGE_PROVIDER_MAX_CONCURRENCY:-15}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "backend virtualenv Python not found: $PYTHON_BIN" >&2
  exit 1
fi

TOTAL_IMAGE_CONCURRENCY=$(( CONCURRENCY * IMAGE_CONCURRENCY ))
if (( TOTAL_IMAGE_CONCURRENCY > IMAGE_PROVIDER_MAX_CONCURRENCY )); then
  echo "ERROR: 总生图并发 CELERY_CONCURRENCY(${CONCURRENCY}) × IMAGE_CONCURRENCY(${IMAGE_CONCURRENCY}) = ${TOTAL_IMAGE_CONCURRENCY} 超出供应商单 Key 上限 ${IMAGE_PROVIDER_MAX_CONCURRENCY}。" >&2
  echo "       请调小 IMAGE_CONCURRENCY 或 CELERY_CONCURRENCY；多 Key 部署时设置 IMAGE_PROVIDER_MAX_CONCURRENCY=15×Key数。" >&2
  exit 1
fi

# 连接数软校验：进程数 × 每进程连接数不应超过 PG max_connections。
DB_POOL_SIZE="${DATABASE_POOL_SIZE:-20}"
DB_MAX_OVERFLOW="${DATABASE_MAX_OVERFLOW:-20}"
PG_MAX_CONNECTIONS="${PG_MAX_CONNECTIONS:-200}"
# 生图组进程数 + 文本组进程数(默认4) + FastAPI(1)
TOTAL_PROCESSES=$(( CONCURRENCY + TEXT_WORKER_CONCURRENCY + 1 ))
TOTAL_DB_CONNECTIONS=$(( TOTAL_PROCESSES * (DB_POOL_SIZE + DB_MAX_OVERFLOW) ))
if (( TOTAL_DB_CONNECTIONS > PG_MAX_CONNECTIONS )); then
  echo "WARNING: 预估 PG 连接数 ${TOTAL_PROCESSES}进程 × ${DB_POOL_SIZE}+${DB_MAX_OVERFLOW} = ${TOTAL_DB_CONNECTIONS} 可能超过 max_connections=${PG_MAX_CONNECTIONS}，请调小连接池。" >&2
fi

exec "$PYTHON_BIN" -m celery \
  -A app.workers.celery_app:celery_app worker \
  --loglevel "${CELERY_LOG_LEVEL:-INFO}" \
  --queues "image" \
  --concurrency "$CONCURRENCY" \
  --prefetch-multiplier 1
