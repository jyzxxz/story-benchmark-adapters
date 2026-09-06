/**
 * Phase 6 helper: poll v2 /tasks/{id} until terminal status.
 *
 * v2 generate endpoints (bible / outline / chapter / vn-graph / voice-lines)
 * all return TaskAccepted { task_id, status, events_url, created }. The
 * caller then needs to poll GET /api/tasks/{task_id} for the terminal
 * status. The TaskRead schema exposes `status`, `progress`, `stage`,
 * `error_code`, `error_detail`, `result_refs`.
 *
 * The polling runs locally in the browser with exponential backoff capped
 * at 5s. A global deadline protects against runaway loops.
 */
import api from '@/api/index'

export interface TaskRead {
  id: string
  status: string
  stage: string | null
  progress: number
  error_code: string | null
  error_detail: string | null
  result_refs: Record<string, any>
}

export interface TaskPollOptions {
  /** ms — total cap before giving up (default 10 min) */
  deadlineMs?: number
  /** ms — initial poll delay (default 1500) */
  initialDelayMs?: number
  /** ms — max poll delay after backoff (default 5000) */
  maxDelayMs?: number
  /** optional progress callback for UI updates */
  onProgress?: (task: TaskRead) => void
}

export interface TaskPollResult {
  task: TaskRead
  succeeded: boolean
  /**
   * True if the deadline elapsed while the task was still running on the
   * backend. Callers should NOT treat this as a failure — the task may
   * still complete successfully shortly after. UI should surface a
   * "still generating, please refresh" message instead of "失败".
   */
  timedOut: boolean
}

const TERMINAL_STATUSES = new Set(['partial', 'succeeded', 'failed', 'cancelled'])

export async function pollTaskUntilTerminal(
  taskId: string,
  opts: TaskPollOptions = {},
): Promise<TaskPollResult> {
  const deadline = Date.now() + (opts.deadlineMs ?? 10 * 60 * 1000)
  let delay = opts.initialDelayMs ?? 1500
  const maxDelay = opts.maxDelayMs ?? 5000

  let task: TaskRead | null = null
  while (Date.now() < deadline) {
    const resp = await api.get<TaskRead>(`/tasks/${taskId}`)
    task = resp.data
    opts.onProgress?.(task)
    if (TERMINAL_STATUSES.has(task.status)) {
      return { task, succeeded: task.status === 'succeeded', timedOut: false }
    }
    await new Promise(resolve => setTimeout(resolve, delay))
    delay = Math.min(Math.round(delay * 1.5), maxDelay)
  }
  // deadline elapsed without a terminal status. The backend task is likely
  // still running (LLM calls commonly take 2–4 min, occasionally 5+). Return
  // the last known task with timedOut=true so the caller can surface a
  // non-fatal "still running" message instead of falsely reporting failure.
  return {
    task: task ?? ({ id: taskId, status: 'timeout', progress: 0 } as TaskRead),
    succeeded: false,
    timedOut: true,
  }
}

/**
 * Format a task's failure into a user-facing message. Falls back through
 * error_detail → error_code → fallback so callers never need to roll their
 * own. Returns null if the task didn't actually fail (succeeded or timedOut).
 */
export function describeTaskFailure(task: TaskRead, fallback = '生成失败，请重试'): string | null {
  if (task.status === 'succeeded' || task.status === 'timeout') return null
  if (task.error_detail && task.error_detail.trim()) return task.error_detail
  if (task.error_code === 'llm.auth_failed') return 'API Key 无效或已失效，请检查后端 .env 的 OPENAI_API_KEY'
  if (task.error_code && task.error_code !== 'generation.failed') {
    return `${task.error_code}：${fallback}`
  }
  return fallback
}
