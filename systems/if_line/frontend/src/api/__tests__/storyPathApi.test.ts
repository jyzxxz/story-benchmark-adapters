import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import type { AxiosRequestConfig } from 'axios'
import {
  createIdempotencyKey,
  createStoryPathApi,
  storyPathRoutes,
  type StoryPathHttpClient,
} from '../storyPathApi'

type HttpMethod = 'get' | 'post' | 'put' | 'patch' | 'delete'

interface RecordedCall {
  method: HttpMethod
  args: unknown[]
}

function recordingClient(): { client: StoryPathHttpClient; calls: RecordedCall[] } {
  const calls: RecordedCall[] = []
  const record = (method: HttpMethod) => (...args: unknown[]) => {
    calls.push({ method, args })
    return Promise.resolve({ data: null })
  }

  return {
    client: {
      get: record('get'),
      post: record('post'),
      put: record('put'),
      patch: record('patch'),
      delete: record('delete'),
    } as unknown as StoryPathHttpClient,
    calls,
  }
}

function configAt(call: RecordedCall, index: number): AxiosRequestConfig {
  return call.args[index] as AxiosRequestConfig
}

test('new authoring client exposes all 63 frozen operations', () => {
  const { client } = recordingClient()
  const authoringApi = createStoryPathApi(client)
  const expected = {
    projects: [
      'artifactMetrics', 'create', 'delete', 'get', 'list', 'metrics', 'readiness', 'update',
    ],
    bible: ['createRevision', 'generate', 'getHead', 'listRevisions', 'updateHead'],
    storyPaths: ['createFromCandidate', 'get', 'list', 'update'],
    outlines: ['createRevision', 'generate', 'getHead', 'listRevisions', 'updateHead'],
    chapters: [
      'append', 'createRevision', 'generate', 'generateBatch', 'getHead', 'getRevision',
      'list', 'listRevisions', 'updateHead',
    ],
    candidates: [
      'generate', 'getHead', 'listCandidates', 'listRevisions', 'promote', 'updateHead',
    ],
    voiceLines: ['generate', 'list'],
    scripts: ['createRevision', 'generate', 'getHead', 'listRevisions', 'updateHead'],
    resources: ['bind', 'list', 'render'],
    vnGraphs: ['compile', 'createRevision', 'getHead', 'getRevision', 'listRevisions', 'updateHead'],
    releases: ['finalizePublish', 'get', 'list', 'publish', 'unpublish'],
    public: [
      'getPathChapterManifest', 'getPathChapterVNGraph', 'getProject',
      'getReleaseManifest', 'listProjects',
    ],
  }

  const actual = Object.fromEntries(
    Object.entries(authoringApi).map(([group, methods]) => [group, Object.keys(methods).sort()]),
  )
  assert.deepEqual(actual, expected)
  assert.equal(Object.values(actual).reduce((total, methods) => total + methods.length, 0), 63)
})

test('UUID route builders encode stable identities and never depend on display order', () => {
  assert.equal(
    storyPathRoutes.pathChapterGenerations('chapter/with space'),
    '/path-chapters/chapter%2Fwith%20space/generations',
  )
  assert.equal(
    storyPathRoutes.candidateSetGenerations('path/id', 'node/id'),
    '/story-paths/path%2Fid/checkpoints/node%2Fid/candidate-set-generations',
  )
  assert.equal(
    storyPathRoutes.scriptGenerations('revision/id'),
    '/chapter-revisions/revision%2Fid/script-generations',
  )
  assert.equal(
    storyPathRoutes.resourceSlot('script/id', 'slot/id'),
    '/chapter-script-revisions/script%2Fid/resource-slots/slot%2Fid',
  )
  assert.equal(
    storyPathRoutes.publicPathChapterVNGraph('release/id', 'chapter/id'),
    '/public/releases/release%2Fid/path-chapters/chapter%2Fid/vn-graph',
  )
})

test('generation and promotion commands send a normalized idempotency key', () => {
  const { client, calls } = recordingClient()
  const authoringApi = createStoryPathApi(client)

  authoringApi.chapters.generate('chapter-revision-id', {}, '  chapter:request-1  ')
  authoringApi.candidates.promote('candidate-id', {}, 'branch:request-2')

  assert.equal(calls[0].method, 'post')
  assert.deepEqual(configAt(calls[0], 2).headers, {
    'Idempotency-Key': 'chapter:request-1',
  })
  assert.deepEqual(configAt(calls[1], 2).headers, {
    'Idempotency-Key': 'branch:request-2',
  })
})

test('head, resource binding, and unpublish writes send If-Match', () => {
  const { client, calls } = recordingClient()
  const authoringApi = createStoryPathApi(client)

  authoringApi.bible.updateHead(9, 'bible-revision-id', 4)
  authoringApi.resources.bind('script-id', 'slot-id', 'asset-version-id', 6)
  authoringApi.releases.unpublish(9, 8)

  assert.deepEqual(configAt(calls[0], 2).headers, { 'If-Match': '4' })
  assert.deepEqual(configAt(calls[1], 2).headers, { 'If-Match': '6' })
  assert.deepEqual(configAt(calls[2], 2).headers, { 'If-Match': '8' })
})

test('invalid command and lock preconditions fail before issuing a request', () => {
  const { client, calls } = recordingClient()
  const authoringApi = createStoryPathApi(client)

  assert.throws(
    () => authoringApi.bible.generate(1, {}, '  '),
    /Idempotency-Key must contain 1 to 255 characters/,
  )
  assert.throws(
    () => authoringApi.outlines.updateHead('path-id', 'revision-id', 0),
    /lockVersion must be a positive integer/,
  )
  assert.throws(
    () => authoringApi.resources.bind('script-id', 'slot-id', 'asset-id', 1.5),
    /lockVersion must be a positive integer/,
  )
  assert.equal(calls.length, 0)
})

test('generated idempotency keys retain a unique suffix when scope is long', () => {
  const key = createIdempotencyKey('chapter-generation-'.repeat(30))

  assert.ok(key.length <= 255)
  assert.match(key, /:[a-z0-9-]{15,}$/i)
})

test('public VNGraph reads support conditional ETag requests and 304 responses', () => {
  const { client, calls } = recordingClient()
  const authoringApi = createStoryPathApi(client)

  authoringApi.public.getPathChapterVNGraph('release-id', 'path-chapter-id', '"graph-hash"')

  const config = configAt(calls[0], 1)
  assert.deepEqual(config.headers, { 'If-None-Match': '"graph-hash"' })
  assert.equal(config.validateStatus?.(200), true)
  assert.equal(config.validateStatus?.(304), true)
  assert.equal(config.validateStatus?.(404), false)
})

test('new client source has no sequence-based chapter identity', () => {
  const sourceUrls = [
    new URL('../storyPathApi.ts', import.meta.url),
    new URL('../storyPathTypes.ts', import.meta.url),
  ]

  for (const sourceUrl of sourceUrls) {
    const source = readFileSync(sourceUrl, 'utf8')
    assert.doesNotMatch(source, /\bchapter_index\b/)
    assert.doesNotMatch(source, /\bchapterIndex\b/)
  }
})

test('draft materialization and finalize-publish hit the new endpoints', () => {
  const { client, calls } = recordingClient()
  const authoringApi = createStoryPathApi(client)

  authoringApi.scripts.createRevision('chapter-revision-id', {
    script_json: { paragraphs: [] },
  })
  authoringApi.vnGraphs.createRevision('script-revision-id', { graph_json: { Nodes: [] } })
  authoringApi.releases.finalizePublish(
    7,
    {
      bible_revision_id: 'bible-revision-id',
      chapter_revision_ids: { 'chapter-1': 'chapter-revision-id' },
    },
    'finalize:key-1',
  )
  authoringApi.chapters.generate(
    'chapter-1',
    {
      bible_revision_id: 'bible-revision-id',
      outline_revision_id: 'outline-revision-id',
      ancestor_revision_overrides: { 'chapter-0': 'previous-draft-revision' },
    },
    'chapter:anchored-1',
  )
  authoringApi.outlines.generate(
    'story-path-1',
    { chapter_count: 3, bible_revision_id: 'bible-revision-id' },
    'outline:anchored-1',
  )

  assert.deepEqual(calls[0].args.slice(0, 2), [
    '/chapter-revisions/chapter-revision-id/script-revisions',
    { script_json: { paragraphs: [] } },
  ])
  assert.deepEqual(calls[1].args.slice(0, 2), [
    '/chapter-script-revisions/script-revision-id/vn-graph-revisions',
    { graph_json: { Nodes: [] } },
  ])
  assert.equal(calls[2].args[0], '/projects/7/finalize-publish')
  assert.deepEqual(configAt(calls[2], 2).headers, { 'Idempotency-Key': 'finalize:key-1' })
  assert.deepEqual(calls[3].args[1], {
    bible_revision_id: 'bible-revision-id',
    outline_revision_id: 'outline-revision-id',
    ancestor_revision_overrides: { 'chapter-0': 'previous-draft-revision' },
  })
  assert.deepEqual(calls[4].args[1], {
    chapter_count: 3,
    bible_revision_id: 'bible-revision-id',
  })
})
