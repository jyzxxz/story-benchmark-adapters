#!/usr/bin/env bash
# =============================================================================
# backend/dev.sh — 后端三件套一键管理（uvicorn:60002 + celery worker + celery beat）
#
# 用法：
#   ./dev.sh start                # 后台启动三件套（已在跑的自动跳过，幂等）
#   ./dev.sh stop                 # 停止三件套（PID 文件 + 按 cwd 精确清扫本仓实例）
#   ./dev.sh restart              # 重启
#   ./dev.sh status               # 状态总览：进程 / 端口 / 各服务日志尾部
#   ./dev.sh logs api|worker|beat # 跟踪对应日志（Ctrl-C 退出）
#
# 日志：  {repo}/logs/uvicorn.log | celery-worker.log | celery-beat.log
# PID：   {backend}/.runtime/pids/{api,worker,beat}.pid
#
# 安全边界：stop 只杀「工作目录 = 本仓 backend」的进程，
# selfimprove 克隆（if_line_selfimprove/backend，复用本仓 venv）不会被误杀。
# =============================================================================
set -euo pipefail

BACKEND_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname -- "$BACKEND_DIR")"
LOG_DIR="$REPO_ROOT/logs"
PID_DIR="$BACKEND_DIR/.runtime/pids"
PYTHON_BIN="$BACKEND_DIR/venv/bin/python"
API_PORT="${API_PORT:-60002}"

mkdir -p "$LOG_DIR" "$PID_DIR"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "❌ $PYTHON_BIN 不存在，先重建 venv：" >&2
  echo "   cd backend && rm -rf venv && conda create -p venv python=3.12 -y && ./venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

pid_alive() { [[ -f "$1" ]] && kill -0 "$(cat "$1")" 2>/dev/null; }

# 找出「工作目录 = 本仓 backend」且匹配 cmdline 的主进程。
# 过滤：可执行文件必须是 python（排除恰好带着模式串的临时 shell）；
# 父进程若也命中同一模式则是 prefork/reloader 子进程，只保留主进程。
sweep_pids() {
  local pattern="$1" pid cwd exe ppid
  local all=() pid_ppid=()
  while read -r pid; do
    [[ "$pid" =~ ^[0-9]+$ ]] || continue
    [[ "$pid" = "$$" ]] && continue
    exe="$(readlink "/proc/$pid/exe" 2>/dev/null || true)"
    [[ "$exe" == *python* ]] || continue
    cwd="$(readlink "/proc/$pid/cwd" 2>/dev/null || true)"
    [[ "$cwd" = "$BACKEND_DIR" ]] || continue
    all+=("$pid")
  done < <(pgrep -f "$pattern" 2>/dev/null || true)
  (( ${#all[@]} == 0 )) && return 0
  for pid in "${all[@]}"; do
    ppid="$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')"
    if [[ -n "$ppid" ]] && printf '%s\n' "${all[@]}" | grep -qx "$ppid"; then
      continue
    fi
    echo "$pid"
  done
}

find_service_pids() { # $1 = api|worker|beat —— pid 文件 + sweep 合并去重
  local svc="$1" pid pids seen=" "
  local pid_file="$PID_DIR/$svc.pid"
  if pid_alive "$pid_file"; then
    pid="$(cat "$pid_file")"
    echo "$pid"
    seen=" $pid "
  else
    rm -f "$pid_file" 2>/dev/null || true
  fi
  case "$svc" in
    api)    pids="$(sweep_pids "uvicorn app.main:app")" ;;
    worker) pids="$(sweep_pids "celery.*app.workers.celery_app:celery_app worker")" ;;
    beat)   pids="$(sweep_pids "celery.*app.workers.celery_app:celery_app beat")" ;;
  esac
  for pid in $pids; do
    [[ "$seen" == *" $pid "* ]] && continue
    echo "$pid"
  done
}

stop_pids() { # $1 = 服务名，其余参数为要停的 pid 列表
  local name="$1"; shift
  local pid
  for pid in "$@"; do
    echo "  停止 $name (pid $pid)..."
    kill -TERM "$pid" 2>/dev/null || true
  done
  for pid in "$@"; do
    local i=0
    while kill -0 "$pid" 2>/dev/null && (( i < 20 )); do
      sleep 0.3; i=$((i + 1))
    done
    if kill -0 "$pid" 2>/dev/null; then
      echo "  $name pid $pid 未退出，强杀..."
      kill -KILL "$pid" 2>/dev/null || true
    fi
  done
  rm -f "$PID_DIR/$name.pid"
}

# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------

cmd_start() {
  # 与 worker/beat 脚本一致：把 .env 展开进环境（AI_IMAGE_API_KEY=${DASHSCOPE_API_KEY} 等别名）
  if [[ -f "$BACKEND_DIR/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$BACKEND_DIR/.env"
    set +a
  fi
  # 日志重定向到文件时强制行缓冲，否则 beat/worker 少量输出会卡在 4KB 缓冲里
  export PYTHONUNBUFFERED=1

  # --- API ---
  if [[ -n "$(find_service_pids api)" ]]; then
    echo "• api 已在运行 (pid $(find_service_pids api | head -1))，跳过"
  else
    echo "• 启动 api (uvicorn :$API_PORT, --reload)..."
    (
      cd "$BACKEND_DIR"
      nohup "$PYTHON_BIN" -m uvicorn app.main:app \
        --host 0.0.0.0 --port "$API_PORT" --workers 1 --reload \
        --env-file "$BACKEND_DIR/.env" \
        >> "$LOG_DIR/uvicorn.log" 2>&1 &
      echo $! > "$PID_DIR/api.pid"
    )
    echo "  pid $(cat "$PID_DIR/api.pid") → logs/uvicorn.log"
  fi

  # --- Worker（单组全队列；生产想拆 text/image 再手动用 start_worker_text/image.sh）---
  if [[ -n "$(find_service_pids worker)" ]]; then
    echo "• worker 已在运行 (pid $(find_service_pids worker | head -1))，跳过"
  else
    echo "• 启动 celery worker（全队列, concurrency=${CELERY_CONCURRENCY:-2}）..."
    (
      cd "$BACKEND_DIR"
      nohup bash "$BACKEND_DIR/start_worker.sh" \
        >> "$LOG_DIR/celery-worker.log" 2>&1 &
      echo $! > "$PID_DIR/worker.pid"
    )
    echo "  pid $(cat "$PID_DIR/worker.pid") → logs/celery-worker.log"
  fi

  # --- Beat ---
  if [[ -n "$(find_service_pids beat)" ]]; then
    echo "• beat 已在运行 (pid $(find_service_pids beat | head -1))，跳过"
  else
    echo "• 启动 celery beat..."
    (
      cd "$BACKEND_DIR"
      nohup bash "$BACKEND_DIR/start_beat.sh" \
        >> "$LOG_DIR/celery-beat.log" 2>&1 &
      echo $! > "$PID_DIR/beat.pid"
    )
    echo "  pid $(cat "$PID_DIR/beat.pid") → logs/celery-beat.log"
  fi

  echo ""
  echo "✅ 启动完成。观察状态：$0 status"
}

cmd_stop() {
  local any_stopped=0
  for svc in api worker beat; do
    local pids
    pids="$(find_service_pids "$svc")"
    if [[ -n "$pids" ]]; then
      stop_pids "$svc" $pids
      any_stopped=1
    else
      echo "• $svc 未在运行"
    fi
  done
  if (( any_stopped )); then echo "✅ 已全部停止。"; else echo "（无进程需要停止）"; fi
}

cmd_status() {
  local fail=0
  echo "================= if_line backend 三件套状态 ================="
  echo "仓库：$REPO_ROOT"
  echo ""

  for svc in api worker beat; do
    local pids first etime state
    pids="$(find_service_pids "$svc")"
    if [[ -n "$pids" ]]; then
      first="$(echo "$pids" | head -1)"
      etime="$(ps -o etime= -p "$first" 2>/dev/null | tr -d ' ')"
      state="✅ 运行中  pid $first  已运行 $etime"
    else
      state="❌ 未运行"
      fail=1
    fi
    printf "%-7s %s\n" "$svc" "$state"
  done

  echo ""
  local http_code
  http_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "http://127.0.0.1:$API_PORT/docs" 2>/dev/null || echo 000)"
  if [[ "$http_code" = "200" ]]; then
    echo "HTTP    ✅ http://127.0.0.1:$API_PORT/docs → 200"
  else
    echo "HTTP    ❌ http://127.0.0.1:$API_PORT/docs → $http_code"
    [[ -n "$(find_service_pids api)" ]] && fail=1
  fi

  echo ""
  echo "---------------- 最近日志（各 3 行）----------------"
  for entry in "api:uvicorn.log" "worker:celery-worker.log" "beat:celery-beat.log"; do
    local name="${entry%%:*}" file="$LOG_DIR/${entry#*:}"
    echo "[$name] $file"
    if [[ -f "$file" ]]; then
      tail -n 3 "$file" | sed 's/^/    /'
    else
      echo "    （无日志文件）"
    fi
  done
  echo "============================================================="
  echo "跟踪日志：$0 logs api|worker|beat"
  (( fail )) && exit 1 || true
}

cmd_logs() {
  local svc="${1:-}"
  case "$svc" in
    api)    tail -n 50 -f "$LOG_DIR/uvicorn.log" ;;
    worker) tail -n 50 -f "$LOG_DIR/celery-worker.log" ;;
    beat)   tail -n 50 -f "$LOG_DIR/celery-beat.log" ;;
    *) echo "用法：$0 logs api|worker|beat" >&2; exit 2 ;;
  esac
}

case "${1:-}" in
  start)   cmd_start ;;
  stop)    cmd_stop ;;
  restart) cmd_stop; echo ""; cmd_start ;;
  status)  cmd_status ;;
  logs)    shift; cmd_logs "$@" ;;
  *) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
