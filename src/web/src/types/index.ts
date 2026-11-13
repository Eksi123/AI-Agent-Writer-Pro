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
  /** 章节正被 AI 编辑（内容锁），保存被拒 */
  CONTENT_LOCKED: 40903,
  STREAM_BUFFER_LOST: 41001,
  NAMING_INVALID: 42201,
  OUTLINE_PARSE_FAILED: 42202,
  INTERNAL_ERROR: 50001,
  LLM_PROVIDER_FAILED: 50201,
  DEPENDENCY_UNAVAILABLE: 50301,
} as const

export type ExportFormat = 'markdown' | 'epub' | 'pdf' | 'txt'

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
  /** 派生值：由大纲小节先后回填，前端只读渲染，不可拖拽重排 */
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

/** 大纲保存返回的**只读**差异报告（大纲不再回填 sort_order） */
export interface OutlineOrderSync {
  volumes_updated: number
  chapters_updated: number
  volume_order: string[]
  warnings: string[]
  /** 只读标记：true 表示仅报告差异，未改动卷章顺序 */
  read_only?: boolean
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
  /** light（浅色）| dark（深色）| eye（护眼）；system 仅为兼容旧数据 */
  theme: 'light' | 'dark' | 'eye' | 'system'
  primary_color: string
  font_size: number
  font_family: string
  reading_width: number
  /** 自动保存开关（与自动保存间隔联动） */
  autosave_enabled: boolean
  autosave_interval_sec: number
  /** 回落城市：浏览器定位失败/被拒时使用，不是定位结果 */
  city: string
  pet_idle_minutes: number
  /** 小说导出目录（空 = 默认桌面 Desktop） */
  export_dir?: string | null
  updated_at?: string | null
}

export interface ExportTask {
  task_id: string
  novel_id: string
  format: ExportFormat
  status: 'pending' | 'running' | 'done' | 'failed' | 'cancelled'
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

/**
 * 聊天输入框上方标记的「已添加内容」块（大纲 / 章节 / 本地文件）。
 * 发送消息时由 AgentPanel 组装进 prompt，大模型需一并处理这些内容。
 */
export interface ContextBlock {
  kind: 'outline' | 'chapter' | 'file'
  label: string
  text: string
  /** 本地文件字节数（用于「所有添加的文件不超过 20M」校验） */
  bytes?: number
  /** 选区润色块携带的章节 id（供大模型定位并写回） */
  chapterId?: string
  /** 选中文本在章节中的所在范围说明（如「第3-5段（全章共42段）」） */
  rangeLabel?: string
  /** 选区前文（帮助模型衔接，≤600 字） */
  before?: string
  /** 选区后文（帮助模型衔接，≤600 字） */
  after?: string
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
  pdf_available: boolean
  langfuse_enabled: boolean
  data_root: string
}

// ---------------------------------------------------------------------------
// LLM（M3）
// ---------------------------------------------------------------------------

export type ProviderType =
  | 'openai'
  | 'openai_compatible'
  | 'anthropic'
  | 'google'
  | 'azure_openai'
  | 'openrouter'

export interface LlmModelDto {
  id: string
  provider_id: string
  model_ref: string
  model_id: string
  display_name?: string | null
  max_context: number
  supports_tools: boolean
  default_params: Record<string, unknown>
  is_default: boolean
  enabled: boolean
  created_at?: string | null
  updated_at?: string | null
}

export interface LlmProviderDto {
  id: string
  name: string
  type: ProviderType
  base_url?: string | null
  api_key_env?: string | null
  api_key_configured: boolean
  api_key_masked: string
  extra?: Record<string, unknown> | null
  enabled: boolean
  priority: number
  /** 大模型级常用参数 */
  timeout_sec: number
  temperature: number
  max_tokens: number
  models: LlmModelDto[]
  created_at?: string | null
  updated_at?: string | null
}

export interface LlmOverview {
  settings: {
    config_version: number
    sse_buffer_size: number
    langfuse_enabled: boolean
    provider_keys: Record<string, boolean>
    suggested_key_envs: string[]
    provider_types: ProviderType[]
  }
  default_model_ref: string | null
  models: {
    model_ref: string
    display_name: string
    provider_name: string
    provider_type: ProviderType
    is_default: boolean
    max_context: number
    supports_tools: boolean
  }[]
}

export interface ProviderTestResult {
  ok: boolean
  provider: string
  model?: string
  latency_ms?: number
  reply_excerpt?: string
  message: string
}

// ---------------------------------------------------------------------------
// Agent（M3）
// ---------------------------------------------------------------------------

export type AgentSessionStatus =
  | 'idle'
  | 'running'
  | 'awaiting_approval'
  | 'awaiting_consult'
  | 'error'

export interface AgentSessionDto {
  id: string
  project_id: string
  project_title?: string
  title: string
  model_ref?: string | null
  status: AgentSessionStatus
  /** 进程内有活 run（会话列表右侧灰色小字「AI思考执行中……」） */
  is_running?: boolean
  active_leaf_id?: string | null
  subagent_ids: string[]
  skill_ids: string[]
  context_chapter_ids: string[]
  created_at?: string | null
  updated_at?: string | null
}

export interface AgentMessageDto {
  id: string
  parent_id?: string | null
  branch_index: number
  branch_count: number
  role: 'user' | 'assistant' | 'tool' | 'system'
  /** normal|steer|notice：普通消息 / 运行中引导消息 / Agent 默认收尾回复 */
  kind?: 'normal' | 'steer' | 'notice'
  content: string
  tool_calls?: { name: string; args?: Record<string, unknown>; id?: string }[] | null
  status: string
  tokens_in: number
  tokens_out: number
  model?: string | null
  run_id?: string | null
  created_at?: string | null
  in_active_path: boolean
}

export interface MessageTree {
  session_id: string
  title?: string
  status?: AgentSessionStatus
  project_id?: string
  active_leaf_id?: string | null
  total: number
  items: AgentMessageDto[]
  /** 最近一次 run（含未决中断）——进入会话时恢复审批/咨询卡片与运行态 */
  latest_run?: {
    run_id: string
    status: AgentRunStatus
    pending_interrupts: AgentInterruptDto[]
    /** 当前 SSE 缓冲区的最新 seq，供前端重连时作为 last_event_id */
    current_seq?: number
  } | null
}

export type AgentRunStatus =
  | 'pending'
  | 'running'
  | 'awaiting_approval'
  | 'awaiting_consult'
  | 'cancelling'
  | 'cancelled'
  | 'completed'
  | 'failed'

export interface AgentInterruptDto {
  id: string
  run_id: string
  session_id: string
  type: 'approval' | 'consult'
  tool_name?: string | null
  payload?: Record<string, unknown> | string | null
  decision?: string | null
  response?: Record<string, unknown> | null
  decided_by?: string | null
  decided_at?: string | null
  created_at?: string | null
}

export interface AgentRunDto {
  id: string
  session_id: string
  project_id: string
  status: AgentRunStatus
  model_ref?: string | null
  recursion_used: number
  tokens_in: number
  tokens_out: number
  fallback_count: number
  langfuse_trace_id?: string | null
  error?: string | null
  started_at?: string | null
  ended_at?: string | null
  workflow_stage?: string | null
  workflow_retry_count: number
  workflow_result?: Record<string, unknown> | string | null
  target_chapter_id?: string | null
  interrupts: AgentInterruptDto[]
}

// ---------------------------------------------------------------------------
// 回收站
// ---------------------------------------------------------------------------

/** 回收站首页的一条（最近删除过章节的小说，最多 20 本） */
export interface RecycledNovel {
  novel_id: string
  title: string
  /** true = 整本小说被删除（回收任一章节会连同小说项目与大纲一起恢复） */
  novel_deleted: boolean
  chapter_count: number
  updated_at?: string | null
}

/** 回收站二级页：被删除的章节 */
export interface RecycledChapter {
  id: string
  title: string
  volume_id: string
  sort_order: number
  word_count: number
  deleted_at?: string | null
}

/** 回收站二级页：卷 → 该卷下被删除的章节 */
export interface RecycledVolume {
  id: string
  title: string
  sort_order: number
  chapters: RecycledChapter[]
  chapter_count: number
}

/** 回收站二级页 DTO */
export interface RecycledContents extends RecycledNovel {
  volumes: RecycledVolume[]
  total_chapters: number
}
