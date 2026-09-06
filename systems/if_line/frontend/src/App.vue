<template>
  <div class="app-container">
    <header class="topbar">
      <div class="topbar-inner">
        <router-link to="/" class="brand">AI 小说 Agent</router-link>
        <div class="topbar-right">
          <router-link to="/vn-graph-player" class="topbar-link">VNGraph 播放器</router-link>
          <template v-if="userStore.loaded && userStore.isLoggedIn">
            <el-dropdown trigger="click" @command="onUserCommand">
              <span class="user-chip">
                <el-avatar :size="26" class="user-avatar">{{ avatarText }}</el-avatar>
                <span class="user-name">{{ userStore.currentUser?.display_name || userStore.currentUser?.email }}</span>
                <el-icon><ArrowDown /></el-icon>
              </span>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="projects">我的项目</el-dropdown-item>
                  <el-dropdown-item command="byok" :icon="Key">
                    文本模型 API Key{{ byokEnabled ? '（已启用）' : '' }}
                  </el-dropdown-item>
                  <el-dropdown-item command="logout" divided>退出登录</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </template>
          <template v-else-if="userStore.loaded && !userStore.isLoggedIn">
            <el-button type="primary" size="small" @click="goLogin">登录 / 注册</el-button>
          </template>
          <template v-else>
            <span class="loading-chip">…</span>
          </template>
        </div>
      </div>
    </header>

    <main class="app-main">
      <router-view />
    </main>

    <ByokSettingsDialog
      v-model="byokDialogVisible"
      @change="onByokStatusChange"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowDown, Key } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useUserStore } from '@/stores/user'
import ByokSettingsDialog from '@/components/ByokSettingsDialog.vue'
import { getByokStatus, type ByokStatus } from '@/security/byok'

const router = useRouter()
const userStore = useUserStore()
const byokDialogVisible = ref(false)
const byokEnabled = ref(getByokStatus().enabled)

const avatarText = computed(() => {
  const name = userStore.currentUser?.display_name || userStore.currentUser?.email || '?'
  return name.charAt(0).toUpperCase()
})

onMounted(() => {
  if (!userStore.loaded) {
    userStore.fetchMe()
  }
})

const goLogin = () => router.push('/login')

const onByokStatusChange = (status: ByokStatus) => {
  byokEnabled.value = status.enabled
}

watch(
  () => userStore.isLoggedIn,
  (isLoggedIn) => {
    byokEnabled.value = isLoggedIn ? getByokStatus().enabled : false
    if (!isLoggedIn) byokDialogVisible.value = false
  }
)

const onUserCommand = async (command: string) => {
  if (command === 'logout') {
    await userStore.logout()
    byokEnabled.value = false
    ElMessage.success('已退出登录')
    router.push('/')
  } else if (command === 'projects') {
    router.push('/')
  } else if (command === 'byok') {
    byokEnabled.value = getByokStatus().enabled
    byokDialogVisible.value = true
  }
}
</script>

<style>
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif;
  background: #eef1f4;
  min-height: 100vh;
}

.app-container {
  min-height: 100vh;
  background: #f5f7fa;
  display: flex;
  flex-direction: column;
}

.topbar {
  background: #fff;
  border-bottom: 1px solid #ebeef5;
  position: sticky;
  top: 0;
  z-index: 100;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
}

.topbar-inner {
  max-width: 1280px;
  margin: 0 auto;
  padding: 0 24px;
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.brand {
  font-weight: 600;
  color: #2c3e50;
  text-decoration: none;
  font-size: 16px;
}

.brand:hover {
  color: #667eea;
}

.topbar-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.topbar-link {
  font-size: 14px;
  color: #606266;
  text-decoration: none;
  padding: 4px 8px;
  border-radius: 4px;
  transition: color 0.15s, background 0.15s;
}

.topbar-link:hover {
  color: #667eea;
  background: #f5f7fa;
}

.user-chip {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  padding: 4px 8px;
  border-radius: 16px;
  transition: background 0.15s;
}

.user-chip:hover {
  background: #f5f7fa;
}

.user-avatar {
  background: #667eea;
  color: #fff;
  font-size: 13px;
  font-weight: 600;
}

.user-name {
  font-size: 14px;
  color: #2c3e50;
  max-width: 140px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.loading-chip {
  color: #909399;
  font-size: 14px;
}

.app-main {
  flex: 1;
  min-height: 0;
}
</style>
