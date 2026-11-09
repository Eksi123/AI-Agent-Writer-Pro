/**
 * 阅读/编辑视图状态：模式切换、当前章节、目录抽屉、夜间模式。
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'

import { confirmLeaveUnsaved } from '@/utils/unsavedGuard'

export type MainMode = 'read' | 'edit'
/** 目录树的特殊节点：大纲固定在第一卷之前 */
export type TocNodeKind = 'outline' | 'volume' | 'chapter'

export const useReaderStore = defineStore('reader', () => {
  const mode = ref<MainMode>('read')
  /** 目录浮在阅读区左上角，默认收起，点击展开 */
  const tocOpen = ref(false)
  /** 夜间模式独立于全局暗色主题（阅读模式专用） */
  const nightMode = ref(false)
  /** 当前打开的章 id；null 表示正在看「大纲」节点 */
  const activeChapterId = ref<string | null>(null)
  const activeOutline = ref(false)
  /** 阅读滚动比例（0..1），用于进度记忆 */
  const scrollRatio = ref(0)

  function setMode(next: MainMode): void {
    mode.value = next
  }

  /**
   * ★ 导航入口一律**先问后改**：切走会卸载当前编辑器组件，而大纲正文是
   *   `OutlineEditor.vue` 的组件内局部状态且无自动保存，事后回滚救不回来。
   *   拦截逻辑集中在 `utils/unsavedGuard`（store 不直接依赖 element-plus 弹窗）。
   *   返回 Promise 以便调用方需要时 await；现有调用点多为即发即忘，不受影响。
   */
  async function openOutline(): Promise<void> {
    if (!(await confirmLeaveUnsaved())) return
    activeOutline.value = true
    activeChapterId.value = null
  }

  async function openChapter(id: string): Promise<void> {
    if (!(await confirmLeaveUnsaved())) return
    activeOutline.value = false
    activeChapterId.value = id
  }

  function toggleToc(): void {
    tocOpen.value = !tocOpen.value
  }

  function toggleNight(): void {
    nightMode.value = !nightMode.value
  }

  return {
    mode,
    tocOpen,
    nightMode,
    activeChapterId,
    activeOutline,
    scrollRatio,
    setMode,
    openOutline,
    openChapter,
    toggleToc,
    toggleNight,
  }
})
