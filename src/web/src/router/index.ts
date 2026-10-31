/**
 * 路由：history 模式（单体部署由后端 SPA 回落承接），路由切换时同步页面标题。
 * - `/`         工作台（三栏主界面 WorkspaceView）
 * - `/settings` 通用设置（SettingsView）
 * - `/ai-tools` AI 设置（大模型 / 子代理 / 技能，AiSettingsView）
 */
import { createRouter, createWebHistory } from 'vue-router'

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
      component: () => import('@/views/AiSettingsView.vue'),
      meta: { title: 'AI 设置' },
    },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

router.afterEach((to) => {
  const title = (to.meta?.title as string) ?? 'AI Writer Pro'
  document.title = title
})
