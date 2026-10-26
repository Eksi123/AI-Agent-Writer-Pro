import { createRouter, createWebHistory } from 'vue-router'

/**
 * 路由（M0 迁移：hash → history，单体部署由后端 SPA 回落承接）。
 * - `/`          工作台（三栏主界面 WorkspaceView）
 * - `/settings`  通用设置 + AI 工具（SettingsView）
 */
export const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'workspace',
      component: () => import('@/views/WorkspaceView.vue'),
      meta: { title: 'AI Writer Pro' },
    },
    {
      path: '/settings',
      name: 'settings',
      component: () => import('@/views/SettingsView.vue'),
      meta: { title: '通用设置' },
    },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

router.afterEach((to) => {
  const title = (to.meta?.title as string) ?? 'AI Writer Pro'
  document.title = title
})
