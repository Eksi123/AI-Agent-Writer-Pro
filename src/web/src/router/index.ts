import { createRouter, createWebHistory } from 'vue-router'

/**
 * 路由（M0 迁移：hash → history，单体部署由后端 SPA 回落承接）。
 * - `/`          工作台（三栏主界面 WorkspaceView）
 * - `/settings`  通用设置（SettingsView）
 * - `/ai-tools`  AI 工具（Provider/Model/子代理/技能/MCP，AiToolsView）
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
    {
      path: '/ai-tools',
      name: 'ai-tools',
      component: () => import('@/views/AiToolsView.vue'),
      meta: { title: 'AI 工具' },
    },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

router.afterEach((to) => {
  const title = (to.meta?.title as string) ?? 'AI Writer Pro'
  document.title = title
})
