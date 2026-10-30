/**
 * 编辑器 store：正文乐观锁（base_version）+ 自动保存 + 冲突状态。
 * 契约见手册「六 · 正文写入乐观锁」。
 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { contentApi } from '@/api'
import { ApiError, errorMessage } from '@/api/http'
import type { ChapterContent, Revision, VersionConflictData } from '@/types'

export const useEditorStore = defineStore('editor', () => {
  const chapterId = ref<string | null>(null)
  const title = ref('')
  /** 当前编辑中的正文（可能与服务端不一致） */
  const draft = ref('')
  /** 读取时拿到的 version，保存时必须回传为 base_version */
  const baseVersion = ref(1)
  const status = ref<'draft' | 'finalized'>('draft')
  const lastWriter = ref<'manual' | 'agent'>('manual')
  const wordCount = ref(0)

  const dirty = ref(false)
  const saving = ref(false)
  const error = ref<string | null>(null)

  /**
   * v1030：工具栏「保存」icon 触发保存请求。
   * 编辑器（正文 / 大纲）监听该自增计数并执行各自的保存，避免跨组件 ref 传递。
   */
  const saveRequestTick = ref(0)

  /**
   * v1031：大纲编辑器的脏状态（正文用 dirty）。
   * 工具栏保存 icon 据此置灰——没有任何待保存内容时不可点击。
   */
  const dirtyOutline = ref(false)

  function requestSave(): void {
    saveRequestTick.value += 1
  }

  /** 409 冲突时的服务端最新内容，供 ConflictBanner 合并/覆盖 */
  const conflict = ref<VersionConflictData | null>(null)
  const revisions = ref<Revision[]>([])

  const hasConflict = computed(() => conflict.value !== null)

  async function openChapter(id: string): Promise<void> {
    chapterId.value = id
    conflict.value = null
    error.value = null
    const data: ChapterContent = await contentApi.read(id)
    draft.value = data.content
    baseVersion.value = data.version
    title.value = data.title ?? ''
    status.value = (data.status as 'draft' | 'finalized') ?? 'draft'
    lastWriter.value = data.last_writer
    wordCount.value = data.word_count
    dirty.value = false
  }

  function markDirty(): void {
    dirty.value = true
  }

  /**
   * 保存正文。
   * @param autosave true 时不产生修订快照（防版本爆炸）
   */
  async function save(autosave = false): Promise<boolean> {
    if (!chapterId.value) return false
    saving.value = true
    error.value = null
    try {
      const result = await contentApi.write(chapterId.value, draft.value, baseVersion.value, autosave)
      baseVersion.value = result.version
      wordCount.value = result.word_count
      lastWriter.value = result.last_writer as 'manual' | 'agent'
      dirty.value = false
      conflict.value = null
      return true
    } catch (err) {
      if (err instanceof ApiError && err.isVersionConflict) {
        // 服务端已变更：把最新内容/版本存入 conflict，交由 ConflictBanner 决策
        conflict.value = err.data as VersionConflictData
        error.value = errorMessage(err)
        return false
      }
      error.value = errorMessage(err)
      throw err
    } finally {
      saving.value = false
    }
  }

  /** 冲突处理：用本地内容覆盖（需带服务端最新版本号重试） */
  async function overwriteWithLocal(): Promise<boolean> {
    if (!chapterId.value || !conflict.value) return false
    const serverVersion = conflict.value.version
    conflict.value = null
    saving.value = true
    try {
      const result = await contentApi.write(chapterId.value, draft.value, serverVersion, false)
      baseVersion.value = result.version
      wordCount.value = result.word_count
      dirty.value = false
      return true
    } catch (err) {
      error.value = errorMessage(err)
      return false
    } finally {
      saving.value = false
    }
  }

  /** 冲突处理：采用服务端内容（放弃本地修改） */
  function acceptServerVersion(): void {
    if (!conflict.value) return
    draft.value = conflict.value.content
    baseVersion.value = conflict.value.version
    wordCount.value = conflict.value.content.length
    conflict.value = null
    dirty.value = false
  }

  async function loadRevisions(): Promise<void> {
    if (!chapterId.value) return
    revisions.value = await contentApi.revisions(chapterId.value)
  }

  async function restoreRevision(revisionId: string): Promise<void> {
    if (!chapterId.value) return
    const data = await contentApi.restore(chapterId.value, revisionId)
    draft.value = data.content
    baseVersion.value = data.version
    wordCount.value = data.word_count
    dirty.value = false
    await loadRevisions()
  }

  async function setStatus(next: 'draft' | 'finalized'): Promise<void> {
    if (!chapterId.value) return
    const { chaptersApi } = await import('@/api')
    await chaptersApi.update(chapterId.value, { status: next })
    status.value = next
  }

  function reset(): void {
    chapterId.value = null
    draft.value = ''
    baseVersion.value = 1
    dirty.value = false
    conflict.value = null
    revisions.value = []
    error.value = null
    dirtyOutline.value = false
  }

  return {
    chapterId,
    title,
    draft,
    baseVersion,
    status,
    lastWriter,
    wordCount,
    dirty,
    saving,
    error,
    conflict,
    revisions,
    hasConflict,
    saveRequestTick,
    dirtyOutline,
    requestSave,
    openChapter,
    markDirty,
    save,
    overwriteWithLocal,
    acceptServerVersion,
    loadRevisions,
    restoreRevision,
    setStatus,
    reset,
  }
})
