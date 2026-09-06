<template>
  <section class="path-tree-pane" aria-label="剧情路径">
    <header class="pane-header">
      <div>
        <h2>剧情路径</h2>
        <span class="pane-count">{{ paths.length }}</span>
      </div>
      <el-tooltip content="刷新路径" placement="bottom">
        <el-button
          :icon="Refresh"
          circle
          size="small"
          :loading="loading"
          aria-label="刷新路径"
          @click="$emit('refresh')"
        />
      </el-tooltip>
    </header>

    <div v-if="loading && paths.length === 0" class="tree-loading">
      <el-skeleton :rows="5" animated />
    </div>
    <el-empty v-else-if="tree.length === 0" description="暂无剧情路径" :image-size="72" />
    <el-tree
      v-else
      ref="treeRef"
      class="path-tree"
      :data="tree"
      node-key="id"
      :props="treeProps"
      :current-node-key="selectedId"
      default-expand-all
      highlight-current
      :expand-on-click-node="false"
      @node-click="onNodeClick"
    >
      <template #default="{ data }">
        <div class="path-node" :class="{ archived: data.status === 'archived' }">
          <el-icon class="path-icon">
            <Guide v-if="data.parent_path_id" />
            <Flag v-else />
          </el-icon>
          <span class="path-title">{{ data.title }}</span>
          <el-tag v-if="!data.parent_path_id" size="small" effect="plain">主线</el-tag>
          <el-tag v-else-if="data.status === 'archived'" size="small" type="info" effect="plain">
            已归档
          </el-tag>
        </div>
      </template>
    </el-tree>

    <footer v-if="selectedPath" class="path-footer">
      <dl class="path-meta">
        <div>
          <dt>状态</dt>
          <dd>{{ selectedPath.status === 'active' ? '创作中' : '已归档' }}</dd>
        </div>
        <div v-if="selectedPath.parent_path_id">
          <dt>分叉点</dt>
          <dd>{{ shortId(selectedPath.fork_checkpoint_node_id) }}</dd>
        </div>
      </dl>
      <div v-if="selectedPath.parent_path_id" class="path-actions">
        <el-button
          :icon="Share"
          size="small"
          :disabled="!selectedPath.fork_checkpoint_node_id"
          @click="$emit('open-checkpoint', selectedPath)"
        >
          同点分支
        </el-button>
        <el-button
          :icon="selectedPath.status === 'active' ? FolderDelete : RefreshLeft"
          size="small"
          :loading="updating"
          @click="$emit('toggle-status', selectedPath)"
        >
          {{ selectedPath.status === 'active' ? '归档路径' : '恢复路径' }}
        </el-button>
      </div>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { ElTree } from 'element-plus'
import { Flag, FolderDelete, Guide, Refresh, RefreshLeft, Share } from '@element-plus/icons-vue'
import type { StoryPath } from '@/api/storyPathTypes'
import { buildStoryPathTree, shortId, type StoryPathTreeNode } from '@/utils/storyPathWorkspace'

const props = defineProps<{
  paths: StoryPath[]
  selectedId: string
  loading?: boolean
  updating?: boolean
}>()

const emit = defineEmits<{
  select: [path: StoryPath]
  refresh: []
  'toggle-status': [path: StoryPath]
  'open-checkpoint': [path: StoryPath]
}>()

const treeRef = ref<InstanceType<typeof ElTree>>()
const treeProps = { children: 'children', label: 'title' }
const tree = computed(() => buildStoryPathTree(props.paths))
const selectedPath = computed(() => props.paths.find((path) => path.id === props.selectedId))

function onNodeClick(node: StoryPathTreeNode): void {
  emit('select', node)
}

watch(
  () => props.selectedId,
  (selectedId) => treeRef.value?.setCurrentKey(selectedId || undefined),
  { flush: 'post' },
)
</script>

<style scoped>
.path-tree-pane {
  display: flex;
  min-width: 0;
  min-height: 0;
  flex-direction: column;
  border-right: 1px solid #dfe3e8;
  background: #f7f8fa;
}

.pane-header {
  display: flex;
  min-height: 58px;
  align-items: center;
  justify-content: space-between;
  padding: 12px 14px;
  border-bottom: 1px solid #dfe3e8;
}

.pane-header > div {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.pane-header h2 {
  margin: 0;
  color: #20262e;
  font-size: 15px;
  font-weight: 650;
}

.pane-count {
  color: #7b8490;
  font-size: 12px;
}

.tree-loading {
  padding: 18px 14px;
}

.path-tree {
  flex: 1;
  overflow: auto;
  padding: 8px;
  background: transparent;
}

.path-tree :deep(.el-tree-node__content) {
  min-height: 38px;
  height: auto;
  margin-bottom: 2px;
  border-radius: 5px;
}

.path-tree :deep(.el-tree-node__content:hover) {
  background: #edf1f5;
}

.path-tree :deep(.is-current > .el-tree-node__content) {
  background: #e3edf8;
  color: #175a8f;
}

.path-node {
  display: flex;
  min-width: 0;
  flex: 1;
  align-items: center;
  gap: 7px;
  padding: 5px 5px 5px 0;
}

.path-node.archived {
  color: #8a929c;
}

.path-icon {
  flex: 0 0 auto;
}

.path-title {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.path-footer {
  padding: 12px 14px 14px;
  border-top: 1px solid #dfe3e8;
  background: #fff;
}

.path-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.path-actions .el-button + .el-button {
  margin-left: 0;
}

.path-meta {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin: 0 0 12px;
}

.path-meta div {
  min-width: 0;
}

.path-meta dt {
  margin-bottom: 3px;
  color: #8a929c;
  font-size: 11px;
}

.path-meta dd {
  margin: 0;
  overflow: hidden;
  color: #343b44;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 980px) {
  .path-tree-pane {
    min-height: 260px;
    border-right: 0;
    border-bottom: 1px solid #dfe3e8;
  }
}
</style>
