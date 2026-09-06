<template>
  <div class="draft-card" :class="{ 'draft-card-drafting': drafting }">
    <div class="draft-card-header">
      <div class="draft-card-title">
        <span v-if="kicker" class="draft-card-kicker">{{ kicker }}</span>
        <strong>{{ title }}</strong>
        <el-tag v-if="drafting" type="warning" size="small" effect="plain">草稿</el-tag>
        <el-tag v-if="drafting && memoryFallback" type="danger" size="small" effect="plain">
          未能持久化
        </el-tag>
      </div>
      <div class="draft-card-tools">
        <span v-if="drafting" class="draft-save-state" :class="`save-${saveState}`">
          {{ saveLabel }}
        </span>
        <slot name="actions" />
      </div>
    </div>

    <el-alert
      v-if="memoryFallback && drafting"
      title="浏览器存储已满，草稿暂存内存，刷新页面将丢失——请尽早物化发布或清理草稿"
      type="error"
      :closable="false"
      class="draft-banner"
    />
    <el-alert
      v-if="staleMessage"
      :title="staleMessage"
      type="warning"
      :closable="false"
      class="draft-banner"
    />
    <div v-if="suggestion" class="draft-suggestion">
      <span class="draft-suggestion-copy">
        AI 产出了新结果 <strong>r{{ suggestion.revisionNo }}</strong>，尚未采用
      </span>
      <div class="draft-suggestion-actions">
        <el-button size="small" text type="primary" @click="$emit('preview-suggestion')">
          预览
        </el-button>
        <el-button size="small" text type="success" @click="$emit('adopt-suggestion')">
          采用进草稿
        </el-button>
        <el-button size="small" text type="info" @click="$emit('dismiss-suggestion')">
          丢弃
        </el-button>
      </div>
    </div>

    <div class="draft-card-body">
      <slot />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

export interface DraftSuggestionInfo {
  revisionId: string
  revisionNo: number
}

const props = defineProps<{
  title: string
  kicker?: string
  /** 是否处于草稿编辑状态（有本地草稿）。 */
  drafting: boolean
  /** 有未保存改动。 */
  dirty?: boolean
  saving?: boolean
  /** 最近一次保存时间（ISO），用于状态展示。 */
  savedAt?: string | null
  staleMessage?: string | null
  memoryFallback?: boolean
  suggestion?: DraftSuggestionInfo | null
}>()

defineEmits<{
  (event: 'preview-suggestion'): void
  (event: 'adopt-suggestion'): void
  (event: 'dismiss-suggestion'): void
}>()

const saveState = computed(() => {
  if (props.saving) return 'saving'
  if (props.dirty) return 'dirty'
  return 'saved'
})

const saveLabel = computed(() => {
  if (props.saving) return '保存中…'
  if (props.dirty) return '有未保存改动'
  if (props.savedAt) {
    const time = new Date(props.savedAt)
    if (!Number.isNaN(time.getTime())) {
      return `已本地保存 ${time.toLocaleTimeString('zh-CN', { hour12: false })}`
    }
  }
  return '已本地保存'
})
</script>

<style scoped>
.draft-card {
  border: 1px solid #dfe3e8;
  border-radius: 6px;
  background: #fbfcfd;
}

.draft-card-drafting {
  border-color: #d8c48f;
  background: #fffdf6;
}

.draft-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 42px;
  padding: 7px 12px;
  border-bottom: 1px solid #e4e8ec;
}

.draft-card-title {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 8px;
  color: #333d47;
  font-size: 13px;
}

.draft-card-kicker {
  color: #7b8490;
  font-size: 11px;
}

.draft-card-tools {
  display: flex;
  align-items: center;
  gap: 8px;
}

.draft-save-state {
  color: #77818c;
  font-size: 11px;
  white-space: nowrap;
}

.save-dirty {
  color: #b88230;
}

.save-saving {
  color: #5b8db8;
}

.draft-banner {
  margin: 10px 12px 0;
}

.draft-suggestion {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin: 10px 12px 0;
  padding: 6px 10px;
  border: 1px solid #c4dcf0;
  border-radius: 5px;
  background: #eef6fc;
  font-size: 12px;
}

.draft-suggestion-copy {
  color: #35566f;
}

.draft-suggestion-copy strong {
  color: #1f4e73;
}

.draft-suggestion-actions {
  display: flex;
  flex: 0 0 auto;
  gap: 2px;
}

.draft-card-body {
  min-width: 0;
}

@media (max-width: 640px) {
  .draft-card-header,
  .draft-suggestion {
    flex-wrap: wrap;
  }
}
</style>
