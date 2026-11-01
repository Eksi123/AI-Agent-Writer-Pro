/**
 * 编辑器 store：正文乐观锁（base_version）+ 自动保存 + 冲突状态。
 * 契约见手册「六 · 正文写入乐观锁」。
 *
 * ★v1101：AI 编辑锁——AI 修改某章期间用户不得同时修改：
 * `lockedChapters`（chapterId → runId）由 SSE `content.lock` 事件与
 * `GET /chapters/locks` 轮询维护；锁定期内 `save()` 直接拒绝（手动/自动同拒）。
 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { contentApi } from '@/api'
import { ApiError, errorMessage } from '@/api/http'
import { ErrorCode } from '@/types'
import type { ChapterContent, Revision, VersionConflictData } from '@/types'

export const useEditorStore = defineStore('editor', () => {
  const chapterId = ref<string | null>(null)
  const title = ref('')
  /** 当前编辑中的正文（可能与服务端不一致） */
  const draft = ref('')
  /** 读取时拿到的 version，保存时必须回传为 base_version */
  const baseVersion = ref(1)
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
   * ★v1031：[回退编辑]（类似 Word 的撤销）——保存当前章节的编辑快照，最多 5 次。
   * 快照由正文编辑器在「一次连续输入的停顿处」压入（见 TiptapEditor 的防抖记录），
   * 工具栏 icon 点击后自增 `undoEditTick`，编辑器据此弹出最近一份快照还原。
   * 只在章节编辑态有效（大纲编辑器不参与）。
   */
  const MAX_UNDO = 5
  const undoStack = ref<string[]>([])
  const undoEditTick = ref(0)

  const canUndo = computed(() => undoStack.value.length > 0)

  /** 压入一份「回退目标」快照；与栈顶相同则忽略；超出 5 份丢弃最旧的 */
  function pushUndoSnapshot(html: string): void {
    if (undoStack.value[undoStack.value.length - 1] === html) return
    undoStack.value = [...undoStack.value, html].slice(-MAX_UNDO)
  }

  function requestUndo(): void {
    if (!undoStack.value.length) return
    undoEditTick.value += 1
  }

  /** 取出最近一份快照（供编辑器还原），栈空返回 null */
  function popUndoSnapshot(): string | null {
    if (!undoStack.value.length) return null
    const snapshot = undoStack.value[undoStack.value.length - 1]
    undoStack.value = undoStack.value.slice(0, -1)
    return snapshot
  }

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

  // ---- ★v1101：AI 编辑锁 ----
  /** chapterId → 持有锁的 run_id */
  const lockedChapters = ref<Record<string, string>>({})

  function isChapterLocked(chapterId: string | null | undefined): boolean {
    return !!chapterId && chapterId in lockedChapters.value
  }

  /** SSE `content.lock` 事件回调 */
  function setChapterLock(chapterId: string, runId: string, locked: boolean): void {
    if (locked) lockedChapters.value = { ...lockedChapters.value, [chapterId]: runId }
    else {
      const next = { ...lockedChapters.value }
      delete next[chapterId]
      lockedChapters.value = next
    }
  }

  /** 启动 / SSE 重连后与服务端对账（防漏事件） */
  async function refreshLocks(): Promise<void> {
    try {
      const list = await contentApi.locks()
      const next: Record<string, string> = {}
      for (const item of list) next[item.chapter_id] = item.run_id
      lockedChapters.value = next
    } catch {
      // 对账失败不阻断编辑（后端 PUT 仍有硬校验兜底）
    }
  }

  async function openChapter(id: string): Promise<void> {
    chapterId.value = id
    conflict.value = null
    error.value = null
    const data: ChapterContent = await contentApi.read(id)
    draft.value = data.content
    baseVersion.value = data.version
    title.value = data.title ?? ''
    lastWriter.value = data.last_writer
    wordCount.value = data.word_count
    dirty.value = false
    // ★v1031：换章即清空回退栈（回退只针对「当前章节」的编辑）
    undoStack.value = []
  }

  function markDirty(): void {
    dirty.value = true
  }

  /**
   * 保存正文。
   * @param autosave true 时不产生修订快照（防版本爆炸）
   * ★v1101：章节被 AI 编辑锁定时直接拒绝（手动与自动保存同拒），不发请求。
   */
  async function save(autosave = false): Promise<boolean> {
    if (!chapterId.value) return false
    if (isChapterLocked(chapterId.value)) return false
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
      if (err instanceof ApiError && err.code === ErrorCode.CONTENT_LOCKED) {
        // 兜底：本地锁状态漏了事件时，以服务端为准补记锁
        const data = err.data as { chapter_id?: string; run_id?: string } | null
        if (data?.chapter_id) setChapterLock(data.chapter_id, data.run_id || '', true)
        return false
      }
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

  function reset(): void {
    chapterId.value = null
    draft.value = ''
    baseVersion.value = 1
    dirty.value = false
    conflict.value = null
    revisions.value = []
    error.value = null
    dirtyOutline.value = false
    undoStack.value = []
  }

  return {
    chapterId,
    title,
    draft,
    baseVersion,
    lastWriter,
    wordCount,
    dirty,
    saving,
    error,
    conflict,
    revisions,
    hasConflict,
    lockedChapters,
    isChapterLocked,
    setChapterLock,
    refreshLocks,
    saveRequestTick,
    dirtyOutline,
    undoEditTick,
    canUndo,
    requestSave,
    pushUndoSnapshot,
    requestUndo,
    popUndoSnapshot,
    openChapter,
    markDirty,
    save,
    overwriteWithLocal,
    acceptServerVersion,
    loadRevisions,
    restoreRevision,
    reset,
  }
})
