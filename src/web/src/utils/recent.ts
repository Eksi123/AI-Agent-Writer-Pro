/**
 * 「最近打开的小说章节」本地记录（v1029 需求 7-(1.2)）。
 *
 * 添加文件页要展示最近打开过的章节（最多 10 条），后端只保存每本小说**单条**阅读进度
 * （`GET /novels/{id}/reading-progress`），没有「最近打开列表」，故在前端用 localStorage 记录。
 */

export interface RecentChapter {
  novelId: string
  novelTitle: string
  volumeId: string
  volumeTitle: string
  chapterId: string
  chapterTitle: string
  /** 打开时间（ISO 字符串），用于排序 */
  at: string
}

const KEY = 'aiwriter:recentChapters'
export const MAX_RECENT = 10

function read(): RecentChapter[] {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? (parsed as RecentChapter[]) : []
  } catch {
    return []
  }
}

function write(items: RecentChapter[]): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(items))
  } catch {
    /* 隐私模式/配额满：忽略，最近记录是增强功能 */
  }
}

/** 记录一次章节打开（同一章去重并提到最前，最多保留 10 条） */
export function recordRecentChapter(item: Omit<RecentChapter, 'at'>): void {
  if (!item.chapterId) return
  const next: RecentChapter[] = [
    { ...item, at: new Date().toISOString() },
    ...read().filter((r) => r.chapterId !== item.chapterId),
  ].slice(0, MAX_RECENT)
  write(next)
}

/** 最近打开的章节（最新在前，**最多 10 条**）；`novelId` 非空时只返回该小说的记录 */
export function listRecentChapters(novelId?: string | null): RecentChapter[] {
  const all = read().sort((a, b) => (a.at < b.at ? 1 : -1))
  const scoped = novelId ? all.filter((r) => r.novelId === novelId) : all
  // 读取侧也截断：localStorage 可能被外部写入超过上限的数据
  return scoped.slice(0, MAX_RECENT)
}

/** 小说被删除后清理其最近记录 */
export function dropRecentByNovel(novelId: string): void {
  write(read().filter((r) => r.novelId !== novelId))
}
