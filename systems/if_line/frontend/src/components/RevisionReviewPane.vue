<template>
  <aside class="revision-pane" aria-label="版本审阅">
    <header class="revision-header">
      <div>
        <span class="revision-eyebrow">版本审阅</span>
        <h2>{{ title }}</h2>
      </div>
      <el-tooltip content="刷新版本" placement="bottom">
        <el-button
          :icon="Refresh"
          circle
          size="small"
          :loading="loading"
          aria-label="刷新版本"
          @click="$emit('refresh')"
        />
      </el-tooltip>
    </header>

    <div class="head-strip">
      <span>当前版本</span>
      <strong>{{ headLabel }}</strong>
    </div>

    <div v-if="loading && revisions.length === 0" class="revision-loading">
      <el-skeleton :rows="5" animated />
    </div>
    <el-empty
      v-else-if="revisions.length === 0"
      :description="emptyLabel"
      :image-size="72"
      class="revision-empty"
    />
    <div v-else class="revision-list" role="listbox" :aria-label="`${title}版本`">
      <button
        v-for="revision in revisions"
        :key="revision.id"
        type="button"
        class="revision-row"
        :class="{
          selected: revision.id === selectedId,
          active: revision.id === headId,
        }"
        :aria-selected="revision.id === selectedId"
        @click="$emit('select', revision.id)"
      >
        <span class="revision-copy">
          <strong>{{ revision.label }}</strong>
          <small>{{ revision.detail }}</small>
        </span>
        <el-tag v-if="revision.id === headId" size="small" type="success" effect="plain">
          当前
        </el-tag>
      </button>
    </div>

    <footer class="revision-actions">
      <el-button
        type="primary"
        :icon="Select"
        :disabled="!canActivate"
        :loading="activating"
        @click="$emit('activate')"
      >
        设为当前版本
      </el-button>
    </footer>
  </aside>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { Refresh, Select } from '@element-plus/icons-vue'
import type { RevisionOption } from '@/utils/storyPathWorkspace'

const props = withDefaults(defineProps<{
  title: string
  revisions: RevisionOption[]
  selectedId: string
  headId: string | null
  loading?: boolean
  activating?: boolean
  disabled?: boolean
  emptyLabel?: string
}>(), {
  loading: false,
  activating: false,
  disabled: false,
  emptyLabel: '暂无可审阅版本',
})

defineEmits<{
  select: [revisionId: string]
  activate: []
  refresh: []
}>()

const selected = computed(() => props.revisions.find((item) => item.id === props.headId))
const headLabel = computed(() => selected.value?.label ?? '尚未选择')
const canActivate = computed(() => (
  !props.disabled && Boolean(props.selectedId) && props.selectedId !== props.headId
))
</script>

<style scoped>
.revision-pane {
  display: flex;
  min-width: 0;
  min-height: 0;
  flex-direction: column;
  border-left: 1px solid #dfe3e8;
  background: #f7f8fa;
}

.revision-header {
  display: flex;
  min-height: 72px;
  align-items: center;
  justify-content: space-between;
  padding: 12px 14px;
  border-bottom: 1px solid #dfe3e8;
}

.revision-eyebrow {
  display: block;
  margin-bottom: 2px;
  color: #7b8490;
  font-size: 11px;
}

.revision-header h2 {
  margin: 0;
  color: #20262e;
  font-size: 15px;
  font-weight: 650;
}

.head-strip {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-height: 44px;
  padding: 9px 14px;
  border-bottom: 1px solid #dfe3e8;
  background: #fff;
  color: #6c7580;
  font-size: 12px;
}

.head-strip strong {
  overflow: hidden;
  color: #28704d;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.revision-loading {
  padding: 18px 14px;
}

.revision-empty {
  flex: 1;
}

.revision-list {
  min-height: 0;
  flex: 1;
  overflow: auto;
  padding: 8px;
}

.revision-row {
  display: flex;
  width: 100%;
  min-height: 54px;
  align-items: center;
  gap: 8px;
  margin-bottom: 5px;
  padding: 9px 10px;
  border: 1px solid transparent;
  border-radius: 5px;
  background: transparent;
  color: #2e353d;
  cursor: pointer;
  font: inherit;
  text-align: left;
}

.revision-row:hover {
  background: #edf1f5;
}

.revision-row.selected {
  border-color: #95b9d8;
  background: #e8f1f8;
}

.revision-row.active:not(.selected) {
  background: #edf7f1;
}

.revision-copy {
  display: flex;
  min-width: 0;
  flex: 1;
  flex-direction: column;
  gap: 3px;
}

.revision-copy strong,
.revision-copy small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.revision-copy strong {
  font-size: 13px;
  font-weight: 600;
}

.revision-copy small {
  color: #7b8490;
  font-size: 11px;
}

.revision-actions {
  padding: 12px 14px;
  border-top: 1px solid #dfe3e8;
  background: #fff;
}

.revision-actions .el-button {
  width: 100%;
}

@media (max-width: 1180px) {
  .revision-pane {
    min-height: 300px;
    border-top: 1px solid #dfe3e8;
    border-left: 0;
  }
}
</style>
