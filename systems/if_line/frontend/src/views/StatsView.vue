<template>
  <main class="stats-view">
    <header class="page-toolbar">
      <div class="toolbar-leading">
        <el-tooltip content="返回项目" placement="bottom">
          <el-button :icon="ArrowLeft" circle aria-label="返回项目" @click="goBack" />
        </el-tooltip>
        <div>
          <span class="page-kicker">PROJECT {{ projectId }}</span>
          <h1>创作统计</h1>
        </div>
      </div>
      <div class="toolbar-actions">
        <el-tooltip content="返回主页" placement="bottom">
          <el-button :icon="HomeFilled" circle aria-label="返回主页" @click="goHome" />
        </el-tooltip>
        <el-tooltip content="刷新统计" placement="bottom">
          <el-button
            :icon="Refresh"
            circle
            :loading="loading"
            aria-label="刷新统计"
            @click="loadMetrics"
          />
        </el-tooltip>
      </div>
    </header>

    <el-skeleton v-if="loading && !metrics" :rows="8" animated class="loading-state" />

    <el-result
      v-else-if="errorMessage"
      icon="error"
      title="统计加载失败"
      :sub-title="errorMessage"
    >
      <template #extra>
        <el-button type="primary" :icon="Refresh" @click="loadMetrics">重试</el-button>
      </template>
    </el-result>

    <template v-else-if="metrics && artifacts">
      <section class="metric-grid" aria-label="项目统计概览">
        <article class="metric-item">
          <span>活动路径</span>
          <strong>{{ activePathCount }}</strong>
          <small>共 {{ pathRows.length }} 条路径</small>
        </article>
        <article class="metric-item metric-item--green">
          <span>已选章节</span>
          <strong>{{ metrics.selected_path_chapter_count }}</strong>
          <small>按 PathChapter UUID 统计</small>
        </article>
        <article class="metric-item metric-item--orange">
          <span>不可变修订</span>
          <strong>{{ totalRevisionCount }}</strong>
          <small>正文、脚本与 VNGraph</small>
        </article>
        <article class="metric-item metric-item--red">
          <span>生成任务</span>
          <strong>{{ metrics.task_counts.total }}</strong>
          <small>平均 {{ formatDuration(averageTaskDuration) }}</small>
        </article>
        <article class="metric-item metric-item--violet">
          <span>素材版本</span>
          <strong>{{ assetRows.length }}</strong>
          <small>平均质量 {{ averageQuality }}</small>
        </article>
      </section>

      <section class="stats-section">
        <header class="section-header">
          <div>
            <h2>修订与任务分布</h2>
            <p>当前项目的不可变修订和异步任务状态</p>
          </div>
        </header>
        <div class="distribution-grid">
          <div class="distribution-block">
            <h3>修订类型</h3>
            <dl class="count-list">
              <div v-for="row in revisionRows" :key="row.key">
                <dt>{{ row.label }}</dt>
                <dd>{{ row.count }}</dd>
              </div>
            </dl>
          </div>
          <div class="distribution-block">
            <h3>任务状态</h3>
            <div class="tag-list">
              <el-tag
                v-for="row in taskStatusRows"
                :key="row.key"
                :type="statusTagType(row.key)"
                effect="plain"
              >
                {{ statusLabel(row.key) }} {{ row.count }}
              </el-tag>
              <span v-if="taskStatusRows.length === 0" class="muted">暂无任务</span>
            </div>
          </div>
          <div class="distribution-block">
            <h3>任务类型</h3>
            <div class="tag-list">
              <el-tag v-for="row in taskKindRows" :key="row.key" type="info" effect="plain">
                {{ kindLabel(row.key) }} {{ row.count }}
              </el-tag>
              <span v-if="taskKindRows.length === 0" class="muted">暂无任务</span>
            </div>
          </div>
        </div>
      </section>

      <section class="stats-section">
        <header class="section-header">
          <div>
            <h2>StoryPath</h2>
            <p>父子关系和章节 Slot 均以 UUID 关联</p>
          </div>
        </header>
        <el-table :data="pathRows" stripe empty-text="暂无 StoryPath">
          <el-table-column label="路径 UUID" min-width="180">
            <template #default="{ row }">
              <code :title="row.id">{{ shortId(row.id) }}</code>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="110">
            <template #default="{ row }">
              <el-tag :type="row.status === 'active' ? 'success' : 'info'" effect="plain" size="small">
                {{ row.status === 'active' ? '活动' : '已归档' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="父路径 UUID" min-width="180">
            <template #default="{ row }">
              <code v-if="row.parentPathId" :title="row.parentPathId">{{ shortId(row.parentPathId) }}</code>
              <span v-else class="muted">根路径</span>
            </template>
          </el-table-column>
          <el-table-column prop="chapterCount" label="章节 Slot" width="120" align="right" />
        </el-table>
      </section>

      <section class="stats-section">
        <header class="section-header section-header--filters">
          <div>
            <h2>生成任务</h2>
            <p>任务来源通过 PathChapter UUID 定位</p>
          </div>
          <div class="filters">
            <el-select v-model="taskKindFilter" clearable placeholder="全部类型" aria-label="任务类型">
              <el-option
                v-for="row in taskKindRows"
                :key="row.key"
                :label="kindLabel(row.key)"
                :value="row.key"
              />
            </el-select>
            <el-select v-model="taskStatusFilter" clearable placeholder="全部状态" aria-label="任务状态">
              <el-option
                v-for="row in taskStatusRows"
                :key="row.key"
                :label="statusLabel(row.key)"
                :value="row.key"
              />
            </el-select>
          </div>
        </header>
        <el-table :data="filteredTaskRows" stripe empty-text="暂无生成任务" max-height="520">
          <el-table-column label="任务 UUID" min-width="170">
            <template #default="{ row }"><code :title="row.id">{{ shortId(row.id) }}</code></template>
          </el-table-column>
          <el-table-column label="类型" min-width="150">
            <template #default="{ row }">{{ kindLabel(row.kind) }}</template>
          </el-table-column>
          <el-table-column label="状态" width="110">
            <template #default="{ row }">
              <el-tag :type="statusTagType(row.status)" effect="plain" size="small">
                {{ statusLabel(row.status) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="PathChapter UUID" min-width="190">
            <template #default="{ row }">
              <code v-if="row.pathChapterId" :title="row.pathChapterId">{{ shortId(row.pathChapterId) }}</code>
              <span v-else class="muted">项目级</span>
            </template>
          </el-table-column>
          <el-table-column label="耗时" width="120" align="right">
            <template #default="{ row }">{{ formatDuration(row.durationSeconds) }}</template>
          </el-table-column>
        </el-table>
      </section>

      <section class="stats-section">
        <header class="section-header">
          <div>
            <h2>素材版本</h2>
            <p>素材来源绑定到不可变 Revision 与 GenerationTask</p>
          </div>
        </header>
        <el-table :data="assetRows" stripe empty-text="暂无素材版本" max-height="520">
          <el-table-column label="版本 UUID" min-width="160">
            <template #default="{ row }"><code :title="row.id">{{ shortId(row.id) }}</code></template>
          </el-table-column>
          <el-table-column label="来源" min-width="130">
            <template #default="{ row }">{{ row.sourceKind || '-' }}</template>
          </el-table-column>
          <el-table-column label="Revision UUID" min-width="170">
            <template #default="{ row }">
              <code v-if="row.sourceRevisionId" :title="row.sourceRevisionId">{{ shortId(row.sourceRevisionId) }}</code>
              <span v-else class="muted">-</span>
            </template>
          </el-table-column>
          <el-table-column label="Task UUID" min-width="160">
            <template #default="{ row }">
              <code v-if="row.generationTaskId" :title="row.generationTaskId">{{ shortId(row.generationTaskId) }}</code>
              <span v-else class="muted">-</span>
            </template>
          </el-table-column>
          <el-table-column label="Storage UUID" min-width="160">
            <template #default="{ row }">
              <code v-if="row.storageObjectId" :title="row.storageObjectId">{{ shortId(row.storageObjectId) }}</code>
              <span v-else class="muted">-</span>
            </template>
          </el-table-column>
          <el-table-column label="质量" width="100" align="right">
            <template #default="{ row }">{{ formatQuality(row.qualityScore) }}</template>
          </el-table-column>
          <el-table-column label="安全状态" width="120">
            <template #default="{ row }">
              <el-tag :type="safetyTagType(row.safetyStatus)" effect="plain" size="small">
                {{ row.safetyStatus }}
              </el-tag>
            </template>
          </el-table-column>
        </el-table>
      </section>
    </template>
  </main>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowLeft, HomeFilled, Refresh } from '@element-plus/icons-vue'
import { storyPathApi } from '@/api/storyPathApi'
import type { ProjectArtifactMetrics, ProjectMetrics } from '@/api/storyPathTypes'
import { getSafeErrorSummary } from '@/security/safeError'

type TagType = 'primary' | 'success' | 'warning' | 'info' | 'danger'

const route = useRoute()
const router = useRouter()
const projectId = computed(() => Number(route.params.id))
const metrics = ref<ProjectMetrics | null>(null)
const artifacts = ref<ProjectArtifactMetrics | null>(null)
const loading = ref(false)
const errorMessage = ref('')
const taskKindFilter = ref('')
const taskStatusFilter = ref('')

const pathRows = computed(() => Object.entries(metrics.value?.story_paths_by_id ?? {}).map(
  ([id, value]) => ({
    id,
    status: value.status,
    parentPathId: value.parent_path_id,
    chapterCount: value.path_chapter_ids.length,
  }),
))

const revisionRows = computed(() => {
  const counts = metrics.value?.revision_counts
  if (!counts) return []
  return [
    { key: 'bible', label: '故事圣经', count: counts.bible },
    { key: 'outline', label: '章节大纲', count: counts.outline },
    { key: 'chapter', label: '章节正文', count: counts.chapter },
    { key: 'script', label: '章节脚本', count: counts.script },
    { key: 'vn_graph', label: 'VNGraph', count: counts.vn_graph },
  ]
})

const taskStatusRows = computed(() => Object.entries(metrics.value?.task_counts.by_status ?? {})
  .map(([key, count]) => ({ key, count })))
const taskKindRows = computed(() => Object.entries(metrics.value?.task_counts.by_kind ?? {})
  .map(([key, count]) => ({ key, count })))

const taskRows = computed(() => Object.entries(artifacts.value?.generation_tasks_by_id ?? {}).map(
  ([id, value]) => ({
    id,
    kind: value.kind,
    status: value.status,
    pathChapterId: value.path_chapter_id,
    durationSeconds: value.duration_seconds,
  }),
))

const assetRows = computed(() => Object.entries(artifacts.value?.asset_versions_by_id ?? {}).map(
  ([id, value]) => ({
    id,
    sourceKind: value.source_kind,
    sourceRevisionId: value.source_revision_id,
    generationTaskId: value.generation_task_id,
    storageObjectId: value.storage_object_id,
    qualityScore: value.quality_score,
    safetyStatus: value.safety_status,
  }),
))

const filteredTaskRows = computed(() => taskRows.value.filter((row) => (
  (!taskKindFilter.value || row.kind === taskKindFilter.value)
  && (!taskStatusFilter.value || row.status === taskStatusFilter.value)
)))
const activePathCount = computed(() => pathRows.value.filter((row) => row.status === 'active').length)
const totalRevisionCount = computed(() => revisionRows.value.reduce((sum, row) => sum + row.count, 0))
const averageTaskDuration = computed(() => {
  const durations = taskRows.value
    .map((row) => row.durationSeconds)
    .filter((value): value is number => value !== null)
  return durations.length ? durations.reduce((sum, value) => sum + value, 0) / durations.length : null
})
const averageQuality = computed(() => {
  const scores = assetRows.value
    .map((row) => row.qualityScore)
    .filter((value): value is number => value !== null)
  if (!scores.length) return '-'
  return (scores.reduce((sum, value) => sum + value, 0) / scores.length).toFixed(2)
})

const shortId = (value: string) => value.slice(0, 8)
const formatDuration = (seconds: number | null): string => {
  if (seconds === null) return '-'
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)} 秒`
  const minutes = Math.floor(seconds / 60)
  const remainder = Math.round(seconds % 60)
  return `${minutes} 分 ${remainder} 秒`
}
const formatQuality = (score: number | null): string => score === null ? '-' : score.toFixed(2)

const statusLabel = (status: string): string => ({
  pending: '等待中',
  queued: '已排队',
  running: '运行中',
  succeeded: '成功',
  completed: '完成',
  failed: '失败',
  cancelled: '已取消',
}[status] ?? status)

const statusTagType = (status: string): TagType => ({
  pending: 'info',
  queued: 'info',
  running: 'primary',
  succeeded: 'success',
  completed: 'success',
  failed: 'danger',
  cancelled: 'warning',
}[status] as TagType | undefined) ?? 'info'

const safetyTagType = (status: string): TagType => ({
  approved: 'success',
  safe: 'success',
  pending: 'warning',
  rejected: 'danger',
  blocked: 'danger',
}[status] as TagType | undefined) ?? 'info'

const kindLabel = (kind: string): string => ({
  bible_generation: '故事圣经',
  outline_generation: '章节大纲',
  chapter_generation: '章节正文',
  chapter_batch_generation: '正文批量生成',
  candidate_set_generation: '分支候选',
  script_generation: '章节脚本',
  resource_render: '素材渲染',
  vn_graph_compilation: 'VNGraph 编译',
}[kind] ?? kind)

const loadMetrics = async () => {
  if (!Number.isSafeInteger(projectId.value) || projectId.value <= 0) {
    errorMessage.value = '项目 ID 无效'
    return
  }

  loading.value = true
  errorMessage.value = ''
  try {
    const [metricsResponse, artifactsResponse] = await Promise.all([
      storyPathApi.projects.metrics(projectId.value),
      storyPathApi.projects.artifactMetrics(projectId.value),
    ])
    metrics.value = metricsResponse.data
    artifacts.value = artifactsResponse.data
  } catch (error) {
    const summary = getSafeErrorSummary(error)
    console.error('加载创作统计失败', summary)
    errorMessage.value = summary.status === 404 ? '项目不存在或已不可访问' : '无法读取项目统计'
    ElMessage.error(errorMessage.value)
  } finally {
    loading.value = false
  }
}

const goBack = () => router.push(`/project/${projectId.value}`)
const goHome = () => router.push('/')

watch(projectId, loadMetrics, { immediate: true })
</script>

<style scoped>
.stats-view {
  min-height: 100vh;
  padding: 24px clamp(16px, 4vw, 48px) 56px;
  color: #20242a;
  background: #f5f7fa;
}

.page-toolbar,
.toolbar-leading,
.toolbar-actions,
.section-header,
.filters {
  display: flex;
  align-items: center;
}

.page-toolbar {
  justify-content: space-between;
  gap: 20px;
  max-width: 1440px;
  margin: 0 auto 24px;
}

.toolbar-leading,
.toolbar-actions {
  gap: 12px;
}

.page-kicker {
  color: #737a84;
  font-size: 12px;
  font-weight: 600;
}

h1,
h2,
h3,
p {
  margin: 0;
}

h1 {
  margin-top: 2px;
  font-size: 24px;
}

.loading-state,
.metric-grid,
.stats-section {
  max-width: 1440px;
  margin-right: auto;
  margin-left: auto;
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(140px, 1fr));
  gap: 12px;
  margin-bottom: 20px;
}

.metric-item {
  min-height: 118px;
  padding: 18px;
  border: 1px solid #dce1e8;
  border-top: 3px solid #2f6fdb;
  border-radius: 6px;
  background: #fff;
}

.metric-item--green { border-top-color: #2f8f61; }
.metric-item--orange { border-top-color: #c77b20; }
.metric-item--red { border-top-color: #bd4b55; }
.metric-item--violet { border-top-color: #7654a8; }

.metric-item span,
.metric-item small {
  display: block;
  color: #737a84;
}

.metric-item strong {
  display: block;
  margin: 8px 0 4px;
  font-size: 30px;
  line-height: 1;
}

.metric-item small {
  font-size: 12px;
}

.stats-section {
  margin-bottom: 18px;
  padding: 20px;
  border: 1px solid #dce1e8;
  border-radius: 6px;
  background: #fff;
}

.section-header {
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}

.section-header h2 {
  font-size: 18px;
}

.section-header p {
  margin-top: 4px;
  color: #737a84;
  font-size: 13px;
}

.distribution-grid {
  display: grid;
  grid-template-columns: 1.1fr 1fr 1.4fr;
  gap: 24px;
}

.distribution-block {
  min-width: 0;
}

.distribution-block h3 {
  margin-bottom: 10px;
  color: #555d68;
  font-size: 13px;
}

.count-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(100px, 1fr));
  gap: 8px 20px;
  margin: 0;
}

.count-list div {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding-bottom: 6px;
  border-bottom: 1px solid #edf0f4;
}

.count-list dt {
  color: #606874;
}

.count-list dd {
  margin: 0;
  font-weight: 700;
}

.tag-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.filters {
  flex-wrap: wrap;
  gap: 8px;
}

.filters :deep(.el-select) {
  width: 150px;
}

code {
  padding: 2px 5px;
  border-radius: 4px;
  color: #354052;
  background: #f0f2f5;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
}

.muted {
  color: #9299a3;
}

@media (max-width: 1050px) {
  .metric-grid {
    grid-template-columns: repeat(3, minmax(140px, 1fr));
  }

  .distribution-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 680px) {
  .stats-view {
    padding: 16px 12px 40px;
  }

  .metric-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .metric-item {
    min-height: 108px;
    padding: 14px;
  }

  .metric-item strong {
    font-size: 26px;
  }

  .stats-section {
    padding: 16px 12px;
    overflow-x: auto;
  }

  .section-header--filters {
    align-items: flex-start;
    flex-direction: column;
  }

  .filters,
  .filters :deep(.el-select) {
    width: 100%;
  }
}
</style>
