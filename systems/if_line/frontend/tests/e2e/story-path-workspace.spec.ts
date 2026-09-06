import { expect, test, type Page, type Route } from '@playwright/test'
import type {
  ProjectRead,
  PublicationState,
  Release,
} from '../../src/api/storyPathTypes'

const user = {
  id: 1,
  email: 'author@example.com',
  display_name: '作者',
  is_active: true,
}

const project: ProjectRead = {
  id: 7,
  root_story_path_id: 'root-path',
  title: '雾港来信',
  characters: [],
  story_start: '',
  story_end: '',
  style: '',
  source_work: null,
  pace: null,
  extra_requirements: null,
  authoring: {
    stage: 'chapters',
    blocking_items: [],
    running_task_count: 0,
    has_unpublished_changes: true,
  },
  publication: {
    state: 'unpublished',
    active_release_id: null,
    published_at: null,
    lock_version: 1,
  },
}

interface WorkspaceObservations {
  outlineChapterCounts: number[]
  outlineIfMatches: string[]
  outlineHeadBodies: unknown[]
  promotedCandidateIds: string[]
  promotionBodies: unknown[]
  projectBodies?: unknown[]
  publishBodies?: unknown[]
  publishKeys?: string[]
  unpublishIfMatches?: string[]
  publicManifestReads?: string[]
}

interface WorkspaceMockOptions {
  publicationState?: PublicationState
}

const paths = [
  {
    id: 'root-path',
    project_id: 7,
    parent_path_id: null,
    fork_path_chapter_id: null,
    fork_checkpoint_node_id: null,
    fork_candidate_id: null,
    base_state_snapshot_id: null,
    title: '港口主线',
    status: 'active',
    lock_version: 3,
  },
  {
    id: 'branch-path',
    project_id: 7,
    parent_path_id: 'root-path',
    fork_path_chapter_id: 'root-chapter-1',
    fork_checkpoint_node_id: 'checkpoint-1',
    fork_candidate_id: 'candidate-1',
    base_state_snapshot_id: 'state-branch',
    title: '追上渡轮',
    status: 'active',
    lock_version: 2,
  },
]

const bibleRevisions = [
  {
    id: 'bible-r1',
    project_id: 7,
    parent_revision_id: null,
    revision_no: 1,
    source_hash: '1'.repeat(64),
    content_hash: '2'.repeat(64),
    content_json: { premise: '雾港中的失踪来信' },
    created_at: '2026-08-09T00:00:00Z',
  },
]

const outlineChapter = (pathChapterId: string, title: string) => ({
  story_path_chapter_id: pathChapterId,
  display_index: 1,
  title,
  summary: `${title}的章节摘要`,
  characters: [],
  visual_keywords: [],
})

const outlineRevision = (
  id: string,
  revisionNo: number,
  storyPathId: string,
  pathChapterId: string,
  title: string,
) => ({
  id,
  story_path_id: storyPathId,
  bible_revision_id: 'bible-r1',
  parent_revision_id: revisionNo === 1 ? null : `${storyPathId}-outline-r1`,
  revision_no: revisionNo,
  source_hash: `${revisionNo}`.repeat(64),
  content_hash: `${revisionNo + 2}`.repeat(64),
  chapters: [outlineChapter(pathChapterId, title)],
})

const rootChapter = {
  id: 'root-chapter-1',
  story_path_id: 'root-path',
  chapter_slot_id: 'root-slot-1',
  display_index: 1,
  predecessor_path_chapter_id: null,
  inherited_from_path_chapter_id: null,
  current_revision_id: 'root-content-r1',
  lock_version: 2,
}

const branchChapter = {
  id: 'branch-chapter-1',
  story_path_id: 'branch-path',
  chapter_slot_id: 'branch-slot-1',
  display_index: 1,
  predecessor_path_chapter_id: null,
  inherited_from_path_chapter_id: 'root-chapter-1',
  current_revision_id: 'branch-content-r1',
  lock_version: 4,
}

const chapterRevision = (id: string, slotId: string, pathId: string, content: string) => ({
  id,
  chapter_slot_id: slotId,
  created_for_story_path_id: pathId,
  parent_revision_id: null,
  revision_no: 1,
  context_manifest: { story_path_id: pathId },
  context_hash: 'a'.repeat(64),
  content_hash: 'b'.repeat(64),
  content,
})

async function json(route: Route, body: unknown, status = 200): Promise<void> {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

async function installWorkspaceMocks(
  page: Page,
  observations: WorkspaceObservations,
  options: WorkspaceMockOptions = {},
): Promise<void> {
  let branchOutlineGenerated = false
  let branchOutlineHead = { revision_id: 'branch-path-outline-r1', lock_version: 3, updated_at: '2026-08-09T00:00:00Z' }
  const promotedPaths: typeof paths = []
  const initialPublicationState = options.publicationState ?? 'unpublished'
  const initialRelease: Release = {
    id: 'release-v1',
    project_id: 7,
    version: 1,
    status: 'published',
    manifest_hash: '1'.repeat(64),
    authoring_fingerprint: '2'.repeat(64),
    release_notes: '第一版',
    published_at: '2026-08-08T08:00:00Z',
    withdrawn_at: null,
  }
  let currentProject: ProjectRead = {
    ...project,
    authoring: {
      ...project.authoring,
      has_unpublished_changes: initialPublicationState !== 'published',
    },
    publication: {
      state: initialPublicationState,
      active_release_id: initialPublicationState === 'unpublished' ? null : initialRelease.id,
      published_at: initialPublicationState === 'unpublished' ? null : initialRelease.published_at,
      lock_version: initialPublicationState === 'unpublished' ? 1 : 3,
    },
  }
  let releaseHistory: Release[] = initialPublicationState === 'unpublished' ? [] : [initialRelease]
  const readinessFingerprint = 'f'.repeat(64)

  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    const method = request.method()

    if (!path.startsWith('/api/')) return route.continue()

    if (path === '/api/auth/me') return json(route, user)
    if (path === '/api/projects' && method === 'GET') return json(route, [currentProject])
    if (path === '/api/projects' && method === 'POST') {
      observations.projectBodies?.push(request.postDataJSON())
      return json(route, project, 201)
    }
    if (path === '/api/projects/7/publication-readiness' && method === 'GET') {
      return json(route, {
        ready: true,
        authoring_fingerprint: readinessFingerprint,
        blocking_items: [],
      })
    }
    if (path === '/api/projects/7/releases' && method === 'GET') {
      return json(route, releaseHistory)
    }
    if (path === '/api/projects/7/publish' && method === 'POST') {
      observations.publishBodies?.push(request.postDataJSON())
      observations.publishKeys?.push(request.headers()['idempotency-key'] || '')
      releaseHistory = releaseHistory.map((release) => (
        release.status === 'published' ? { ...release, status: 'superseded' } : release
      ))
      const release: Release = {
        id: 'release-v2',
        project_id: 7,
        version: 2,
        status: 'published',
        manifest_hash: '3'.repeat(64),
        authoring_fingerprint: readinessFingerprint,
        release_notes: '渡轮线完成',
        published_at: '2026-08-09T09:00:00Z',
        withdrawn_at: null,
      }
      releaseHistory = [release, ...releaseHistory]
      currentProject = {
        ...currentProject,
        authoring: { ...currentProject.authoring, has_unpublished_changes: false },
        publication: {
          state: 'published',
          active_release_id: release.id,
          published_at: release.published_at,
          lock_version: currentProject.publication.lock_version + 1,
        },
      }
      return json(route, release, 201)
    }
    if (path === '/api/projects/7/unpublish' && method === 'POST') {
      observations.unpublishIfMatches?.push(request.headers()['if-match'] || '')
      const activeReleaseId = currentProject.publication.active_release_id
      let withdrawn: Release | undefined
      releaseHistory = releaseHistory.map((release) => {
        if (release.id !== activeReleaseId) return release
        withdrawn = { ...release, status: 'withdrawn', withdrawn_at: '2026-08-09T10:00:00Z' }
        return withdrawn
      })
      currentProject = {
        ...currentProject,
        authoring: { ...currentProject.authoring, has_unpublished_changes: true },
        publication: {
          state: 'unpublished',
          active_release_id: null,
          published_at: null,
          lock_version: currentProject.publication.lock_version + 1,
        },
      }
      return json(route, withdrawn)
    }
    if (path === '/api/projects/7' && method === 'GET') return json(route, currentProject)
    if (path === '/api/projects/7/metrics' && method === 'GET') {
      return json(route, {
        project_id: 7,
        story_paths_by_id: {
          'root-path': {
            status: 'active',
            parent_path_id: null,
            path_chapter_ids: ['root-chapter-1'],
          },
          'branch-path': {
            status: 'active',
            parent_path_id: 'root-path',
            path_chapter_ids: ['branch-chapter-1'],
          },
        },
        selected_path_chapter_count: 2,
        revision_counts: { bible: 1, outline: 2, chapter: 2, script: 1, vn_graph: 1 },
        task_counts: {
          total: 2,
          by_status: { running: 1, succeeded: 1 },
          by_kind: { chapter_generation: 1, vn_graph_compilation: 1 },
        },
      })
    }
    if (path === '/api/projects/7/metrics/artifacts' && method === 'GET') {
      return json(route, {
        project_id: 7,
        generation_tasks_by_id: {
          'task-chapter': {
            kind: 'chapter_generation',
            status: 'succeeded',
            path_chapter_id: 'branch-chapter-1',
            duration_seconds: 72.4,
          },
          'task-graph': {
            kind: 'vn_graph_compilation',
            status: 'running',
            path_chapter_id: 'branch-chapter-1',
            duration_seconds: null,
          },
        },
        asset_versions_by_id: {
          'asset-version-1': {
            source_kind: 'chapter_script_revision',
            source_revision_id: 'script-revision-1',
            generation_task_id: 'task-asset-1',
            storage_object_id: 'storage-object-1',
            quality_score: 0.94,
            safety_status: 'approved',
          },
        },
      })
    }
    if (path === '/api/public/projects' && method === 'GET') {
      return json(route, [{
        id: 7,
        title: '雾港来信',
        summary: '一封来信改变了雾港的航线。',
        cover_url: null,
        release_id: 'public-release-v3',
        release_version: 3,
        manifest_hash: '9'.repeat(64),
        published_at: '2026-08-09T08:00:00Z',
      }])
    }
    if (path === '/api/public/releases/public-release-v3/manifest' && method === 'GET') {
      observations.publicManifestReads?.push('public-release-v3')
      return json(route, {
        release_id: 'public-release-v3',
        version: 3,
        manifest_hash: '9'.repeat(64),
        manifest: { schema_version: 'story-path-release-v1', root_story_path_id: 'root-path' },
      })
    }
    if (path === '/api/projects/7/story-paths' && method === 'GET') {
      return json(route, [...paths, ...promotedPaths])
    }
    if (path === '/api/projects/7/bible-revisions') return json(route, bibleRevisions)
    if (path === '/api/projects/7/bible-head') {
      return json(route, { revision_id: 'bible-r1', lock_version: 2, updated_at: '2026-08-09T00:00:00Z' })
    }

    if (path === '/api/story-paths/root-path/outline-revisions') {
      return json(route, [outlineRevision('root-path-outline-r1', 1, 'root-path', rootChapter.id, '收到来信')])
    }
    if (path === '/api/story-paths/root-path/outline-head') {
      return json(route, { revision_id: 'root-path-outline-r1', lock_version: 2, updated_at: '2026-08-09T00:00:00Z' })
    }
    if (path === '/api/story-paths/root-path/chapters') return json(route, [rootChapter])

    if (path === '/api/story-paths/branch-path/outline-generations' && method === 'POST') {
      const body = request.postDataJSON() as { chapter_count: number }
      observations.outlineChapterCounts.push(body.chapter_count)
      branchOutlineGenerated = true
      return json(route, {
        task_id: 'outline-task-120',
        status: 'queued',
        events_url: '/api/tasks/outline-task-120/events',
        created: true,
      }, 202)
    }
    if (path === '/api/story-paths/branch-path/outline-revisions') {
      const current = outlineRevision('branch-path-outline-r1', 1, 'branch-path', branchChapter.id, '登上渡轮')
      const generated = outlineRevision('branch-path-outline-r2', 2, 'branch-path', branchChapter.id, '渡轮追踪')
      return json(route, branchOutlineGenerated ? [generated, current] : [current])
    }
    if (path === '/api/story-paths/branch-path/outline-head' && method === 'PUT') {
      observations.outlineIfMatches.push(request.headers()['if-match'] || '')
      observations.outlineHeadBodies.push(request.postDataJSON())
      branchOutlineHead = {
        revision_id: 'branch-path-outline-r2',
        lock_version: 4,
        updated_at: '2026-08-09T00:01:00Z',
      }
      return json(route, branchOutlineHead)
    }
    if (path === '/api/story-paths/branch-path/outline-head') return json(route, branchOutlineHead)
    if (path === '/api/story-paths/branch-path/chapters') return json(route, [branchChapter])

    if (path === '/api/path-chapters/root-chapter-1/revisions') {
      return json(route, [chapterRevision('root-content-r1', 'root-slot-1', 'root-path', '主线正文')])
    }
    if (path === '/api/path-chapters/root-chapter-1/head') {
      return json(route, { revision_id: 'root-content-r1', lock_version: 2, updated_at: '2026-08-09T00:00:00Z' })
    }
    if (path === '/api/path-chapters/branch-chapter-1/revisions') {
      return json(route, [chapterRevision('branch-content-r1', 'branch-slot-1', 'branch-path', '分支正文')])
    }
    if (path === '/api/path-chapters/branch-chapter-1/head') {
      return json(route, { revision_id: 'branch-content-r1', lock_version: 4, updated_at: '2026-08-09T00:00:00Z' })
    }

    if (path === '/api/chapter-revisions/root-content-r1/script-revisions') return json(route, [])
    if (path === '/api/chapter-revisions/root-content-r1/script-head') {
      return json(route, { revision_id: null, lock_version: 1, updated_at: '2026-08-09T00:00:00Z' })
    }
    if (path === '/api/chapter-revisions/branch-content-r1/script-revisions') return json(route, [])
    if (path === '/api/chapter-revisions/branch-content-r1/script-head') {
      return json(route, { revision_id: null, lock_version: 1, updated_at: '2026-08-09T00:00:00Z' })
    }

    if (path === '/api/story-paths/root-path/checkpoints/checkpoint-1/candidate-set-revisions') {
      return json(route, [{
        id: 'root-candidate-set-r1',
        story_path_id: 'root-path',
        checkpoint_node_id: 'checkpoint-1',
        chapter_revision_id: 'root-content-r1',
        state_snapshot_id: 'state-root',
        revision_no: 1,
        source_hash: 'e'.repeat(64),
        content_hash: 'f'.repeat(64),
      }])
    }
    if (path === '/api/story-paths/root-path/checkpoints/checkpoint-1/candidate-set-head') {
      return json(route, { revision_id: 'root-candidate-set-r1', lock_version: 2, updated_at: '2026-08-09T00:00:00Z' })
    }
    if (path === '/api/candidate-set-revisions/root-candidate-set-r1/candidates') {
      return json(route, [
        {
          id: 'root-candidate-a',
          candidate_set_revision_id: 'root-candidate-set-r1',
          option_key: 'A',
          preview_text: '从货舱潜入驾驶室',
          state_delta: { route: 'cargo' },
        },
        {
          id: 'root-candidate-b',
          candidate_set_revision_id: 'root-candidate-set-r1',
          option_key: 'B',
          preview_text: '在甲板公开交涉',
          state_delta: { route: 'deck' },
        },
      ])
    }

    if (path === '/api/story-paths/branch-path/checkpoints/checkpoint-1/candidate-set-revisions') {
      return json(route, [{
        id: 'candidate-set-r1',
        story_path_id: 'branch-path',
        checkpoint_node_id: 'checkpoint-1',
        chapter_revision_id: 'branch-content-r1',
        state_snapshot_id: 'state-branch',
        revision_no: 1,
        source_hash: 'c'.repeat(64),
        content_hash: 'd'.repeat(64),
      }])
    }
    if (path === '/api/story-paths/branch-path/checkpoints/checkpoint-1/candidate-set-head') {
      return json(route, { revision_id: 'candidate-set-r1', lock_version: 2, updated_at: '2026-08-09T00:00:00Z' })
    }
    if (path === '/api/candidate-set-revisions/candidate-set-r1/candidates') {
      return json(route, [
        {
          id: 'candidate-a',
          candidate_set_revision_id: 'candidate-set-r1',
          option_key: 'A',
          preview_text: '从货舱潜入驾驶室',
          state_delta: { route: 'cargo' },
        },
        {
          id: 'candidate-b',
          candidate_set_revision_id: 'candidate-set-r1',
          option_key: 'B',
          preview_text: '在甲板公开交涉',
          state_delta: { route: 'deck' },
        },
      ])
    }
    if (/^\/api\/branch-candidates\/candidate-[ab]\/story-paths$/.test(path) && method === 'POST') {
      const candidateId = path.split('/').at(-2) || ''
      observations.promotedCandidateIds.push(candidateId)
      observations.promotionBodies.push(request.postDataJSON())
      const child = {
        ...paths[1],
        id: `child-${candidateId}`,
        parent_path_id: 'branch-path',
        fork_candidate_id: candidateId,
        title: candidateId === 'candidate-a' ? '货舱潜入' : '甲板交涉',
        lock_version: 1,
      }
      promotedPaths.push(child)
      return json(route, child, 201)
    }
    if (/^\/api\/story-paths\/child-candidate-[ab]\/outline-revisions$/.test(path)) return json(route, [])
    if (/^\/api\/story-paths\/child-candidate-[ab]\/outline-head$/.test(path)) {
      return json(route, { revision_id: null, lock_version: 1, updated_at: '2026-08-09T00:00:00Z' })
    }
    if (/^\/api\/story-paths\/child-candidate-[ab]\/chapters$/.test(path)) return json(route, [])

    if (path === '/api/tasks/outline-task-120') {
      return json(route, {
        id: 'outline-task-120',
        status: 'succeeded',
        stage: 'completed',
        progress: 100,
        error_code: null,
        error_detail: null,
        result_refs: { outline_revision_id: 'branch-path-outline-r2' },
      })
    }

    return json(route, { detail: `unmocked ${method} ${path}` }, 404)
  })
}

test('keeps sibling chapter identity in UUID route state', async ({ page }) => {
  const observations = {
    outlineChapterCounts: [], outlineIfMatches: [], outlineHeadBodies: [],
    promotedCandidateIds: [], promotionBodies: [],
  }
  await installWorkspaceMocks(page, observations)
  await page.goto('/project/7?path=branch-path&chapter=branch-chapter-1')

  await expect(page.getByRole('heading', { name: '雾港来信' })).toBeVisible()
  await expect(page.getByText('港口主线', { exact: true })).toBeVisible()
  await expect(page.getByText('追上渡轮', { exact: true })).toBeVisible()

  await page.getByRole('tab', { name: '路径章节' }).click()
  await expect(page.getByRole('heading', { name: '登上渡轮' })).toBeVisible()
  await expect(page).toHaveURL(/path=branch-path/)
  await expect(page).toHaveURL(/chapter=branch-chapter-1/)

  await page.getByText('港口主线', { exact: true }).click()
  await expect(page.getByRole('heading', { name: '收到来信' })).toBeVisible()
  await expect(page).toHaveURL(/path=root-path/)
  await expect(page).toHaveURL(/chapter=root-chapter-1/)
})

test('project metrics use StoryPath and immutable artifact identities on mobile', async ({ page }) => {
  const observations = {
    outlineChapterCounts: [], outlineIfMatches: [], outlineHeadBodies: [],
    promotedCandidateIds: [], promotionBodies: [],
  }
  await page.setViewportSize({ width: 390, height: 844 })
  await installWorkspaceMocks(page, observations)
  await page.goto('/project/7/stats')

  await expect(page.getByRole('heading', { name: '创作统计' })).toBeVisible()
  await expect(page.locator('code[title="root-path"]').first()).toBeVisible()
  await expect(page.locator('code[title="branch-chapter-1"]').first()).toBeVisible()
  await expect(page.locator('code[title="script-revision-1"]')).toBeVisible()
  await expect(page.locator('code[title="storage-object-1"]')).toBeVisible()
  await expect(page.getByText('第1章')).toHaveCount(0)

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
  expect(overflow).toBeLessThanOrEqual(1)
})

test('reopens the parent checkpoint when creating another sibling branch', async ({ page }) => {
  const observations = {
    outlineChapterCounts: [], outlineIfMatches: [], outlineHeadBodies: [],
    promotedCandidateIds: [], promotionBodies: [],
  }
  await installWorkspaceMocks(page, observations)
  await page.goto('/project/7?path=branch-path&chapter=branch-chapter-1')

  await page.getByRole('button', { name: '同点分支' }).click()

  await expect(page).toHaveURL(/path=root-path/)
  await expect(page).toHaveURL(/chapter=root-chapter-1/)
  await expect(page).toHaveURL(/checkpoint=checkpoint-1/)
  await expect(page.getByRole('tab', { name: '分支候选' })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByText('从货舱潜入驾驶室', { exact: true })).toBeVisible()
  await expect(page.getByText('在甲板公开交涉', { exact: true })).toBeVisible()
})

test('sends arbitrary outline count and requires explicit Head activation', async ({ page }) => {
  const observations = {
    outlineChapterCounts: [], outlineIfMatches: [], outlineHeadBodies: [],
    promotedCandidateIds: [], promotionBodies: [],
  }
  await installWorkspaceMocks(page, observations)
  await page.goto('/project/7?path=branch-path&chapter=branch-chapter-1')

  await page.getByRole('tab', { name: '章节大纲' }).click()
  const chapterCount = page.getByRole('spinbutton', { name: '大纲章节数' })
  await chapterCount.fill('120')
  await page.getByRole('button', { name: '生成大纲' }).click()

  await expect.poll(() => observations.outlineChapterCounts).toEqual([120])
  await expect(page.getByText('大纲 r2', { exact: true })).toBeVisible()
  await expect(page.locator('.head-strip')).toContainText('大纲 r1')

  await page.getByRole('button', { name: '设为当前版本' }).click()
  await expect.poll(() => observations.outlineIfMatches).toEqual(['3'])
  expect(observations.outlineHeadBodies).toEqual([{ revision_id: 'branch-path-outline-r2' }])
  await expect(page.locator('.head-strip')).toContainText('大纲 r2')
})

test('promotes only a reviewed candidate into a child StoryPath', async ({ page }) => {
  const observations = {
    outlineChapterCounts: [], outlineIfMatches: [], outlineHeadBodies: [],
    promotedCandidateIds: [], promotionBodies: [],
  }
  await installWorkspaceMocks(page, observations)
  await page.goto('/project/7?path=branch-path&chapter=branch-chapter-1&checkpoint=checkpoint-1')

  await page.getByRole('tab', { name: '分支候选' }).click()
  await expect(page.getByText('从货舱潜入驾驶室', { exact: true })).toBeVisible()
  await expect(page.getByText('在甲板公开交涉', { exact: true })).toBeVisible()

  await page.locator('.candidate-item').filter({ hasText: '从货舱潜入驾驶室' })
    .getByRole('button', { name: '创建路径' }).click()
  const dialog = page.getByRole('dialog', { name: '创建剧情路径' })
  await dialog.getByRole('textbox').fill('货舱潜入')
  await dialog.getByRole('button', { name: '创建路径' }).click()
  await expect(dialog).toBeHidden()

  await expect.poll(() => observations.promotedCandidateIds).toEqual(['candidate-a'])
  expect(observations.promotionBodies).toEqual([{ title: '货舱潜入' }])
  await expect(page.getByLabel('剧情路径', { exact: true }).getByText('货舱潜入', { exact: true })).toBeVisible()
  await expect(page).toHaveURL(/path=child-candidate-a/)
})

test('remains operable without page-level horizontal overflow on mobile', async ({ page }) => {
  const observations = {
    outlineChapterCounts: [], outlineIfMatches: [], outlineHeadBodies: [],
    promotedCandidateIds: [], promotionBodies: [],
  }
  await page.setViewportSize({ width: 390, height: 844 })
  await installWorkspaceMocks(page, observations)
  await page.goto('/project/7?path=branch-path&chapter=branch-chapter-1')

  await expect(page.getByRole('heading', { name: '雾港来信' })).toBeVisible()
  await page.getByRole('tab', { name: '章节大纲' }).click()
  await expect(page.getByRole('spinbutton', { name: '大纲章节数' })).toBeVisible()
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
  expect(overflow).toBeLessThanOrEqual(1)
})

test('creates the root StoryPath project without fixed chapter or visibility fields', async ({ page }) => {
  const observations = {
    outlineChapterCounts: [], outlineIfMatches: [], outlineHeadBodies: [],
    promotedCandidateIds: [], promotionBodies: [], projectBodies: [] as unknown[],
  }
  await installWorkspaceMocks(page, observations)
  await page.goto('/create')

  await expect(page.getByText('约8章')).toHaveCount(0)
  await expect(page.getByText('作品可见性')).toHaveCount(0)
  await page.getByLabel('小说标题').fill('雾港来信')
  await page.getByPlaceholder('输入角色名后按回车添加').fill('林遥')
  await page.getByPlaceholder('输入角色名后按回车添加').press('Enter')
  await page.getByLabel('故事开头').fill('一封没有寄件人的信抵达雾港。')
  await page.getByLabel('故事结尾').fill('灯塔重新亮起。')
  await page.getByRole('button', { name: '开始创作' }).click()

  await expect.poll(() => observations.projectBodies).toHaveLength(1)
  expect(observations.projectBodies[0]).toMatchObject({
    title: '雾港来信',
    characters: [{ name: '林遥' }],
    pace: 'medium',
  })
  expect(observations.projectBodies[0]).not.toHaveProperty('visibility')
  await expect(page).toHaveURL(/\/project\/7/)
})

test('publishes a changed workspace atomically and unpublishes with the latest lock', async ({ page }) => {
  const observations: WorkspaceObservations = {
    outlineChapterCounts: [], outlineIfMatches: [], outlineHeadBodies: [],
    promotedCandidateIds: [], promotionBodies: [], publishBodies: [], publishKeys: [],
    unpublishIfMatches: [],
  }
  await installWorkspaceMocks(page, observations, { publicationState: 'changes_pending' })
  await page.goto('/project/7?path=branch-path&chapter=branch-chapter-1&publication=1')

  const panel = page.locator('.publication-drawer')
  await expect(panel.getByRole('heading', { name: '发布管理' })).toBeVisible()
  await expect(panel.locator('.publication-state')).toContainText('有未发布修改')
  await expect(panel.getByText('检查通过', { exact: true })).toBeVisible()
  await expect(panel.getByRole('heading', { name: '当前公开版本' })).toBeVisible()
  await expect(panel.locator('.release-facts')).toContainText('release-v1')
  const viewportWidth = page.viewportSize()?.width ?? 0
  await expect.poll(async () => {
    const bounds = await panel.boundingBox()
    return Math.round((bounds?.x ?? 0) + (bounds?.width ?? 0))
  }).toBe(viewportWidth)

  await panel.getByLabel('发布说明').fill('渡轮线完成')
  await panel.getByRole('button', { name: '发布新版本' }).click()

  await expect.poll(() => observations.publishBodies).toEqual([{
    expected_authoring_fingerprint: 'f'.repeat(64),
    release_notes: '渡轮线完成',
  }])
  expect(observations.publishKeys).toHaveLength(1)
  expect(observations.publishKeys?.[0]).toMatch(/^publish:7:/)
  await expect(panel.locator('.publication-state')).toContainText('已公开')
  await expect(panel.locator('.release-facts')).toContainText('release-v2')
  await expect(panel.locator('.release-row').filter({ hasText: 'v1' })).toContainText('已取代')

  await panel.getByRole('button', { name: '撤回发布' }).click()
  await page.getByRole('button', { name: '撤回', exact: true }).click()

  await expect.poll(() => observations.unpublishIfMatches).toEqual(['4'])
  await expect(panel.locator('.publication-state')).toContainText('未发布')
  await expect(panel.getByRole('heading', { name: '当前公开版本' })).toHaveCount(0)
})

test('project list uses publication projections and opens the unified manager', async ({ page }) => {
  const observations: WorkspaceObservations = {
    outlineChapterCounts: [], outlineIfMatches: [], outlineHeadBodies: [],
    promotedCandidateIds: [], promotionBodies: [],
  }
  await installWorkspaceMocks(page, observations, { publicationState: 'changes_pending' })
  await page.goto('/')

  const ownerCard = page.locator('.owner-card').filter({ hasText: '雾港来信' })
  await expect(ownerCard).toContainText('有未发布修改')
  await expect(ownerCard).toContainText('等待正文')
  await expect(page.getByRole('button', { name: '发布为公开' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '撤回为私有' })).toHaveCount(0)

  await ownerCard.getByRole('button', { name: '发布管理' }).click()
  await expect(page).toHaveURL(/\/project\/7\?publication=1/)
  await expect(page.locator('.publication-drawer').getByRole('heading', { name: '发布管理' })).toBeVisible()
})

test('public catalog reads the immutable active Release manifest', async ({ page }) => {
  const observations: WorkspaceObservations = {
    outlineChapterCounts: [], outlineIfMatches: [], outlineHeadBodies: [],
    promotedCandidateIds: [], promotionBodies: [], publicManifestReads: [],
  }
  await installWorkspaceMocks(page, observations)
  await page.goto('/')
  await page.getByRole('tab', { name: '公开作品' }).click()

  const publicCard = page.locator('.public-card').filter({ hasText: '雾港来信' })
  await expect(publicCard).toContainText('v3')
  await expect(publicCard).toContainText('一封来信改变了雾港的航线。')
  await publicCard.getByRole('button', { name: '查看公开版本' }).click()

  await expect.poll(() => observations.publicManifestReads).toEqual(['public-release-v3'])
  const dialog = page.getByRole('dialog', { name: '雾港来信' })
  await expect(dialog).toContainText('story-path-release-v1')
  await expect(dialog).toContainText('root-path')
})

test('publication manager fits a mobile viewport without horizontal overflow', async ({ page }) => {
  const observations: WorkspaceObservations = {
    outlineChapterCounts: [], outlineIfMatches: [], outlineHeadBodies: [],
    promotedCandidateIds: [], promotionBodies: [],
  }
  await page.setViewportSize({ width: 390, height: 844 })
  await installWorkspaceMocks(page, observations, { publicationState: 'changes_pending' })
  await page.goto('/project/7?publication=1')

  const panel = page.locator('.publication-drawer')
  await expect(panel.getByRole('heading', { name: '发布管理' })).toBeVisible()
  await expect.poll(async () => Math.round((await panel.boundingBox())?.x ?? -1)).toBe(0)
  const bounds = await page.getByRole('dialog').boundingBox()
  expect(bounds?.x).toBeGreaterThanOrEqual(-0.5)
  expect(bounds?.x).toBeLessThanOrEqual(0.5)
  expect(bounds?.width).toBeLessThanOrEqual(391)
  expect(bounds?.width).toBeGreaterThanOrEqual(389)
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
  expect(overflow).toBeLessThanOrEqual(1)
})
