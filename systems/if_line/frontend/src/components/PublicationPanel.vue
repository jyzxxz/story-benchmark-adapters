<template>
  <el-drawer
    :model-value="modelValue"
    size="500px"
    append-to-body
    class="publication-drawer"
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <template #header>
      <div class="drawer-heading">
        <div>
          <span>公开版本</span>
          <h2>发布管理</h2>
        </div>
        <el-tag :type="publicationTagType" effect="plain">
          {{ publicationLabel }}
        </el-tag>
      </div>
    </template>

    <div v-if="project" v-loading="loading" class="publication-panel">
      <section class="publication-state" :class="`state-${project.publication.state}`">
        <el-icon>
          <CircleCheck v-if="project.publication.state === 'published'" />
          <WarningFilled v-else-if="project.publication.state === 'changes_pending'" />
          <Lock v-else />
        </el-icon>
        <div>
          <strong>{{ publicationLabel }}</strong>
          <p>{{ publicationDescription }}</p>
        </div>
      </section>

      <section class="panel-section">
        <header class="section-header">
          <div>
            <span>READINESS</span>
            <h3>发布检查</h3>
          </div>
          <el-tooltip content="刷新发布状态" placement="bottom">
            <el-button
              :icon="Refresh"
              circle
              size="small"
              :loading="loading"
              aria-label="刷新发布状态"
              @click="loadPublication()"
            />
          </el-tooltip>
        </header>

        <div v-if="readiness" class="readiness-summary">
          <span class="readiness-dot" :class="{ ready: readiness.ready }" />
          <strong>{{ readiness.ready ? '检查通过' : `${readiness.blocking_items.length} 项阻塞` }}</strong>
          <code>{{ shortId(readiness.authoring_fingerprint, 14) }}</code>
        </div>
        <el-skeleton v-else :rows="2" animated />

        <ul v-if="readiness && readiness.blocking_items.length > 0" class="blocker-list">
          <li v-for="(item, index) in readiness.blocking_items" :key="`${item.code}:${index}`">
            <div>
              <strong>{{ blockerLabel(item.code) }}</strong>
              <code>{{ item.code }}</code>
            </div>
            <p v-if="item.detail">{{ item.detail }}</p>
            <div v-if="item.story_path_id || item.path_chapter_id" class="blocker-refs">
              <span v-if="item.story_path_id">路径 {{ shortId(item.story_path_id, 12) }}</span>
              <span v-if="item.path_chapter_id">章节 {{ shortId(item.path_chapter_id, 12) }}</span>
            </div>
          </li>
        </ul>
      </section>

      <section v-if="activeRelease" class="panel-section">
        <header class="section-header">
          <div>
            <span>ACTIVE RELEASE</span>
            <h3>当前公开版本</h3>
          </div>
          <el-tag type="success" effect="plain">v{{ activeRelease.version }}</el-tag>
        </header>
        <dl class="release-facts">
          <div>
            <dt>Release</dt>
            <dd><code>{{ shortId(activeRelease.id, 16) }}</code></dd>
          </div>
          <div>
            <dt>发布时间</dt>
            <dd>{{ formatDate(activeRelease.published_at) }}</dd>
          </div>
          <div>
            <dt>Manifest</dt>
            <dd><code>{{ shortId(activeRelease.manifest_hash, 16) }}</code></dd>
          </div>
        </dl>
        <p v-if="activeRelease.release_notes" class="release-notes">
          {{ activeRelease.release_notes }}
        </p>
      </section>

      <section class="panel-section publish-section">
        <header class="section-header">
          <div>
            <span>PUBLISH</span>
            <h3>{{ activeRelease ? '发布新版本' : '首次发布' }}</h3>
          </div>
        </header>
        <el-input
          v-model="releaseNotes"
          type="textarea"
          :rows="3"
          maxlength="2000"
          show-word-limit
          resize="vertical"
          aria-label="发布说明"
          placeholder="发布说明（可选）"
        />
        <div class="publish-command">
          <span>{{ publishStatusText }}</span>
          <el-button
            type="primary"
            :icon="Promotion"
            :disabled="!canPublish"
            :loading="publishing"
            @click="publish"
          >
            {{ publishButtonLabel }}
          </el-button>
        </div>
      </section>

      <section class="panel-section history-section">
        <header class="section-header">
          <div>
            <span>HISTORY</span>
            <h3>Release 历史</h3>
          </div>
          <span class="history-count">{{ releases.length }}</span>
        </header>
        <el-empty v-if="releases.length === 0" description="尚无公开版本" :image-size="64" />
        <div v-else class="release-history">
          <article v-for="release in releases" :key="release.id" class="release-row">
            <div class="release-version">v{{ release.version }}</div>
            <div class="release-copy">
              <strong>{{ releaseStatusLabel(release.status) }}</strong>
              <span>{{ formatDate(release.published_at) }}</span>
            </div>
            <el-tag
              :type="releaseStatusType(release.status)"
              size="small"
              effect="plain"
            >
              {{ release.id === activeRelease?.id ? '当前' : releaseStatusLabel(release.status) }}
            </el-tag>
          </article>
        </div>
      </section>

      <section v-if="activeRelease" class="unpublish-section">
        <div>
          <strong>撤回公开版本</strong>
          <span>当前 Release 将立即停止公开读取</span>
        </div>
        <el-button
          type="danger"
          plain
          :icon="CircleClose"
          :disabled="publishing || loading"
          :loading="unpublishing"
          @click="unpublish"
        >
          撤回发布
        </el-button>
      </section>
    </div>
  </el-drawer>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  CircleCheck,
  CircleClose,
  Lock,
  Promotion,
  Refresh,
  WarningFilled,
} from '@element-plus/icons-vue'
import { createIdempotencyKey, storyPathApi } from '@/api/storyPathApi'
import type {
  ProjectRead,
  PublicationReadiness,
  Release,
  ReleaseStatus,
} from '@/api/storyPathTypes'
import { getSafeErrorSummary } from '@/security/safeError'
import { shortId } from '@/utils/storyPathWorkspace'

const props = defineProps<{
  modelValue: boolean
  project: ProjectRead | null
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  updated: [project: ProjectRead]
}>()

const readiness = ref<PublicationReadiness | null>(null)
const releases = ref<Release[]>([])
const releaseNotes = ref('')
const loading = ref(false)
const publishing = ref(false)
const unpublishing = ref(false)
let loadToken = 0
let pendingPublishIdentity = ''
let pendingPublishKey = ''

const activeRelease = computed(() => releases.value.find(
  (release) => release.id === props.project?.publication.active_release_id,
) ?? null)

const publicationLabel = computed(() => {
  if (props.project?.publication.state === 'published') return '已公开'
  if (props.project?.publication.state === 'changes_pending') return '有未发布修改'
  return '未发布'
})

const publicationDescription = computed(() => {
  if (props.project?.publication.state === 'published') return '公开内容与当前审阅版本一致'
  if (props.project?.publication.state === 'changes_pending') return '当前公开版本继续可读，新修改尚未发布'
  return '当前项目没有可公开读取的活动版本'
})

const publicationTagType = computed<'success' | 'warning' | 'info'>(() => {
  if (props.project?.publication.state === 'published') return 'success'
  if (props.project?.publication.state === 'changes_pending') return 'warning'
  return 'info'
})

const canPublish = computed(() => Boolean(
  readiness.value?.ready
  && props.project?.authoring.has_unpublished_changes
  && props.project.authoring.running_task_count === 0
  && !publishing.value
  && !unpublishing.value,
))

const publishButtonLabel = computed(() => {
  if (!props.project?.authoring.has_unpublished_changes) return '当前内容已发布'
  return activeRelease.value ? '发布新版本' : '发布'
})

const publishStatusText = computed(() => {
  if ((props.project?.authoring.running_task_count || 0) > 0) return '等待创作任务完成'
  if (!props.project?.authoring.has_unpublished_changes) return '无需创建新 Release'
  if (!readiness.value?.ready) return '发布检查未通过'
  return '将当前审阅版本冻结为新的公开 Release'
})

function blockerLabel(code: string): string {
  if (code.startsWith('bible.')) return '故事设定未就绪'
  if (code.startsWith('story_path.') || code.startsWith('path.')) return '剧情路径未就绪'
  if (code.startsWith('outline.')) return '章节大纲未就绪'
  if (code.startsWith('chapter.')) return '章节正文未就绪'
  if (code.startsWith('script.') || code.startsWith('resource.')) return '章节脚本或资源未就绪'
  if (code.startsWith('vngraph.') || code.startsWith('vn_graph.')) return 'VNGraph 未就绪'
  return '发布条件未满足'
}

function releaseStatusLabel(status: ReleaseStatus): string {
  return {
    published: '已公开',
    superseded: '已取代',
    withdrawn: '已撤回',
  }[status]
}

function releaseStatusType(status: ReleaseStatus): 'success' | 'info' | 'warning' {
  if (status === 'published') return 'success'
  if (status === 'withdrawn') return 'warning'
  return 'info'
}

function formatDate(value: string): string {
  return new Date(value).toLocaleString('zh-CN')
}

function reportPublicationError(context: string, error: unknown, fallback: string): void {
  const summary = getSafeErrorSummary(error)
  console.error(context, summary)
  if (summary.status === 409) ElMessage.warning('创作或发布状态已变化，请重新检查')
  else if (summary.status === 422) ElMessage.warning('当前审阅内容尚未达到发布条件')
  else ElMessage.error(fallback)
}

async function loadPublication(showError = true): Promise<void> {
  const projectId = props.project?.id
  if (!projectId) return
  const token = ++loadToken
  loading.value = true
  try {
    const [projectResponse, readinessResponse, releasesResponse] = await Promise.all([
      storyPathApi.projects.get(projectId),
      storyPathApi.projects.readiness(projectId),
      storyPathApi.releases.list(projectId),
    ])
    if (token !== loadToken) return
    readiness.value = readinessResponse.data
    releases.value = releasesResponse.data
    emit('updated', projectResponse.data)
  } catch (error) {
    if (token === loadToken && showError) {
      reportPublicationError('load publication panel', error, '发布状态加载失败')
    }
  } finally {
    if (token === loadToken) loading.value = false
  }
}

async function publish(): Promise<void> {
  const projectId = props.project?.id
  const fingerprint = readiness.value?.authoring_fingerprint
  if (!projectId || !fingerprint || !canPublish.value) return
  const replacingActiveRelease = Boolean(activeRelease.value)
  const notes = releaseNotes.value.trim()
  const commandIdentity = `${projectId}:${fingerprint}:${notes}`
  if (commandIdentity !== pendingPublishIdentity || !pendingPublishKey) {
    pendingPublishIdentity = commandIdentity
    pendingPublishKey = createIdempotencyKey(`publish:${projectId}`)
  }
  publishing.value = true
  try {
    await storyPathApi.releases.publish(
      projectId,
      {
        expected_authoring_fingerprint: fingerprint,
        release_notes: notes || null,
      },
      pendingPublishKey,
    )
    pendingPublishIdentity = ''
    pendingPublishKey = ''
    releaseNotes.value = ''
    await loadPublication(false)
    ElMessage.success(replacingActiveRelease ? '新公开版本已生效' : '项目已发布')
  } catch (error) {
    if (getSafeErrorSummary(error).status !== undefined) {
      pendingPublishIdentity = ''
      pendingPublishKey = ''
    }
    reportPublicationError('publish project', error, '发布失败')
    await loadPublication(false)
  } finally {
    publishing.value = false
  }
}

async function unpublish(): Promise<void> {
  const project = props.project
  if (!project?.publication.active_release_id) return
  try {
    await ElMessageBox.confirm(
      '撤回后当前 Release 将立即停止公开读取。',
      '撤回发布',
      { type: 'warning', confirmButtonText: '撤回', cancelButtonText: '取消' },
    )
  } catch {
    return
  }

  unpublishing.value = true
  try {
    await storyPathApi.releases.unpublish(project.id, project.publication.lock_version)
    await loadPublication(false)
    ElMessage.success('公开版本已撤回')
  } catch (error) {
    reportPublicationError('unpublish project', error, '撤回失败')
    await loadPublication(false)
  } finally {
    unpublishing.value = false
  }
}

watch(
  () => props.modelValue,
  (visible) => {
    if (visible && props.project?.id) void loadPublication()
  },
  { immediate: true },
)

watch(
  () => props.project?.id,
  (projectId, previousId) => {
    if (props.modelValue && projectId && projectId !== previousId) void loadPublication()
  },
)
</script>

<style scoped>
.drawer-heading,
.section-header,
.publish-command,
.unpublish-section,
.release-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.drawer-heading {
  width: 100%;
  padding-right: 12px;
}

.drawer-heading span,
.section-header span {
  display: block;
  margin-bottom: 2px;
  color: #7c8691;
  font-size: 10px;
}

.drawer-heading h2,
.section-header h3 {
  margin: 0;
  color: #222930;
  letter-spacing: 0;
}

.drawer-heading h2 {
  font-size: 18px;
}

.section-header h3 {
  font-size: 14px;
}

.publication-panel {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding-bottom: 24px;
}

.publication-state {
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr);
  align-items: center;
  gap: 10px;
  padding: 14px;
  border: 1px solid #d8dee5;
  border-radius: 6px;
  background: #f6f8fa;
}

.publication-state > .el-icon {
  font-size: 24px;
}

.publication-state strong {
  color: #283039;
  font-size: 14px;
}

.publication-state p {
  margin: 3px 0 0;
  color: #65707c;
  font-size: 12px;
  line-height: 1.5;
}

.publication-state.state-published {
  border-color: #b9d8c5;
  background: #edf7f1;
  color: #28704d;
}

.publication-state.state-changes_pending {
  border-color: #e2cfaa;
  background: #fbf6e9;
  color: #976818;
}

.panel-section {
  border: 1px solid #dfe3e8;
  border-radius: 6px;
  background: #fff;
}

.section-header {
  min-height: 54px;
  padding: 10px 12px;
  border-bottom: 1px solid #e7eaee;
}

.readiness-summary {
  display: grid;
  grid-template-columns: 10px minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px;
  padding: 13px 12px;
  color: #3f4852;
  font-size: 12px;
}

.readiness-summary code,
.release-facts code {
  color: #737d88;
  font-size: 11px;
}

.readiness-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #c55252;
}

.readiness-dot.ready {
  background: #2f855a;
}

.blocker-list {
  margin: 0;
  padding: 0 12px 12px;
  list-style: none;
}

.blocker-list li {
  padding: 10px 0;
  border-top: 1px solid #edf0f2;
}

.blocker-list li > div:first-child {
  display: flex;
  justify-content: space-between;
  gap: 10px;
}

.blocker-list strong {
  color: #3e4650;
  font-size: 12px;
}

.blocker-list code,
.blocker-list p,
.blocker-refs {
  color: #7a8490;
  font-size: 11px;
}

.blocker-list p {
  margin: 5px 0 0;
  line-height: 1.5;
}

.blocker-refs {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 5px;
}

.release-facts {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin: 0;
  padding: 12px;
}

.release-facts dt {
  margin-bottom: 4px;
  color: #89929c;
  font-size: 10px;
}

.release-facts dd {
  margin: 0;
  overflow: hidden;
  color: #3d454f;
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.release-notes {
  margin: 0;
  padding: 0 12px 12px;
  color: #59636e;
  font-size: 12px;
  line-height: 1.55;
  overflow-wrap: anywhere;
}

.publish-section > .el-textarea {
  padding: 12px 12px 0;
}

.publish-command {
  padding: 10px 12px 12px;
}

.publish-command > span {
  color: #79838e;
  font-size: 11px;
}

.history-count {
  color: #6d7782;
  font-size: 12px;
}

.release-history {
  padding: 6px 12px;
}

.release-row {
  min-height: 52px;
  border-bottom: 1px solid #edf0f2;
}

.release-row:last-child {
  border-bottom: 0;
}

.release-version {
  width: 34px;
  color: #27313b;
  font-size: 13px;
  font-weight: 700;
}

.release-copy {
  display: flex;
  min-width: 0;
  flex: 1;
  flex-direction: column;
  gap: 2px;
}

.release-copy strong {
  color: #3a434d;
  font-size: 12px;
}

.release-copy span {
  color: #89929c;
  font-size: 10px;
}

.unpublish-section {
  padding: 12px 0 0;
  border-top: 1px solid #dfe3e8;
}

.unpublish-section > div {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.unpublish-section strong {
  color: #454d56;
  font-size: 12px;
}

.unpublish-section span {
  color: #89929c;
  font-size: 10px;
}

@media (max-width: 520px) {
  :global(.publication-drawer.el-drawer) {
    width: 100% !important;
    max-width: 100vw;
  }

  .release-facts {
    grid-template-columns: 1fr;
  }

  .publish-command,
  .unpublish-section {
    align-items: stretch;
    flex-direction: column;
  }

  .publish-command .el-button,
  .unpublish-section .el-button {
    width: 100%;
  }
}
</style>
