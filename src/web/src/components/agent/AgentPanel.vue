<script setup lang="ts">
/**
 * AI 写作助手会话框（v1028 重构，整体参考 VS Code Copilot 会话框）：
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
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAgentStore } from '@/stores/agent'
import { useProjectStore } from '@/stores/project'
import { useReaderStore } from '@/stores/reader'
import { agentApi, configApi, llmApi } from '@/api'
import { errorMessage } from '@/api/http'
import { t } from '@/i18n'
import { describeAgentError, type AgentErrorInfo } from '@/utils/agentErrors'
import type { ContextBlock, LlmOverview } from '@/types'
import ApprovalCard from './ApprovalCard.vue'
import ConsultCard from './ConsultCard.vue'
import PlanTodoList from './PlanTodoList.vue'
import GateStatusBar from './GateStatusBar.vue'
import ContextPicker from './ContextPicker.vue'
import ToolsPicker from './ToolsPicker.vue'

const agent = useAgentStore()
const project = useProjectStore()
const reader = useReaderStore()

const input = ref('')
const sending = ref(false)
const messageHost = ref<HTMLElement | null>(null)
const overview = ref<LlmOverview | null>(null)
/**
 * v1028：选中的模型（未选中或无模型 → Model Off）。
 * ★v1031：默认值取「上次使用的模型」（localStorage 记忆），而不是后端的默认模型。
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

/** v1028：会话记录最多展示 50 条 */
const MAX_SESSIONS = 50
const sessionList = computed(() => agent.sessions.slice(0, MAX_SESSIONS))

/**
 * v1029：输入框随内容自适应上拉，**最多到页面高度的 1/3**。
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

/** v1029：token 使用量（x.yK Token） */
const tokenLabel = computed(() =>
  t('agent.tokenTotal', { n: (agent.usage.tokens_total / 1000).toFixed(1) }),
)

// ---------------------------------------------------------------------------
// 上下文使用情况：{当前模型已使用上下文token}/{当前模型的上下文窗口值}
// ---------------------------------------------------------------------------

const selectedModel = computed(() =>
  (overview.value?.models ?? []).find((m) => m.model_ref === modelRef.value) ?? null,
)

const contextWindowK = computed(() => {
  const win = selectedModel.value?.max_context
  return win ? Math.round(win / 1000) : 0
})

/** 已使用上下文 = 最近一次模型调用的输入 token（近似当前上下文占用） */
const contextUsedK = computed(() => Math.max(0, Math.round(agent.lastUsage.tokens_in / 1000)))

/**
 * v1029（大模型选择弹窗 1.4）：剩余上下文百分比 = 1 − 已占用token ÷ 窗口总容量。
 * - 无模型 / 窗口未知 → null（不展示，避免误导）；
 * - 结果夹在 [0, 100]：占用超过窗口时归 0（此时后端压缩机制已介入）；
 * - 每次模型回复刷新（lastUsage 由 usage 事件更新），新会话回到 100%。
 */
const contextRemainingPct = computed<number | null>(() => {
  const model = selectedModel.value
  const win = model?.max_context
  if (!model || !win || win <= 0) return null
  const used = Math.max(0, agent.lastUsage.tokens_in)
  const remaining = Math.round((1 - used / win) * 100)
  return Math.max(0, Math.min(100, remaining))
})

/** 剩余 <20% → 高亮警示（接近窗口，压缩机制即将/已经介入） */
const contextLow = computed(
  () => contextRemainingPct.value !== null && contextRemainingPct.value < 20,
)

const contextLabel = computed(() =>
  selectedModel.value ? `${contextUsedK.value}K / ${contextWindowK.value}K` : '0K',
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
}

const displayMessages = computed<DisplayMessage[]>(() => {
  const persisted: DisplayMessage[] = agent.messages.map((m) => ({
    key: m.id,
    role: m.role === 'user' ? 'user' : m.role === 'assistant' ? 'assistant' : 'tool',
    content: String(m.content ?? ''),
    toolCalls: m.tool_calls ?? [],
    streaming: false,
    createdAt: m.created_at,
  }))
  // ★v1031 续：AI 思考结束后只展示每轮问答的**最后回复**——中间的思考过程、
  // 工具调用卡片、工具结果一律折叠不显示；运行中的实时过程由下方 live 流式消息展示。
  const shown = collapseTurns(persisted)
  // v1030续(1)：乐观回显——刚发出、后端消息树还没回来的用户消息先本地展示；
  // 后端已持久化同一条（末尾 user 消息内容一致）时不再重复显示。
  // ★v1031：pending 带本地发送时间戳，乐观回显的用户消息下方同样显示日期时间。
  const pending = agent.pendingUserMessage
  if (pending) {
    const lastUser = [...shown].reverse().find((m) => m.role === 'user')
    if (lastUser?.content !== pending.content) {
      shown.push({
        key: 'pending-user',
        role: 'user',
        content: pending.content,
        toolCalls: [],
        streaming: false,
        createdAt: pending.sentAt,
      })
    }
  }
  const live: DisplayMessage[] = agent.streaming.map((s) => ({
    key: s.messageId,
    role: s.messageId.startsWith('tool:') ? 'tool' : 'assistant',
    content: s.content,
    toolCalls: s.toolCalls,
    streaming: !s.done,
    createdAt: null,
  }))
  return [...shown, ...live]
})

/**
 * ★v1031 续：把已持久化的消息按「轮」折叠——每个 user 消息开启一轮，
 * 该轮到下一个 user 消息之间的 assistant / tool 消息都视为思考过程，
 * 只保留**最后一条有正文的 assistant 消息**作为本轮回复（并去掉它身上的工具卡片）。
 * 找不到有正文的 assistant（异常轮）时原样保留，避免渲染空气泡。
 */
function collapseTurns(list: DisplayMessage[]): DisplayMessage[] {
  const out: DisplayMessage[] = []
  let turn: DisplayMessage[] = []
  const flush = (): void => {
    if (!turn.length) return
    let finalIdx = -1
    for (let i = turn.length - 1; i >= 0; i--) {
      if (turn[i].role === 'assistant' && turn[i].content.trim()) {
        finalIdx = i
        break
      }
    }
    if (finalIdx >= 0) out.push({ ...turn[finalIdx], toolCalls: [] })
    else out.push(...turn)
    turn = []
  }
  for (const m of list) {
    if (m.role === 'user') {
      flush()
      out.push(m)
    } else {
      turn.push(m)
    }
  }
  flush()
  return out
}

/** 每条问答下方的小字日期时间 */
function stampOf(iso?: string | null): string {
  const d = iso ? new Date(iso) : new Date()
  if (Number.isNaN(d.getTime())) return ''
  const pad = (n: number): string => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
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
     * ★v1031：默认选中「上次使用的模型」，而不是后端标记的默认（新建）模型。
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
  // v1029：输入框最大高度 = 视口的 1/3，随窗口尺寸变化重算
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
 * ★v1031 续：对话视图粘性守卫——回答结束（或任何非用户主动操作）后若会话视图被
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

// ★v1031：删除「导出会话」功能（含会话记录列表 ··· 里的导出入口）

// ---------------------------------------------------------------------------
// 发送 / 运行中三选项（停止并发送 / 通过消息引导发送 / 添加到队列）
// ---------------------------------------------------------------------------

const canSend = computed(() => input.value.trim().length > 0 && !!project.currentNovelId)

/**
 * ★v1031：底部发送按键的形态判定依据——运行中是否已输入「追加消息」。
 * 有输入 → 追加形态（停止后发送 / 添加到队列 / 添加为引导）；无输入 → 停止形态。
 */
const hasInput = computed(() => input.value.trim().length > 0)

/**
 * v1030续(4)：Agent / 大模型运行报错时，给出**中文提示 + 应该怎么做**（灰色异常态）。
 * 由 store 里的 {error, errorCode} 归一而来，避免直接把英文/原始 message 抛给用户。
 */
const errorInfo = computed<AgentErrorInfo | null>(() =>
  agent.error ? describeAgentError(agent.errorCode, agent.error) : null,
)

/** 用户手动关掉报错卡片（例如已修好配置，准备重试） */
function dismissError(): void {
  agent.clearError()
}

/**
 * v1031：输入提示改到空态文案下方展示（小字），输入框 placeholder 只保留兜底提示：
 * 无小说项目 → 「还没有小说项目哦~」；有项目无会话 → 空（提示已在空态区展示）。
 */
const inputPlaceholderText = computed(() =>
  project.currentNovelId ? (agent.currentSessionId ? t('agent.inputPlaceholder') : '') : t('agent.noNovel'),
)

async function send(): Promise<void> {
  const content = input.value.trim()
  if (!content || sending.value) return
  if (!agent.currentSessionId) {
    await newSession()
    if (!agent.currentSessionId) return
  }
  sending.value = true
  input.value = ''
  // ★v1031：实际发起问答的模型即为「上次使用的模型」
  rememberModelRef(modelRef.value)
  try {
    await agent.sendMessage(content, modelRef.value || undefined)
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
  try {
    await agent.sendMessage(content, modelRef.value || undefined)
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
  agent.queueMessage(content, modelRef.value || undefined)
  input.value = ''
  ElMessage.success(t('agent.queued'))
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
// 「+」添加内容（v1029）：大纲 / 章节 / 本地文件，改由 ContextPicker 统一承载
// ---------------------------------------------------------------------------

/** 已添加的上下文块（随下一条消息一起发送；发送后释放） */
const blocks = ref<ContextBlock[]>([])
const addPopover = ref(false)

/**
 * v1031：正文选区[AI润色]加入的上下文块（限定 AI 修改范围）。
 * 与「添加文件」同形制展示/发送；本轮问答结束后一并释放。
 * v1030续(6)：块内带上「所在章节 + 段落范围 + 前后文」，让大模型知道这段文本
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
 * 组装最终消息：已选工具（#子代理 / @技能）+ 上下文块 + 用户输入。
 * v1029：把 `#子代理名` / `@技能名` 与已添加的大纲/章节/文件一并写进消息，
 * 大模型需要同时处理这些内容并主动使用这些工具。
 * v1030续(6)：选区润色块额外带上「所在章节 / 段落范围 / 前后文」与写回要求，
 * 让大模型知道这段文本在文章中的所在范围然后修改。
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

  for (const b of allBlocks.value) {
    if (b.rangeLabel && b.chapterId) {
      // 选区润色块：位置 + 前后文 + 修改要求
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
      parts.push(lines.join('\n'))
      continue
    }
    parts.push(`【${b.label}】\n${b.text}`)
  }

  if (question) parts.push(`【我的问题】\n${question}`)
  return parts.join('\n\n---\n\n')
}

/** v1029：当前问答结束后释放文件缓存（上下文块清空）；v1031：选区润色块一并释放 */
function releaseAttached(): void {
  blocks.value = []
  agent.clearPendingSelections()
}

// ---------------------------------------------------------------------------
// 「工具」：子代理 / 技能（v1029 由 ToolsPicker 承载目录展示）
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
    const [subs, sks] = await Promise.all([configApi.listSubagents(), configApi.listSkills(true)])
    toolNames.value = {
      subagents: new Map(subs.map((s) => [s.id, s.name])),
      // v1030：会话级技能 = 包内写作技能 + 用户自建（基础工具对主代理恒可用，不进 Chips）
      skills: new Map(sks.filter((s) => s.kind !== 'tool').map((s) => [s.id, s.name])),
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

watch(
  () => agent.currentSession,
  async (sess) => {
    if (!sess) {
      chips.value = []
      return
    }
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

/** v1029：选中子代理/技能 → chips 展示 `#名` / `@名`，并持久化到会话（后端据此装配 agent） */
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
// 门禁生成当前章
// ---------------------------------------------------------------------------

const activeChapterTitle = computed(() => {
  const found = reader.activeChapterId ? project.findChapter(reader.activeChapterId) : null
  return found?.chapter.title ?? ''
})

async function generateCurrentChapter(): Promise<void> {
  const chapterId = reader.activeChapterId
  if (!chapterId) {
    ElMessage.warning('请先在目录中选择要生成的章节')
    return
  }
  if (!agent.currentSessionId) {
    await newSession()
    if (!agent.currentSessionId) return
  }
  try {
    await ElMessageBox.confirm(
      `将按大纲「${activeChapterTitle.value}」的章简介生成正文（四阶段门禁：存在性→衔接预检→生成→匹配后检）。` +
        '现有正文会先存快照，可回滚。继续吗？',
      'AI 生成本章正文',
      { confirmButtonText: t('common.confirm'), cancelButtonText: t('common.cancel'), type: 'info' },
    )
  } catch {
    return
  }
  await agent.generateChapter(chapterId, activeChapterTitle.value, modelRef.value || undefined)
}

/** 回车/点击发送：把已选工具与上下文块合并进 input，再走正常发送或运行中三选项 */
function onSubmit(): void {
  if (!project.currentNovelId) return
  // v1029：已添加的大纲/章节/本地文件、已选的 #子代理 / @技能 与用户输入合并成一条消息
  // v1031：选区[AI润色]块同样合并（限定 AI 修改范围）
  if ((allBlocks.value.length || chips.value.length) && input.value.trim()) {
    input.value = composeMessage()
  }
  if (!input.value.trim()) return
  // 运行中 → 弹出三选项（停止并发送 / 通过消息引导发送 / 添加到队列）
  if (agent.running) {
    sendMenu.value = true
    return
  }
  if (!canSend.value) return
  void send()
}

/**
 * v1029：当前问答结束后释放文件缓存。
 * `agent.running` 由 true → false 表示本轮 run 收尾（run.end / 缓冲丢失 / 取消），
 * 此时把已添加的大纲、章节、本地文件内容从内存中释放（不再随下一条消息携带）。
 */
watch(
  () => agent.running,
  (running, wasRunning) => {
    if (wasRunning && !running) releaseAttached()
  },
)
</script>

<template>
  <div class="agent-panel">
    <!-- 头部：当前会话数量 + 当前小说项目 token 使用量（v1028 删除「AI助手」与 + 号） -->
    <div class="panel-head">
      <!-- v1029：会话数换成「层叠文档」icon（原对话气泡留给 token 统计区分度） -->
      <span class="head-stat" :title="t('agent.sessionListTitle')">
        <svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden="true">
          <path d="M12 3 3 7.5 12 12l9-4.5L12 3zM5 11.2 3 12.2l9 4.5 9-4.5-2-1-7 3.5-7-3.5zM5 15.4 3 16.4l9 4.5 9-4.5-2-1-7 3.5-7-3.5z" />
        </svg>
        {{ sessionCountLabel }}
      </span>
      <span v-if="project.currentNovel" class="head-stat" :title="t('agent.usage', {
        in: agent.lastUsage.tokens_in, out: agent.lastUsage.tokens_out })">
        <svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden="true">
          <path d="M13 2 4.5 13.5H11L9.5 22 19 9.5h-6.5L13 2z" />
        </svg>
        {{ tokenLabel }}
      </span>
      <div class="head-spacer" />
    </div>

    <!-- v1029：删除「尚未配置大模型…」提示——未配置或未选择大模型时不需要提示
         v1030续：头部不再放[新建会话]；运行状态条（AI 正在思考… / 停止）移入会话内部 -->

    <!-- ============ 无小说项目 ============ -->
    <div v-if="!project.currentNovel" class="panel-body center-body">
      <div class="empty-tip">{{ t('agent.noNovel') }}</div>
    </div>

    <!-- ============ 会话记录列表 ============ -->
    <div v-else-if="!agent.currentSessionId" class="panel-body scroll-y">
      <div v-if="!sessionList.length" class="center-body">
        <!-- v1031：无会话时输入提示以小字展示在空态文案下方（输入框不再显示该提示） -->
        <div class="empty-hint-group">
          <div class="empty-tip">{{ t('agent.noSession') }}</div>
          <div class="empty-input-hint">{{ t('agent.inputPlaceholder') }}</div>
        </div>
      </div>
      <template v-else>
        <!-- v1030续(3)：删除「会话记录 2/50」这一行——会话数量已在最上方头部展示 -->
        <!-- v1030续(4)：会话列表态下的报错同样给出中文提示与处置建议（灰色） -->
        <div v-if="errorInfo" class="msg msg-assistant msg-error" role="alert">
          <div class="msg-content error-content">
            <div class="error-title">{{ errorInfo.title }}</div>
            <div class="error-advice">{{ errorInfo.advice }}</div>
          </div>
          <div class="msg-foot">
            <el-button text size="small" class="msg-copy" @click="dismissError">
              {{ t('agent.errorDismiss') }}
            </el-button>
          </div>
        </div>
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
          <el-dropdown trigger="click" placement="bottom-end" @command="(cmd: string) => {
            if (cmd === 'rename') renameSession(s.id, s.title)
            else if (cmd === 'delete') removeSession(s.id)
          }">
            <span class="session-more" @click.stop>···</span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="rename">{{ t('agent.renameSession') }}</el-dropdown-item>
                <!-- ★v1031：删除「导出会话」入口 -->
                <el-dropdown-item command="delete">{{ t('agent.deleteSession') }}</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </template>
    </div>

    <!-- ============ 会话对话 ============ -->
    <template v-else>
      <!-- v1030续(1)：会话内只保留「返回 + 会话标题」，右上角的[新建会话]与[···]已删除
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
          <div class="msg-content">
            <template v-if="msg.toolCalls?.length">
              <div v-for="(tc, i) in msg.toolCalls" :key="i" class="tool-card">
                <code>{{ formatToolCall(tc) }}</code>
              </div>
            </template>
            <span v-if="msg.content" class="msg-text">{{ msg.content }}</span>
            <span v-if="msg.streaming" class="cursor-blink">▌</span>
          </div>
          <!-- v1028：每个问答下方小字标记日期和时间 -->
          <div class="msg-foot">
            <span v-if="msg.createdAt" class="msg-time">{{ stampOf(msg.createdAt) }}</span>
            <span v-else-if="msg.streaming" class="msg-time">{{ t('agent.running') }}</span>
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

        <!-- v1030续(1)：「AI 正在思考…」放进会话内部（模仿主流 AI Agent），
             不再占用会话框最上方一整行
             ★v1031：[停止] 从状态条移到底部发送按键（三形态：发送 / 停止 / 追加消息） -->
        <div
          v-if="agent.running || agent.activeSubagents.length || agent.gateStage"
          class="status-inline"
        >
          <span v-if="agent.running" class="dot-pulse" />
          <span v-if="agent.running" class="status-text">{{ t('agent.running') }}</span>
          <el-tag v-for="sa in agent.activeSubagents" :key="sa" size="small" type="info">
            {{ t('agent.subagentLane') }}: {{ sa }}
          </el-tag>
          <el-tag v-if="agent.gateStage" size="small" type="warning">
            {{ t('agent.gateStage') }}: {{ agent.gateStage }}
          </el-tag>
        </div>

        <!-- v1030续(4)：Agent / 大模型运行报错 → 中文提示 + 应该怎么做，
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

        <GateStatusBar
          v-if="agent.gateStage"
          :stage="agent.gateStage"
          :retry-count="agent.gateRetryCount"
          :g1-skipped="agent.gateG1Skipped"
          :results="agent.gateResults"
        />

        <ApprovalCard v-if="agent.pendingInterrupt" :interrupt="agent.pendingInterrupt" />
        <ConsultCard v-if="agent.pendingConsult" :consult="agent.pendingConsult" />
      </div>
    </template>

    <!-- ============ 输入区（copilot 形制） ============ -->
    <div class="panel-input">
      <!-- 已添加的上下文块（v1031：含正文选区[AI润色]加入的限定范围块） -->
      <div v-if="allBlocks.length" class="blocks-row">
        <el-tag
          v-for="(b, i) in allBlocks"
          :key="`${b.kind}-${b.label}-${i}`"
          size="small"
          closable
          :type="b.kind === 'outline' ? 'warning' : b.kind === 'chapter' ? 'success' : 'info'"
          @close="i < selectionBlocks.length ? removeSelectionBlock(i) : removeBlock(i - selectionBlocks.length)"
        >
          {{ b.label }}
        </el-tag>
      </div>

      <!-- 已选工具（v1029：子代理显示 #名，技能显示 @名） -->
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

      <!-- 队列提示 -->
      <div v-if="agent.queued.length" class="queue-hint">
        <span class="muted text-sm">队列中 {{ agent.queued.length }} 条待发送</span>
        <el-button text size="small" @click="agent.clearQueue()">清空</el-button>
      </div>

      <div class="input-box" :class="{ focused: false }">
        <!-- v1029：随输入自适应上拉，最多到页面高度的 1/3 -->
        <el-input
          v-model="input"
          type="textarea"
          :autosize="inputAutosize"
          resize="none"
          :disabled="!project.currentNovelId"
          :placeholder="inputPlaceholderText"
          class="chat-input"
          @keydown.enter.exact.prevent="onSubmit"
        />

        <!-- 输入框内工具条：+ / 工具 / Model / 上下文使用情况 / 发送箭头 -->
        <div class="input-bar">
          <!-- ＋：v1029 添加文件页（搜索框 + 小说项目大纲或文章 + 本地文件 + 最近打开的章节） -->
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

          <!-- 工具：v1029 只展示 [写作子代理] / [写作技能] 两个入口，没有内容时不显示 -->
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

          <!-- Model（v1029）：左侧无 icon、Model Off 不用斜体；
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
                  <!-- ★v1031 续：去掉模型名后的「· 33K」上下文窗口小字（用户不需要） -->
                  {{ m.display_name }}{{ m.is_default ? ' ★' : '' }}
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>

          <!-- v1029（1.4）：Model Off 右侧显示「剩余上下文百分比」= 1 − 已占用 ÷ 窗口。
               每次模型回复刷新；新会话回到 100%；<20% 时高亮警示。
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

          <!-- ★v1031：删除[重新生成]功能 -->
          <el-tooltip
            v-if="reader.activeChapterId"
            :content="`按大纲生成《${activeChapterTitle}》正文（四阶段门禁）`"
            placement="top"
          >
            <el-button
              size="small"
              type="warning"
              plain
              class="gen-btn"
              :disabled="agent.running"
              @click="generateCurrentChapter"
            >
              {{ t('agent.generateChapter') }}
            </el-button>
          </el-tooltip>

          <!-- ★v1031：底部这颗按键统一承载「发送 / 停止 / 追加消息」三种形态（模仿主流大模型与 Agent）：
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

.head-spacer {
  flex: 1;
}

/* v1030续(1)：运行状态条移入会话内部（原 .status-bar 顶部整行已删除） */
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

/* ★v1031：[停止] 已移到底部发送按键，状态条里的 .status-stop 随之删除 */

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

/* v1030续(4)：报错卡片——中文提示 + 处置建议，整块灰色（异常不可用） */
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

/* v1031：空态文案 + 输入提示（小字）垂直居中组合 */
.empty-hint-group {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 0 16px;
}

.empty-input-hint {
  color: var(--text-muted);
  font-size: 11px;
  text-align: center;
  line-height: 1.7;
  opacity: 0.85;
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

.queue-hint {
  display: flex;
  align-items: center;
  gap: 8px;
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
  /* v1029：行高固定，配合 autosize 的最大行数换算「最多到页面高度的 1/3」 */
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

/* v1029：Model Off 不用斜体，仅以弱化色区分「未选择模型」 */
.model-btn.off {
  color: var(--text-muted);
}

.bar-ctx {
  font-size: 11px;
  color: var(--text-muted);
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

/* v1029（1.4）：剩余上下文 <20% → 警示色，提示压缩机制即将/已经介入 */
.bar-ctx.is-low {
  color: var(--el-color-danger, #f56c6c);
  font-weight: 600;
}

.bar-spacer {
  flex: 1;
}

/* ★v1031：[重新生成]已删除，.regen-btn 样式一并移除 */
.gen-btn {
  padding: 3px 8px;
  height: 24px;
  font-size: 11px;
}

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

/* ★v1031：停止形态（运行中且输入框为空）——红色方块，点击立即停止本轮回答 */
.send-btn.stop {
  background: var(--el-color-danger);
}

.send-menu {
  display: flex;
  flex-direction: column;
}

/* v1030续(2)：每种发送模式 = 名称 + 一句说明（模仿主流大模型 / Agent） */
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
</style>
