/**
 * Agent store（M3）：会话列表、消息树、流式运行状态、中断卡片。
 *
 * - 会话按小说项目隔离（右侧栏只显示当前项目）；
 * - 发消息 → 202 + run_id → SseClient 订阅 → 流式渲染；
 * - interrupt.request → 审批卡片；consult.request → 咨询卡片（M4 启用）；
 * - file.changed → 通知工作台热更新正文/目录树。
 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { agentApi } from '@/api'
import { ApiError, errorMessage } from '@/api/http'
import { SseClient, type SseEnvelope } from '@/api/sse'
import { useEditorStore } from '@/stores/editor'
import { useReaderStore } from '@/stores/reader'
import type { AgentInterruptDto, AgentMessageDto, AgentRunDto, AgentSessionDto, MessageTree } from '@/types'

/** 全局单飞：同一时刻只允许一个会话「AI 思考执行中」（后端硬校验，AI 直接回复提示） */
export const GLOBAL_BUSY_HINT = '当前已有其他会话在进行AI思考哦，等任务完成后再聊吧~'

/** 流式中的助手消息（按 message_id 聚合 delta） */
export interface StreamingMessage {
  messageId: string
  content: string
  toolCalls: { name: string; args?: Record<string, unknown>; id?: string }[]
  done: boolean
}

/** 排队待发送的补充消息（可编辑 / 删除 / 立即发送；展示在会话框内） */
export interface QueuedMessage {
  id: number
  content: string
  modelRef?: string
  hiddenContext?: string
  /** 入队时间（会话框内展示） */
  queuedAt?: string
}

/** 待决审批卡片 */
export interface PendingInterrupt {
  interruptId: string
  toolName: string | null
  args: Record<string, unknown> | null
  decisions: string[]
  risk?: string
  reason?: string
  /** 单条人话标签（如「第二卷第三章《章名》的正文」），批量卡片逐条展示用 */
  itemLabel?: string
  /** 原始虚拟路径（仅前端分组展示，不直接给用户看） */
  path?: string
}

/** 待决咨询卡片（M4 流程使用；ask_user 多问题卡片带 questions） */
export interface PendingConsult {
  interruptId: string
  question: string
  options: { value: string; label: string }[] | string[]
  allowText: boolean
  workflowStage?: string
  /** 输入框占位提示文字（如「在此输入章节简介内容，提交后继续生成」） */
  textPlaceholder?: string
  /** ask_user 多问题清单（缺省 = 单问旧契约） */
  questions?: AskUserQuestion[]
}

/** ask_user 卡片的一个问题 */
export interface AskUserQuestion {
  question: string
  header?: string
  options: { label: string; description?: string }[]
  multi_select?: boolean
}

/**
 * 正文选区[AI润色]加入的「选中文本块」。
 * 除文本本身外，还带上章节 id、所在段落范围与前后文，让大模型知道这段文本
 * 在文章中的位置，从而只针对该范围润色并写回。
 * label = `章节名_节选X`（X 为同一章内已加入的选区序号，正整数），保持标签简短。
 */
export interface PendingSelectionBlock {
  label: string
  text: string
  chapterId?: string
  chapterTitle?: string
  rangeLabel?: string
  before?: string
  after?: string
}

export const useAgentStore = defineStore('agent', () => {
  const sessions = ref<AgentSessionDto[]>([])
  const currentSessionId = ref<string | null>(null)
  const messages = ref<AgentMessageDto[]>([])
  const activeLeafId = ref<string | null>(null)
  const currentRun = ref<AgentRunDto | null>(null)
  const streaming = ref<StreamingMessage[]>([])
  /** 待决审批卡片队列（同类多条时合并成一张分页卡片，逐条决策后一次提交） */
  const pendingInterrupts = ref<PendingInterrupt[]>([])
  const pendingConsult = ref<PendingConsult | null>(null)
  const running = ref(false)
  /** run 处于暂停态（等待审批/咨询卡片答复）——此时不展示未完成的中间消息 */
  const runPaused = ref(false)
  const loading = ref(false)
  const error = ref<string | null>(null)
  /**
   * 刚发出的用户消息（乐观回显）。
   * 主流 AI Agent 的行为是「发出去就立刻出现在会话里」，而后端消息树要等 run 收尾才刷新，
   * 因此先本地回显，`refreshMessages()` 拿到真实消息后自动置空替换。
   * 改为对象，带上本地时间戳，使乐观回显的用户消息下方也能显示日期时间。
   */
  const pendingUserMessage = ref<{ content: string; sentAt: string } | null>(null)
  /**
   * 错误码（与后端 ErrorCode 对齐）。
   * 会话框据此给出**中文提示 + 用户该怎么做**，并以灰色（异常不可用）呈现。
   */
  const errorCode = ref<number | null>(null)

  /** 统一记录错误：中文文案 + 错误码（供会话框给出处置建议） */
  function setError(err: unknown, fallback?: string): void {
    error.value = errorMessage(err, fallback)
    errorCode.value = err instanceof ApiError ? err.code : null
  }

  /** 直接写入错误（用于 SSE 传输层等非 ApiError 场景） */
  function setRawError(message: string, code: number | null = null): void {
    error.value = message
    errorCode.value = code
  }

  /** 新一轮请求/运行开始前清空上一条错误 */
  function clearError(): void {
    error.value = null
    errorCode.value = null
  }

  /** file.changed 通知（工作台监听后热更新） */
  const fileChanged = ref<{ path: string; chapterId: string | null; kind: string; at: number } | null>(null)
  /** 计划待办（plan.update） */
  const todos = ref<{ content: string; status: string }[]>([])
  /** 流程状态（workflow.update，M4） */
  const workflowStage = ref<string | null>(null)
  const workflowRetryCount = ref(0)
  const workflowPrecheckSkipped = ref(false)
  const workflowResults = ref<Record<string, string>>({})
  /** 子代理泳道 */
  const activeSubagents = ref<string[]>([])
  /** 最近一次运行的用量 */
  const lastUsage = ref<{ tokens_in: number; tokens_out: number }>({ tokens_in: 0, tokens_out: 0 })
  /** 进行中 run 的累计消耗（含子代理，随 usage 事件的 run_tokens_* 实时刷新）——
   * 头部「全部会话消耗」= 已完成 run 总和（usage）+ 本值；
   * cached_in = 输入 token 中命中 provider 前缀缓存的累计（缓存命中率分子） */
  const runCumulative = ref<{ tokens_in: number; tokens_out: number; cached_in: number }>({
    tokens_in: 0,
    tokens_out: 0,
    cached_in: 0,
  })
  /** 当前项目 token 使用量 + 会话数（会话框头部展示）
   * 同时保留入/出/缓存命中明细，供头部 Token 悬浮提示与缓存命中率展示 */
  const usage = ref<{
    session_count: number
    tokens_in: number
    tokens_out: number
    tokens_total: number
    cached_in: number
  }>({ session_count: 0, tokens_in: 0, tokens_out: 0, tokens_total: 0, cached_in: 0 })
  /** 运行中「添加到队列」的待发消息（run.end 后自动发送首条）
 * 队列项带 id，支持编辑内容 / 删除 / 立即发送（改变排队状态） */
  const queued = ref<QueuedMessage[]>([])
  let queueSeq = 0

  /**
   * 运行中「通过消息引导发送」的引导消息（会话内可见，展示在队列消息之前）。
   * 后端语义：发送时即落库（kind=steer），run 的每次模型调用前注入提示词；
   * `consumed` 由 `steer.consumed` 事件置真——未消费的引导气泡显示在「AI 思考」下方
   * 且可编辑/删除，已消费的显示在「AI 思考」上方。run 结束后清空（持久化行仍可见）。
   */
  const steered = ref<{ messageId: string; content: string; sentAt: string; consumed: boolean }[]>([])

  function addSteered(messageId: string, content: string): void {
    steered.value = [
      ...steered.value,
      { messageId, content, sentAt: new Date().toISOString(), consumed: false },
    ]
  }

  /** `steer.consumed`：引导已被注入下一次模型调用（位置切换到「AI 思考」上方） */
  function markSteerConsumed(messageIds: string[]): void {
    const ids = new Set(messageIds)
    steered.value = steered.value.map((s) => (ids.has(s.messageId) ? { ...s, consumed: true } : s))
  }

  function updateSteerLocal(messageId: string, content: string): void {
    steered.value = steered.value.map((s) => (s.messageId === messageId ? { ...s, content } : s))
  }

  function removeSteerLocal(messageId: string): void {
    steered.value = steered.value.filter((s) => s.messageId !== messageId)
  }

  function clearSteered(): void {
    steered.value = []
  }

  /**
   * Agent 默认收尾回复（暂停/网络异常/模型接口异常）——run.notice 事件即时展示；
   * run 结束 refreshMessages 后由持久化 kind=notice 行接管，本地副本清空。
   */
  const noticeMessage = ref<{ content: string; kind: string } | null>(null)

  function clearNotice(): void {
    noticeMessage.value = null
  }

  /** 最近一次 run.end 的 status（onClose 据此决定队列是否自动发送） */
  const lastRunEndStatus = ref<string | null>(null)
  /**
   * 当前「AI 思考执行中」的会话 id 集合（全局单飞：至多 1 个）。
   * 会话列表条目右侧据此显示灰色小字「AI思考执行中……」；
   * 发送时本地先拦一道（已有其它会话在跑），后端 run_manager 全局单飞兜底。
   */
  const runningSessionIds = ref<Set<string>>(new Set())
  /** 本地标记的起始时刻（宽限窗口内以本地为准，之后以服务端 is_running 为准） */
  const runningSince = new Map<string, number>()
  const RUNNING_GRACE_MS = 5000
  let runningPollTimer: ReturnType<typeof setInterval> | null = null

  function markSessionRunning(sessionId: string): void {
    const next = new Set(runningSessionIds.value)
    next.add(sessionId)
    runningSessionIds.value = next
    runningSince.set(sessionId, Date.now())
    const idx = sessions.value.findIndex((s) => s.id === sessionId)
    if (idx >= 0) sessions.value[idx] = { ...sessions.value[idx], is_running: true }
    startRunningPoll()
  }

  function markSessionIdle(sessionId: string): void {
    runningSince.delete(sessionId)
    if (!runningSessionIds.value.has(sessionId)) return
    const next = new Set(runningSessionIds.value)
    next.delete(sessionId)
    runningSessionIds.value = next
    const idx = sessions.value.findIndex((s) => s.id === sessionId)
    if (idx >= 0) sessions.value[idx] = { ...sessions.value[idx], is_running: false }
    if (!next.size) stopRunningPoll()
  }

  /** 有会话在跑时每 3s 轻量轮询列表（SSE 事件漏收 / 停在列表页时的兜底同步） */
  function startRunningPoll(): void {
    if (runningPollTimer) return
    runningPollTimer = setInterval(() => {
      if (!runningSessionIds.value.size) {
        stopRunningPoll()
        return
      }
      void refreshSessionsLight()
    }, 3000)
  }

  function stopRunningPoll(): void {
    if (runningPollTimer) {
      clearInterval(runningPollTimer)
      runningPollTimer = null
    }
  }
  /**
   * / 正文编辑器悬浮[AI润色]按钮加入的「选中文本块」。
   * 与「添加文件」同形制：随下一条消息一起发送，限定 AI 的修改范围；本轮问答结束后释放。
   * `chapterId` 让大模型知道这段文本属于哪一章（可定位并写回）；`rangeLabel` 说明所在行范围。
   */
  const pendingSelectionBlocks = ref<PendingSelectionBlock[]>([])

  function attachSelection(block: PendingSelectionBlock): string | null {
    // 标签统一为 `章节名_节选X`（X = 本章已有选区数 + 1），保持聊天框上方标签简短。
    // 完全重复（同章、同范围、同文本）则不再加入。
    const dup = pendingSelectionBlocks.value.some(
      (b) => b.chapterId === block.chapterId && b.rangeLabel === block.rangeLabel && b.text === block.text,
    )
    if (dup) return null
    const chapterTitle = block.chapterTitle || block.label || '章节正文'
    const seq =
      pendingSelectionBlocks.value.filter((b) => b.chapterId === block.chapterId).length + 1
    const label = `${chapterTitle}_节选${seq}`
    pendingSelectionBlocks.value = [...pendingSelectionBlocks.value, { ...block, label }]
    return label
  }

  function removePendingSelection(index: number): void {
    pendingSelectionBlocks.value = pendingSelectionBlocks.value.filter((_, i) => i !== index)
  }

  function clearPendingSelections(): void {
    pendingSelectionBlocks.value = []
  }

  let sseClient: SseClient | null = null
  let currentRunId: string | null = null

  const currentSession = computed(
    () => sessions.value.find((s) => s.id === currentSessionId.value) ?? null,
  )

  /** 最近一次加载会话列表的项目 id（列表页 currentSession 为空时轮询兜底用） */
  const lastNovelId = ref<string | null>(null)

  async function loadSessions(novelId: string): Promise<void> {
    loading.value = true
    clearError()
    lastNovelId.value = novelId
    // 切换小说项目 → 上下文占用清零（剩余比例回到 100%）
    lastUsage.value = { tokens_in: 0, tokens_out: 0 }
    try {
      sessions.value = await agentApi.listSessions(novelId)
      // 进入页面即按服务端 is_running 初始化「AI思考执行中……」标记
      runningSessionIds.value = new Set(sessions.value.filter((s) => s.is_running).map((s) => s.id))
      runningSince.clear()
      if (runningSessionIds.value.size) startRunningPoll()
      else stopRunningPoll()
      await loadUsage(novelId)
      // 会话记录列表默认不选中任何会话（点击记录才进入），最多展示 50 条
      if (sessions.value.length === 0) {
        currentSessionId.value = null
        messages.value = []
      }
    } catch (err) {
      setError(err)
    } finally {
      loading.value = false
    }
  }

  /** 轻量刷新会话列表（不改变当前选中，run 结束后更新标题/排序）
   * 同时以服务端 `is_running` 对账运行中标记（SSE 漏收时兜底）；
   * 列表页（currentSession 为空）用 lastNovelId 兜底，保证轮询在列表页也生效。 */
  async function refreshSessionsLight(): Promise<void> {
    const novelId = currentSession.value?.project_id ?? lastNovelId.value
    if (!novelId) return
    try {
      const list = await agentApi.listSessions(novelId)
      sessions.value = list
      // 以服务端 is_running 为准对账；本地刚发出（宽限窗口内）的保留，
      // 避免「消息已发、服务端尚未落 running」的瞬时空窗。
      const now = Date.now()
      const merged = new Set(list.filter((s) => s.is_running).map((s) => s.id))
      for (const [id, since] of runningSince) {
        if (now - since < RUNNING_GRACE_MS) merged.add(id)
        else runningSince.delete(id)
      }
      runningSessionIds.value = merged
      if (!merged.size) stopRunningPoll()
    } catch {
      /* 忽略：下一轮轮询/切换会再取 */
    }
  }

  /** 当前小说项目的会话数量与累计 token 使用量 */
  async function loadUsage(novelId?: string | null): Promise<void> {
    const id = novelId ?? (currentSession.value?.project_id ?? null)
    if (!id) return
    try {
      // 会话数量：项目级
      const u = await agentApi.usage(id)
      // Token 消耗：应用级（跨所有项目）
      const appU = await agentApi.appUsage()
      usage.value = {
        session_count: u.session_count,
        tokens_in: appU.tokens_in,
        tokens_out: appU.tokens_out,
        tokens_total: appU.tokens_total,
        cached_in: appU.cached_in ?? 0,
      }
    } catch {
      /* 用量读取失败不阻断会话 */
    }
  }

  /** 运行中「添加到队列」；队列项可带隐藏上下文；返回队列项 id */
  function queueMessage(content: string, modelRef?: string, hiddenContext?: string): number {
    queueSeq += 1
    queued.value = [
      ...queued.value,
      { id: queueSeq, content, modelRef, hiddenContext, queuedAt: new Date().toISOString() },
    ]
    return queueSeq
  }

  /** 编辑排队消息内容 */
  function updateQueued(
    id: number,
    patch: Partial<Pick<QueuedMessage, 'content' | 'modelRef' | 'hiddenContext'>>,
  ): void {
    queued.value = queued.value.map((q) => (q.id === id ? { ...q, ...patch } : q))
  }

  /** 删除排队消息 */
  function removeQueued(id: number): void {
    queued.value = queued.value.filter((q) => q.id !== id)
  }

  /** 把排队消息移到队首（配合「立即发送」= 停止当前 run 后由 onClose 自动发队首） */
  function queueToHead(id: number): void {
    const item = queued.value.find((q) => q.id === id)
    if (!item) return
    queued.value = [item, ...queued.value.filter((q) => q.id !== id)]
  }

  function clearQueue(): void {
    queued.value = []
  }

  function backToList(): void {
    stopStream()
    currentSessionId.value = null
    messages.value = []
    steered.value = []
    noticeMessage.value = null
    lastRunEndStatus.value = null
    lastUsage.value = { tokens_in: 0, tokens_out: 0 }
    pendingInterrupts.value = []
    pendingConsult.value = null
    streaming.value = []
    todos.value = []
    workflowStage.value = null
    workflowResults.value = {}
    pendingUserMessage.value = null
  }

  async function createSession(novelId: string, title?: string, modelRef?: string): Promise<AgentSessionDto> {
    const sess = await agentApi.createSession({ novel_id: novelId, title, model_ref: modelRef })
    sessions.value.unshift(sess)
    await selectSession(sess.id)
    await loadUsage(novelId)
    return sess
  }

  /** 由未决中断清单恢复审批/咨询卡片（进入会话与暂停态 run.end 共用）。
   * ★ 与现有 pendingInterrupts 合并去重，避免覆盖 SSE 新推送的中断。
   * ★ 按内容去重：相同 reason + itemLabel 的中断只保留第一个（防止后端重复创建）。
   */
  function restorePendingCards(items: AgentInterruptDto[]): void {
    const existingIds = new Set(pendingInterrupts.value.map((p) => p.interruptId))
    const existingContent = new Set(
      pendingInterrupts.value.map((p) => `${p.reason || ''}|${p.itemLabel || ''}`)
    )
    for (const it of items) {
      // 已存在的中断跳过（SSE 可能已推送）
      if (existingIds.has(it.id)) continue
      const payload = (typeof it.payload === 'object' && it.payload !== null ? it.payload : {}) as Record<string, unknown>
      const reason = payload.reason as string | undefined
      const itemLabel =
        (payload.item_label as string | undefined) ?? (payload.itemLabel as string | undefined)
      const contentKey = `${reason || ''}|${itemLabel || ''}`
      // 内容相同的中断跳过（防止后端重复创建）
      if (existingContent.has(contentKey)) continue
      if (it.type === 'approval') {
        pendingInterrupts.value = [
          ...pendingInterrupts.value,
          {
            interruptId: it.id,
            toolName: (payload.tool_name as string | null) ?? it.tool_name ?? null,
            args: (payload.args as Record<string, unknown> | null) ?? null,
            decisions: (payload.decisions as string[]) ?? ['approve', 'edit', 'reject', 'respond'],
            risk: payload.risk as string | undefined,
            reason,
            itemLabel,
            path: (payload.path as string | undefined) ?? undefined,
          },
        ]
        existingIds.add(it.id)
        existingContent.add(contentKey)
      } else if (!pendingConsult.value || pendingConsult.value.interruptId !== it.id) {
        // 咨询卡片只有一个，只在不存在或 id 不同时覆盖
        pendingConsult.value = {
          interruptId: it.id,
          question: String(payload.question ?? ''),
          options: (payload.options as PendingConsult['options']) ?? [],
          allowText: Boolean(payload.allow_text ?? true),
          workflowStage: payload.workflow_stage as string | undefined,
          textPlaceholder: payload.text_placeholder as string | undefined,
          questions: (payload.questions as PendingConsult['questions']) ?? undefined,
        }
      }
    }
  }

  async function selectSession(sessionId: string): Promise<void> {
    stopStream()
    currentSessionId.value = sessionId
    pendingInterrupts.value = []
    pendingConsult.value = null
    runPaused.value = false
    streaming.value = []
    steered.value = []
    noticeMessage.value = null
    lastRunEndStatus.value = null
    todos.value = []
    workflowStage.value = null
    workflowRetryCount.value = 0
    workflowPrecheckSkipped.value = false
    workflowResults.value = {}
    pendingUserMessage.value = null
    // 先清零（→ 剩余 100%），再按该会话最近一次 run 还原真实占用；
    // 全新会话没有任何 run，还原结果仍是 0，故「新会话回到 100%」自然成立。
    lastUsage.value = { tokens_in: 0, tokens_out: 0 }
    try {
      const tree: MessageTree = await agentApi.getMessages(sessionId)
      messages.value = tree.items.filter((m) => m.in_active_path)
      activeLeafId.value = tree.active_leaf_id ?? null
      // 恢复未决的审批/咨询卡片 + 运行态（SSE 事件在刷新/重进时会漏收，
      // 不恢复则 run 永久挂起、用户看不到卡片）
      const lr = tree.latest_run
      if (lr && (lr.status === 'awaiting_approval' || lr.status === 'awaiting_consult' || lr.status === 'running' || lr.status === 'pending')) {
        currentRunId = lr.run_id
        running.value = true
        runPaused.value = lr.status === 'awaiting_approval' || lr.status === 'awaiting_consult'
        markSessionRunning(sessionId)
        // ★ 先清空再恢复，防止已处理的中断卡片残留
        pendingInterrupts.value = []
        pendingConsult.value = null
        restorePendingCards(lr.pending_interrupts ?? [])
        // 重连事件流：缓冲仍在则续传，已丢则 onBufferLost 回落重拉消息树
        startStream(lr.run_id)
      } else {
        // run 已结束或不存在：清除所有卡片和运行态
        pendingInterrupts.value = []
        pendingConsult.value = null
        runPaused.value = false
        running.value = false
      }
    } catch (err) {
      setError(err)
    }
    try {
      const u = await agentApi.sessionUsage(sessionId)
      // 用户在拉取期间又切走 → 丢弃过期结果，避免把别的会话用量算进来
      if (currentSessionId.value === sessionId) {
        lastUsage.value = { tokens_in: u.tokens_in, tokens_out: u.tokens_out }
      }
    } catch {
      /* 用量读取失败不阻断会话：保持 100% */
    }
  }

  async function refreshMessages(): Promise<void> {
    if (!currentSessionId.value) return
    try {
      const tree = await agentApi.getMessages(currentSessionId.value)
      messages.value = tree.items.filter((m) => m.in_active_path)
      activeLeafId.value = tree.active_leaf_id ?? null
      // 真实消息已到，撤掉乐观回显
      pendingUserMessage.value = null
      /**
       * 同时清空流式缓冲——否则 run 收尾后「已持久化的回复」与
       * 「流式回复」会同时渲染，界面上出现两条一模一样的消息。
       */
      streaming.value = []
    } catch (err) {
      setError(err)
    }
  }

  async function renameSession(id: string, title: string): Promise<void> {
    const updated = await agentApi.renameSession(id, title)
    const idx = sessions.value.findIndex((s) => s.id === id)
    if (idx >= 0) sessions.value[idx] = { ...sessions.value[idx], ...updated }
  }

  async function deleteSession(id: string): Promise<void> {
    await agentApi.deleteSession(id)
    sessions.value = sessions.value.filter((s) => s.id !== id)
    // 删除会话同时清除其「思考执行中」标记
    markSessionIdle(id)
    if (currentSessionId.value === id) {
      currentSessionId.value = null
      messages.value = []
      lastUsage.value = { tokens_in: 0, tokens_out: 0 }
    }
  }

  async function switchBranch(messageId: string): Promise<void> {
    if (!currentSessionId.value) return
    await agentApi.switchBranch(currentSessionId.value, messageId)
    await refreshMessages()
  }

  async function sendMessage(content: string, modelRef?: string, hiddenContext?: string): Promise<void> {
    if (!currentSessionId.value) {
      error.value = '请先选择或新建会话'
      errorCode.value = null
      return
    }
    clearError()
    // 乐观回显——发出的用户消息立即出现在会话里（run 收尾刷新后替换）
    // 记下发送时刻；并带上「当前正在编辑/阅读的章」——
    // 后端意图检测命中且消息未点名章节时，用它作流程目标章
    pendingUserMessage.value = { content, sentAt: new Date().toISOString() }
    try {
      const result = await agentApi.postMessage(currentSessionId.value, {
        content,
        model_ref: modelRef,
        active_chapter_id: activeChapterIdForWorkflow() || undefined,
        // 隐藏上下文（润色提示词）随本轮送给模型，不入消息树不展示
        hidden_context: hiddenContext || undefined,
      })
      // 全局单飞拦截：AI 的提示回复已落消息树 → 撤乐观回显并刷新消息即可看到
      if (!result.run_id) {
        try {
          await refreshMessages()
        } catch {
          /* 刷新失败也清除 pending，避免用户消息永远卡在"发送中" */
        } finally {
          pendingUserMessage.value = null
        }
        return
      }
      currentRunId = result.run_id
      running.value = true
      runPaused.value = false
      // 新 run 开始：run 级累计清零（头部实时部分从零起算）
      runCumulative.value = { tokens_in: 0, tokens_out: 0, cached_in: 0 }
      // 会话进入「AI 思考执行中」（列表右侧灰色小字 + 全局单飞本地预检）
      markSessionRunning(currentSessionId.value)
      // **不**清零 lastUsage——上下文只增不减，清零会让百分比闪回 100%；
      // 保留上一次的值直到本次 run 的 usage 事件刷新它。
      startStream(result.run_id)
      // 首条消息发出后后端即刻把会话标题改为「首条消息文字」，
      // 这里轻量刷新会话列表，让标题/列表立刻生效（不必等 run 结束）。
      void refreshSessionsLight()
    } catch (err) {
      // 发送失败 → 撤掉乐观回显，避免留下未真正发出的消息
      pendingUserMessage.value = null
      setError(err)
    }
  }

  /**
   * 当前正在编辑/阅读的章 id（供后端流程意图检测作目标章回落）。
   * 编辑模式看 editor store，阅读模式看 reader store 的当前章。
   */
  function activeChapterIdForWorkflow(): string | null {
    if (useEditorStore().chapterId) return useEditorStore().chapterId
    return useReaderStore().activeChapterId ?? null
  }

  /** 删除[生成本章]按钮——流程改由用户消息意图触发（后端检测） */

  /** 删除[重新生成]功能（store 侧 regenerate 一并移除） */

  function startStream(runId: string, lastEventId?: number): void {
    stopStream()
    sseClient = new SseClient({
      url: agentApi.streamUrl(runId, lastEventId),
      onEvent: handleEvent,
      onBufferLost: () => {
        // 缓冲丢失 → 回落重拉消息树（手册「六 · SSE 契约」）
        void refreshMessages()
        running.value = false
      },
      onError: (err) => {
        // SSE 传输层错误：给出中文提示，会话框据此显示灰色异常态
        setRawError(err.message || '与服务器的实时连接已断开')
      },
      onClose: () => {
        // run.end 后收尾：把流式消息合入正式列表
        running.value = false
        void refreshMessages()
        // 引导消息已随 run 落库（kind=steer）：清空本地副本，持久化行接管展示
        clearSteered()
        // 收尾 notice 同样已落库（kind=notice）：本地副本清空，避免双显示
        clearNotice()
        // run **正常完成**后才自动发送队列首条（「添加到队列」）；
        // 停止/失败后保留队列不发送——会话里只留「用户消息 +（引导）+ 默认回复」。
        if (lastRunEndStatus.value === 'completed' && queued.value.length) {
          const next = queued.value[0]
          queued.value = queued.value.slice(1)
          void sendMessage(next.content, next.modelRef, next.hiddenContext)
        }
        lastRunEndStatus.value = null
        // 会话标题/用量随运行结束刷新（首问总结、token 累计）
        void loadUsage()
        void refreshSessionsLight()
      },
    })
    sseClient.connect()
  }

  function handleEvent(envelope: SseEnvelope): void {
    const data = envelope.data as Record<string, unknown>
    switch (envelope.event) {
      case 'message.delta': {
        const mid = String(data.message_id ?? '')
        const delta = String(data.delta ?? '')
        let item = streaming.value.find((s) => s.messageId === mid)
        if (!item) {
          item = { messageId: mid, content: '', toolCalls: [], done: false }
          streaming.value.push(item)
        }
        item.content += delta
        break
      }
      case 'message.complete': {
        const mid = String(data.message_id ?? '')
        let item = streaming.value.find((s) => s.messageId === mid)
        if (!item) {
          item = { messageId: mid, content: '', toolCalls: [], done: false }
          streaming.value.push(item)
        }
        if (typeof data.content === 'string' && data.content) item.content = data.content
        if (Array.isArray(data.tool_calls)) {
          item.toolCalls = data.tool_calls as StreamingMessage['toolCalls']
        }
        item.done = true
        break
      }
      case 'tool.start':
        // 工具卡片由 tool.result 完整呈现；start 仅记录活跃状态
        break
      case 'tool.result': {
        const mid = `tool:${String(data.tool_name)}:${envelope.seq}`
        streaming.value.push({
          messageId: mid,
          content: JSON.stringify(data),
          toolCalls: [{ name: String(data.tool_name), args: { status: String(data.status ?? '') } }],
          done: true,
        })
        break
      }
      case 'subagent.start': {
        const name = String(data.subagent_name ?? '')
        if (name && !activeSubagents.value.includes(name)) activeSubagents.value.push(name)
        // 子代理启动也作为中间过程收纳进「模型思考与工具调用过程」
        streaming.value.push({
          messageId: `subagent:start:${name}:${envelope.seq}`,
          content: '',
          toolCalls: [{ name: `子代理 ${name}`, args: { status: 'start' } }],
          done: false,
        })
        break
      }
      case 'subagent.end': {
        const name = String(data.subagent_name ?? '')
        activeSubagents.value = activeSubagents.value.filter((n) => n !== name)
        streaming.value.push({
          messageId: `subagent:end:${name}:${envelope.seq}`,
          content: '',
          toolCalls: [{ name: `子代理 ${name}`, args: { status: 'end' } }],
          done: true,
        })
        break
      }
      case 'plan.update':
        todos.value = (data.todos as { content: string; status: string }[]) ?? []
        break
      case 'workflow.update':
        workflowStage.value = (data.workflow_stage as string) ?? null
        workflowResults.value = {
          ...workflowResults.value,
          [String(data.workflow_stage ?? '')]: String(data.workflow_result ?? ''),
        }
        workflowRetryCount.value = Number(data.retry_count ?? workflowRetryCount.value)
        break
      case 'file.changed':
        fileChanged.value = {
          path: String(data.virtual_path ?? ''),
          chapterId: (data.chapter_id as string | null) ?? null,
          kind: String(data.change_kind ?? 'write'),
          at: Date.now(),
        }
        break
      case 'content.lock': {
        // AI 编辑锁（Agent 写章正文时加锁 / run 收尾释放）→ editor store
        // 带 project_id，供前端做项目级忙碌判定（禁删卷/章/项目）
        useEditorStore().setChapterLock(
          String(data.chapter_id ?? ''),
          String(data.run_id ?? ''),
          Boolean(data.locked),
          (data.project_id as string | undefined) ?? undefined,
        )
        break
      }
      case 'usage': {
        // 单会话上下文比例只统计**主 Agent** 的单次调用（子代理上下文不注入主会话）
        if (!Boolean(data.subagent)) {
          lastUsage.value = {
            tokens_in: Number(data.tokens_in ?? 0),
            tokens_out: Number(data.tokens_out ?? 0),
          }
        }
        // 头部全部会话消耗：run 级累计（含子代理）
        runCumulative.value = {
          tokens_in: Number(data.run_tokens_in ?? 0),
          tokens_out: Number(data.run_tokens_out ?? 0),
          cached_in: Number(data.run_cached_in ?? 0),
        }
        break
      }
      case 'interrupt.request': {
        const item: PendingInterrupt = {
          interruptId: String(data.interrupt_id ?? ''),
          toolName: (data.tool_name as string | null) ?? null,
          args: (data.args as Record<string, unknown> | null) ?? null,
          decisions: (data.decisions as string[]) ?? ['approve', 'edit', 'reject', 'respond'],
          risk: data.risk as string | undefined,
          reason: data.reason as string | undefined,
          itemLabel: (data.item_label as string | undefined) ?? undefined,
          path: (data.path as string | undefined) ?? undefined,
        }
        // 同类多条合并进同一张分页卡片（按 interruptId 去重，重连补发不重复入队）
        if (!pendingInterrupts.value.some((p) => p.interruptId === item.interruptId)) {
          pendingInterrupts.value = [...pendingInterrupts.value, item]
        }
        break
      }
      case 'consult.request':
        pendingConsult.value = {
          interruptId: String(data.interrupt_id ?? ''),
          question: String(data.question ?? ''),
          options: (data.options as PendingConsult['options']) ?? [],
          allowText: Boolean(data.allow_text ?? true),
          workflowStage: data.workflow_stage as string | undefined,
          textPlaceholder: data.text_placeholder as string | undefined,
          questions: (data.questions as PendingConsult['questions']) ?? undefined,
        }
        break
      case 'error':
        setRawError(String(data.message ?? '运行出错'), Number(data.code ?? 0) || null)
        break
      case 'steer.consumed':
        markSteerConsumed((data.message_ids as string[] | undefined) ?? [])
        break
      case 'run.notice':
        noticeMessage.value = {
          content: String(data.content ?? ''),
          kind: String(data.kind ?? ''),
        }
        // 已有落库收尾回复 → 撤掉灰色报错卡片（避免一条回复 + 灰卡双显示）
        clearError()
        break
      case 'run.end':
        running.value = false
        lastRunEndStatus.value = String(data.status ?? '')
        runPaused.value =
          String(data.status ?? '') === 'awaiting_approval' ||
          String(data.status ?? '') === 'awaiting_consult'
        // 暂停态：清空实时流式区（中间思考文本不再展示，等卡片答复后续跑）
        if (runPaused.value) streaming.value = []
        // run 结束（含暂停态 awaiting_*）→ 解除列表「AI思考执行中……」标记
        if (!runPaused.value) {
          if (currentSessionId.value) markSessionIdle(currentSessionId.value)
        }
        // 暂停态且仍有未决中断（多中断只决了其一）→ 恢复剩余卡片供继续处理
        if (
          (String(data.status ?? '') === 'awaiting_approval' || String(data.status ?? '') === 'awaiting_consult') &&
          currentRunId
        ) {
          void agentApi
            .getMessages(String(currentSessionId.value ?? ''))
            .then((tree) => restorePendingCards(tree.latest_run?.pending_interrupts ?? []))
            .catch(() => undefined)
        }
        // 运行以 failed 结束时，把后端错误转成中文提示（灰色异常态）；
        // 已落库收尾 notice（暂停/网络/模型异常）时不显示灰卡——notice 气泡即回复
        if (String(data.status ?? '') === 'failed') {
          if (data.notice) clearError()
          else {
            const msg = String(data.error ?? '') || 'AI 运行已中断'
            // error 事件已给出更精确的文案/错误码时不覆盖
            if (!error.value) setRawError(msg)
          }
        }
        // 宽限探测恢复续跑成功（error 事件先于续跑发出）→ 撤掉过期的灰色报错卡片
        if (String(data.status ?? '') === 'completed') clearError()
        break
    }
  }

  /**
   * 批量提交审批决策：`decisions` 与 `pendingInterrupts` 一一对应（缺省 approve）。
   * ★ 只 resolve 第一个中断，后端 resume 时会按已决行处理其余中断（避免 run 结束后重复调用报错）。
   * ★ 失败的中断保留在 pendingInterrupts 中，用户可重新处理。
   * ★ run 已结束时静默处理，不显示错误（用户重新发送消息即可）。
   */
  async function resolveInterrupts(
    decisions: ('approve' | 'approve_session' | 'edit' | 'reject' | 'respond')[],
    payloads?: ({ edited_args?: Record<string, unknown>; message?: string } | undefined)[],
  ): Promise<void> {
    const queue = [...pendingInterrupts.value]
    if (!queue.length) return

    // 只 resolve 第一个中断，其余由后端 resume 时处理
    const firstDecision = decisions[0] ?? 'approve'
    const firstPayload = payloads?.[0]
    try {
      const result = await agentApi.resolveInterrupt(queue[0].interruptId, { decision: firstDecision, ...firstPayload })
      // run 已结束：静默处理，不显示错误（post 已解包，result 即 data：{ run_ended, ... }）
      if (result?.run_ended) {
        pendingInterrupts.value = []
        running.value = false
        runPaused.value = false
        if (currentSessionId.value) markSessionIdle(currentSessionId.value)
        return
      }
    } catch (err) {
      // 第一个就失败了，保留所有中断供用户重新处理
      setError(err)
      return
    }

    // 第一个成功后，移除所有中断（后端会处理其余已决中断）
    pendingInterrupts.value = []

    // resume 会重开流；重新订阅（seq 续增）
    if (currentRunId) {
      running.value = true
      runPaused.value = false
      if (currentSessionId.value) markSessionRunning(currentSessionId.value)
      startStream(currentRunId, sseClient?.currentSeq ?? undefined)
    }
  }

  async function resolveInterrupt(
    decision: 'approve' | 'approve_session' | 'edit' | 'reject' | 'respond',
    payload?: { edited_args?: Record<string, unknown>; message?: string },
  ): Promise<void> {
    await resolveInterrupts([decision], [payload])
  }

  async function resolveConsult(decision: 'option' | 'text', value: string): Promise<void> {
    if (!pendingConsult.value) return
    const id = pendingConsult.value.interruptId
    try {
      const result = await agentApi.resolveInterrupt(id, {
        decision,
        choice: decision === 'option' ? value : undefined,
        text: decision === 'text' ? value : undefined,
      })
      // run 已结束：静默处理（post 已解包，result 即 data）
      if (result?.run_ended) {
        pendingConsult.value = null
        running.value = false
        runPaused.value = false
        if (currentSessionId.value) markSessionIdle(currentSessionId.value)
        return
      }
      // 成功后才清空卡片
      pendingConsult.value = null
      if (currentRunId) {
        running.value = true
        runPaused.value = false
        if (currentSessionId.value) markSessionRunning(currentSessionId.value)
        startStream(currentRunId, sseClient?.currentSeq ?? undefined)
      }
    } catch (err) {
      // 失败时保留卡片，用户可重新提交
      setError(err)
    }
  }

  async function cancelRun(): Promise<void> {
    if (!currentRunId) return
    try {
      await agentApi.cancelRun(currentRunId)
      running.value = false
      runPaused.value = false
      if (currentSessionId.value) markSessionIdle(currentSessionId.value)
      await refreshMessages()
    } catch (err) {
      setError(err)
    }
  }

  /** 运行中「通过消息引导发送」——把引导文本注入正在跑的 run（节点完成后进入提示词） */
  async function steerCurrentRun(content: string): Promise<void> {
    if (!currentRunId || !running.value) {
      // 无进行中的 run → 回落为普通发送
      await sendMessage(content)
      return
    }
    try {
      const res = await agentApi.steerRun(currentRunId, content)
      addSteered(res.message_id, content)
    } catch (err) {
      setError(err)
      throw err
    }
  }

  /** 编辑未消费的引导消息（后端同步更新落库行与队列项） */
  async function editSteer(messageId: string, content: string): Promise<void> {
    if (!currentRunId) return
    await agentApi.updateSteer(currentRunId, messageId, content)
    updateSteerLocal(messageId, content)
  }

  /** 删除未消费的引导消息（后端同步删落库行与队列项） */
  async function removeSteer(messageId: string): Promise<void> {
    if (!currentRunId) return
    await agentApi.deleteSteer(currentRunId, messageId)
    removeSteerLocal(messageId)
  }

  function stopStream(): void {
    sseClient?.close()
    sseClient = null
  }

  function clearFileChanged(): void {
    fileChanged.value = null
  }

  return {
    sessions,
    currentSessionId,
    currentSession,
    messages,
    activeLeafId,
    currentRun,
    streaming,
    pendingInterrupts,
    pendingConsult,
    running,
    runPaused,
    loading,
    error,
    errorCode,
    pendingUserMessage,
    clearError,
    fileChanged,
    todos,
    workflowStage,
    workflowRetryCount,
    workflowPrecheckSkipped,
    workflowResults,
    activeSubagents,
    lastUsage,
    runCumulative,
    usage,
    queued,
    steered,
    clearSteered,
    noticeMessage,
    clearNotice,
    editSteer,
    removeSteer,
    runningSessionIds,
    pendingSelectionBlocks,
    attachSelection,
    removePendingSelection,
    clearPendingSelections,
    loadSessions,
    loadUsage,
    refreshSessionsLight,
    createSession,
    selectSession,
    backToList,
    queueMessage,
    updateQueued,
    removeQueued,
    queueToHead,
    clearQueue,
    refreshMessages,
    renameSession,
    deleteSession,
    switchBranch,
    sendMessage,
    resolveInterrupt,
    resolveInterrupts,
    resolveConsult,
    cancelRun,
    steerCurrentRun,
    stopStream,
    clearFileChanged,
  }
})
