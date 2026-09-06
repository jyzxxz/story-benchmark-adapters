/**
 * 客户端草稿区（纯 localStorage，永不写服务端）。
 *
 * 设计要点（与后端"物化"机制配套）：
 * - 草稿只存本地：`if-line:draft:v1:{kind}:{subjectId}`。subject 与 head 键控解耦：
 *   bible→projectId、outline→storyPathId、chapter/script/graph→pathChapterId，
 *   正文固化（head 漂移）后草稿不失锚。
 * - 值为 `{ schema, kind, subjectId, baseRevisionId, payload, upstreamHashes, updatedAt }`。
 *   baseRevisionId = 草稿复制自的已固化修订（物化时作为 parent）。
 * - upstreamHashes = 保存草稿那一刻的上游有效内容 hash（上游=草稿优先，无草稿取 head 的
 *   content_hash）。**编辑草稿不刷新 upstreamHashes** —— 它是 stale 判定的基线。
 * - stale：当前上游有效 hash ≠ upstreamHashes → 提示"上游已改，本环节需重做"。
 * - 建议：AI 生成结果=未激活修订。最新 ready 修订 R 满足
 *   R.id ∉ {head.revision_id, draft.baseRevisionId, dismissedRevisionId} → 待选建议。
 * - 配额超限（QuotaExceededError）降级：本次会话内改用内存兜底，标记
 *   usingMemoryFallback 供 UI 提示"草稿未能持久化，刷新将丢失"。
 *
 * 本文件刻意不依赖 Vue（可在 node:test 下直接测试）；组件侧自行持有响应式引用。
 */
import type { UUID } from '../api/storyPathTypes'

export type ArtifactKind = 'bible' | 'outline' | 'chapter' | 'script' | 'graph'

export const ARTIFACT_KINDS: readonly ArtifactKind[] = [
  'bible',
  'outline',
  'chapter',
  'script',
  'graph',
]

export const DRAFT_SCHEMA_VERSION = 1
export const DRAFT_STORAGE_PREFIX = 'if-line:draft:v1'
export const DISMISSAL_STORAGE_PREFIX = 'if-line:draft-dismissed'

export interface StorageLike {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
  removeItem(key: string): void
}

export interface ArtifactDraftRecord<TPayload = unknown> {
  schema: typeof DRAFT_SCHEMA_VERSION
  kind: ArtifactKind
  subjectId: string
  /** 草稿复制自的已固化修订；null 表示从空白起步。 */
  baseRevisionId: UUID | null
  payload: TPayload
  /** 保存时刻的上游有效内容 hash（编辑不刷新，是 stale 判定基线）。 */
  upstreamHashes: Record<string, string>
  updatedAt: string
}

export interface DraftWriteInput<TPayload = unknown> {
  /** 省略 = 沿用现有草稿的基线（编辑保存场景）；显式 null = 清空。 */
  baseRevisionId?: UUID | null
  payload: TPayload
  /** 省略 = 沿用现有 upstreamHashes（编辑不刷新基线）。 */
  upstreamHashes?: Record<string, string>
}

export interface DraftWriteResult {
  persisted: boolean
  fallbackToMemory: boolean
}

export interface ArtifactDraftStoreOptions {
  storage?: StorageLike
  now?: () => Date
}

export interface ArtifactDraftStore {
  readDraft<TPayload = unknown>(
    kind: ArtifactKind,
    subjectId: string | number,
  ): ArtifactDraftRecord<TPayload> | null
  writeDraft<TPayload = unknown>(
    kind: ArtifactKind,
    subjectId: string | number,
    input: DraftWriteInput<TPayload>,
  ): DraftWriteResult
  clearDraft(kind: ArtifactKind, subjectId: string | number): void
  dismissSuggestion(
    projectId: number,
    kind: ArtifactKind,
    subjectId: string | number,
    revisionId: UUID,
  ): void
  dismissedSuggestion(
    projectId: number,
    kind: ArtifactKind,
    subjectId: string | number,
  ): string | null
  clearDismissal(projectId: number, kind: ArtifactKind, subjectId: string | number): void
  /** 有草稿曾因配额超限降级到内存（刷新即丢），UI 应提示。 */
  readonly usingMemoryFallback: boolean
}

export const draftStorageKey = (kind: ArtifactKind, subjectId: string | number): string =>
  `${DRAFT_STORAGE_PREFIX}:${kind}:${subjectId}`

export const dismissalStorageKey = (
  projectId: number,
  kind: ArtifactKind,
  subjectId: string | number,
): string => `${DISMISSAL_STORAGE_PREFIX}:${projectId}:${kind}:${subjectId}`

export function createMemoryStorage(): StorageLike {
  const entries = new Map<string, string>()
  return {
    getItem: (key) => (entries.has(key) ? (entries.get(key) as string) : null),
    setItem: (key, value) => {
      entries.set(key, String(value))
    },
    removeItem: (key) => {
      entries.delete(key)
    },
  }
}

function resolveDefaultStorage(): StorageLike {
  try {
    if (typeof globalThis.localStorage !== 'undefined') return globalThis.localStorage
  } catch {
    // SSR / 受限环境：落到内存兜底。
  }
  return createMemoryStorage()
}

export function createArtifactDraftStore(
  options: ArtifactDraftStoreOptions = {},
): ArtifactDraftStore {
  const storage = options.storage ?? resolveDefaultStorage()
  const now = options.now ?? (() => new Date())
  // 配额超限后的会话内兜底（持久层写坏时读仍可用）。
  const memoryDrafts = new Map<string, ArtifactDraftRecord>()
  const memoryDismissals = new Map<string, string>()
  let memoryFallbackActive = false

  const parseDraft = <TPayload>(raw: string | null): ArtifactDraftRecord<TPayload> | null => {
    if (!raw) return null
    try {
      const parsed = JSON.parse(raw) as Partial<ArtifactDraftRecord<TPayload>> | null
      if (!parsed || typeof parsed !== 'object') return null
      if (parsed.schema !== DRAFT_SCHEMA_VERSION) return null
      if (typeof parsed.kind !== 'string' || typeof parsed.subjectId !== 'string') return null
      return {
        schema: DRAFT_SCHEMA_VERSION,
        kind: parsed.kind as ArtifactKind,
        subjectId: parsed.subjectId,
        baseRevisionId: parsed.baseRevisionId ?? null,
        payload: (parsed.payload ?? null) as TPayload,
        upstreamHashes: parsed.upstreamHashes ?? {},
        updatedAt: typeof parsed.updatedAt === 'string' ? parsed.updatedAt : now().toISOString(),
      }
    } catch {
      return null
    }
  }

  return {
    readDraft<TPayload>(kind, subjectId) {
      const key = draftStorageKey(kind, subjectId)
      const draft = parseDraft<TPayload>(storage.getItem(key))
      if (draft) return draft
      // 损坏条目顺手清掉，避免反复踩坑。
      storage.removeItem(key)
      const fallback = memoryDrafts.get(key)
      return fallback ? (fallback as ArtifactDraftRecord<TPayload>) : null
    },

    writeDraft<TPayload>(kind, subjectId, input) {
      const key = draftStorageKey(kind, subjectId)
      const existing = parseDraft<TPayload>(storage.getItem(key)) ?? memoryDrafts.get(key)
      const record: ArtifactDraftRecord<TPayload> = {
        schema: DRAFT_SCHEMA_VERSION,
        kind,
        subjectId: String(subjectId),
        baseRevisionId: input.baseRevisionId === undefined
          ? (existing?.baseRevisionId ?? null)
          : input.baseRevisionId,
        payload: input.payload,
        // 编辑保存不传 upstreamHashes → 沿用旧基线，保证 stale 判定不因编辑刷新。
        upstreamHashes: input.upstreamHashes ?? existing?.upstreamHashes ?? {},
        updatedAt: now().toISOString(),
      }
      try {
        storage.setItem(key, JSON.stringify(record))
        memoryDrafts.delete(key)
        return { persisted: true, fallbackToMemory: false }
      } catch {
        // QuotaExceededError 等：会话内降级内存，UI 据 usingMemoryFallback 提示。
        memoryFallbackActive = true
        memoryDrafts.set(key, record as ArtifactDraftRecord)
        return { persisted: false, fallbackToMemory: true }
      }
    },

    clearDraft(kind, subjectId) {
      const key = draftStorageKey(kind, subjectId)
      storage.removeItem(key)
      memoryDrafts.delete(key)
    },

    dismissSuggestion(projectId, kind, subjectId, revisionId) {
      const key = dismissalStorageKey(projectId, kind, subjectId)
      try {
        storage.setItem(key, revisionId)
        memoryDismissals.delete(key)
      } catch {
        memoryFallbackActive = true
        memoryDismissals.set(key, revisionId)
      }
    },

    dismissedSuggestion(projectId, kind, subjectId) {
      const key = dismissalStorageKey(projectId, kind, subjectId)
      try {
        const stored = storage.getItem(key)
        if (stored) return stored
      } catch {
        // 读取失败也走内存兜底。
      }
      return memoryDismissals.get(key) ?? null
    },

    clearDismissal(projectId, kind, subjectId) {
      const key = dismissalStorageKey(projectId, kind, subjectId)
      storage.removeItem(key)
      memoryDismissals.delete(key)
    },

    get usingMemoryFallback() {
      return memoryFallbackActive
    },
  }
}

let defaultStore: ArtifactDraftStore | null = null

/** 组件侧入口：全局共享同一个草稿存储。 */
export function useArtifactDraft(): ArtifactDraftStore {
  if (!defaultStore) defaultStore = createArtifactDraftStore()
  return defaultStore
}

/* ------------------------------ 草稿指纹 ------------------------------ */

/**
 * 稳定的客户端指纹（键排序的 JSON）。只用于本地"上游草稿内容是否变化"的判定，
 * 绝不与后端 content_hash 混用/比较——两套哈希空间各自闭环：
 * 保存与校验时对同一来源（草稿 or head）使用同一配方即可。
 */
export function draftFingerprint(value: unknown): string {
  if (value === null || value === undefined) return 'null'
  if (Array.isArray(value)) return `[${value.map((item) => draftFingerprint(item)).join(',')}]`
  if (typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>).sort(([left], [right]) =>
      left < right ? -1 : left > right ? 1 : 0,
    )
    return `{${entries
      .map(([key, item]) => `${JSON.stringify(key)}:${draftFingerprint(item)}`)
      .join(',')}}`
  }
  return JSON.stringify(value) ?? 'null'
}

/* ------------------------------ stale 判定 ------------------------------ */

/**
 * 当前上游有效内容 hash（调用方按"草稿优先、无草稿取 head content_hash"组装）
 * 与草稿保存时刻的 upstreamHashes 不一致 → stale，需要重做。
 */
export function isDraftStale(
  draft: Pick<ArtifactDraftRecord, 'upstreamHashes'>,
  currentUpstreamHashes: Record<string, string | null | undefined>,
): boolean {
  return Object.entries(draft.upstreamHashes).some(([subject, recordedHash]) => {
    const currentHash = currentUpstreamHashes[subject]
    return !currentHash || currentHash !== recordedHash
  })
}

/** 组装上游有效 hash：草稿 hash 优先，回落到 head content_hash；空值丢弃。 */
export function buildUpstreamHashes(
  entries: Record<string, string | null | undefined>,
): Record<string, string> {
  const hashes: Record<string, string> = {}
  for (const [subject, hash] of Object.entries(entries)) {
    if (hash) hashes[subject] = hash
  }
  return hashes
}

/* ------------------------------ 建议（待选 AI 结果） ------------------------------ */

export interface RevisionSuggestionCandidate {
  id: UUID
  revision_no: number
  status?: string
}

/** 与后端 readiness 一致：只有 ready/complete 修订可作为建议。 */
export const READY_SUGGESTION_STATUSES: readonly string[] = ['ready', 'complete']

/**
 * 最新 ready 修订 R，且 R.id ∉ {head.revision_id, draft.baseRevisionId, dismissedRevisionId}
 * → 待选建议；否则 null。revision_no 大者优先。
 * minRevisionNo：head 的序号。传入了就要求候选比 head 新——回写/AutoCreator 把 head 顶到
 * r{n+1} 后，被顶掉的旧 head r{n} 不是"新结果"，不应再作为建议出现。
 */
export function findPendingSuggestion<T extends RevisionSuggestionCandidate>(
  revisions: readonly T[],
  options: {
    headRevisionId?: UUID | null
    baseRevisionId?: UUID | null
    dismissedRevisionId?: string | null
    minRevisionNo?: number | null
    readyStatuses?: readonly string[]
  } = {},
): T | null {
  const readyStatuses = new Set(options.readyStatuses ?? READY_SUGGESTION_STATUSES)
  const minRevisionNo = options.minRevisionNo
  const excluded = new Set(
    [options.headRevisionId, options.baseRevisionId, options.dismissedRevisionId].filter(
      (value): value is string => Boolean(value),
    ),
  )
  const sorted = [...revisions].sort((left, right) => right.revision_no - left.revision_no)
  for (const revision of sorted) {
    if (revision.status !== undefined && !readyStatuses.has(revision.status)) continue
    if (minRevisionNo != null && revision.revision_no <= minRevisionNo) continue
    if (excluded.has(revision.id)) continue
    return revision
  }
  return null
}

/* ------------------------------ 保存防抖 ------------------------------ */

export interface SaverTimers<THandle = unknown> {
  setTimeout(callback: () => void, delayMs: number): THandle
  clearTimeout(handle: THandle): void
}

export interface DebouncedSaver {
  schedule(save: () => void): void
  flush(): void
  cancel(): void
  readonly pending: boolean
}

const defaultTimers: SaverTimers<ReturnType<typeof setTimeout>> = {
  setTimeout: (callback, delayMs) => setTimeout(callback, delayMs),
  clearTimeout: (handle) => clearTimeout(handle),
}

/** 编辑侧保存防抖（默认 ~800ms），避免每次击键都写 localStorage。 */
export function createDebouncedSaver<THandle = ReturnType<typeof setTimeout>>(
  delayMs = 800,
  timers: SaverTimers<THandle> = defaultTimers as unknown as SaverTimers<THandle>,
): DebouncedSaver {
  let handle: THandle | null = null
  let queued: (() => void) | null = null

  const schedule = (save: () => void): void => {
    if (handle !== null) timers.clearTimeout(handle)
    queued = save
    handle = timers.setTimeout(() => {
      handle = null
      const saveFn = queued
      queued = null
      saveFn?.()
    }, delayMs)
  }

  const flush = (): void => {
    if (handle !== null) {
      timers.clearTimeout(handle)
      handle = null
    }
    const saveFn = queued
    queued = null
    saveFn?.()
  }

  const cancel = (): void => {
    if (handle !== null) timers.clearTimeout(handle)
    handle = null
    queued = null
  }

  return {
    schedule,
    flush,
    cancel,
    get pending() {
      return handle !== null
    },
  }
}
