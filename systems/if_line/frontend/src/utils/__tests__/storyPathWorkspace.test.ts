import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { test } from 'node:test'
import type { StoryPath } from '@/api/storyPathTypes'
import {
  buildStoryPathTree,
  shortId,
  titleForPathChapter,
  toRevisionOptions,
} from '../storyPathWorkspace'

function path(values: Partial<StoryPath> & Pick<StoryPath, 'id' | 'title'>): StoryPath {
  return {
    project_id: 7,
    parent_path_id: null,
    fork_path_chapter_id: null,
    fork_checkpoint_node_id: null,
    fork_candidate_id: null,
    base_state_snapshot_id: null,
    status: 'active',
    lock_version: 1,
    ...values,
  }
}

test('buildStoryPathTree keeps sibling branches under the exact parent UUID', () => {
  const roots = buildStoryPathTree([
    path({ id: 'branch-b', title: '路线 B', parent_path_id: 'root' }),
    path({ id: 'root', title: '主线' }),
    path({ id: 'branch-a', title: '路线 A', parent_path_id: 'root' }),
    path({ id: 'grandchild', title: '二级分支', parent_path_id: 'branch-a' }),
  ])

  assert.equal(roots.length, 1)
  assert.equal(roots[0].id, 'root')
  assert.deepEqual(roots[0].children.map((item) => item.id), ['branch-a', 'branch-b'])
  assert.equal(roots[0].children[0].children[0].id, 'grandchild')
})

test('buildStoryPathTree keeps orphaned and cyclic paths visible at the root', () => {
  const roots = buildStoryPathTree([
    path({ id: 'orphan', title: '孤立路径', parent_path_id: 'missing' }),
    path({ id: 'cycle-a', title: '循环 A', parent_path_id: 'cycle-b' }),
    path({ id: 'cycle-b', title: '循环 B', parent_path_id: 'cycle-a' }),
  ])

  assert.deepEqual(new Set(roots.map((item) => item.id)), new Set(['orphan', 'cycle-a', 'cycle-b']))
})

test('revision options use immutable revision metadata rather than display order', () => {
  const options = toRevisionOptions([
    { id: 'revision-new', revision_no: 4, content_hash: 'a'.repeat(64) },
    { id: 'revision-old', revision_no: 2, content_hash: 'b'.repeat(64) },
  ], '正文')

  assert.deepEqual(options, [
    { id: 'revision-new', label: '正文 r4', detail: 'a'.repeat(12) },
    { id: 'revision-old', label: '正文 r2', detail: 'b'.repeat(12) },
  ])
  assert.equal(shortId('1234567890'), '12345678')
})

test('chapter titles only join through the exact path chapter UUID', () => {
  const chapter = { id: 'branch-chapter', display_index: 1 }
  const samePositionOnAnotherPath = {
    story_path_chapter_id: 'root-chapter',
    display_index: 1,
    title: '主线标题',
    summary: '主线摘要',
  }

  assert.equal(titleForPathChapter(chapter, [samePositionOnAnotherPath]), '第 1 章')
  assert.equal(titleForPathChapter(chapter, [
    samePositionOnAnotherPath,
    { ...samePositionOnAnotherPath, story_path_chapter_id: chapter.id, title: '分支标题' },
  ]), '分支标题')
})

test('StoryPath workspace does not import index-based authoring clients', () => {
  const source = readFileSync(new URL('../../views/WorkflowView.vue', import.meta.url), 'utf8')

  assert.doesNotMatch(source, /\bchapter_index\b/)
  assert.doesNotMatch(source, /\bchapterIndex\b/)
  assert.doesNotMatch(source, /@\/api\/(?:chapterApi|revisionsApi|workflowApi|releaseApi)/)
})

test('legacy sequence-based authoring clients and routes are removed', () => {
  const removedFiles = [
    '../../api/assetApi.ts',
    '../../api/chapterApi.ts',
    '../../api/chapterScriptAssetApi.ts',
    '../../api/projectApi.ts',
    '../../api/releaseApi.ts',
    '../../api/revisionsApi.ts',
    '../../api/statsApi.ts',
    '../../api/visualAssetApi.ts',
    '../../api/voiceApi.ts',
    '../../api/workflowApi.ts',
    '../../views/StoryBibleView.vue',
    '../../views/OutlineReviewView.vue',
    '../../views/ChapterGenerateView.vue',
    '../../views/AssetPromptView.vue',
    '../../views/ImmersiveReaderView.vue',
    '../../views/VNGraphPreviewView.vue',
  ]

  for (const relativePath of removedFiles) {
    assert.equal(existsSync(new URL(relativePath, import.meta.url)), false, relativePath)
  }

  const routerSource = readFileSync(new URL('../../router/index.ts', import.meta.url), 'utf8')
  assert.doesNotMatch(routerSource, /\bchapterIndex\b/)
  assert.doesNotMatch(routerSource, /\/project\/:id\/(?:bible|outline|chapter|read|vn-graph)/)
})

test('publication UI uses projections and the atomic release client only', () => {
  const projectList = readFileSync(new URL('../../views/ProjectListView.vue', import.meta.url), 'utf8')
  const publicationPanel = readFileSync(new URL('../../components/PublicationPanel.vue', import.meta.url), 'utf8')
  const source = `${projectList}\n${publicationPanel}`

  assert.doesNotMatch(source, /@\/api\/(?:projectApi|releaseApi)/)
  assert.doesNotMatch(source, /\b(?:visibility|is_draft)\b/)
  assert.match(publicationPanel, /storyPathApi\.projects\.readiness/)
  assert.match(publicationPanel, /storyPathApi\.releases\.publish/)
  assert.match(publicationPanel, /storyPathApi\.releases\.unpublish/)
})
