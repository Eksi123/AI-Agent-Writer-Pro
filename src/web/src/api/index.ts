/**
 * 各模块 API 接口封装，与后端路由树（手册「六」）一一对应。
 * ★卷章无 reorder / move 接口：v1029 起位置编辑走「插入」（create 带 ordinal，其后各项顺延）。
 */
import { del, get, http, patch, post, put } from './http'
import type {
  AgentRunDto,
  AgentSessionDto,
  Chapter,
  ChapterContent,
  ContentWriteResult,
  ExportFormat,
  ExportTask,
  Health,
  LlmModelDto,
  LlmOverview,
  LlmProviderDto,
  MessageTree,
  Novel,
  Outline,
  OutlineValidate,
  ProviderTestResult,
  ProviderType,
  ReadingProgress,
  Revision,
  RewriteResult,
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
// 卷（★v1029：无拖拽重排，但支持按 ordinal 插入到指定位置）
// ---------------------------------------------------------------------------

export const volumesApi = {
  list: (novelId: string) => get<Volume[]>(`/novels/${novelId}/volumes`),

  /** ordinal 缺省 = 追加末尾；传值 = 插入到该位置（其后各卷顺延 +1） */
  create: (novelId: string, title: string, summary = '', ordinal?: number) =>
    post<Volume>(`/novels/${novelId}/volumes`, { title, summary, ordinal }),

  update: (volId: string, payload: { title?: string; summary?: string }) =>
    patch<Volume>(`/volumes/${volId}`, payload),

  remove: (volId: string) =>
    del<{ id: string; deleted: boolean; chapters_deleted: number }>(`/volumes/${volId}`),
}

// ---------------------------------------------------------------------------
// 章（★v1029：无跨卷移动，支持按 ordinal 插入；删空卷 → 卷一并删除）
// ---------------------------------------------------------------------------

export const chaptersApi = {
  list: (volId: string) => get<Chapter[]>(`/volumes/${volId}/chapters`),

  /** ordinal 缺省 = 追加卷末尾；传值 = 插入到该位置（其后各章顺延 +1） */
  create: (volId: string, title: string, content = '', ordinal?: number) =>
    post<Chapter>(`/volumes/${volId}/chapters`, { title, content, ordinal }),

  /** v1029：重命名弹窗可同时改所属卷名（volume_title 默认传当前卷名 = 不修改） */
  update: (
    chapterId: string,
    payload: { title?: string; status?: 'draft' | 'finalized'; volume_title?: string },
  ) => patch<Chapter>(`/chapters/${chapterId}`, payload),

  remove: (chapterId: string) =>
    del<{ id: string; deleted: boolean; volume_id?: string; volume_deleted?: boolean }>(
      `/chapters/${chapterId}`,
    ),
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

  /**
   * 选区 AI 改写（改写/扩写/缩写/润色）：只返回转换结果，**不写文件**。
   * 用户「接受」后由调用方走 write() 落盘（保留乐观锁与修订快照）。
   */
  rewrite: (
    chapterId: string,
    payload: {
      selected_text: string
      mode: 'rewrite' | 'expand' | 'condense' | 'polish'
      model_ref?: string
      context_before?: string
      context_after?: string
      instruction?: string
    },
  ) => post<RewriteResult>(`/chapters/${chapterId}/rewrite`, payload),
}

// ---------------------------------------------------------------------------
// 大纲（★v1029：参考资料，保存只返回只读差异报告，不再回填卷章 sort_order）
// ---------------------------------------------------------------------------

export const outlineApi = {
  read: (novelId: string) => get<Outline>(`/novels/${novelId}/outline`),

  /** 保存大纲；后端返回 order_sync（read_only 差异报告），不改卷章顺序 */
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

  /** v1028：恢复默认设置 */
  reset: () => post<UserSettings>('/settings/reset'),
}

// ---------------------------------------------------------------------------
// LLM Provider / Model（M3）
// ---------------------------------------------------------------------------

export const llmApi = {
  overview: () => get<LlmOverview>('/llm/overview'),

  listProviders: () => get<LlmProviderDto[]>('/llm/providers'),

  createProvider: (payload: {
    name: string
    type: ProviderType
    base_url?: string
    api_key_env?: string
    api_key_value?: string
    extra?: Record<string, unknown>
    enabled?: boolean
    priority?: number
    timeout_sec?: number
    temperature?: number
    max_tokens?: number
  }) => post<LlmProviderDto>('/llm/providers', payload),

  updateProvider: (
    id: string,
    payload: {
      name?: string
      type?: ProviderType
      base_url?: string
      api_key_env?: string
      api_key_value?: string
      clear_api_key?: boolean
      extra?: Record<string, unknown>
      enabled?: boolean
      priority?: number
      timeout_sec?: number
      temperature?: number
      max_tokens?: number
    },
  ) => patch<LlmProviderDto>(`/llm/providers/${id}`, payload),

  deleteProvider: (id: string) => del<{ id: string; deleted: boolean }>(`/llm/providers/${id}`),

  testProvider: (id: string, modelId?: string) =>
    post<ProviderTestResult>(`/llm/providers/${id}/test`, { model_id: modelId ?? null }),

  listModels: (providerId?: string) =>
    get<LlmModelDto[]>('/llm/models', { params: providerId ? { provider_id: providerId } : undefined }),

  createModel: (payload: {
    provider_id: string
    model_id: string
    display_name?: string
    max_context?: number
    supports_tools?: boolean
    default_params?: Record<string, unknown>
    is_default?: boolean
    enabled?: boolean
    fallback_chain?: string[]
  }) => post<LlmModelDto>('/llm/models', payload),

  updateModel: (
    id: string,
    payload: {
      model_id?: string
      display_name?: string
      max_context?: number
      supports_tools?: boolean
      default_params?: Record<string, unknown>
      is_default?: boolean
      enabled?: boolean
      fallback_chain?: string[]
    },
  ) => patch<LlmModelDto>(`/llm/models/${id}`, payload),

  deleteModel: (id: string) => del<{ id: string; deleted: boolean }>(`/llm/models/${id}`),

  reloadConfig: () => post<{ config_version: number; langfuse_enabled: boolean }>('/config/reload'),
}

// ---------------------------------------------------------------------------
// 子代理 / 技能（M5）
// ---------------------------------------------------------------------------

export interface SubagentDto {
  id: string
  name: string
  description: string
  system_prompt?: string | null
  model_ref?: string | null
  tools: string[]
  skills: string[]
  mode: 'isolated' | 'fork'
  enabled: boolean
  is_builtin: boolean
  created_at?: string | null
  updated_at?: string | null
}

export interface SkillDto {
  id: string
  name: string
  description: string
  content_md: string
  allowed_tools: string[]
  assets: unknown[]
  enabled: boolean
  is_builtin: boolean
  materialized: boolean
  /** v1028：内置技能对应的真实工具名（read_file/edit_file/ls/grep/glob/web_search） */
  tool_name?: string
  created_at?: string | null
  updated_at?: string | null
}

export const configApi = {
  listSubagents: () => get<SubagentDto[]>('/subagents'),

  createSubagent: (payload: {
    name: string
    description: string
    system_prompt?: string
    model_ref?: string
    tools?: string[]
    skills?: string[]
    mode?: string
    enabled?: boolean
  }) => post<SubagentDto>('/subagents', payload),

  updateSubagent: (id: string, payload: Partial<Omit<SubagentDto, 'id'>>) =>
    patch<SubagentDto>(`/subagents/${id}`, payload),

  deleteSubagent: (id: string) => del<{ id: string; deleted: boolean }>(`/subagents/${id}`),

  resetSubagent: (id: string) => post<SubagentDto>(`/subagents/${id}/reset`),

  listSkills: () => get<SkillDto[]>('/skills'),

  createSkill: (payload: {
    name: string
    description: string
    body?: string
    allowed_tools?: string[]
    enabled?: boolean
  }) => post<SkillDto>('/skills', payload),

  updateSkill: (id: string, payload: {
    name?: string
    description?: string
    body?: string
    allowed_tools?: string[]
    enabled?: boolean
  }) => patch<SkillDto>(`/skills/${id}`, payload),

  deleteSkill: (id: string) => del<{ id: string; deleted: boolean }>(`/skills/${id}`),

  resyncSkills: () => post<{ materialized: number; removed: number; total: number }>('/skills/resync'),
}

// ---------------------------------------------------------------------------
// 动态组件（M6）
// ★v1028：前端已删除「MCP 服务」管理页（mcpApi 随之移除）；
//   后端 /mcp-servers API 与内置天气 MCP 保留，天气 MCP 仍由 Agent 自动装配。
// ---------------------------------------------------------------------------

export interface WeatherDto {
  city: string
  /** v1029：具体地址（反向地理编码得到，可为空）——第一排城市右侧小字 */
  address?: string | null
  latitude: number
  longitude: number
  temperature: number | null
  apparent_temperature: number | null
  humidity: number | null
  wind_speed: number | null
  /** v1029：蒲福风力等级（0–12），组件以「级」为单位展示风速 */
  wind_level?: number | null
  weather_code: number | null
  description: string
  icon: string
  observed_at?: string | null
  /** v1028：空气质量指数（US AQI）与等级（优/良/轻度污染…） */
  aqi?: number | null
  aqi_level?: string | null
}

export interface PetStateDto {
  state: 'writing' | 'idle'
  idle_minutes_threshold: number
  novel_id?: string | null
  last_updated?: string | null
}

export const widgetsApi = {
  /** 坐标优先；无坐标传 city；都不传后端回落设置城市。refresh=true 跳过缓存取实时数据 */
  weather: (params: { latitude?: number; longitude?: number; city?: string; refresh?: boolean }) =>
    get<WeatherDto>('/widgets/weather', { params }),

  city: (latitude: number, longitude: number) =>
    get<{ city: string; latitude: number; longitude: number; source: string }>('/widgets/city', {
      params: { latitude, longitude },
    }),

  petState: (novelId?: string) =>
    get<PetStateDto>('/widgets/pet-state', { params: novelId ? { novel_id: novelId } : undefined }),
}

// ---------------------------------------------------------------------------
// Agent 会话 / 消息 / 运行（M3）
// ---------------------------------------------------------------------------

export const agentApi = {
  listSessions: (novelId?: string) =>
    get<AgentSessionDto[]>('/agent/sessions', { params: novelId ? { novel_id: novelId } : undefined }),

  /** v1028：项目用量（会话数量 + 累计 token） */
  usage: (novelId: string) =>
    get<{ project_id: string; session_count: number; tokens_in: number; tokens_out: number; tokens_total: number }>(
      '/agent/usage',
      { params: { novel_id: novelId } },
    ),

  /** v1029：会话上下文占用（最近一次 run 的输入 token） */
  sessionUsage: (sessionId: string) =>
    get<{ session_id: string; tokens_in: number; tokens_out: number }>(
      `/agent/sessions/${sessionId}/usage`,
    ),

  createSession: (payload: { novel_id: string; title?: string; model_ref?: string }) =>
    post<AgentSessionDto>('/agent/sessions', payload),

  renameSession: (id: string, title: string) => patch<AgentSessionDto>(`/agent/sessions/${id}`, { title }),

  deleteSession: (id: string) => del<{ id: string; deleted: boolean }>(`/agent/sessions/${id}`),

  updateContext: (
    id: string,
    payload: {
      model_ref?: string | null
      subagent_ids?: string[]
      skill_ids?: string[]
      context_chapter_ids?: string[]
    },
  ) => patch<AgentSessionDto>(`/agent/sessions/${id}/context`, payload),

  getMessages: (id: string) => get<MessageTree>(`/agent/sessions/${id}/messages`),

  /** 发消息 → 202 + {run_id}；事件走 streamUrl(run_id) */
  postMessage: (
    id: string,
    payload: { content: string; model_ref?: string; parent_id?: string; target_chapter_id?: string },
  ) => post<{ run_id: string; session_id: string }>(`/agent/sessions/${id}/messages`, payload),

  switchBranch: (id: string, messageId: string) =>
    post<{ session_id: string; active_leaf_id: string }>(`/agent/sessions/${id}/branch`, {
      message_id: messageId,
    }),

  getRun: (runId: string) => get<AgentRunDto>(`/agent/runs/${runId}`),

  cancelRun: (runId: string) => post<{ run_id: string; cancelled: boolean }>(`/agent/runs/${runId}/cancel`),

  /** v1028：运行中「通过消息引导发送」（把引导文本注入正在跑的 run） */
  steerRun: (runId: string, content: string) =>
    post<{ run_id: string; steered: boolean }>(`/agent/runs/${runId}/steer`, { content }),

  resolveInterrupt: (
    interruptId: string,
    payload: {
      decision: 'approve' | 'edit' | 'reject' | 'respond' | 'option' | 'text'
      edited_args?: Record<string, unknown>
      message?: string
      choice?: string
      text?: string
    },
  ) => post<{ interrupt_id: string; run_id: string; resumed: boolean }>(
    `/agent/interrupts/${interruptId}/resolve`,
    payload,
  ),

  /** SSE 流地址（run 级）。EventSource 同源直连 /api/v1。 */
  streamUrl: (runId: string, lastEventId?: number) =>
    `/api/v1/agent/runs/${runId}/stream${lastEventId ? `?last_event_id=${lastEventId}` : ''}`,
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
