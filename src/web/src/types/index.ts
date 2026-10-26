/**
 * 后端 DTO 类型定义，与 src/app/schemas/__init__.py 对应。
 * 所有 REST 响应均为统一信封 { code, message, data }。
 */

export interface Envelope<T> {
  code: number
  message: string
  data: T
}

/** 错误码（与 core/errors.py 的 ErrorCode 一致） */
export const ErrorCode = {
  OK: 0,
  BAD_REQUEST: 40001,
  FORBIDDEN: 40301,
  NOT_FOUND: 40401,
  VERSION_CONFLICT: 40901,
  RUN_STATE_CONFLICT: 40902,
  STREAM_BUFFER_LOST: 41001,
  NAMING_INVALID: 42201,
  OUTLINE_PARSE_FAILED: 42202,
  INTERNAL_ERROR: 50001,
  LLM_PROVIDER_FAILED: 50201,
  DEPENDENCY_UNAVAILABLE: 50301,
} as const

export type ExportFormat = 'markdown' | 'epub' | 'mobi' | 'txt'

export interface Novel {
  id: string
  title: string
  author_name?: string | null
  genre?: string | null
  synopsis?: string | null
  status: string
  outline_file: string
  chapter_count: number
  total_words: number
  created_at?: string | null
  updated_at?: string | null
}

export interface Chapter {
  id: string
  volume_id: string
  title: string
  /** ★派生值：由大纲小节先后回填，前端只读渲染，不可拖拽重排 */
  sort_order: number
  word_count: number
  status: 'draft' | 'finalized'
  version: number
  updated_at?: string | null
}

export interface Volume {
  id: string
  title: string
  summary: string
  sort_order: number
  chapters: Chapter[]
  chapter_count: number
  word_count: number
}

export interface ChapterContent {
  id: string
  volume_id: string
  project_id: string
  title?: string | null
  content: string
  word_count: number
  status: string
  /** 乐观锁版本号；保存时必须回传为 base_version */
  version: number
  updated_at?: string | null
  last_writer: 'manual' | 'agent'
  path?: string
}

/** 409/40901 冲突时 data 的内容，供 ConflictBanner 做合并/覆盖 */
export interface VersionConflictData {
  id: string
  content: string
  version: number
  updated_at?: string | null
  last_writer: string
  base_version: number
}

export interface ContentWriteResult {
  id: string
  volume_id: string
  version: number
  word_count: number
  updated_at?: string | null
  last_writer: string
  snapshotted: boolean
}

export interface Revision {
  id: string
  chapter_id: string
  project_id: string
  volume_id: string
  version: number
  word_count: number
  source: 'manual' | 'agent'
  run_id?: string | null
  created_at?: string | null
  preview: string
}

export interface ReadingProgress {
  novel_id: string
  chapter_id?: string | null
  scroll_ratio: number
  updated_at?: string | null
}

export interface OutlineIssue {
  level: 'error' | 'warning'
  line?: number | null
  message: string
}

export interface OutlineValidate {
  novel_id: string
  ok: boolean
  volume_count: number
  chapter_count: number
  appendix_count: number
  issues: OutlineIssue[]
  missing: Record<string, unknown>[]
  mismatches: Record<string, unknown>[]
  unregistered: Record<string, unknown>[]
}

export interface OutlineOrderSync {
  volumes_updated: number
  chapters_updated: number
  volume_order: string[]
  warnings: string[]
}

export interface Outline {
  novel_id: string
  path: string
  filename: string
  content: string
  word_count: number
  order_synced: boolean
  order_sync?: OutlineOrderSync
}

export interface UserSettings {
  theme: 'light' | 'dark' | 'system'
  primary_color: string
  font_size: number
  font_family: string
  reading_width: number
  autosave_interval_sec: number
  /** ★回落城市：浏览器定位失败/被拒时使用，不是定位结果 */
  city: string
  pet_idle_minutes: number
  updated_at?: string | null
}

export interface ExportTask {
  task_id: string
  novel_id: string
  format: ExportFormat
  status: 'pending' | 'running' | 'done' | 'failed'
  progress: number
  download_url?: string | null
  filename?: string | null
  error?: string | null
  created_at?: string | null
  size?: number
}

export interface SearchItem {
  doc_ref: string
  kind: 'outline' | 'summary'
  title: string
  snippet: string
  score: number
}

export interface SearchResult {
  query: string
  scope: string
  total: number
  /** true = FTS5 不可用，已降级 LIKE */
  degraded: boolean
  items: SearchItem[]
}

export interface Health {
  status: string
  version: string
  fts5_available: boolean
  fts5_reason: string
  sqlite_version: string
  calibre_available: boolean
  langfuse_enabled: boolean
  data_root: string
}
