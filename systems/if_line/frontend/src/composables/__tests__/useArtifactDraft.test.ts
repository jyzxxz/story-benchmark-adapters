import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  createArtifactDraftStore,
  createDebouncedSaver,
  createMemoryStorage,
  dismissalStorageKey,
  draftFingerprint,
  draftStorageKey,
  findPendingSuggestion,
  isDraftStale,
  buildUpstreamHashes,
  type StorageLike,
} from '../useArtifactDraft'

function fixedClock(iso = '2026-08-27T08:00:00.000Z') {
  return () => new Date(iso)
}

test('draft writes land under a kind+subject namespaced localStorage key and round-trip', () => {
  const storage = createMemoryStorage()
  const store = createArtifactDraftStore({ storage, now: fixedClock() })

  const result = store.writeDraft('bible', 9, {
    baseRevisionId: 'bible-revision-1',
    payload: { content_json: { characters: [{ name: '林晚' }] } },
    upstreamHashes: {},
  })

  assert.deepEqual(result, { persisted: true, fallbackToMemory: false })
  assert.equal(draftStorageKey('bible', 9), 'if-line:draft:v1:bible:9')
  assert.ok(storage.getItem('if-line:draft:v1:bible:9'))

  const draft = store.readDraft<{ content_json: { characters: { name: string }[] } }>('bible', 9)
  assert.ok(draft)
  assert.equal(draft.baseRevisionId, 'bible-revision-1')
  assert.equal(draft.payload.content_json.characters[0].name, '林晚')
  assert.equal(draft.updatedAt, '2026-08-27T08:00:00.000Z')
})

test('drafts are isolated per kind and per subject id', () => {
  const storage = createMemoryStorage()
  const store = createArtifactDraftStore({ storage, now: fixedClock() })

  store.writeDraft('chapter', 'path-chapter-a', { baseRevisionId: null, payload: '正文 A' })
  store.writeDraft('chapter', 'path-chapter-b', { baseRevisionId: null, payload: '正文 B' })
  store.writeDraft('script', 'path-chapter-a', { baseRevisionId: null, payload: { paragraphs: [] } })

  assert.equal(store.readDraft<string>('chapter', 'path-chapter-a')?.payload, '正文 A')
  assert.equal(store.readDraft<string>('chapter', 'path-chapter-b')?.payload, '正文 B')
  assert.ok(store.readDraft('script', 'path-chapter-a'))
  assert.equal(store.readDraft('outline', 'path-chapter-a'), null)
})

test('editing a draft keeps baseRevisionId and upstreamHashes frozen unless explicitly changed', () => {
  const storage = createMemoryStorage()
  const store = createArtifactDraftStore({ storage, now: fixedClock() })

  store.writeDraft('chapter', 'path-chapter-a', {
    baseRevisionId: 'chapter-revision-1',
    payload: '第一稿',
    upstreamHashes: { bible: 'bible-hash-1', outline: 'outline-hash-1' },
  })
  // 编辑保存：只给 payload，基线与上游 hash 不被刷新。
  store.writeDraft('chapter', 'path-chapter-a', { payload: '第二稿' })

  const draft = store.readDraft<string>('chapter', 'path-chapter-a')
  assert.ok(draft)
  assert.equal(draft.payload, '第二稿')
  assert.equal(draft.baseRevisionId, 'chapter-revision-1')
  assert.deepEqual(draft.upstreamHashes, { bible: 'bible-hash-1', outline: 'outline-hash-1' })
})

test('clearDraft removes persisted drafts, corrupted entries read as null and are swept', () => {
  const storage = createMemoryStorage()
  const store = createArtifactDraftStore({ storage, now: fixedClock() })

  store.writeDraft('outline', 'story-path-1', { baseRevisionId: null, payload: { chapters: [] } })
  store.clearDraft('outline', 'story-path-1')
  assert.equal(store.readDraft('outline', 'story-path-1'), null)
  assert.equal(storage.getItem(draftStorageKey('outline', 'story-path-1')), null)

  storage.setItem(draftStorageKey('graph', 'path-chapter-c'), '{not-json')
  assert.equal(store.readDraft('graph', 'path-chapter-c'), null)
  assert.equal(storage.getItem(draftStorageKey('graph', 'path-chapter-c')), null)
})

test('quota failures degrade to an in-memory fallback flagged for UI warning', () => {
  const entries = new Map<string, string>()
  const failingStorage: StorageLike = {
    getItem: (key) => (entries.has(key) ? (entries.get(key) as string) : null),
    setItem: () => {
      throw new Error('QuotaExceededError')
    },
    removeItem: (key) => {
      entries.delete(key)
    },
  }
  const store = createArtifactDraftStore({ storage: failingStorage, now: fixedClock() })

  const result = store.writeDraft('script', 'path-chapter-a', {
    baseRevisionId: 'script-revision-1',
    payload: { paragraphs: [1] },
  })

  assert.deepEqual(result, { persisted: false, fallbackToMemory: true })
  assert.equal(store.usingMemoryFallback, true)
  // 会话内仍可读回草稿。
  assert.equal(store.readDraft<{ paragraphs: number[] }>('script', 'path-chapter-a')?.payload.paragraphs[0], 1)

  store.clearDraft('script', 'path-chapter-a')
  assert.equal(store.readDraft('script', 'path-chapter-a'), null)
})

test('suggestion dismissals persist under a project-scoped key', () => {
  const storage = createMemoryStorage()
  const store = createArtifactDraftStore({ storage, now: fixedClock() })

  assert.equal(
    dismissalStorageKey(9, 'bible', 9),
    'if-line:draft-dismissed:9:bible:9',
  )

  store.dismissSuggestion(9, 'bible', 9, 'suggested-revision-7')
  assert.equal(store.dismissedSuggestion(9, 'bible', 9), 'suggested-revision-7')
  assert.equal(storage.getItem('if-line:draft-dismissed:9:bible:9'), 'suggested-revision-7')

  store.clearDismissal(9, 'bible', 9)
  assert.equal(store.dismissedSuggestion(9, 'bible', 9), null)
})

test('stale detection compares recorded upstream hashes against the current effective hashes', () => {
  const draft = { upstreamHashes: { bible: 'bible-hash-1', outline: 'outline-hash-1' } }

  assert.equal(
    isDraftStale(draft, { bible: 'bible-hash-1', outline: 'outline-hash-1' }),
    false,
  )
  // 上游 bible 草稿/固化内容变了 → stale。
  assert.equal(isDraftStale(draft, { bible: 'bible-hash-2', outline: 'outline-hash-1' }), true)
  // 上游 hash 取不到（head 缺失）同样视为 stale。
  assert.equal(isDraftStale(draft, { bible: null, outline: 'outline-hash-1' }), true)
  assert.equal(isDraftStale(draft, { outline: 'outline-hash-1' }), true)

  assert.deepEqual(
    buildUpstreamHashes({ bible: 'b1', outline: null, chapter: undefined, script: 's1' }),
    { bible: 'b1', script: 's1' },
  )
})

test('suggestion filter picks the newest ready revision outside head/base/dismissed', () => {
  const revisions = [
    { id: 'revision-1', revision_no: 1, status: 'ready' },
    { id: 'revision-2', revision_no: 2, status: 'ready' },
    { id: 'revision-3', revision_no: 3, status: 'queued' },
    { id: 'revision-4', revision_no: 4, status: 'ready' },
  ]

  // revision-4 是 head、revision-2 是草稿基线、revision-3 未就绪 → 剩 revision-1。
  assert.equal(
    findPendingSuggestion(revisions, {
      headRevisionId: 'revision-4',
      baseRevisionId: 'revision-2',
      dismissedRevisionId: null,
    })?.id,
    'revision-1',
  )

  // 被丢弃过的建议不再出现。
  assert.equal(
    findPendingSuggestion(revisions, {
      headRevisionId: 'revision-4',
      baseRevisionId: 'revision-2',
      dismissedRevisionId: 'revision-1',
    }),
    null,
  )

  // 没有排除项时取最新 ready（跳过 queued）。
  assert.equal(findPendingSuggestion(revisions, {})?.id, 'revision-4')

  // 全部被排除/未就绪 → 无建议。
  assert.equal(
    findPendingSuggestion([{ id: 'only', revision_no: 1, status: 'running' }], {}),
    null,
  )
})

test('minRevisionNo gate: revisions older than head are not suggestions even when not excluded', () => {
  // 回写把 head 顶到 r3 后，旧 head r2 不该再以"新结果"出现。
  const revisions = [
    { id: 'revision-1', revision_no: 1, status: 'ready' },
    { id: 'revision-2', revision_no: 2, status: 'ready' },
    { id: 'revision-3', revision_no: 3, status: 'ready' },
  ]
  assert.equal(
    findPendingSuggestion(revisions, { headRevisionId: 'revision-3', minRevisionNo: 3 }),
    null,
  )
  // 比 head 新的 ready 修订仍然入选。
  const newer = [...revisions, { id: 'revision-4', revision_no: 4, status: 'ready' }]
  assert.equal(
    findPendingSuggestion(newer, { headRevisionId: 'revision-3', minRevisionNo: 3 })?.id,
    'revision-4',
  )
  // 不传 minRevisionNo 时维持原行为（旧修订仍可作为建议）。
  assert.equal(
    findPendingSuggestion(revisions, { headRevisionId: 'revision-3' })?.id,
    'revision-2',
  )
})

test('draftFingerprint is key-order stable and value-sensitive', () => {
  assert.equal(
    draftFingerprint({ b: 1, a: { d: [1, 2], c: 'x' } }),
    draftFingerprint({ a: { c: 'x', d: [1, 2] }, b: 1 }),
  )
  assert.notEqual(draftFingerprint({ a: 1 }), draftFingerprint({ a: 2 }))
  assert.notEqual(draftFingerprint([1, 2]), draftFingerprint([2, 1]))
  assert.equal(draftFingerprint('正文草稿'), '"正文草稿"')
  assert.equal(draftFingerprint(null), 'null')
})

test('debounced saver collapses rapid schedules and supports flush/cancel', () => {
  interface FakeTimer {
    id: number
    callback: () => void
    delay: number
  }
  const timers: FakeTimer[] = []
  let nextId = 1
  const fakeTimers = {
    setTimeout(callback: () => void, delay: number) {
      const id = nextId++
      timers.push({ id, callback, delay })
      return id
    },
    clearTimeout(handle: number) {
      const index = timers.findIndex((timer) => timer.id === handle)
      if (index >= 0) timers.splice(index, 1)
    },
  }

  const saver = createDebouncedSaver(800, fakeTimers)
  const saved: string[] = []

  saver.schedule(() => saved.push('first'))
  saver.schedule(() => saved.push('second'))
  assert.equal(saver.pending, true)
  assert.equal(timers.length, 1)
  assert.equal(timers[0].delay, 800)

  timers.splice(0).forEach((timer) => timer.callback())
  assert.deepEqual(saved, ['second'])
  assert.equal(saver.pending, false)

  saver.schedule(() => saved.push('cancelled'))
  saver.cancel()
  assert.equal(saver.pending, false)
  assert.equal(timers.length, 0)
  timers.splice(0).forEach((timer) => timer.callback())
  assert.deepEqual(saved, ['second'])

  saver.schedule(() => saved.push('flushed'))
  saver.flush()
  assert.deepEqual(saved, ['second', 'flushed'])
  assert.equal(saver.pending, false)
})
