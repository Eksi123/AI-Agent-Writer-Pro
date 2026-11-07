<script setup lang="ts">
/**
 * AI 写作助手会话框：
 *
 * 头部：只展示「当前会话数量 + 当前小说项目 token 使用量」（删除原「AI助手」标题与 + 号）
 * 中部：
 *   - 无小说项目 → “还没有小说项目哦~”
 *   - 有项目未进入会话 → 会话记录列表（标题=首问总结，小字日期；最新在上；最多 50 条）
 *     · 无任何会话 → “快点来开启AI写作吧~”
 *   - 已进入会话 → 一问一答（每条下方小字标记日期时间）
 * 输入框：+ / 工具 / Model / 上下文使用情况 / 发送箭头
 *   - 运行中点击发送箭头 → 三选项：停止并发送 / 通过消息引导发送 / 添加到队列
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAgentStore } from '@/stores/agent'
import { useProjectStore } from '@/stores/project'
import { agentApi, configApi, llmApi } from '@/api'
import { errorMessage } from '@/api/http'
import { t } from '@/i18n'
import { describeAgentError, type AgentErrorInfo } from '@/utils/agentErrors'
import type { ContextBlock, LlmOverview } from '@/types'
import ApprovalCard from './ApprovalCard.vue'
import ConsultCard from './ConsultCard.vue'
import PlanTodoList from './PlanTodoList.vue'
import WorkflowStatusBar from './WorkflowStatusBar.vue'
import ContextPicker from './ContextPicker.vue'
import ToolsPicker from './ToolsPicker.vue'

const agent = useAgentStore()
const project = useProjectStore()

const input = ref('')
const sending = ref(false)
const messageHost = ref<HTMLElement | null>(null)
/** 「模型思考和工具调用」过程框展开态（默认展开；弹卡隐藏期间不展示，续跑后保持原态） */
const processOpen = ref(true)
/** 审批/询问卡片待处理中——过程框与最终回复都不展示（同属 AI 思考中间过程） */
const cardPending = computed(
  () => agent.pendingInterrupts.length > 0 || agent.pendingConsult !== null,
)
const overview = ref<LlmOverview | null>(null)
/**
 * 选中的模型（未选中或无模型 → Model Off）。
 * 默认值取「上次使用的模型」（localStorage 记忆），而不是后端的默认模型。
 */
const modelRef = ref<string>('')

const LAST_MODEL_KEY = 'aiwriter:lastModelRef'

function readLastModelRef(): string | null {
  try {
    return localStorage.getItem(LAST_MODEL_KEY)
  } catch {
    return null
  }
}

function rememberModelRef(modelRefValue: string): void {
  try {
    if (modelRefValue) localStorage.setItem(LAST_MODEL_KEY, modelRefValue)
    else localStorage.removeItem(LAST_MODEL_KEY)
  } catch {
    /* 隐私模式/配额满：忽略，模型记忆是增强功能 */
  }
}

/** 选择模型即记住，下次进入会话框默认就是它 */
function onModelPicked(value: string): void {
  modelRef.value = value
  rememberModelRef(value)
}
/** 发送方式三选项弹层 */
const sendMenu = ref(false)
/** 本轮待随消息发送的隐藏上下文（选区润色提示词，不在会话展示） */
const pendingHidden = ref('')

/** 会话记录最多展示 50 条 */
const MAX_SESSIONS = 50
const sessionList = computed(() => agent.sessions.slice(0, MAX_SESSIONS))

/**
 * 会话是否「AI 思考执行中」——服务端 is_running 或本地运行标记任一命中即算
 * （本地标记覆盖「刚发出消息、服务端尚未落 running」的瞬时空窗）。
 */
function isSessionRunning(s: { id: string; is_running?: boolean }): boolean {
  return Boolean(s.is_running) || agent.runningSessionIds.has(s.id)
}

/**
 * 输入框随内容自适应上拉，**最多到页面高度的 1/3**。
 * Element Plus 的 autosize 以「行数」为单位，故按视口高度换算最大行数
 * （行高与 .chat-input 的 line-height 保持一致）。
 */
const INPUT_LINE_HEIGHT = 22
const maxInputRows = ref(4)

function recalcInputRows(): void {
  const limit = Math.floor(window.innerHeight / 3 / INPUT_LINE_HEIGHT)
  maxInputRows.value = Math.max(3, limit)
}

const inputAutosize = computed(() => ({ minRows: 2, maxRows: maxInputRows.value }))

// ---------------------------------------------------------------------------
// 头部：会话数量 + 项目 token 使用量
// ---------------------------------------------------------------------------

const sessionCountLabel = computed(() => t('agent.sessionCount', { n: agent.usage.session_count }))

/**
 * token 数量统一单位——千级显示 K、百万级显示 M（整数时去掉多余的 .0）。
 * 例：47324 → 47.3K；1200000 → 1.2M；33000 → 33K；500 → 500。
 */
function formatTokens(n: number): string {
  const v = Math.max(0, Math.round(n))
  if (v >= 1_000_000) return `${stripTrailingZero((v / 1_000_000).toFixed(1))}M`
  if (v >= 1_000) return `${stripTrailingZero((v / 1_000).toFixed(1))}K`
  return String(v)
}

function stripTrailingZero(s: string): string {
  return s.endsWith('.0') ? s.slice(0, -2) : s
}

/** token 使用量——头部统计**全部会话**的输入+输出消耗：
 * 已完成 run 总和（usage.tokens_total）+ 进行中 run 的实时累计（runCumulative，含子代理） */
const tokenLabel = computed(() => {
  const total = agent.usage.tokens_total + agent.runCumulative.tokens_in + agent.runCumulative.tokens_out
  return t('agent.tokenTotal', { n: formatTokens(total) })
})

/**
 * token 缓存命中率 = 命中前缀缓存的输入 token ÷ 总输入 token（动态实时）。
 * 聚合口径与头部 token 统计一致：已完成 run（usage）+ 进行中 run 实时累计（runCumulative）。
 * 无任何输入 token（新项目/未跑过 run）→ null（不展示图标，避免 0% 误导）。
 */
const cacheTotals = computed(() => ({
  in: agent.usage.tokens_in + agent.runCumulative.tokens_in,
  cached: agent.usage.cached_in + agent.runCumulative.cached_in,
}))

const cacheHitPct = computed<number | null>(() => {
  const { in: tin, cached } = cacheTotals.value
  if (tin <= 0) return null
  return Math.max(0, Math.min(100, Math.round((cached / tin) * 100)))
})

const cacheHitLabel = computed(() =>
  cacheHitPct.value === null ? '' : t('agent.cacheHitRate', { n: String(cacheHitPct.value) }),
)

// ---------------------------------------------------------------------------
// 上下文使用情况：{当前模型已使用上下文token}/{当前模型的上下文窗口值}
// ---------------------------------------------------------------------------

const selectedModel = computed(() =>
  (overview.value?.models ?? []).find((m) => m.model_ref === modelRef.value) ?? null,
)

/** 已使用上下文 = 最近一次模型调用的输入 token（近似当前上下文占用）；单位统一 K/M */
const contextLabel = computed(() =>
  selectedModel.value
    ? `${formatTokens(Math.max(0, agent.lastUsage.tokens_in))} / ${formatTokens(selectedModel.value.max_context || 0)}`
    : '0',
)

/**
 * （大模型选择弹窗 1.4）：剩余上下文百分比 = 1 − 已注入token ÷ 模型配置窗口。
 * - 已注入 = 当前会话最近一次模型调用的输入 token（含隐藏上下文/前情提要）；
 * - 窗口 = 当前选中模型的 Context Length（模型行 max_context）；
 * - 无模型 / 窗口未知 → null（不展示，避免误导）；
 * - 结果夹在 [0, 100]：占用超过窗口时归 0；
 * - 剩余 <10% 时后端自动开启上下文压缩（触发点 = 窗口 × 90%）。
 */
const contextRemainingPct = computed<number | null>(() => {
  const model = selectedModel.value
  const win = model?.max_context
  if (!model || !win || win <= 0) return null
  const used = Math.max(0, agent.lastUsage.tokens_in)
  const remaining = Math.round((1 - used / win) * 100)
  return Math.max(0, Math.min(100, remaining))
})

/** 剩余 <20% → 高亮警示（压缩机制即将/已经介入） */
const contextLow = computed(
  () => contextRemainingPct.value !== null && contextRemainingPct.value < 20,
)

// ---------------------------------------------------------------------------
// 消息渲染（一问一答 + 日期时间）
// ---------------------------------------------------------------------------

interface DisplayMessage {
  key: string
  role: 'user' | 'assistant' | 'tool'
  content: string
  toolCalls: { name: string; args?: Record<string, unknown> }[]
  streaming: boolean
  createdAt?: string | null
  /** 用户消息携带的添加文件标签（仿 VS Code Copilot，展示在气泡上方） */
  attachments?: string[]
}

/**
 * 发送内容 → 添加文件标签 的映射。
 * 消息正文（含隐藏上下文后的可见部分）作为键，持久化用户气泡与乐观回显气泡
 * 都按内容查表展示附件 chips；会话内常驻，历史消息也能看到当时带了哪些文件。
 */
const attachmentLabels = ref<Record<string, string[]>>({})

/**
 * 内部注入消息（系统守护纠正 / 系统修复 / 引导 / 收尾 nudge）——
 * 属 Agent 内部上下文，**永远不作为回复内容、不渲染成气泡**（含历史遗留落库数据）。
 */
const INTERNAL_PREFIXES = ['（系统守护', '（系统修复', '（用户补充指示']

function isInternalMessage(m: { role: string; content?: string | null }): boolean {
  if (m.role === 'system') return true
  const text = String(m.content ?? '')
  return INTERNAL_PREFIXES.some((p) => text.startsWith(p))
}

const displayMessages = computed<DisplayMessage[]>(() => {
  const persisted: DisplayMessage[] = agent.messages
    // 中间件注入的 system（前情提要等）属内部上下文，绝不渲染成气泡
    .filter((m) => m.role !== 'system')
    // 内部注入消息（系统守护/引导/nudge）绝不渲染——含历史遗留落库数据
    .filter((m) => !isInternalMessage(m))
    .map((m) => ({
      key: m.id,
      // tool 消息保留独立角色——collapseTurns 视其为中间过程，
      // 绝不当作「最终回复」展示（修复重进会话看到 @@ lines … 工具结果的问题）
      role: m.role === 'user' ? 'user' : m.role === 'tool' ? 'tool' : 'assistant',
      content: String(m.content ?? ''),
      toolCalls: m.tool_calls ?? [],
      streaming: false,
      createdAt: m.created_at,
      // 用户气泡上方展示发送时携带的添加文件
      attachments: m.role === 'user' ? attachmentLabels.value[String(m.content ?? '')] : undefined,
    }))
  // AI 思考结束后只展示每轮问答的**最后回复**——中间的思考过程、
  // 工具调用卡片、工具结果一律折叠不显示；运行中的实时过程由下方 live 流式消息展示。
  // 暂停态（审批/咨询卡片等待答复）视同运行中：不落库的中间消息不会进来，
  // 但持久化里若残留本轮中间文本也一并隐藏，只留卡片。
  const shown = collapseTurns(persisted, agent.running || agent.runPaused)
  // 去重：steered 消息若内容已出现在持久化消息中（后端已将其作为普通用户消息
  // 持久化），则从 shown 中移除，避免同一条内容同时以「引导」气泡和普通气泡展示。
  const steeredContents = new Set(agent.steered.map((s) => s.content))
  const deduped = shown.filter(
    (m) => !(m.role === 'user' && steeredContents.has(m.content)),
  )
  // 乐观回显——刚发出、后端消息树还没回来的用户消息先本地展示；
  // 后端已持久化同一条（末尾 user 消息内容一致）时不再重复显示。
  // pending 带本地发送时间戳，乐观回显的用户消息下方同样显示日期时间。
  const pending = agent.pendingUserMessage
  if (pending) {
    const lastUser = [...deduped].reverse().find((m) => m.role === 'user')
    if (lastUser?.content !== pending.content) {
      deduped.push({
        key: 'pending-user',
        role: 'user',
        content: pending.content,
        toolCalls: [],
        streaming: false,
        createdAt: pending.sentAt,
        attachments: attachmentLabels.value[pending.content],
      })
    }
  }
  // 中间思考/工具调用/子代理过程**永不展示在会话框中**（用户要求）；
  // 气泡里只保留每轮问答的最终总结回复。
  return deduped
})

/**
 * 把已持久化的消息按「轮」折叠——每个 user 消息开启一轮，
 * 该轮到下一个 user 消息之间的 assistant / tool 消息都视为思考过程，
 * 只保留**最后一条有正文的 assistant 消息**作为本轮回复（并去掉它身上的工具卡片）。
 * 找不到有正文的 assistant（异常轮）时原样保留，避免渲染空气泡。
 */
/**
 * 每轮问答只展示**最终回复**——中间思考、工具调用卡片一律折叠到下方「思考与工具调用过程」区。
 *
 * **运行中隐藏当前 turn 的中间过程**：run 未结束时，persisted 里的 assistant/tool 消息
 * 可能是中间处理文本或工具调用结果，并非最终回复；此时由下方 live 流式区承载进度。
 * 但当 run 处于 awaiting_approval / awaiting_consult 状态时，审批/咨询卡片会独立渲染，
 * 中间过程仍需隐藏以避免与卡片重复展示。run 结束后正常展示最终回复。
 */
function collapseTurns(list: DisplayMessage[], isRunning: boolean): DisplayMessage[] {
  const out: DisplayMessage[] = []
  let turn: DisplayMessage[] = []
  const flush = (skipNonUserIfRunning: boolean): void => {
    if (!turn.length) return
    // 运行中：不展示当前 turn 的任何非 user 消息（中间过程由 live 区承载）
    if (skipNonUserIfRunning && turn.some((m) => m.role !== 'user')) {
      turn = []
      return
    }
    let finalIdx = -1
    for (let i = turn.length - 1; i >= 0; i--) {
      if (turn[i].role === 'assistant' && turn[i].content.trim()) {
        finalIdx = i
        break
      }
    }
    if (finalIdx >= 0) out.push({ ...turn[finalIdx], toolCalls: [] })
    turn = []
  }
  for (const m of list) {
    if (m.role === 'user') {
      flush(isRunning)
      out.push(m)
    } else {
      turn.push(m)
    }
  }
  flush(isRunning)
  return out
}

/** 每条问答下方的小字日期时间 */
function stampOf(iso?: string | null): string {
  const d = iso ? new Date(iso) : new Date()
  if (Number.isNaN(d.getTime())) return ''
  const pad = (n: number): string => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/**
 * AI 回复按 Markdown 渲染（加粗 / 列表 / 表格 / 代码块等），
 * 与大纲阅读视图同形制（markdown-it + DOMPurify 消毒）。
 */
const md = new MarkdownIt({ html: false, linkify: true, breaks: true })

/** 去掉回复末尾的多余空行/空白（模型常以 \n\n 收尾，渲染后会留出大片空白） */
function trimTrailing(text: string): string {
  return text.replace(/\s+$/, '')
}

function renderMarkdown(text: string): string {
  return DOMPurify.sanitize(md.render(text || ''))
}

/** 会话记录里的小字号日期 */
function dateOf(iso?: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const pad = (n: number): string => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

async function scrollToBottom(): Promise<void> {
  await nextTick()
  const el = messageHost.value
  if (el) el.scrollTop = el.scrollHeight
}

watch(() => displayMessages.value.length, () => void scrollToBottom())
watch(
  () => agent.streaming.map((s) => s.content.length).join(','),
  () => void scrollToBottom(),
)

// ---------------------------------------------------------------------------
// 加载
// ---------------------------------------------------------------------------

async function loadOverview(): Promise<void> {
  try {
    overview.value = await llmApi.overview()
    /**
     * 默认选中「上次使用的模型」，而不是后端标记的默认（新建）模型。
     * 记忆值失效（模型已删/被禁用）时回落到后端默认模型；都没有则留空（Model Off）。
     */
    if (!modelRef.value) {
      const remembered = readLastModelRef()
      const available = overview.value.models.some((m) => m.model_ref === remembered)
      const def = overview.value.models.find((m) => m.is_default)
      modelRef.value = available ? remembered! : (def?.model_ref ?? '')
    }
  } catch {
    overview.value = null
  }
}

const hasModels = computed(() => (overview.value?.models.length ?? 0) > 0)

onMounted(async () => {
  // 输入框最大高度 = 视口的 1/3，随窗口尺寸变化重算
  recalcInputRows()
  window.addEventListener('resize', recalcInputRows)
  await loadOverview()
  if (project.currentNovelId) await agent.loadSessions(project.currentNovelId)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', recalcInputRows)
  agent.stopStream()
})

watch(
  () => project.currentNovelId,
  async (id) => {
    markUserLeftChat()
    agent.backToList()
    if (id) await agent.loadSessions(id)
  },
)

/**
 * 对话视图粘性守卫——回答结束（或任何非用户主动操作）后若会话视图被
 * 隐式重置回列表，自动粘回原会话，保证「回答结束后仍停留在对话视图」。
 * 用户主动返回 / 删除会话 / 切换小说时先 markUserLeftChat()，不参与粘回。
 */
const lastViewedSessionId = ref<string | null>(null)
let userLeftChat = false

function markUserLeftChat(): void {
  userLeftChat = true
}

function restoreChatView(): void {
  const target = lastViewedSessionId.value
  if (!target || agent.running) return // 运行中不抢视图，等 run 结束由 running watcher 粘回
  void agent.selectSession(target)
}

watch(
  () => agent.currentSessionId,
  (id, prev) => {
    if (id) lastViewedSessionId.value = id
    if (!id && prev && !userLeftChat) restoreChatView()
    if (!id) userLeftChat = false
  },
)

watch(
  () => agent.running,
  (running, wasRunning) => {
    if (wasRunning && !running && !agent.currentSessionId && !userLeftChat) restoreChatView()
  },
)

watch(
  () => agent.fileChanged,
  (evt) => {
    if (evt) ElMessage.info(t('agent.fileChanged', { path: evt.path }))
  },
)

// ---------------------------------------------------------------------------
// 会话操作
// ---------------------------------------------------------------------------

async function openSession(id: string): Promise<void> {
  await agent.selectSession(id)
}

function backToList(): void {
  markUserLeftChat()
  agent.backToList()
  void agent.refreshSessionsLight()
}

async function newSession(): Promise<void> {
  if (!project.currentNovelId) return
  try {
    await agent.createSession(project.currentNovelId, undefined, modelRef.value || undefined)
  } catch (err) {
    ElMessage.error(errorMessage(err))
  }
}

async function renameSession(id: string, title: string): Promise<void> {
  try {
    const { value } = await ElMessageBox.prompt(t('agent.renameSession'), '', {
      inputValue: title,
      confirmButtonText: t('common.confirm'),
      cancelButtonText: t('common.cancel'),
      inputValidator: (v: string) => (v && v.trim() ? true : t('common.required')),
    })
    await agent.renameSession(id, value.trim())
  } catch {
    /* 取消 */
  }
}

async function removeSession(id: string): Promise<void> {
  try {
    await ElMessageBox.confirm(t('agent.deleteSessionConfirm'), t('agent.deleteSession'), {
      confirmButtonText: t('common.delete'),
      cancelButtonText: t('common.cancel'),
      type: 'warning',
    })
    markUserLeftChat()
    await agent.deleteSession(id)
    agent.backToList()
  } catch {
    /* 取消 */
  }
}

// 删除「导出会话」功能（含会话记录列表 ··· 里的导出入口）

// ---------------------------------------------------------------------------
// 发送 / 运行中三选项（停止并发送 / 通过消息引导发送 / 添加到队列）
// ---------------------------------------------------------------------------

const canSend = computed(() => input.value.trim().length > 0 && !!project.currentNovelId)

/**
 * 底部发送按键的形态判定依据——运行中是否已输入「追加消息」。
 * 有输入 → 追加形态（停止后发送 / 添加到队列 / 添加为引导）；无输入 → 停止形态。
 */
const hasInput = computed(() => input.value.trim().length > 0)

/**
 * Agent / 大模型运行报错时，给出**中文提示 + 应该怎么做**（灰色异常态）。
 * 由 store 里的 {error, errorCode} 归一而来，避免直接把英文/原始 message 抛给用户。
 * 
 * 注意：后端连接失败错误（50001/uvicorn/后端未连接）由 BackendOfflineBanner 在主页面展示，
 * 不在会话框内重复显示。
 */
const errorInfo = computed<AgentErrorInfo | null>(() => {
  if (!agent.error) return null
  // 后端连接失败错误由 BackendOfflineBanner 处理，不在会话框显示
  if (
    agent.error.includes('50001') ||
    agent.error.includes('后端未连接') ||
    agent.error.includes('uvicorn')
  ) {
    return null
  }
  return describeAgentError(agent.errorCode, agent.error)
})

/** 用户手动关掉报错卡片（例如已修好配置，准备重试） */
function dismissError(): void {
  agent.clearError()
}

/** 聊天框任何情况下都不展示输入提示（placeholder 已删除） */

async function send(): Promise<void> {
  const content = input.value.trim()
  if (!content || sending.value) return
  if (!agent.currentSessionId) {
    keepChipsAcrossSessionChange.value = true
    try {
      await newSession()
      if (!agent.currentSessionId) return
      await persistChips()
    } finally {
      keepChipsAcrossSessionChange.value = false
    }
  }
  sending.value = true
  input.value = ''
  // 实际发起问答的模型即为「上次使用的模型」
  rememberModelRef(modelRef.value)
  // 隐藏上下文（润色提示词）随本条消息发送后清空
  const hidden = pendingHidden.value
  pendingHidden.value = ''
  try {
    await agent.sendMessage(content, modelRef.value || undefined, hidden || undefined)
  } catch (err) {
    ElMessage.error(errorMessage(err))
  } finally {
    sending.value = false
  }
}

/** 运行中：停止当前 run 后立即发送 */
async function stopAndSend(): Promise<void> {
  sendMenu.value = false
  const content = input.value.trim()
  if (!content) return
  await agent.cancelRun()
  input.value = ''
  const hidden = pendingHidden.value
  pendingHidden.value = ''
  try {
    await agent.sendMessage(content, modelRef.value || undefined, hidden || undefined)
  } catch (err) {
    ElMessage.error(errorMessage(err))
  }
}

/** 运行中：把引导文本注入正在跑的 run（不中断、不新建 run） */
async function steerSend(): Promise<void> {
  sendMenu.value = false
  const content = input.value.trim()
  if (!content) return
  try {
    await agent.steerCurrentRun(content)
    input.value = ''
    ElMessage.success(t('agent.steered'))
  } catch (err) {
    ElMessage.error(errorMessage(err))
  }
}

/** 运行中：加入队列，当前 run 结束后自动发送 */
function queueSend(): void {
  sendMenu.value = false
  const content = input.value.trim()
  if (!content) return
  const hidden = pendingHidden.value
  pendingHidden.value = ''
  agent.queueMessage(content, modelRef.value || undefined, hidden || undefined)
  input.value = ''
  ElMessage.success(t('agent.queued'))
}

// ---------------------------------------------------------------------------
// 排队消息——在会话流内展示；点击气泡回聊天框编辑/再发送；× 删除。
// AI 已在思考处理的消息（普通用户气泡）不提供任何编辑/删除入口。
// ---------------------------------------------------------------------------

/** 点击排队气泡：内容回到聊天输入框（移出队列），用户编辑后再次发送 */
function editQueuedFromChat(id: number): void {
  const item = agent.queued.find((q) => q.id === id)
  if (!item) return
  agent.removeQueued(id)
  input.value = item.content
  pendingHidden.value = item.hiddenContext ?? ''
  void nextTick(() => {
    const box = document.querySelector<HTMLTextAreaElement>('.chat-input textarea')
    box?.focus()
  })
}

function removeQueueItem(id: number): void {
  agent.removeQueued(id)
}

async function onStop(): Promise<void> {
  await agent.cancelRun()
  ElMessage.info(t('agent.cancelledRun'))
}

function copyText(text: string): void {
  void navigator.clipboard.writeText(text)
  ElMessage.success(t('common.copySuccess'))
}

function formatToolCall(tc: { name: string; args?: Record<string, unknown> }): string {
  try {
    const args = JSON.stringify(tc.args ?? {})
    return `${tc.name}(${args.length > 200 ? `${args.slice(0, 200)}…` : args})`
  } catch {
    return tc.name
  }
}

// ---------------------------------------------------------------------------
// 「+」添加内容：大纲 / 章节 / 本地文件，改由 ContextPicker 统一承载
// ---------------------------------------------------------------------------

/** 已添加的上下文块（随下一条消息一起发送；发送后释放） */
const blocks = ref<ContextBlock[]>([])
const addPopover = ref(false)

/**
 * 正文选区[AI润色]加入的上下文块（限定 AI 修改范围）。
 * 与「添加文件」同形制展示/发送；本轮问答结束后一并释放。
 * 块内带上「所在章节 + 段落范围 + 前后文」，让大模型知道这段文本
 * 在文章中的位置，从而只针对该范围润色修改。
 */
const selectionBlocks = computed<ContextBlock[]>(() =>
  agent.pendingSelectionBlocks.map((b) => ({
    kind: 'chapter' as const,
    label: b.label,
    text: b.text,
    chapterId: b.chapterId,
    rangeLabel: b.rangeLabel,
    before: b.before,
    after: b.after,
  })),
)

/** 输入框上方展示的全部上下文块 = 添加文件 + 选区润色块 */
const allBlocks = computed<ContextBlock[]>(() => [...selectionBlocks.value, ...blocks.value])

/**
 * 可见消息只含「添加文件」块；选区润色块走**隐藏上下文**
 * （随消息一起发给 Agent，但不在会话里展示提示词）。
 */
const visibleBlocks = computed<ContextBlock[]>(() =>
  allBlocks.value.filter((b) => !(b.rangeLabel && b.chapterId)),
)

function removeSelectionBlock(index: number): void {
  agent.removePendingSelection(index)
}

/** 已添加文件的字节合计（供 ContextPicker 做 20MB 上限校验） */
const attachedBytes = computed(() => blocks.value.reduce((s, b) => s + (b.bytes ?? 0), 0))

/** ContextPicker 选中一块内容 → 去重后加入 */
function onPicked(block: ContextBlock): void {
  const dup = allBlocks.value.some((b) => b.kind === block.kind && b.label === block.label)
  if (dup) {
    addPopover.value = false
    return
  }
  blocks.value = [...blocks.value, block]
  ElMessage.success(`已添加「${block.label}」`)
}

function removeBlock(idx: number): void {
  blocks.value = blocks.value.filter((_, i) => i !== idx)
}

/**
 * 组装最终**可见**消息：已选工具（#子代理 / @技能）+ 用户输入。
 * 添加文件/大纲/章节的**正文不再塞进对话框**——内容改走隐藏上下文
 * （随消息送给模型，但会话里只保留文件名标记与用户问题）。
 */
function composeMessage(): string {
  const question = input.value.trim()
  const parts: string[] = []

  if (chips.value.length) {
    const marks = chips.value
      .map((c) => (c.kind === 'subagent' ? `#${c.label}` : `@${c.label}`))
      .join(' ')
    parts.push(`【本次指定使用的工具】${marks}\n请优先调用以上工具完成本次任务。`)
  }

  if (question) parts.push(question)
  return parts.join('\n\n---\n\n')
}

/**
 * 组装**隐藏上下文**（选区润色提示词）——随本轮消息一起发给 Agent，
 * 后端以 system 消息请求级注入模型，不入消息树、不在会话里展示。
 * 添加的大纲/章节/本地文件正文也走这里——对话框里只留文件名标记，
 * 内容以「【文件名】…」形式随隐藏上下文送给模型。
 */
function composeHiddenContext(): string {
  const sections: string[] = []

  const attached = visibleBlocks.value
  if (attached.length) {
    sections.push(
      attached.map((b) => `【${b.label}】\n${b.text}`).join('\n\n---\n\n'),
    )
  }

  const polys = allBlocks.value.filter((b) => b.rangeLabel && b.chapterId)
  if (polys.length) {
    const parts = polys.map((b) => {
      const lines = [
        `【选中待润色文本】${b.label}`,
        `所在章节 id：${b.chapterId}`,
        `所在范围：${b.rangeLabel}`,
      ]
      if (b.before) lines.push('', '（选区前文，仅供衔接，请勿修改）', b.before)
      lines.push('', '（选中文本原文，只允许修改这一段）', b.text)
      if (b.after) lines.push('', '（选区后文，仅供衔接，请勿修改）', b.after)
      lines.push(
        '',
        '请按我的要求润色以上选中文本：只输出修改后的这一段（不要输出前文/后文），' +
          '完成后把修改结果写回该章节对应位置（写回前会先存快照，可回滚）。',
      )
      return lines.join('\n')
    })
    sections.push(parts.join('\n\n---\n\n'))
  }

  return sections.join('\n\n---\n\n')
}

/** 当前问答结束后释放文件缓存（上下文块清空）；选区润色块一并释放 */
function releaseAttached(): void {
  blocks.value = []
  agent.clearPendingSelections()
}

// ---------------------------------------------------------------------------
// 「工具」：子代理 / 技能
// ---------------------------------------------------------------------------

interface ChipItem {
  kind: 'subagent' | 'skill'
  id: string
  label: string
}
const chips = ref<ChipItem[]>([])
const toolPopover = ref(false)

/** id → name 映射：把会话已存的选中项回填成 chips（目录列表由 ToolsPicker 自行加载） */
const toolNames = ref<{ subagents: Map<string, string>; skills: Map<string, string> }>({
  subagents: new Map(),
  skills: new Map(),
})

async function loadToolNames(): Promise<void> {
  try {
    const [subs, sks] = await Promise.all([configApi.listSubagents(), configApi.listSkills()])
    toolNames.value = {
      subagents: new Map(subs.map((s) => [s.id, s.name])),
      // 会话级技能 = 用户自建 + 会话级内置写作技能（web_search）；其余基础工具恒可用不进 Chips
      skills: new Map(
        sks
          .filter((s) => s.kind !== 'tool' || s.session_builtin === true)
          .map((s) => [s.id, s.name]),
      ),
    }
  } catch {
    /* 名称解析失败不阻断输入（chips 会退回显示 id） */
  }
}

/** ToolsPicker 需要的高亮状态 */
const selectedSubagentIds = computed(() =>
  chips.value.filter((c) => c.kind === 'subagent').map((c) => c.id),
)
const selectedSkillIds = computed(() =>
  chips.value.filter((c) => c.kind === 'skill').map((c) => c.id),
)

/** 发送时若尚无会话：新建会话后把已选 chips 持久化进去（而非被会话切换回填清掉） */
const keepChipsAcrossSessionChange = ref(false)

watch(
  () => agent.currentSession,
  async (sess) => {
    if (!sess) {
      chips.value = []
      return
    }
    // 发送时新建会话的流程自行保留/持久化 chips，避免被此处回填清空
    if (keepChipsAcrossSessionChange.value) return
    await loadToolNames()
    const items: ChipItem[] = []
    for (const id of sess.subagent_ids) {
      items.push({ kind: 'subagent', id, label: toolNames.value.subagents.get(id) ?? id })
    }
    for (const id of sess.skill_ids) {
      items.push({ kind: 'skill', id, label: toolNames.value.skills.get(id) ?? id })
    }
    chips.value = items
  },
)

/** 选中子代理/技能 → chips 展示 `#名` / `@名`，并持久化到会话（后端据此装配 agent） */
async function toggleChip(kind: ChipItem['kind'], id: string, label: string): Promise<void> {
  const exists = chips.value.some((c) => c.kind === kind && c.id === id)
  chips.value = exists
    ? chips.value.filter((c) => !(c.kind === kind && c.id === id))
    : [...chips.value, { kind, id, label }]
  await persistChips()
}

async function persistChips(): Promise<void> {
  if (!agent.currentSessionId) return
  try {
    await agentApi.updateContext(agent.currentSessionId, {
      subagent_ids: selectedSubagentIds.value,
      skill_ids: selectedSkillIds.value,
    })
  } catch (err) {
    ElMessage.error(errorMessage(err))
  }
}

function removeChip(kind: ChipItem['kind'], id: string): void {
  chips.value = chips.value.filter((c) => !(c.kind === kind && c.id === id))
  void persistChips()
}

// ---------------------------------------------------------------------------
// [生成本章]按钮已移除——流程（四阶段工作流）改为用户在对话中表达
// 「按大纲某章简介生成/修改正文」的意图时由后端自动触发。
// ---------------------------------------------------------------------------

/** 回车/点击发送：把已选工具与上下文块合并进 input，再走正常发送或运行中三选项 */
function onSubmit(): void {
  if (!project.currentNovelId) return
  // 已添加的大纲/章节/本地文件、已选的 #子代理 / @技能 与用户输入合并成一条消息
  // 选区[AI润色]块不再拼进可见消息——提示词走隐藏上下文随消息一起发送
  // 添加文件正文也走隐藏上下文；只有附件没有输入文字时同样允许发送
  pendingHidden.value = composeHiddenContext()
  if (input.value.trim() && (visibleBlocks.value.length || chips.value.length)) {
    input.value = composeMessage()
  }
  if (!input.value.trim()) return
  // 记录「发送内容 → 添加文件标签」，用户气泡上方据此展示附件 chips
  if (visibleBlocks.value.length) {
    attachmentLabels.value = {
      ...attachmentLabels.value,
      [input.value.trim()]: visibleBlocks.value.map((b) => b.label),
    }
  }
  // 发送后清空输入区附件 chips（内容已随隐藏上下文送出，不再重复添加）
  releaseAttached()
  // 运行中 → 弹出三选项（停止并发送 / 通过消息引导发送 / 添加到队列）
  if (agent.running) {
    sendMenu.value = true
    return
  }
  if (!canSend.value) return
  void send()
}

/**
 * 当前问答结束后释放文件缓存。
 * `agent.running` 由 true → false 表示本轮 run 收尾（run.end / 缓冲丢失 / 取消），
 * 此时把已添加的大纲、章节、本地文件内容从内存中释放（不再随下一条消息携带）。
 */
watch(
  () => agent.running,
  (running, wasRunning) => {
    if (wasRunning && !running) {
      releaseAttached()
      pendingHidden.value = ''
    }
  },
)
</script>

<template>
  <div class="agent-panel">
    <!-- 头部：当前会话数量 + 当前小说项目 token 使用量 -->
    <div class="panel-head">
      <!-- 会话数换成「层叠文档」icon（原对话气泡留给 token 统计区分度） -->
      <span class="head-stat" :title="t('agent.sessionListTitle')">
        <svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden="true">
          <path d="M12 3 3 7.5 12 12l9-4.5L12 3zM5 11.2 3 12.2l9 4.5 9-4.5-2-1-7 3.5-7-3.5zM5 15.4 3 16.4l9 4.5 9-4.5-2-1-7 3.5-7-3.5z" />
        </svg>
        {{ sessionCountLabel }}
      </span>
      <!-- Token 消耗量悬浮展示 input / output 各自明细 -->
      <el-tooltip
        v-if="project.currentNovel"
        placement="bottom"
        :show-after="120"
        popper-class="usage-tip-popper"
      >
        <template #content>
          <!-- 悬浮只展示一行入/出明细（单位 K/M） -->
          <div class="usage-tip">{{ t('agent.usage', {
            in: formatTokens(agent.usage.tokens_in), out: formatTokens(agent.usage.tokens_out) }) }}</div>
        </template>
        <span class="head-stat">
          <svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden="true">
            <path d="M13 2 4.5 13.5H11L9.5 22 19 9.5h-6.5L13 2z" />
          </svg>
          {{ tokenLabel }}
        </span>
      </el-tooltip>
      <!-- Token 缓存命中率：命中前缀缓存的输入 token 占比（动态实时，run 进行中随 usage 事件刷新） -->
      <el-tooltip
        v-if="project.currentNovel && cacheHitPct !== null"
        placement="bottom"
        :show-after="120"
        popper-class="usage-tip-popper"
      >
        <template #content>
          <div class="usage-tip">
            缓存命中 {{ formatTokens(cacheTotals.cached) }} / 输入 {{ formatTokens(cacheTotals.in) }}
          </div>
        </template>
        <span class="head-stat" :class="{ 'cache-good': cacheHitPct >= 50, 'cache-low': cacheHitPct < 20 }">
          <svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden="true">
            <path d="M12 2C7 2 3 3.8 3 6s4 4 9 4 9-1.8 9-4-4-4-9-4zm9 8c0 2.2-4 4-9 4s-9-1.8-9-4v4c0 2.2 4 4 9 4s9-1.8 9-4v-4zm0 6c0 2.2-4 4-9 4s-9-1.8-9-4v4c0 2.2 4 4 9 4s9-1.8 9-4v-4z" />
          </svg>
          {{ cacheHitLabel }}
        </span>
      </el-tooltip>
      <div class="head-spacer" />
    </div>

    <!-- 删除「尚未配置大模型…」提示——未配置或未选择大模型时不需要提示
         头部不再放[新建会话]；运行状态条（AI 正在思考… / 停止）移入会话内部 -->

    <!-- ============ 无小说项目 ============ -->
    <div v-if="!project.currentNovel" class="panel-body center-body">
      <div class="empty-tip">{{ t('agent.noNovel') }}</div>
    </div>

    <!-- ============ 会话记录列表 ============ -->
    <div v-else-if="!agent.currentSessionId" class="panel-body scroll-y">
      <div v-if="!sessionList.length" class="center-body">
        <!-- 聊天框任何情况下都不再展示输入提示文案 -->
        <div class="empty-tip">{{ t('agent.noSession') }}</div>
      </div>
      <template v-else>
        <!-- 删除「会话记录 2/50」这一行——会话数量已在最上方头部展示 -->
        <div
          v-for="s in sessionList"
          :key="s.id"
          class="session-item"
          @click="openSession(s.id)"
        >
          <div class="session-main">
            <div class="session-title">{{ s.title }}</div>
            <div class="session-date">{{ dateOf(s.updated_at || s.created_at) }}</div>
          </div>
          <!-- 该会话有 Agent 正在思考执行 → 条目右侧灰色小字提示 -->
          <span v-if="isSessionRunning(s)" class="session-running">{{ t('agent.sessionRunning') }}</span>
          <el-dropdown trigger="click" placement="bottom-end" @command="(cmd: string) => {
            if (cmd === 'rename') renameSession(s.id, s.title)
            else if (cmd === 'delete') removeSession(s.id)
          }">
            <span class="session-more" @click.stop>···</span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="rename">{{ t('agent.renameSession') }}</el-dropdown-item>
                <!-- 删除「导出会话」入口 -->
                <el-dropdown-item command="delete">{{ t('agent.deleteSession') }}</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </template>
    </div>

    <!-- ============ 会话对话 ============ -->
    <template v-else>
      <!-- 会话内只保留「返回 + 会话标题」，右上角的[新建会话]与[···]已删除
           （重命名 / 导出 / 删除改由会话记录列表的 ··· 提供；新会话回到列表直接发消息即可开启） -->
      <div class="chat-head">
        <el-button text size="small" class="back-btn" @click="backToList">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true">
            <path d="M15.4 4.6a1 1 0 0 0-1.4 0l-6.7 6.7a1 1 0 0 0 0 1.4l6.7 6.7a1 1 0 0 0 1.4-1.4L9.4 12l6-6a1 1 0 0 0 0-1.4z" />
          </svg>
        </el-button>
        <span class="chat-title">{{ agent.currentSession?.title }}</span>
      </div>

      <div ref="messageHost" class="panel-body scroll-y">
        <div v-if="!displayMessages.length && !agent.running" class="center-body">
          <div class="empty-tip">{{ t('agent.noSession') }}</div>
        </div>

        <div
          v-for="msg in displayMessages"
          :key="msg.key"
          class="msg"
          :class="[`msg-${msg.role}`, { 'msg-streaming': msg.streaming }]"
        >
          <!-- 添加文件标签（仿 VS Code Copilot，展示在用户消息上方；灰色不可删除） -->
          <div v-if="msg.attachments?.length" class="msg-attachments">
            <el-tag
              v-for="label in msg.attachments"
              :key="label"
              size="small"
              type="info"
              effect="plain"
              class="attachment-tag"
              disable-transitions
            >
              {{ label }}
            </el-tag>
          </div>
          <div class="msg-content">
            <template v-if="msg.toolCalls?.length">
              <div v-for="(tc, i) in msg.toolCalls" :key="i" class="tool-card">
                <code>{{ formatToolCall(tc) }}</code>
              </div>
            </template>
            <div v-if="msg.content && msg.role === 'assistant'" class="msg-text">
              <!-- eslint-disable-next-line vue/no-v-html -->
              <div class="msg-prose" v-html="renderMarkdown(trimTrailing(msg.content))" />
            </div>
            <span v-else-if="msg.content" class="msg-text">{{ msg.content }}</span>
            <span v-if="msg.streaming" class="cursor-blink">▌</span>
          </div>
          <!-- 每个问答下方小字标记日期和时间 -->
          <div class="msg-foot">
            <span v-if="msg.createdAt" class="msg-time">{{ stampOf(msg.createdAt) }}</span>
            <span v-else-if="msg.streaming" class="msg-time">{{ t('agent.running') }}</span>
            <!-- 用户消息底部复制按钮（与时间戳并排） -->
            <el-button
              v-if="msg.role === 'user' && msg.content"
              text
              size="small"
              class="msg-copy"
              @click="copyText(msg.content)"
            >
              {{ t('agent.copyMessage') }}
            </el-button>
            <!-- AI 回复底部复制按钮 -->
            <el-button
              v-if="msg.role === 'assistant' && msg.content && !msg.streaming"
              text
              size="small"
              class="msg-copy"
              @click="copyText(msg.content)"
            >
              {{ t('agent.copyMessage') }}
            </el-button>
          </div>
        </div>

        <!-- 「AI 正在思考…」放进会话内部（模仿主流 AI Agent），
             不再占用会话框最上方一整行
             [停止] 从状态条移到底部发送按键（三形态：发送 / 停止 / 追加消息）
             置于「模型思考与工具调用过程」折叠区**上方**；
             子代理/流程标签一并收纳进过程区头部，状态行只留思考提示 -->
        <div v-if="agent.running" class="status-inline">
          <span class="dot-pulse" />
          <span class="status-text">{{ t('agent.running') }}</span>
        </div>

        <!-- 「模型思考和工具调用」过程框（AI 思考处理的中间过程）：
             仅运行中展示；出现审批/询问卡片（同属中间过程）时整框隐藏，
             用户处理完卡片续跑后重新出现；run 结束即隐藏，全程不暴露最终回复 -->
        <div v-if="agent.running && !cardPending" class="process-box">
          <button type="button" class="process-toggle" @click="processOpen = !processOpen">
            <span class="caret">{{ processOpen ? '▾' : '▸' }}</span>
            <span>{{ t('agent.processToggle') }}</span>
          </button>
          <div v-if="processOpen" class="process-body scroll-y">
            <template v-for="s in agent.streaming" :key="s.messageId">
              <div v-for="(tc, i) in s.toolCalls" :key="`${s.messageId}-t${i}`" class="tool-card">
                <code>{{ formatToolCall(tc) }}</code>
              </div>
              <div v-if="s.content && !s.toolCalls.length" class="process-text">{{ s.content }}</div>
            </template>
          </div>
        </div>

        <!-- 运行中「通过消息引导发送」的引导消息（展示在队列消息之前；
             已注入正在跑的 run，不可编辑/删除；run 结束后清空） -->
        <div v-for="s in agent.steered" :key="`s-${s.id}`" class="queue-bubble msg-user">
          <div class="msg-content steer-content">
            <el-tag size="small" type="warning" effect="plain" class="steer-tag">引导</el-tag>
            <span class="msg-text">{{ s.content }}</span>
          </div>
          <div class="msg-foot">
            <span class="msg-time">{{ stampOf(s.sentAt) }}</span>
          </div>
        </div>

        <!-- 排队中的补充消息（展示在「AI 正在思考…」下方；点击回聊天框编辑/再发送，× 在右上角删除） -->
        <div v-for="q in agent.queued" :key="`q-${q.id}`" class="queue-bubble msg-user">
          <button
            class="queue-del"
            :title="t('common.delete')"
            @click.stop="removeQueueItem(q.id)"
          >
            ×
          </button>
          <div class="msg-content" @click="editQueuedFromChat(q.id)">
            <span class="msg-text">{{ q.content }}</span>
          </div>
          <div class="msg-foot">
            <span v-if="q.queuedAt" class="msg-time">{{ stampOf(q.queuedAt) }}</span>
          </div>
        </div>

        <!-- 中间思考/工具调用/子代理过程**永不展示在会话框中**（用户要求）：
             过程区整块移除；会话里只留用户消息、最终总结回复、审批/咨询卡片与状态行 -->


        <!-- Agent / 大模型运行报错 → 中文提示 + 应该怎么做，
             整块灰色（代表异常不可用），像一条助手消息一样留在会话里 -->
        <div v-if="errorInfo" class="msg msg-assistant msg-error" role="alert">
          <div class="msg-content error-content">
            <div class="error-title">{{ errorInfo.title }}</div>
            <div class="error-advice">{{ errorInfo.advice }}</div>
          </div>
          <div class="msg-foot">
            <span class="msg-time">{{ t('agent.errorFoot') }}</span>
            <el-button text size="small" class="msg-copy" @click="dismissError">
              {{ t('agent.errorDismiss') }}
            </el-button>
          </div>
        </div>

        <PlanTodoList v-if="agent.todos.length" :todos="agent.todos" />

        <WorkflowStatusBar
          v-if="agent.workflowStage"
          :stage="agent.workflowStage"
          :retry-count="agent.workflowRetryCount"
          :g1-skipped="agent.workflowPrecheckSkipped"
          :results="agent.workflowResults"
        />

        <ApprovalCard v-if="agent.pendingInterrupts.length" :interrupts="agent.pendingInterrupts" />
        <ConsultCard v-if="agent.pendingConsult" :consult="agent.pendingConsult" />
      </div>
    </template>

    <!-- ============ 输入区（copilot 形制） ============ -->
    <div class="panel-input">
      <!-- 已添加的上下文块（发送后存为工作区文件，AI 按需读取；提示语见 title） -->
      <div v-if="allBlocks.length" class="blocks-row">
        <el-tag
          v-for="(b, i) in allBlocks"
          :key="`${b.kind}-${b.label}-${i}`"
          size="small"
          closable
          :type="b.kind === 'outline' ? 'warning' : b.kind === 'chapter' ? 'success' : 'info'"
          :title="
            i < selectionBlocks.length
              ? b.label
              : t('agent.attachmentStoredHint', { label: b.label })
          "
          @close="i < selectionBlocks.length ? removeSelectionBlock(i) : removeBlock(i - selectionBlocks.length)"
        >
          {{ b.label }}
        </el-tag>
      </div>

      <!-- 已选工具 -->
      <div v-if="chips.length" class="chips-row">
        <el-tag
          v-for="c in chips"
          :key="`${c.kind}-${c.id}`"
          size="small"
          closable
          :type="c.kind === 'subagent' ? 'primary' : 'success'"
          @close="removeChip(c.kind, c.id)"
        >
          {{ c.kind === 'subagent' ? `#${c.label}` : `@${c.label}` }}
        </el-tag>
      </div>

      <!-- 排队消息改在会话流内展示（点击气泡回聊天框编辑/再发送），输入区不再列表展示 -->

      <div class="input-box" :class="{ focused: false }">
        <!-- 随输入自适应上拉，最多到页面高度的 1/3 -->
        <el-input
          v-model="input"
          type="textarea"
          :autosize="inputAutosize"
          resize="none"
          :disabled="!project.currentNovelId"
          class="chat-input"
          @keydown.enter.exact.prevent="onSubmit"
        />

        <!-- 输入框内工具条：+ / 工具 / Model / 上下文使用情况 / 发送箭头 -->
        <div class="input-bar">
          <!-- ＋：添加文件页（搜索框 + 小说项目大纲或文章 + 本地文件 + 最近打开的章节） -->
          <el-popover
            v-model:visible="addPopover"
            placement="top-start"
            :width="300"
            trigger="click"
            popper-class="agent-pop"
          >
            <template #reference>
              <button class="bar-btn" :disabled="!project.currentNovelId" :title="t('agent.addContext')">＋</button>
            </template>
            <ContextPicker
              :attached-bytes="attachedBytes"
              :open="addPopover"
              @add="onPicked"
              @close="addPopover = false"
            />
          </el-popover>

          <!-- 工具：只展示 [写作子代理] / [写作技能] 两个入口，没有内容时不显示 -->
          <el-popover
            v-model:visible="toolPopover"
            placement="top-start"
            :width="280"
            trigger="click"
            popper-class="agent-pop"
          >
            <template #reference>
              <button class="bar-btn" :disabled="!project.currentNovelId">{{ t('agent.tools') }}</button>
            </template>
            <ToolsPicker
              :selected-subagent-ids="selectedSubagentIds"
              :selected-skill-ids="selectedSkillIds"
              :open="toolPopover"
              @toggle="toggleChip"
              @close="toolPopover = false"
            />
          </el-popover>

          <!-- Model：左侧无 icon、Model Off 不用斜体；
               弹窗内**不显示** Model Off 选项；无模型时在弹窗里提示「尚未配置大模型」；
               点击某模型名 → 收起弹窗，按钮文案变为该模型名称 -->
          <el-dropdown trigger="click" placement="top-start" @command="(v: string) => onModelPicked(v)">
            <button class="bar-btn model-btn" :class="{ off: !selectedModel }">
              {{ selectedModel ? selectedModel.display_name : t('agent.modelOff') }}
            </button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item v-if="!hasModels" disabled>
                  {{ t('agent.noModelsHint') }}
                </el-dropdown-item>
                <el-dropdown-item
                  v-for="m in overview?.models ?? []"
                  :key="m.model_ref"
                  :command="m.model_ref"
                  :class="{ 'is-selected': m.model_ref === modelRef }"
                >
                  <!-- 去掉模型名后的「· 33K」上下文窗口小字（用户不需要） -->
                  {{ m.display_name }}{{ m.is_default ? ' ★' : '' }}
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>

          <!-- （1.4）：Model Off 右侧显示「剩余上下文百分比」= 1 − 已注入 ÷ 配置窗口。
          每次模型回复刷新；新会话回到 100%；剩余 <10% 时高亮警示（压缩已介入）。
               未选模型 / 窗口未知时不展示。详细用量放进 title。 -->
          <span
            v-if="contextRemainingPct !== null"
            class="bar-ctx"
            :class="{ 'is-low': contextLow }"
            :title="`${t('agent.contextUsage')}：${contextLabel}`"
          >
            {{ t('agent.contextRemaining', { n: contextRemainingPct }) }}
          </span>

          <div class="bar-spacer" />

          <!-- 底部这颗按键统一承载「发送 / 停止 / 追加消息」三种形态（模仿主流大模型与 Agent）：
               · 未在运行                → 发送箭头（可发送时点亮）
               · 运行中 + 输入框为空      → 停止形态（方块），点击立即停止本轮回答
               · 运行中 + 已输入追加内容  → 追加形态，弹出「停止后发送 / 添加到队列 / 添加为引导」 -->
          <el-popover
            v-if="agent.running && hasInput"
            v-model:visible="sendMenu"
            placement="top-end"
            :width="250"
            trigger="click"
          >
            <template #reference>
              <button class="send-btn running" :title="t('agent.appendMessage')">
                <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <path d="M19 7H8" />
                  <path d="M11 3 7 7l4 4" />
                  <path d="M7 17h10" />
                </svg>
              </button>
            </template>
            <div class="send-menu">
              <button class="send-menu-item" @click="stopAndSend">
                <span class="smi-name">{{ t('agent.sendAnyway') }}</span>
                <span class="smi-hint">{{ t('agent.sendAnywayHint') }}</span>
              </button>
              <button class="send-menu-item" @click="queueSend">
                <span class="smi-name">{{ t('agent.queue') }}</span>
                <span class="smi-hint">{{ t('agent.queueHint') }}</span>
              </button>
              <button class="send-menu-item" @click="steerSend">
                <span class="smi-name">{{ t('agent.steer') }}</span>
                <span class="smi-hint">{{ t('agent.steerHint') }}</span>
              </button>
            </div>
          </el-popover>

          <!-- 运行中且没有输入追加内容 → 停止形态 -->
          <button
            v-else-if="agent.running"
            class="send-btn stop"
            :title="t('agent.stop')"
            @click="onStop"
          >
            <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" aria-hidden="true">
              <rect x="6" y="6" width="12" height="12" rx="1.5" />
            </svg>
          </button>

          <button
            v-else
            class="send-btn"
            :disabled="!canSend"
            :title="t('agent.send')"
            @click="onSubmit"
          >
            <svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor" aria-hidden="true">
              <path d="M3.4 20.4 21 12 3.4 3.6 3.4 10l12 2-12 2z" />
            </svg>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.agent-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

/* ---- 头部：会话数量 + token 用量 ---- */
.panel-head {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 9px 12px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
  font-size: 12px;
}

.head-stat {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--text-muted);
  white-space: nowrap;
}

/* 缓存命中率着色：≥50% 绿（缓存生效良好）、<20% 橙（几乎未命中） */
.head-stat.cache-good {
  color: var(--el-color-success);
}

.head-stat.cache-low {
  color: var(--el-color-warning);
}

.head-spacer {
  flex: 1;
}

/* 运行状态条移入会话内部（原 .status-bar 顶部整行已删除） */
.status-inline {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 2px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  flex-wrap: wrap;
}

.status-text {
  color: var(--el-text-color-secondary);
}

/* 「模型思考和工具调用」过程区：无外框，仅运行中显示、弹卡隐藏、续跑恢复 */
.process-box {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.process-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  border: none;
  background: transparent;
  color: var(--el-text-color-secondary);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
  padding: 2px 0;
  text-align: left;
}

.process-toggle .caret {
  flex: none;
  font-size: 10px;
}

.process-body {
  max-height: 180px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.process-text {
  font-size: 12px;
  line-height: 1.5;
  color: var(--el-text-color-secondary);
  white-space: pre-wrap;
  word-break: break-word;
}

/* [停止] 已移到底部发送按键，状态条里的 .status-stop 随之删除 */

.dot-pulse {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--el-color-primary);
  animation: pulse 1s infinite ease-in-out;
}

@keyframes pulse {
  0%, 100% { opacity: 0.3; }
  50% { opacity: 1; }
}

/* 报错卡片——中文提示 + 处置建议，整块灰色（异常不可用） */
.msg-error .error-content {
  background: var(--bg-hover);
  border: 1px solid var(--border);
  border-left: 3px solid var(--text-muted);
  border-radius: 8px;
  padding: 8px 10px;
  /* 灰色代表异常不可用 */
  color: var(--text-muted);
  max-width: 100%;
}

.error-title {
  font-size: 12.5px;
  font-weight: 600;
  line-height: 1.6;
}

.error-advice {
  margin-top: 3px;
  font-size: 11.5px;
  line-height: 1.6;
  opacity: 0.9;
}

/* ---- 主体 ---- */
.panel-body {
  flex: 1;
  min-height: 0;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.center-body {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 120px;
}

.empty-tip {
  color: var(--text-muted);
  font-size: 13px;
  text-align: center;
  line-height: 1.8;
}

/* ---- 会话记录列表 ---- */
.session-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 8px;
  border-radius: 6px;
  cursor: pointer;
}

.session-item:hover {
  background: var(--bg-hover);
}

.session-main {
  flex: 1;
  min-width: 0;
}

.session-title {
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.session-date {
  font-size: 11px;
  color: var(--text-muted);
  margin-top: 2px;
}

/* 会话条目右侧「AI思考执行中……」灰色小字 */
.session-running {
  flex-shrink: 0;
  font-size: 11px;
  color: var(--text-muted);
  white-space: nowrap;
}

.session-more {
  flex-shrink: 0;
  width: 22px;
  text-align: center;
  color: var(--text-muted);
  font-weight: 700;
  opacity: 0;
}

.session-item:hover .session-more {
  opacity: 1;
}

/* ---- 会话对话 ---- */
.chat-head {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 8px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

.back-btn {
  padding: 2px 6px;
}

.chat-title {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.msg {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.msg-user .msg-content {
  background: var(--el-color-primary-light-9);
  border-radius: 8px;
  padding: 8px 10px;
  align-self: flex-end;
  max-width: 92%;
}

.msg-assistant .msg-content,
.msg-tool .msg-content {
  background: var(--el-fill-color-light);
  border-radius: 8px;
  padding: 8px 10px;
  max-width: 100%;
}

.msg-text {
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 13px;
  line-height: 1.6;
}

.msg-streaming .msg-content {
  border-left: 2px solid var(--el-color-primary);
}

.msg-foot {
  display: flex;
  align-items: center;
  gap: 8px;
}

.msg-user .msg-foot {
  justify-content: flex-end;
}

.msg-time {
  font-size: 10px;
  color: var(--text-muted);
}

.msg-copy {
  padding: 0;
  height: 18px;
  font-size: 11px;
}

.cursor-blink {
  animation: blink 1s step-start infinite;
  color: var(--el-color-primary);
}

@keyframes blink {
  50% { opacity: 0; }
}

/* 用户消息上方的添加文件标签行（灰色、不可删除） */
.msg-attachments {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
  justify-content: flex-end;
  margin-bottom: 4px;
}

.attachment-tag {
  color: var(--text-muted);
  border-color: var(--border);
  background: var(--el-fill-color);
  cursor: default;
}

/* 排队消息气泡（位于「AI 正在思考…」下方；点击回聊天框编辑，× 右上角删除） */
.queue-bubble {
  position: relative;
  align-self: flex-end;
  max-width: 92%;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 2px;
}

.queue-bubble .msg-content {
  cursor: pointer;
  background: var(--el-color-primary-light-9);
  border: 1px dashed var(--el-color-primary-light-5);
  border-radius: 8px;
  padding: 8px 10px;
  max-width: 100%;
}

.queue-bubble .msg-content:hover {
  border-color: var(--el-color-primary);
}

/* 引导消息：不可交互，warning 虚线边 + 小标签 */
.steer-content {
  cursor: default;
  border-color: var(--el-color-warning-light-5);
  background: var(--el-color-warning-light-9);
  display: flex;
  align-items: flex-start;
  gap: 6px;
}

.steer-content:hover {
  border-color: var(--el-color-warning-light-5);
}

.steer-tag {
  flex-shrink: 0;
  margin-top: 2px;
}

.queue-del {
  position: absolute;
  top: -6px;
  right: -6px;
  z-index: 2;
  width: 16px;
  height: 16px;
  border: none;
  border-radius: 50%;
  background: var(--el-fill-color-darker, var(--el-fill-color));
  color: var(--text-muted);
  font-size: 12px;
  line-height: 1;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.queue-del:hover {
  color: var(--el-color-danger);
  background: var(--el-color-danger-light-9);
}

/* AI 回复的 markdown 排版（复用全局 .novel-prose，压缩外边距） */
.msg-prose {
  font-size: 13px;
  line-height: 1.6;
}

.msg-prose :deep(h1),
.msg-prose :deep(h2),
.msg-prose :deep(h3) {
  margin: 8px 0 4px;
  font-size: 14px;
}

.msg-prose :deep(p) {
  margin: 4px 0;
}

.msg-prose :deep(pre) {
  background: var(--el-fill-color);
  border-radius: 6px;
  padding: 6px 8px;
  overflow-x: auto;
  font-size: 12px;
}

.msg-prose :deep(table) {
  border-collapse: collapse;
  font-size: 12px;
  margin: 6px 0;
}

.msg-prose :deep(th),
.msg-prose :deep(td) {
  border: 1px solid var(--border);
  padding: 3px 8px;
}

.msg-prose :deep(ul),
.msg-prose :deep(ol) {
  padding-left: 18px;
  margin: 4px 0;
}

.tool-card {
  background: var(--el-fill-color);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 5px 7px;
  font-size: 11px;
  overflow-x: auto;
  margin-bottom: 4px;
}

.tool-card code {
  font-family: var(--el-font-family-mono, monospace);
  white-space: pre;
}

/* ---- 输入区 ---- */
.panel-input {
  border-top: 1px solid var(--border);
  padding: 8px 10px 10px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.blocks-row,
.chips-row {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}

.input-box {
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--bg-panel);
  overflow: hidden;
  transition: border-color 0.15s;
}

.input-box:focus-within {
  border-color: var(--el-color-primary);
}

.chat-input :deep(.el-textarea__inner) {
  border: none;
  box-shadow: none;
  background: transparent;
  padding: 8px 10px 4px;
  font-size: 13px;
  /* 行高固定，配合 autosize 的最大行数换算「最多到页面高度的 1/3」 */
  line-height: 22px;
}

.input-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 8px 6px;
}

.bar-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: transparent;
  color: var(--text-muted);
  font-size: 11px;
  line-height: 1.4;
  cursor: pointer;
  white-space: nowrap;
  transition: color 0.15s, border-color 0.15s;
}

.bar-btn:hover:not(:disabled) {
  color: var(--el-color-primary);
  border-color: var(--el-color-primary);
}

.bar-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.model-btn {
  max-width: 130px;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Model Off 不用斜体，仅以弱化色区分「未选择模型」 */
.model-btn.off {
  color: var(--text-muted);
}

.bar-ctx {
  font-size: 11px;
  color: var(--text-muted);
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

/* （1.4）：剩余上下文 <20% → 警示色，提示压缩机制即将/已经介入 */
.bar-ctx.is-low {
  color: var(--el-color-danger, #f56c6c);
  font-weight: 600;
}

.bar-spacer {
  flex: 1;
}

/* [重新生成]已删除，.regen-btn 样式一并移除；[生成本章]删除，.gen-btn 一并移除 */

.send-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: none;
  border-radius: 50%;
  background: var(--el-color-primary);
  color: #fff;
  cursor: pointer;
  flex-shrink: 0;
  transition: opacity 0.15s, background 0.15s;
}

.send-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.send-btn.running {
  background: var(--el-color-warning);
}

/* 停止形态（运行中且输入框为空）——红色方块，点击立即停止本轮回答 */
.send-btn.stop {
  background: var(--el-color-danger);
}

.send-menu {
  display: flex;
  flex-direction: column;
}

/* 每种发送模式 = 名称 + 一句说明（模仿主流大模型 / Agent） */
.send-menu-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  border: none;
  background: transparent;
  text-align: left;
  padding: 7px 8px;
  cursor: pointer;
  border-radius: 4px;
}

.send-menu-item + .send-menu-item {
  border-top: 1px solid var(--border);
}

.smi-name {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text);
}

.smi-hint {
  font-size: 11px;
  line-height: 1.5;
  color: var(--text-muted);
}

.send-menu-item:hover {
  background: var(--bg-hover);
}

.send-menu-item:hover .smi-name {
  color: var(--el-color-primary);
}

.muted {
  color: var(--text-muted);
}

.text-sm {
  font-size: 12px;
}
</style>

<style>
.el-dropdown-menu__item.is-selected {
  color: var(--el-color-primary);
  font-weight: 600;
}

/* 头部 Token 悬浮明细（popper teleport 到 body，需全局样式） */
.usage-tip {
  font-size: 12px;
  line-height: 1.6;
  white-space: nowrap;
}
</style>
