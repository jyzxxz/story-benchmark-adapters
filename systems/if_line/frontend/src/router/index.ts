import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'
import { useUserStore } from '@/stores/user'

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    name: 'Home',
    component: () => import('@/views/ProjectListView.vue')
  },
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/LoginView.vue'),
    meta: { public: true }
  },
  {
    path: '/public/three-kingdoms',
    name: 'ThreeKingdomsPublic',
    component: () => import('@/views/ThreeKingdomsPublicView.vue'),
    meta: { public: true }
  },
  {
    path: '/vn-graph-player',
    name: 'VNGraphPlayer',
    component: () => import('@/views/VNGraphPlayerView.vue'),
    meta: { public: true }
  },
  {
    path: '/create',
    name: 'CreateProject',
    component: () => import('@/views/ProjectCreateView.vue'),
    meta: { requiresAuth: true }
  },
  {
    path: '/project/:id',
    name: 'Project',
    component: () => import('@/views/WorkflowView.vue')
  },
  {
    path: '/project/:id/stats',
    name: 'Stats',
    component: () => import('@/views/StatsView.vue'),
    meta: { requiresAuth: true }
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

router.beforeEach(async (to) => {
  const userStore = useUserStore()
  if (!userStore.loaded) {
    await userStore.fetchMe()
  }

  if (to.meta.requiresAuth && !userStore.isLoggedIn) {
    try {
      await userStore.ensureGuest()
    } catch {
      return { path: '/login', query: { redirect: to.fullPath } }
    }
  }

  if (to.name === 'Login' && userStore.isLoggedIn && !userStore.isGuest) {
    return { path: '/' }
  }

  return true
})

export default router
