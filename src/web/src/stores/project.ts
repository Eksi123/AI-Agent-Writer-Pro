/**
 * 项目 store：小说列表、当前项目、卷章树（只读顺序）。
 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { novelsApi, volumesApi } from '@/api'
import { ApiError, errorMessage } from '@/api/http'
import { t } from '@/i18n'
import { useEditorStore } from '@/stores/editor'
import { useReaderStore } from '@/stores/reader'
import type { Chapter, Novel, Volume } from '@/types'
import { confirmLeaveUnsaved } from '@/utils/unsavedGuard'

export const useProjectStore = defineStore('project', () => {
  const novels = ref<Novel[]>([])
  const volumes = ref<Volume[]>([])
  const currentNovelId = ref<string | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  const currentNovel = computed(() => novels.value.find((n) => n.id === currentNovelId.value) ?? null)

  /** 当前项目的卷章树，按 sort_order 只读排序（不可拖拽重排） */
  const volumeTree = computed(() =>
    [...volumes.value].sort((a, b) => a.sort_order - b.sort_order),
  )

  const totalWords = computed(() => volumes.value.reduce((s, v) => s + (v.word_count ?? 0), 0))
  const totalChapters = computed(() => volumes.value.reduce((s, v) => s + (v.chapter_count ?? 0), 0))

  async function loadNovels(): Promise<void> {
    loading.value = true
    error.value = null
    try {
      novels.value = await novelsApi.list()
    } catch (err) {
      error.value = errorMessage(err)
      throw err
    } finally {
      loading.value = false
    }
  }

  async function selectNovel(id: string | null): Promise<void> {
    // ★ 切项目会同时销毁章正文与大纲两个编辑器（大纲正文是组件内局部状态、无自动保存），
    //   故在改动任何状态之前先拦未保存的修改；用户选择留下时，项目**不得**被切走。
    if (id !== currentNovelId.value && !(await confirmLeaveUnsaved())) return
    currentNovelId.value = id
    volumes.value = []
    if (!id) return
    await loadVolumes()
    // 切换小说后默认展示大纲页，避免残留旧章节 id 导致「章节不存在」
    const reader = useReaderStore()
    // 此处脏标志已在上面的拦截中清掉，不会重复弹窗
    await reader.openOutline()
  }

  async function loadVolumes(): Promise<void> {
    if (!currentNovelId.value) return
    loading.value = true
    try {
      volumes.value = await volumesApi.list(currentNovelId.value)
    } catch (err) {
      error.value = errorMessage(err)
    } finally {
      loading.value = false
    }
  }

  async function createNovel(payload: { title: string; author_name?: string; genre?: string; synopsis?: string }): Promise<Novel> {
    const novel = await novelsApi.create(payload)
    await loadNovels()
    return novel
  }

  async function renameNovel(id: string, title: string): Promise<void> {
    await novelsApi.update(id, { title })
    await loadNovels()
  }

  /** [编辑] 弹窗提交——可同时修改书名 / 题材 / 简介 */
  async function updateNovel(
    id: string,
    payload: { title?: string; genre?: string; synopsis?: string },
  ): Promise<void> {
    await novelsApi.update(id, payload)
    await loadNovels()
  }

  async function deleteNovel(id: string): Promise<void> {
    await novelsApi.remove(id)
    if (currentNovelId.value === id) {
      currentNovelId.value = null
      volumes.value = []
      // ★ 被删项目的编辑器缓冲已无意义（正文随项目进了回收站），必须连脏标志一起清掉。
      //   否则随后「回落到列表首个」的 `selectNovel` 会被未保存拦截挡住，并试图把稿子
      //   保存到一个**已删除**的项目上 → 保存必然失败 → 拦截判定「留在原地」→
      //   界面卡在已删除的项目上切不走。
      useEditorStore().reset()
    }
    await loadNovels()
  }

  async function duplicateNovel(id: string, title: string): Promise<Novel> {
    const novel = await novelsApi.duplicate(id, title)
    await loadNovels()
    return novel
  }

  /**
   * 新建卷。
   * `ordinal` 缺省 = 追加末尾；传值 = 插入到该位置（其后各卷顺延 +1）。
   * `firstChapterTitle` = 随卷一并创建的第一章名（不允许产生空卷）。
   * **任何入口新建卷都必须带上第一章**——调用方漏传时回落到 `第一章`，
   * 保证「新建卷的同时也初始化创建该卷下的第一章」，目录里不会出现找不到章节的空卷。
   */
  async function createVolume(
    title: string,
    summary = '',
    ordinal?: number,
    firstChapterTitle?: string,
  ): Promise<Volume> {
    if (!currentNovelId.value) throw new ApiError(40401, t('common.unknownError'))
    const vol = await volumesApi.create(
      currentNovelId.value,
      title,
      summary,
      ordinal,
      firstChapterTitle?.trim() || '第一章',
    )
    await loadVolumes()
    return vol
  }

  async function updateVolume(volId: string, payload: { title?: string; summary?: string }): Promise<void> {
    await volumesApi.update(volId, payload)
    await loadVolumes()
  }

  async function deleteVolume(volId: string): Promise<number> {
    const res = await volumesApi.remove(volId)
    await loadVolumes()
    return res.chapters_deleted
  }

  function findChapter(chapterId: string): { volume: Volume; chapter: Chapter } | null {
    for (const volume of volumes.value) {
      const chapter = volume.chapters.find((c) => c.id === chapterId)
      if (chapter) return { volume, chapter }
    }
    return null
  }

  /** 按 sort_order 展平的章节序列（阅读翻页用） */
  const flatChapters = computed(() =>
    volumeTree.value.flatMap((v) =>
      [...v.chapters].sort((a, b) => a.sort_order - b.sort_order).map((c) => ({ volume: v, chapter: c })),
    ),
  )

  return {
    novels,
    volumes,
    currentNovelId,
    currentNovel,
    volumeTree,
    flatChapters,
    totalWords,
    totalChapters,
    loading,
    error,
    loadNovels,
    selectNovel,
    loadVolumes,
    createNovel,
    renameNovel,
    updateNovel,
    deleteNovel,
    duplicateNovel,
    createVolume,
    updateVolume,
    deleteVolume,
    findChapter,
  }
})
