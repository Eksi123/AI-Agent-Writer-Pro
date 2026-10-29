/**
 * 阅读/编辑视图状态：模式切换、当前章节、目录抽屉、夜间模式。
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'

export type MainMode = 'read' | 'edit'
export type EditTab = 'content' | 'volumes'
/** 目录树的特殊节点：大纲固定在第一卷之前 */
export type TocNodeKind = 'outline' | 'volume' | 'chapter'

export const useReaderStore = defineStore('reader', () => {
  const mode = ref<MainMode>('read')
  const editTab = ref<EditTab>('content')
  /** v1028：目录浮在阅读区左上角，默认收起，点击展开 */
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

  function openOutline(): void {
    activeOutline.value = true
    activeChapterId.value = null
  }

  function openChapter(id: string): void {
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
    editTab,
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
