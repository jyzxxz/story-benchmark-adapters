<!--
  VNGraphPlayerView.vue — VNGraph 播放器独立 demo 页（免登录）

  三种加载方式：
    1. 本地样例下拉（src/data/vnGraphSamples.ts，编译前的 res:// 产物）
    2. 公开 API 拉取（releaseId + pathChapterId → storyPathApi.public.getPathChapterVNGraph）
    3. 文本粘贴 JSON

  resBaseUrl 可选：把样例里的 res:// 映射到本地静态目录；从 API 拉到的图已是真实 URL，无需配置。
-->
<template>
  <div class="vng-page">
    <div class="vng-container">
      <header class="vng-header">
        <h1>VNGraph 播放器</h1>
        <p class="vng-subtitle">
          加载 VNNodeLibrary.txt 定义的剧情流 JSON，逐句播放对白 / 背景 / 立绘 / 选项。
        </p>
      </header>

      <!-- 加载控制面板 -->
      <section class="vng-panel">
        <div class="vng-panel-row">
          <label class="vng-field">
            <span class="vng-label">加载方式</span>
            <el-select v-model="inputMode" size="small" style="width: 200px">
              <el-option label="本地样例" value="sample" />
              <el-option label="公开 API 拉取" value="api" />
              <el-option label="粘贴 JSON" value="paste" />
            </el-select>
          </label>

          <label class="vng-field">
            <span class="vng-label">res:// 资源根目录（可选）</span>
            <el-input
              v-model="resBaseUrl"
              size="small"
              style="width: 280px"
              placeholder="如 https://cdn.example.com/assets"
            />
          </label>
        </div>

        <!-- 本地样例 -->
        <div v-if="inputMode === 'sample'" class="vng-panel-row">
          <label class="vng-field">
            <span class="vng-label">选择样例</span>
            <el-select v-model="selectedSampleKey" size="small" style="width: 360px">
              <el-option
                v-for="s in samples"
                :key="s.key"
                :label="`${s.label}（${s.description}）`"
                :value="s.key"
              />
            </el-select>
          </label>
          <el-button type="primary" size="small" @click="loadSample">加载样例</el-button>
        </div>

        <!-- 公开 API -->
        <div v-else-if="inputMode === 'api'" class="vng-panel-row">
          <label class="vng-field">
            <span class="vng-label">Release ID</span>
            <el-input v-model="releaseId" size="small" style="width: 280px" placeholder="UUID" />
          </label>
          <label class="vng-field">
            <span class="vng-label">Path Chapter ID</span>
            <el-input v-model="pathChapterId" size="small" style="width: 280px" placeholder="UUID" />
          </label>
          <el-button
            type="primary"
            size="small"
            :loading="apiLoading"
            :disabled="!releaseId || !pathChapterId"
            @click="loadFromApi"
          >拉取</el-button>
          <span v-if="apiError" class="vng-error">{{ apiError }}</span>
        </div>

        <!-- 粘贴 JSON -->
        <div v-else class="vng-panel-row vng-paste-row">
          <el-input
            v-model="pastedJson"
            type="textarea"
            :rows="5"
            placeholder='粘贴完整 VNGraph JSON，例如 {"Version":1,"StartNodeIndex":1,"Nodes":[...]}'
          />
          <el-button type="primary" size="small" @click="loadPasted">解析并加载</el-button>
          <span v-if="pasteError" class="vng-error">{{ pasteError }}</span>
        </div>

        <div v-if="parseError" class="vng-panel-row">
          <span class="vng-error">{{ parseError }}</span>
        </div>
      </section>

      <!-- 当前图元信息 -->
      <section v-if="currentGraph" class="vng-meta">
        <span>节点数：{{ currentGraph.Nodes.length }}</span>
        <span>起始节点：{{ currentGraph.StartNodeIndex }}</span>
        <span v-if="currentSource">来源：{{ currentSource }}</span>
      </section>

      <!-- 播放器 -->
      <section v-if="currentGraph" class="vng-stage-wrap">
        <VNGraphPlayer
          :graph="currentGraph"
          :res-base-url="resBaseUrl"
          @ended="onEnded"
        />
      </section>
      <el-empty v-else description="选择样例、拉取或粘贴一张 VNGraph 后开始播放" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import VNGraphPlayer from '@/components/VNGraphPlayer.vue'
import { vnGraphSamples } from '@/data/vnGraphSamples'
import type { VNGraph } from '@/data/threeKingdoms'
import { storyPathApi } from '@/api/storyPathApi'

const inputMode = ref<'sample' | 'api' | 'paste'>('sample')
const resBaseUrl = ref('')

// 样例
const samples = vnGraphSamples
const selectedSampleKey = ref(samples[0]?.key ?? '')
const currentGraph = ref<VNGraph | null>(null)
const currentSource = ref('')
const parseError = ref('')

// API
const releaseId = ref('')
const pathChapterId = ref('')
const apiLoading = ref(false)
const apiError = ref('')

// 粘贴
const pastedJson = ref('')
const pasteError = ref('')

function clearErrors() {
  parseError.value = ''
  apiError.value = ''
  pasteError.value = ''
}

function loadSample() {
  clearErrors()
  const s = samples.find((x) => x.key === selectedSampleKey.value)
  if (!s) {
    parseError.value = '未找到该样例'
    return
  }
  currentGraph.value = s.graph
  currentSource.value = `本地样例：${s.label}`
  ElMessage.success(`已加载样例：${s.label}`)
}

async function loadFromApi() {
  clearErrors()
  if (!releaseId.value || !pathChapterId.value) {
    apiError.value = '请填写 Release ID 和 Path Chapter ID'
    return
  }
  apiLoading.value = true
  try {
    const resp = await storyPathApi.public.getPathChapterVNGraph(releaseId.value, pathChapterId.value)
    // 304 视为无新数据（这里没存 etag，简单提示）
    if (resp.status === 304) {
      apiError.value = '资源未变更（304），请刷新重试'
      return
    }
    const graph = resp.data as unknown as VNGraph
    if (!graph || !Array.isArray(graph.Nodes)) {
      apiError.value = '返回数据不是有效的 VNGraph（缺少 Nodes 数组）'
      return
    }
    currentGraph.value = graph
    currentSource.value = `公开 API：release ${releaseId.value.slice(0, 8)}… / chapter ${pathChapterId.value.slice(0, 8)}…`
    ElMessage.success('已从公开 API 加载')
  } catch (e: unknown) {
    const err = e as { response?: { status?: number; data?: unknown } }
    const status = err.response?.status
    apiError.value = `拉取失败${status ? `（HTTP ${status}）` : ''}：${String(e)}`
  } finally {
    apiLoading.value = false
  }
}

function loadPasted() {
  clearErrors()
  if (!pastedJson.value.trim()) {
    pasteError.value = '请粘贴 JSON 文本'
    return
  }
  try {
    const data = JSON.parse(pastedJson.value) as unknown
    if (!data || typeof data !== 'object' || !Array.isArray((data as VNGraph).Nodes)) {
      pasteError.value = 'JSON 缺少 Nodes 数组，不是有效的 VNGraph'
      return
    }
    currentGraph.value = data as VNGraph
    currentSource.value = '粘贴的 JSON'
    ElMessage.success('已解析并加载')
  } catch (e) {
    pasteError.value = `JSON 解析失败：${e instanceof Error ? e.message : String(e)}`
  }
}

function onEnded() {
  ElMessage.info('播放已到达结尾')
}

// 切换加载方式时清错
watch(inputMode, () => clearErrors())

// 支持 ?sample=<key> 直接加载指定样例（便于分享链接与自动化验证）
const route = useRoute()
const querySample = typeof route.query.sample === 'string' ? route.query.sample : ''
if (querySample && samples.some((s) => s.key === querySample)) {
  selectedSampleKey.value = querySample
}

// 公开作品深链：/vn-graph-player?releaseId=..&pathChapterId=.. 直接拉取公开图播放
const queryReleaseId = typeof route.query.releaseId === 'string' ? route.query.releaseId : ''
const queryPathChapterId = typeof route.query.pathChapterId === 'string' ? route.query.pathChapterId : ''
if (queryReleaseId && queryPathChapterId) {
  inputMode.value = 'api'
  releaseId.value = queryReleaseId
  pathChapterId.value = queryPathChapterId
  void loadFromApi()
} else {
  // 初始自动加载（首个样例或 query 指定的样例），便于直接看到效果
  loadSample()
}
</script>

<style scoped>
.vng-page {
  min-height: 100vh;
  background: #f5f7fa;
  padding: 24px 0 48px;
}
.vng-container {
  max-width: 1100px;
  margin: 0 auto;
  padding: 0 24px;
}
.vng-header {
  margin-bottom: 20px;
}
.vng-header h1 {
  font-size: 22px;
  font-weight: 600;
  color: #2c3e50;
  margin: 0 0 6px;
}
.vng-subtitle {
  font-size: 13px;
  color: #606266;
  margin: 0;
  line-height: 1.5;
}
.vng-panel {
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  padding: 16px 20px;
  margin-bottom: 16px;
}
.vng-panel-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}
.vng-panel-row:last-child {
  margin-bottom: 0;
}
.vng-paste-row {
  align-items: flex-start;
}
.vng-field {
  display: inline-flex;
  flex-direction: column;
  gap: 4px;
}
.vng-label {
  font-size: 12px;
  color: #909399;
}
.vng-error {
  font-size: 12px;
  color: #f56c6c;
}
.vng-meta {
  display: flex;
  gap: 16px;
  font-size: 12px;
  color: #909399;
  margin-bottom: 12px;
  padding: 0 4px;
}
.vng-stage-wrap {
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  overflow: hidden;
}
@media (max-width: 768px) {
  .vng-container { padding: 0 12px; }
}
</style>
