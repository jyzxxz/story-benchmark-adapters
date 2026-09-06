#!/usr/bin/env bash
# 单组全队列 worker（兼容/开发用）。生产部署建议改用分队列脚本：
#   start_worker_image.sh —— 只消费 image 队列（生图专属）
#   start_worker_text.sh  —— 消费 text/audio/compile/maintenance（其余任务）
# 两者配对使用可避免文本/TTS 任务与生图任务互相争抢 worker 进程。
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
# Keep the default queue as a safety net for messages produced by an older
# deployment while all current task names are explicitly routed below.
QUEUES="${CELERY_QUEUES:-text,image,audio,compile,maintenance,celery}"
# SQLite compatibility deployments must keep this at 1. PostgreSQL deployments
# can raise CELERY_CONCURRENCY after observing provider and DB limits.
# Pool sizing: each worker process holds its own SQLAlchemy engine pool sized
# by DATABASE_POOL_SIZE + DATABASE_MAX_OVERFLOW. With pool=20+overflow=20 and
# the FastAPI process sharing the same PG (max_connections=200), concurrency=2
# peaks at 3×40=120 connections — comfortably under 200.
CONCURRENCY="${CELERY_CONCURRENCY:-2}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "backend virtualenv Python not found: $PYTHON_BIN" >&2
  exit 1
fi

exec "$PYTHON_BIN" -m celery \
  -A app.workers.celery_app:celery_app worker \
  --loglevel "${CELERY_LOG_LEVEL:-INFO}" \
  --queues "$QUEUES" \
  --concurrency "$CONCURRENCY" \
  --prefetch-multiplier 1
