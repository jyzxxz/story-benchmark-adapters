<template>
  <div class="project-list-page">
    <header class="page-toolbar">
      <div>
        <span class="page-kicker">IF LINE</span>
        <h1>小说项目</h1>
      </div>
      <div class="page-actions">
        <el-button
          v-if="userStore.isLoggedIn"
          type="primary"
          :icon="Plus"
          @click="goToCreate"
        >
          创建项目
        </el-button>
        <el-button v-else type="primary" :icon="User" @click="goLogin">
          登录后创建
        </el-button>
        <el-tooltip content="刷新列表" placement="bottom">
          <el-button
            :icon="Refresh"
            circle
            :loading="loading"
            aria-label="刷新列表"
            @click="refreshCurrentTab"
          />
        </el-tooltip>
      </div>
    </header>

    <el-tabs v-model="activeTab" class="project-tabs" @tab-change="onTabChange">
      <el-tab-pane v-if="userStore.isLoggedIn" label="我的项目" name="mine">
        <el-skeleton
          v-if="loading && myProjects.length === 0"
          :rows="6"
          animated
          class="loading-placeholder"
        />
        <el-empty v-else-if="myProjects.length === 0" description="还没有小说项目" class="empty-state">
          <el-button type="primary" :icon="Plus" @click="goToCreate">创建项目</el-button>
        </el-empty>
        <div v-else class="project-grid">
          <article
            v-for="project in myProjects"
            :key="project.id"
            class="project-card owner-card"
            @click="goToProject(project.id)"
          >
            <header class="card-header">
              <h2>{{ project.title }}</h2>
              <div class="header-tags">
                <el-tag :type="publicationTagType(project.publication.state)" size="small" effect="plain">
                  {{ publicationLabel(project.publication.state) }}
                </el-tag>
                <el-tag :type="authoringTagType(project.authoring.stage)" size="small" effect="plain">
                  {{ authoringLabel(project.authoring.stage) }}
                </el-tag>
              </div>
            </header>

            <p class="story-preview">
              {{ project.story_start || '尚未填写故事开头' }}
            </p>

            <dl class="project-facts">
              <div>
                <dt>核心人物</dt>
                <dd>{{ characterNames(project.characters) }}</dd>
              </div>
              <div>
                <dt>风格</dt>
                <dd>{{ project.style || '未设置' }}</dd>
              </div>
              <div>
                <dt>节奏</dt>
                <dd>{{ paceLabel(project.pace) }}</dd>
              </div>
            </dl>

            <div class="project-state-line">
              <span v-if="project.authoring.running_task_count > 0">
                <el-icon class="spinning"><Loading /></el-icon>
                {{ project.authoring.running_task_count }} 个任务运行中
              </span>
              <span v-else-if="project.authoring.stage !== 'ready'">
                <el-icon><Warning /></el-icon>
                {{ project.authoring.blocking_items.length > 0
                  ? `${project.authoring.blocking_items.length} 项待处理`
                  : authoringLabel(project.authoring.stage) }}
              </span>
              <span v-else>
                <el-icon><CircleCheck /></el-icon>
                创作内容已就绪
              </span>
              <span v-if="project.authoring.has_unpublished_changes">
                <el-icon><Edit /></el-icon>
                有未发布修改
              </span>
              <span v-else>
                <el-icon><Connection /></el-icon>
                公开内容已同步
              </span>
            </div>

            <footer class="card-actions">
              <el-button type="primary" size="small" :icon="EditPen" @click.stop="goToProject(project.id)">
                继续创作
              </el-button>
              <el-button
                size="small"
                :type="project.publication.state === 'changes_pending' ? 'warning' : undefined"
                :icon="Promotion"
                @click.stop="goToPublication(project.id)"
              >
                发布管理
              </el-button>
              <el-button size="small" :icon="DataAnalysis" @click.stop="viewStats(project.id)">
                统计
              </el-button>
              <el-button
                type="danger"
                plain
                size="small"
                :icon="Delete"
                aria-label="删除项目"
                @click.stop="confirmDelete(project)"
              />
            </footer>
          </article>
        </div>
      </el-tab-pane>

      <el-tab-pane label="公开作品" name="public">
        <div class="public-library-band">
          <div>
            <span>PUBLIC LIBRARY</span>
            <strong>活动 Release</strong>
          </div>
          <p>公开目录仅展示当前活动且完整可读的不可变版本。</p>
        </div>

        <div class="project-grid public-grid">
          <article class="project-card featured-classic-card" @click="goToThreeKingdoms">
            <header class="card-header">
              <h2>三国演义</h2>
              <div class="header-tags">
                <el-tag type="success" size="small" effect="plain">公开</el-tag>
                <el-tag type="warning" size="small" effect="plain">120 回</el-tag>
              </div>
            </header>
            <div class="classic-body">
              <div class="classic-seal">三<br>国</div>
              <div>
                <strong>〔明〕罗贯中</strong>
                <p>白话正文、人物势力、十二幕结构与 VN 节点图。</p>
              </div>
            </div>
            <footer class="card-actions">
              <el-button type="primary" size="small" :icon="Reading" @click.stop="goToThreeKingdoms">
                阅读作品
              </el-button>
              <el-button size="small" @click.stop="goToThreeKingdomsBible">Story Bible</el-button>
            </footer>
          </article>

          <el-skeleton
            v-if="loading && publicProjects.length === 0"
            :rows="6"
            animated
            class="loading-placeholder"
          />
          <article
            v-for="project in publicProjects"
            :key="project.release_id"
            class="project-card public-card"
            @click="openPublicRelease(project)"
          >
            <div v-if="project.cover_url" class="cover-frame">
              <el-image :src="project.cover_url" fit="cover" lazy />
            </div>
            <header class="card-header">
              <h2>{{ project.title }}</h2>
              <div class="header-tags">
                <el-tag type="success" size="small" effect="plain">公开</el-tag>
                <el-tag type="info" size="small" effect="plain">v{{ project.release_version }}</el-tag>
              </div>
            </header>
            <p class="story-preview">{{ project.summary || '暂无公开摘要' }}</p>
            <dl class="public-facts">
              <div>
                <dt>发布时间</dt>
                <dd>{{ formatDate(project.published_at) }}</dd>
              </div>
              <div>
                <dt>Manifest</dt>
                <dd><code>{{ shortId(project.manifest_hash, 14) }}</code></dd>
              </div>
            </dl>
            <footer class="card-actions">
              <el-button type="primary" size="small" :icon="Document" @click.stop="openPublicRelease(project)">
                查看公开版本
              </el-button>
            </footer>
          </article>
        </div>
      </el-tab-pane>
    </el-tabs>

    <el-dialog
      v-model="publicDialogVisible"
      :title="selectedPublicProject?.title || '公开版本'"
      width="min(820px, 94vw)"
    >
      <div v-loading="publicManifestLoading" class="public-release-dialog">
        <div v-if="selectedPublicProject" class="public-release-heading">
          <div>
            <span>Release v{{ selectedPublicProject.release_version }}</span>
            <strong>{{ shortId(selectedPublicProject.release_id, 18) }}</strong>
          </div>
          <el-tag type="success" effect="plain">活动版本</el-tag>
        </div>
        <template v-if="publicManifest">
          <div v-if="publicChapters.length" class="public-chapter-list">
            <div
              v-for="(chapter, index) in publicChapters"
              :key="chapter.path_chapter_id ?? index"
              class="public-chapter-row"
            >
              <div class="public-chapter-info">
                <strong>第 {{ chapter.display_index ?? index + 1 }} 章</strong>
                <span v-if="chapter.title" class="public-chapter-title">{{ chapter.title }}</span>
                <span v-if="!chapter.has_graph" class="public-chapter-nograph">无 VN 图</span>
              </div>
              <el-button
                type="primary"
                size="small"
                :icon="CaretRight"
                :disabled="!chapter.path_chapter_id || !chapter.has_graph"
                @click="playPublicChapter(chapter.path_chapter_id)"
              >
                播放 VN 图
              </el-button>
            </div>
          </div>
          <el-empty v-else description="该版本暂无公开章节" :image-size="72" />
          <details class="public-manifest-raw">
            <summary>查看原始 manifest JSON</summary>
            <pre>{{ prettyJson(publicManifest.manifest) }}</pre>
          </details>
        </template>
        <el-empty v-else-if="!publicManifestLoading" description="公开清单不可读取" />
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  CaretRight,
  CircleCheck,
  Connection,
  DataAnalysis,
  Delete,
  Document,
  Edit,
  EditPen,
  Loading,
  Plus,
  Promotion,
  Reading,
  Refresh,
  User,
  Warning,
} from '@element-plus/icons-vue'
import { storyPathApi } from '@/api/storyPathApi'
import type {
  AuthoringStage,
  JsonObject,
  ProjectRead,
  PublicationState,
  PublicProject,
  ReleaseManifest,
} from '@/api/storyPathTypes'
import { getSafeErrorSummary } from '@/security/safeError'
import { useUserStore } from '@/stores/user'
import { shortId } from '@/utils/storyPathWorkspace'

const router = useRouter()
const userStore = useUserStore()

const activeTab = ref<'mine' | 'public'>('public')
const myProjects = ref<ProjectRead[]>([])
const publicProjects = ref<PublicProject[]>([])
const loading = ref(false)
const publicDialogVisible = ref(false)
const publicManifestLoading = ref(false)
const publicManifest = ref<ReleaseManifest | null>(null)

interface PublicChapterRow {
  display_index: number | null
  title: string | null
  path_chapter_id: string | null
  has_graph: boolean
}

const publicChapters = computed<PublicChapterRow[]>(() => {
  const manifest = publicManifest.value?.manifest
  if (!manifest || typeof manifest !== 'object') return []
  const storyPaths = (manifest as { story_paths?: unknown }).story_paths
  if (!Array.isArray(storyPaths)) return []
  // 章节标题存在各路径 outline_revision.chapters 里，按 story_path_chapter_id 建映射
  const titleById = new Map<string, string>()
  for (const sp of storyPaths) {
    if (!sp || typeof sp !== 'object') continue
    const outline = (sp as Record<string, unknown>).outline_revision
    const outlineChapters =
      outline && typeof outline === 'object' ? (outline as Record<string, unknown>).chapters : null
    if (!Array.isArray(outlineChapters)) continue
    for (const c of outlineChapters) {
      if (!c || typeof c !== 'object') continue
      const id = (c as Record<string, unknown>).story_path_chapter_id
      const title = (c as Record<string, unknown>).title
      if (typeof id === 'string' && typeof title === 'string' && title.trim()) {
        titleById.set(id, title.trim())
      }
    }
  }
  // 章节条目在各路径 chapters 下（path_chapter_id / display_index / vn_graph_revision）
  const rows: PublicChapterRow[] = []
  for (const sp of storyPaths) {
    if (!sp || typeof sp !== 'object') continue
    const chapters = (sp as Record<string, unknown>).chapters
    if (!Array.isArray(chapters)) continue
    for (const c of chapters) {
      if (!c || typeof c !== 'object') continue
      const item = c as Record<string, unknown>
      const pathChapterId = typeof item.path_chapter_id === 'string' ? item.path_chapter_id : null
      rows.push({
        display_index: typeof item.display_index === 'number' ? item.display_index : null,
        title: pathChapterId ? titleById.get(pathChapterId) ?? null : null,
        path_chapter_id: pathChapterId,
        has_graph: Boolean(item.vn_graph_revision && typeof item.vn_graph_revision === 'object'),
      })
    }
  }
  return rows.sort((left, right) => (left.display_index ?? 0) - (right.display_index ?? 0))
})

function playPublicChapter(pathChapterId: string): void {
  const releaseId = selectedPublicProject.value?.release_id
  if (!releaseId || !pathChapterId) return
  router.push({
    path: '/vn-graph-player',
    query: { releaseId, pathChapterId },
  })
}
const selectedPublicProject = ref<PublicProject | null>(null)

type TagType = 'primary' | 'success' | 'warning' | 'info' | 'danger'

async function loadMyProjects(): Promise<void> {
  if (!userStore.isLoggedIn) {
    myProjects.value = []
    return
  }
  loading.value = true
  try {
    const response = await storyPathApi.projects.list()
    myProjects.value = response.data
  } catch (error) {
    const summary = getSafeErrorSummary(error)
    console.error('load owner projects', summary)
    if (summary.status !== 401) ElMessage.error('项目列表加载失败')
  } finally {
    loading.value = false
  }
}

async function loadPublicProjects(): Promise<void> {
  loading.value = true
  try {
    const response = await storyPathApi.public.listProjects()
    publicProjects.value = response.data
  } catch (error) {
    console.error('load public projects', getSafeErrorSummary(error))
    ElMessage.error('公开作品加载失败')
  } finally {
    loading.value = false
  }
}

function refreshCurrentTab(): void {
  if (activeTab.value === 'mine') void loadMyProjects()
  else void loadPublicProjects()
}

function onTabChange(name: string | number): void {
  if (String(name) === 'mine') void loadMyProjects()
  else void loadPublicProjects()
}

function publicationLabel(state: PublicationState): string {
  return {
    unpublished: '未发布',
    published: '已公开',
    changes_pending: '有未发布修改',
  }[state]
}

function publicationTagType(state: PublicationState): TagType {
  if (state === 'published') return 'success'
  if (state === 'changes_pending') return 'warning'
  return 'info'
}

function authoringLabel(stage: AuthoringStage): string {
  return {
    bible: '等待设定',
    story_paths: '等待路径',
    outlines: '等待大纲',
    chapters: '等待正文',
    scripts: '等待脚本',
    vn_graphs: '等待 VNGraph',
    ready: '创作就绪',
    blocked: '存在阻塞',
  }[stage]
}

function authoringTagType(stage: AuthoringStage): TagType {
  if (stage === 'ready') return 'success'
  if (stage === 'blocked') return 'danger'
  return 'info'
}

function characterNames(characters: JsonObject[]): string {
  const names = characters
    .map((character) => character.name)
    .filter((name): name is string => typeof name === 'string' && Boolean(name.trim()))
  return names.join('、') || '未设置'
}

function paceLabel(pace: string | null): string {
  return {
    fast: '紧凑',
    medium: '均衡',
    slow: '舒缓',
  }[pace || ''] || pace || '未设置'
}

function formatDate(value: string | null): string {
  return value ? new Date(value).toLocaleDateString('zh-CN') : '-'
}

function prettyJson(value: unknown): string {
  return JSON.stringify(value ?? {}, null, 2)
}

function goLogin(): void {
  void router.push('/login')
}

function goToCreate(): void {
  void router.push('/create')
}

function goToThreeKingdoms(): void {
  void router.push('/public/three-kingdoms')
}

function goToThreeKingdomsBible(): void {
  void router.push('/public/three-kingdoms?section=bible')
}

function goToProject(projectId: number): void {
  void router.push({ name: 'Project', params: { id: String(projectId) } })
}

function goToPublication(projectId: number): void {
  void router.push({
    name: 'Project',
    params: { id: String(projectId) },
    query: { publication: '1' },
  })
}

function viewStats(projectId: number): void {
  void router.push(`/project/${projectId}/stats`)
}

async function openPublicRelease(project: PublicProject): Promise<void> {
  selectedPublicProject.value = project
  publicManifest.value = null
  publicDialogVisible.value = true
  publicManifestLoading.value = true
  try {
    const response = await storyPathApi.public.getReleaseManifest(project.release_id)
    if (selectedPublicProject.value?.release_id === project.release_id) {
      publicManifest.value = response.data
    }
  } catch (error) {
    console.error('load public release manifest', getSafeErrorSummary(error))
    ElMessage.error('公开版本读取失败')
  } finally {
    if (selectedPublicProject.value?.release_id === project.release_id) {
      publicManifestLoading.value = false
    }
  }
}

async function confirmDelete(project: ProjectRead): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确定删除“${project.title}”吗？此操作不可恢复。`,
      '删除项目',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }

  try {
    await storyPathApi.projects.delete(project.id)
    myProjects.value = myProjects.value.filter((item) => item.id !== project.id)
    ElMessage.success('项目已删除')
  } catch (error) {
    const summary = getSafeErrorSummary(error)
    console.error('delete project', summary)
    if (summary.status === 409 && project.publication.active_release_id) {
      try {
        await ElMessageBox.alert(
          '项目仍有活动公开版本，请先在发布管理中撤回。',
          '无法删除',
          { type: 'warning', confirmButtonText: '打开发布管理' },
        )
      } finally {
        goToPublication(project.id)
      }
      return
    }
    if (summary.status === 404) {
      myProjects.value = myProjects.value.filter((item) => item.id !== project.id)
      ElMessage.warning('项目已不存在')
      return
    }
    ElMessage.error('项目删除失败')
  }
}

onMounted(async () => {
  if (!userStore.loaded) await userStore.fetchMe()
  activeTab.value = userStore.isLoggedIn ? 'mine' : 'public'
  if (activeTab.value === 'mine') await loadMyProjects()
  else await loadPublicProjects()
})
</script>

<style scoped>
.project-list-page {
  min-height: calc(100vh - 56px);
  padding: 24px;
  background: #eef1f4;
  color: #252c34;
}

.page-toolbar {
  display: flex;
  max-width: 1240px;
  min-height: 68px;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  margin: 0 auto 8px;
}

.page-kicker,
.public-library-band span {
  display: block;
  margin-bottom: 2px;
  color: #7b8490;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0;
}

.page-toolbar h1 {
  margin: 0;
  font-size: 22px;
  font-weight: 680;
}

.page-actions,
.header-tags,
.card-actions,
.project-state-line,
.public-library-band,
.public-release-heading {
  display: flex;
  align-items: center;
  gap: 8px;
}

.project-tabs {
  max-width: 1240px;
  margin: 0 auto;
}

.project-tabs :deep(.el-tabs__header) {
  margin-bottom: 18px;
}

.loading-placeholder {
  min-height: 260px;
  padding: 24px;
  border: 1px solid #dfe3e8;
  background: #fff;
}

.empty-state {
  min-height: 360px;
  border: 1px solid #dfe3e8;
  background: #fff;
}

.project-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 360px), 1fr));
  gap: 16px;
}

.project-card {
  display: flex;
  min-width: 0;
  min-height: 320px;
  flex-direction: column;
  padding: 18px;
  border: 1px solid #dfe3e8;
  border-radius: 7px;
  background: #fff;
  cursor: pointer;
  transition: border-color 0.16s, box-shadow 0.16s;
}

.project-card:hover {
  border-color: #9fb5c8;
  box-shadow: 0 7px 18px rgba(32, 45, 56, 0.08);
}

.card-header {
  display: flex;
  min-width: 0;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.card-header h2 {
  min-width: 0;
  margin: 0;
  overflow: hidden;
  color: #222930;
  font-size: 17px;
  font-weight: 680;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.header-tags {
  flex: 0 0 auto;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.story-preview {
  display: -webkit-box;
  min-height: 62px;
  margin: 18px 0;
  overflow: hidden;
  color: #626c77;
  font-size: 13px;
  line-height: 1.65;
  overflow-wrap: anywhere;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
}

.project-facts,
.public-facts {
  display: grid;
  gap: 10px;
  margin: 0;
}

.project-facts {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.public-facts {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.project-facts div,
.public-facts div {
  min-width: 0;
}

.project-facts dt,
.public-facts dt {
  margin-bottom: 4px;
  color: #8b949e;
  font-size: 10px;
}

.project-facts dd,
.public-facts dd {
  margin: 0;
  overflow: hidden;
  color: #3c444e;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.project-state-line {
  flex-wrap: wrap;
  margin-top: 18px;
  padding-top: 12px;
  border-top: 1px solid #edf0f2;
}

.project-state-line span {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: #6d7782;
  font-size: 11px;
}

.spinning {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.card-actions {
  flex-wrap: wrap;
  margin-top: auto;
  padding-top: 18px;
}

.card-actions .el-button + .el-button {
  margin-left: 0;
}

.public-library-band {
  justify-content: space-between;
  margin-bottom: 16px;
  padding: 13px 16px;
  border: 1px solid #cfd8df;
  background: #f8fafb;
}

.public-library-band > div {
  min-width: 170px;
}

.public-library-band strong {
  font-size: 14px;
}

.public-library-band p {
  margin: 0;
  color: #6e7883;
  font-size: 12px;
}

.featured-classic-card {
  border-color: #cbb99b;
  background: #fffdf9;
}

.classic-body {
  display: grid;
  grid-template-columns: 60px minmax(0, 1fr);
  align-items: center;
  gap: 16px;
  margin: 22px 0;
}

.classic-seal {
  display: grid;
  width: 58px;
  height: 82px;
  place-items: center;
  background: #8f322d;
  color: #fff7eb;
  font-family: 'STKaiti', 'KaiTi', serif;
  font-size: 22px;
  font-weight: 700;
  line-height: 1.05;
  text-align: center;
}

.classic-body strong {
  color: #39342d;
  font-size: 14px;
}

.classic-body p {
  margin: 8px 0 0;
  color: #746b5e;
  font-size: 12px;
  line-height: 1.6;
}

.cover-frame {
  width: calc(100% + 36px);
  aspect-ratio: 16 / 7;
  margin: -18px -18px 16px;
  overflow: hidden;
  border-radius: 6px 6px 0 0;
  background: #e8ecef;
}

.cover-frame .el-image {
  width: 100%;
  height: 100%;
}

.public-release-dialog {
  min-height: 260px;
}

.public-release-heading {
  justify-content: space-between;
  margin-bottom: 12px;
  padding: 11px 12px;
  border: 1px solid #dfe3e8;
  background: #f7f9fa;
}

.public-release-heading > div {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 3px;
}

.public-release-heading span {
  color: #727c87;
  font-size: 11px;
}

.public-release-heading strong {
  overflow: hidden;
  color: #303841;
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.public-chapter-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 14px;
}

.public-chapter-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 14px;
  border: 1px solid #e2e7ec;
  border-radius: 7px;
  background: #fbfcfd;
}

.public-chapter-info {
  display: flex;
  min-width: 0;
  align-items: baseline;
  gap: 10px;
  color: #2f3944;
  font-size: 13px;
}

.public-chapter-info strong {
  flex: 0 0 auto;
}

.public-chapter-title {
  overflow: hidden;
  color: #6d7683;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.public-chapter-nograph {
  color: #b8860b;
  font-size: 11px;
}

.public-manifest-raw summary {
  color: #7b8490;
  cursor: pointer;
  font-size: 12px;
  user-select: none;
}

.public-manifest-raw pre {
  max-height: min(52vh, 520px);
  margin: 8px 0 0;
}

.public-release-dialog pre {
  max-height: min(62vh, 620px);
  margin: 0;
  overflow: auto;
  padding: 14px;
  border: 1px solid #dfe3e8;
  border-radius: 5px;
  background: #f8fafb;
  color: #343d46;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', monospace;
  font-size: 11px;
  line-height: 1.6;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

@media (max-width: 720px) {
  .project-list-page {
    padding: 14px 12px 24px;
  }

  .page-toolbar,
  .public-library-band {
    align-items: flex-start;
    flex-direction: column;
  }

  .page-actions {
    width: 100%;
  }

  .page-actions .el-button:first-child {
    flex: 1;
  }

  .project-facts {
    grid-template-columns: 1fr;
  }

  .project-card {
    min-height: 0;
  }
}
</style>
