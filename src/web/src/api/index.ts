/**
 * 各模块 API 接口封装，与后端路由树（手册「六」）一一对应。
 * ★卷章无任何 reorder / move 接口：顺序是大纲派生值（铁律 10）。
 */
import { del, get, http, patch, post, put } from './http'
import type {
  Chapter,
  ChapterContent,
  ContentWriteResult,
  ExportFormat,
  ExportTask,
  Health,
  Novel,
  Outline,
  OutlineValidate,
  ReadingProgress,
  Revision,
  SearchResult,
  UserSettings,
  Volume,
} from '@/types'

// ---------------------------------------------------------------------------
// 健康检查
// ---------------------------------------------------------------------------

export const healthApi = {
  get: () => get<Health>('/health'),
}

// ---------------------------------------------------------------------------
// 项目
// ---------------------------------------------------------------------------

export const novelsApi = {
  list: () => get<Novel[]>('/novels'),

  create: (payload: { title: string; author_name?: string; genre?: string; synopsis?: string }) =>
    post<Novel>('/novels', payload),

  get: (id: string) => get<Novel>(`/novels/${id}`),

  update: (
    id: string,
    payload: { title?: string; author_name?: string; genre?: string; synopsis?: string },
  ) => patch<Novel>(`/novels/${id}`, payload),

  remove: (id: string) => del<{ id: string; status: string; trash_dir: string | null }>(`/novels/${id}`),

  duplicate: (id: string, title: string) => post<Novel>(`/novels/${id}/duplicate`, { title }),

  rebuildIndex: (id: string) =>
    post<{ project_id: string; chapter_count: number; total_words: number; dirty: number }>(
      `/novels/${id}/index/rebuild`,
    ),
}

// ---------------------------------------------------------------------------
// 阅读进度
// ---------------------------------------------------------------------------

export const progressApi = {
  get: (novelId: string) => get<ReadingProgress>(`/novels/${novelId}/reading-progress`),

  save: (novelId: string, chapterId: string | null, scrollRatio: number) =>
    put<ReadingProgress>(`/novels/${novelId}/reading-progress`, {
      chapter_id: chapterId,
      scroll_ratio: scrollRatio,
    }),
}

// ---------------------------------------------------------------------------
// 卷（★只读顺序，无重排接口）
// ---------------------------------------------------------------------------

export const volumesApi = {
  list: (novelId: string) => get<Volume[]>(`/novels/${novelId}/volumes`),

  create: (novelId: string, title: string, summary = '') =>
    post<Volume>(`/novels/${novelId}/volumes`, { title, summary }),

  update: (volId: string, payload: { title?: string; summary?: string }) =>
    patch<Volume>(`/volumes/${volId}`, payload),

  remove: (volId: string) =>
    del<{ id: string; deleted: boolean; chapters_deleted: number }>(`/volumes/${volId}`),
}

// ---------------------------------------------------------------------------
// 章（★只读顺序，无重排 / 无跨卷移动）
// ---------------------------------------------------------------------------

export const chaptersApi = {
  list: (volId: string) => get<Chapter[]>(`/volumes/${volId}/chapters`),

  create: (volId: string, title: string, content = '') =>
    post<Chapter>(`/volumes/${volId}/chapters`, { title, content }),

  update: (chapterId: string, payload: { title?: string; status?: 'draft' | 'finalized' }) =>
    patch<Chapter>(`/chapters/${chapterId}`, payload),

  remove: (chapterId: string) => del<{ id: string; deleted: boolean }>(`/chapters/${chapterId}`),
}

// ---------------------------------------------------------------------------
// 正文（乐观锁）
// ---------------------------------------------------------------------------

export const contentApi = {
  /** 读正文，返回的 version 保存时须回传为 base_version */
  read: (chapterId: string) => get<ChapterContent>(`/chapters/${chapterId}/content`),

  /**
   * 写正文。
   * @param baseVersion 读取时拿到的 version；不符返回 409/40901
   * @param autosave true 时不产生修订快照（防版本爆炸）
   */
  write: (chapterId: string, content: string, baseVersion: number, autosave = false) =>
    put<ContentWriteResult>(`/chapters/${chapterId}/content`, {
      content,
      base_version: baseVersion,
      autosave,
    }),

  revisions: (chapterId: string) => get<Revision[]>(`/chapters/${chapterId}/revisions`),

  restore: (chapterId: string, revisionId: string) =>
    post<ChapterContent & { restored_from: string }>(
      `/chapters/${chapterId}/revisions/${revisionId}/restore`,
    ),
}

// ---------------------------------------------------------------------------
// 大纲（★顺序的唯一写入口：PUT 成功后后端回填卷章 sort_order）
// ---------------------------------------------------------------------------

export const outlineApi = {
  read: (novelId: string) => get<Outline>(`/novels/${novelId}/outline`),

  /** 保存大纲；后端执行 sync_order_from_outline 回填卷章顺序 */
  save: (novelId: string, content: string) => put<Outline>(`/novels/${novelId}/outline`, { content }),

  validate: (novelId: string) => get<OutlineValidate>(`/novels/${novelId}/outline/validate`),
}

// ---------------------------------------------------------------------------
// 检索
// ---------------------------------------------------------------------------

export const searchApi = {
  search: (novelId: string, q: string, scope: 'all' | 'outline' | 'summary' = 'all', limit = 20) =>
    get<SearchResult>(`/novels/${novelId}/search`, { params: { q, scope, limit } }),

  rebuildSummaries: (novelId: string) =>
    post<{ project_id: string; indexed: number; fts5: boolean; summary_files: number }>(
      `/novels/${novelId}/summaries/rebuild`,
    ),
}

// ---------------------------------------------------------------------------
// 通用设置
// ---------------------------------------------------------------------------

export const settingsApi = {
  get: () => get<UserSettings>('/settings'),

  save: (payload: Partial<UserSettings>) => put<UserSettings>('/settings', payload),
}

// ---------------------------------------------------------------------------
// 导出（异步任务）
// ---------------------------------------------------------------------------

export const exportApi = {
  start: (novelId: string, format: ExportFormat) =>
    post<ExportTask>(`/novels/${novelId}/export`, null, { params: { format } }),

  status: (novelId: string, taskId: string) =>
    get<ExportTask>(`/novels/${novelId}/export-tasks/${taskId}`),

  downloadUrl: (novelId: string, taskId: string) =>
    `/api/v1/novels/${novelId}/export-tasks/${taskId}/download`,

  /** 轮询直到任务结束（done / failed） */
  async waitForDone(novelId: string, taskId: string, timeoutMs = 120_000): Promise<ExportTask> {
    const started = Date.now()
    for (;;) {
      const task = await exportApi.status(novelId, taskId)
      if (task.status === 'done' || task.status === 'failed') return task
      if (Date.now() - started > timeoutMs) {
        return { ...task, status: 'failed', error: '导出超时' }
      }
      await new Promise((r) => setTimeout(r, 300))
    }
  },
}

/** 触发浏览器下载导出产物 */
export async function downloadExport(novelId: string, taskId: string): Promise<void> {
  const url = exportApi.downloadUrl(novelId, taskId)
  const res = await http.get(url, { responseType: 'blob' })
  const blob = res.data as unknown as Blob
  const disposition = (res.headers as Record<string, string>)['content-disposition'] ?? ''
  const match = /filename\*?=(?:UTF-8''|\s*)"?([^";]+)"?/i.exec(disposition)
  const filename = match ? decodeURIComponent(match[1]) : `novel-${taskId}.zip`

  const objectUrl = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = objectUrl
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(objectUrl)
}
