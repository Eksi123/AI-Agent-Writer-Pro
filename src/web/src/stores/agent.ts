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
import { ErrorCode } from '@/types'
import type { AgentMessageDto, AgentRunDto, AgentSessionDto, MessageTree } from '@/types'

/** ★v1101：同时「AI 思考执行中」的会话上限（与后端 MAX_CONCURRENT_RUNS 一致） */
export const MAX_CONCURRENT_SESSIONS = 5

/** 流式中的助手消息（按 message_id 聚合 delta） */
export interface StreamingMessage {
  messageId: string
  content: string
  toolCalls: { name: string; args?: Record<string, unknown>; id?: string }[]
  done: boolean
}

/** 待决审批卡片 */
export interface PendingInterrupt {
  interruptId: string
  toolName: string | null
  args: Record<string, unknown> | null
  decisions: string[]
  risk?: string
  reason?: string
}

/** 待决咨询卡片（M4 门禁使用） */
export interface PendingConsult {
  interruptId: string
  question: string
  options: { value: string; label: string }[] | string[]
  allowText: boolean
  gateStage?: string
}

/**
 * v1030续(6)：正文选区[AI润色]加入的「选中文本块」。
 * 除文本本身外，还带上章节 id、所在段落范围与前后文，让大模型知道这段文本
 * 在文章中的位置，从而只针对该范围润色并写回。
 * ★label = `章节名_节选X`（X 为同一章内已加入的选区序号，正整数），保持标签简短。
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
  const pendingInterrupt = ref<PendingInterrupt | null>(null)
  const pendingConsult = ref<PendingConsult | null>(null)
  const running = ref(false)
  const loading = ref(false)
  const error = ref<string | null>(null)
  /**
   * v1030续(1)：刚发出的用户消息（乐观回显）。
   * 主流 AI Agent 的行为是「发出去就立刻出现在会话里」，而后端消息树要等 run 收尾才刷新，
   * 因此先本地回显，`refreshMessages()` 拿到真实消息后自动置空替换。
   * ★v1031：改为对象，带上本地时间戳，使乐观回显的用户消息下方也能显示日期时间。
   */
  const pendingUserMessage = ref<{ content: string; sentAt: string } | null>(null)
  /**
   * v1030续：错误码（与后端 ErrorCode 对齐）。
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
  /** 门禁状态（gate.update，M4） */
  const gateStage = ref<string | null>(null)
  const gateRetryCount = ref(0)
  const gateG1Skipped = ref(false)
  const gateResults = ref<Record<string, string>>({})
  /** 子代理泳道 */
  const activeSubagents = ref<string[]>([])
  /** 最近一次运行的用量 */
  const lastUsage = ref<{ tokens_in: number; tokens_out: number }>({ tokens_in: 0, tokens_out: 0 })
  /** v1028：当前项目 token 使用量 + 会话数（会话框头部展示）
   * ★v1101：同时保留入/出明细，供头部 Token 悬浮提示展示 */
  const usage = ref<{
    session_count: number
    tokens_in: number
    tokens_out: number
    tokens_total: number
  }>({ session_count: 0, tokens_in: 0, tokens_out: 0, tokens_total: 0 })
  /** v1028：运行中「添加到队列」的待发消息（run.end 后自动发送首条） */
  const queued = ref<{ content: string; modelRef?: string; hiddenContext?: string }[]>([])
  /**
   * ★v1101：当前「AI 思考执行中」的会话 id 集合（上限 5 个，后端硬校验）。
   * 会话列表条目右侧据此显示灰色小字「AI思考执行中……」；
   * 发送时本地先拦一道（同会话重复发送），跨会话并发由后端 40904 兜底。
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
   * v1031 / v1030续(6)：正文编辑器悬浮[AI润色]按钮加入的「选中文本块」。
   * 与「添加文件」同形制：随下一条消息一起发送，限定 AI 的修改范围；本轮问答结束后释放。
   * `chapterId` 让大模型知道这段文本属于哪一章（可定位并写回）；`rangeLabel` 说明所在行范围。
   */
  const pendingSelectionBlocks = ref<PendingSelectionBlock[]>([])

  function attachSelection(block: PendingSelectionBlock): string | null {
    // ★v1030续：标签统一为 `章节名_节选X`（X = 本章已有选区数 + 1），保持聊天框上方标签简短。
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

  /** ★v1101：最近一次加载会话列表的项目 id（列表页 currentSession 为空时轮询兜底用） */
  const lastNovelId = ref<string | null>(null)

  async function loadSessions(novelId: string): Promise<void> {
    loading.value = true
    clearError()
    lastNovelId.value = novelId
    // v1029：切换小说项目 → 上下文占用清零（剩余比例回到 100%）
    lastUsage.value = { tokens_in: 0, tokens_out: 0 }
    try {
      sessions.value = await agentApi.listSessions(novelId)
      // ★v1101：进入页面即按服务端 is_running 初始化「AI思考执行中……」标记
      runningSessionIds.value = new Set(sessions.value.filter((s) => s.is_running).map((s) => s.id))
      runningSince.clear()
      if (runningSessionIds.value.size) startRunningPoll()
      else stopRunningPoll()
      await loadUsage(novelId)
      // v1028：会话记录列表默认不选中任何会话（点击记录才进入），最多展示 50 条
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

  /** v1028：轻量刷新会话列表（不改变当前选中，run 结束后更新标题/排序）
   * ★v1101：同时以服务端 `is_running` 对账运行中标记（SSE 漏收时兜底）；
   * 列表页（currentSession 为空）用 lastNovelId 兜底，保证轮询在列表页也生效。 */
  async function refreshSessionsLight(): Promise<void> {
    const novelId = currentSession.value?.project_id ?? lastNovelId.value
    if (!novelId) return
    try {
      const list = await agentApi.listSessions(novelId)
      sessions.value = list
      // ★v1101：以服务端 is_running 为准对账；本地刚发出（宽限窗口内）的保留，
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

  /** v1028：当前小说项目的会话数量与累计 token 使用量 */
  async function loadUsage(novelId?: string | null): Promise<void> {
    const id = novelId ?? (currentSession.value?.project_id ?? null)
    if (!id) return
    try {
      const u = await agentApi.usage(id)
      usage.value = {
        session_count: u.session_count,
        tokens_in: u.tokens_in,
        tokens_out: u.tokens_out,
        tokens_total: u.tokens_total,
      }
    } catch {
      /* 用量读取失败不阻断会话 */
    }
  }

  /** v1028：运行中「添加到队列」；★v1101：队列项可带隐藏上下文 */
  function queueMessage(content: string, modelRef?: string, hiddenContext?: string): void {
    queued.value = [...queued.value, { content, modelRef, hiddenContext }]
  }

  function clearQueue(): void {
    queued.value = []
  }

  function backToList(): void {
    stopStream()
    currentSessionId.value = null
    messages.value = []
    lastUsage.value = { tokens_in: 0, tokens_out: 0 }
    pendingInterrupt.value = null
    pendingConsult.value = null
    streaming.value = []
    todos.value = []
    gateStage.value = null
    gateResults.value = {}
    pendingUserMessage.value = null
  }

  async function createSession(novelId: string, title?: string, modelRef?: string): Promise<AgentSessionDto> {
    const sess = await agentApi.createSession({ novel_id: novelId, title, model_ref: modelRef })
    sessions.value.unshift(sess)
    await selectSession(sess.id)
    await loadUsage(novelId)
    return sess
  }

  async function selectSession(sessionId: string): Promise<void> {
    stopStream()
    currentSessionId.value = sessionId
    pendingInterrupt.value = null
    pendingConsult.value = null
    streaming.value = []
    todos.value = []
    gateStage.value = null
    gateRetryCount.value = 0
    gateG1Skipped.value = false
    gateResults.value = {}
    pendingUserMessage.value = null
    // v1029：先清零（→ 剩余 100%），再按该会话最近一次 run 还原真实占用；
    // 全新会话没有任何 run，还原结果仍是 0，故「新会话回到 100%」自然成立。
    lastUsage.value = { tokens_in: 0, tokens_out: 0 }
    try {
      const tree: MessageTree = await agentApi.getMessages(sessionId)
      messages.value = tree.items.filter((m) => m.in_active_path)
      activeLeafId.value = tree.active_leaf_id ?? null
      // ★v1101：恢复未决的审批/咨询卡片 + 运行态（SSE 事件在刷新/重进时会漏收，
      // 不恢复则 run 永久挂起、用户看不到卡片）
      const lr = tree.latest_run
      if (lr && (lr.status === 'awaiting_approval' || lr.status === 'awaiting_consult' || lr.status === 'running' || lr.status === 'pending')) {
        currentRunId = lr.run_id
        running.value = true
        markSessionRunning(sessionId)
        for (const it of lr.pending_interrupts ?? []) {
          const payload = (it.payload ?? {}) as Record<string, unknown>
          if (it.type === 'approval') {
            pendingInterrupt.value = {
              interruptId: it.id,
              toolName: (payload.tool_name as string | null) ?? it.tool_name ?? null,
              args: (payload.args as Record<string, unknown> | null) ?? null,
              decisions: (payload.decisions as string[]) ?? ['approve', 'edit', 'reject', 'respond'],
              risk: payload.risk as string | undefined,
              reason: payload.reason as string | undefined,
            }
          } else {
            pendingConsult.value = {
              interruptId: it.id,
              question: String(payload.question ?? ''),
              options: (payload.options as PendingConsult['options']) ?? [],
              allowText: Boolean(payload.allow_text ?? true),
              gateStage: payload.gate_stage as string | undefined,
            }
          }
        }
        // 重连事件流：缓冲仍在则续传，已丢则 onBufferLost 回落重拉消息树
        startStream(lr.run_id)
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
      // v1030续：真实消息已到，撤掉乐观回显
      pendingUserMessage.value = null
      /**
       * ★v1031：同时清空流式缓冲——否则 run 收尾后「已持久化的回复」与
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
    // ★v1101：删除会话同时清除其「思考执行中」标记
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
    // ★v1101：并发上限本地预检（后端 40904 兜底）：已有 5 个会话在思考且不含本会话 → 拒绝
    if (
      runningSessionIds.value.size >= MAX_CONCURRENT_SESSIONS &&
      !runningSessionIds.value.has(currentSessionId.value)
    ) {
      error.value = `同时思考执行的会话已达上限（${MAX_CONCURRENT_SESSIONS} 个），请等待其中某个完成后再发送`
      errorCode.value = ErrorCode.RUN_CONCURRENCY_LIMIT
      return
    }
    clearError()
    // v1030续(1)：乐观回显——发出的用户消息立即出现在会话里（run 收尾刷新后替换）
    // ★v1101：记下发送时刻；并带上「当前正在编辑/阅读的章」——
    // 后端意图检测命中且消息未点名章节时，用它作门禁目标章
    pendingUserMessage.value = { content, sentAt: new Date().toISOString() }
    try {
      const { run_id: runId } = await agentApi.postMessage(currentSessionId.value, {
        content,
        model_ref: modelRef,
        active_chapter_id: activeChapterIdForGate() || undefined,
        // ★v1101：隐藏上下文（润色提示词）随本轮送给模型，不入消息树不展示
        hidden_context: hiddenContext || undefined,
      })
      currentRunId = runId
      running.value = true
      // ★v1101：会话进入「AI 思考执行中」（列表右侧灰色小字 + 并发上限本地预检）
      markSessionRunning(currentSessionId.value)
      // v1029：**不**清零 lastUsage——上下文只增不减，清零会让百分比闪回 100%；
      // 保留上一次的值直到本次 run 的 usage 事件刷新它。
      startStream(runId)
      // ★v1031 续：首条消息发出后后端即刻把会话标题改为「首条消息文字」，
      // 这里轻量刷新会话列表，让标题/列表立刻生效（不必等 run 结束）。
      void refreshSessionsLight()
    } catch (err) {
      // v1030续：发送失败 → 撤掉乐观回显，避免留下未真正发出的消息
      pendingUserMessage.value = null
      setError(err)
    }
  }

  /**
   * ★v1101：当前正在编辑/阅读的章 id（供后端门禁意图检测作目标章回落）。
   * 编辑模式看 editor store，阅读模式看 reader store 的当前章。
   */
  function activeChapterIdForGate(): string | null {
    if (useEditorStore().chapterId) return useEditorStore().chapterId
    return useReaderStore().activeChapterId ?? null
  }

  /** ★v1101：删除[生成本章]按钮——门禁改由用户消息意图触发（后端检测） */

  /** ★v1031：删除[重新生成]功能（store 侧 regenerate 一并移除） */

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
        // v1028：run 结束后自动发送队列中的首条消息（「添加到队列」）
        if (queued.value.length) {
          const next = queued.value[0]
          queued.value = queued.value.slice(1)
          void sendMessage(next.content, next.modelRef, next.hiddenContext)
        }
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
        break
      }
      case 'subagent.end': {
        const name = String(data.subagent_name ?? '')
        activeSubagents.value = activeSubagents.value.filter((n) => n !== name)
        break
      }
      case 'plan.update':
        todos.value = (data.todos as { content: string; status: string }[]) ?? []
        break
      case 'gate.update':
        gateStage.value = (data.gate_stage as string) ?? null
        gateResults.value = {
          ...gateResults.value,
          [String(data.gate_stage ?? '')]: String(data.gate_result ?? ''),
        }
        gateRetryCount.value = Number(data.retry_count ?? gateRetryCount.value)
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
        // ★v1101：AI 编辑锁（Agent 写章正文时加锁 / run 收尾释放）→ editor store
        useEditorStore().setChapterLock(
          String(data.chapter_id ?? ''),
          String(data.run_id ?? ''),
          Boolean(data.locked),
        )
        break
      }
      case 'usage':
        lastUsage.value = {
          tokens_in: Number(data.tokens_in ?? 0),
          tokens_out: Number(data.tokens_out ?? 0),
        }
        break
      case 'interrupt.request':
        pendingInterrupt.value = {
          interruptId: String(data.interrupt_id ?? ''),
          toolName: (data.tool_name as string | null) ?? null,
          args: (data.args as Record<string, unknown> | null) ?? null,
          decisions: (data.decisions as string[]) ?? ['approve', 'edit', 'reject', 'respond'],
          risk: data.risk as string | undefined,
          reason: data.reason as string | undefined,
        }
        break
      case 'consult.request':
        pendingConsult.value = {
          interruptId: String(data.interrupt_id ?? ''),
          question: String(data.question ?? ''),
          options: (data.options as PendingConsult['options']) ?? [],
          allowText: Boolean(data.allow_text ?? true),
          gateStage: data.gate_stage as string | undefined,
        }
        break
      case 'error':
        setRawError(String(data.message ?? '运行出错'), Number(data.code ?? 0) || null)
        break
      case 'run.end':
        running.value = false
        // ★v1101：run 结束（含暂停态 awaiting_*）→ 解除列表「AI思考执行中……」标记
        if (String(data.status ?? '') !== 'awaiting_approval' && String(data.status ?? '') !== 'awaiting_consult') {
          if (currentSessionId.value) markSessionIdle(currentSessionId.value)
        }
        // v1030续：运行以 failed 结束时，把后端错误转成中文提示（灰色异常态）
        if (String(data.status ?? '') === 'failed') {
          const msg = String(data.error ?? '') || 'AI 运行已中断'
          // error 事件已给出更精确的文案/错误码时不覆盖
          if (!error.value) setRawError(msg)
        }
        break
    }
  }

  async function resolveInterrupt(
    decision: 'approve' | 'edit' | 'reject' | 'respond',
    payload?: { edited_args?: Record<string, unknown>; message?: string },
  ): Promise<void> {
    if (!pendingInterrupt.value) return
    const id = pendingInterrupt.value.interruptId
    pendingInterrupt.value = null
    try {
      await agentApi.resolveInterrupt(id, { decision, ...payload })
      // resume 会重开流；重新订阅（seq 续增）
      if (currentRunId) {
        running.value = true
        if (currentSessionId.value) markSessionRunning(currentSessionId.value)
        startStream(currentRunId, sseClient?.currentSeq ?? undefined)
      }
    } catch (err) {
      setError(err)
    }
  }

  async function resolveConsult(decision: 'option' | 'text', value: string): Promise<void> {
    if (!pendingConsult.value) return
    const id = pendingConsult.value.interruptId
    pendingConsult.value = null
    try {
      await agentApi.resolveInterrupt(id, {
        decision,
        choice: decision === 'option' ? value : undefined,
        text: decision === 'text' ? value : undefined,
      })
      if (currentRunId) {
        running.value = true
        if (currentSessionId.value) markSessionRunning(currentSessionId.value)
        startStream(currentRunId, sseClient?.currentSeq ?? undefined)
      }
    } catch (err) {
      setError(err)
    }
  }

  async function cancelRun(): Promise<void> {
    if (!currentRunId) return
    try {
      await agentApi.cancelRun(currentRunId)
      running.value = false
      if (currentSessionId.value) markSessionIdle(currentSessionId.value)
      await refreshMessages()
    } catch (err) {
      setError(err)
    }
  }

  /** v1028：运行中「通过消息引导发送」——把引导文本注入正在跑的 run */
  async function steerCurrentRun(content: string): Promise<void> {
    if (!currentRunId || !running.value) {
      // 无进行中的 run → 回落为普通发送
      await sendMessage(content)
      return
    }
    try {
      await agentApi.steerRun(currentRunId, content)
    } catch (err) {
      setError(err)
      throw err
    }
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
    pendingInterrupt,
    pendingConsult,
    running,
    loading,
    error,
    errorCode,
    pendingUserMessage,
    clearError,
    fileChanged,
    todos,
    gateStage,
    gateRetryCount,
    gateG1Skipped,
    gateResults,
    activeSubagents,
    lastUsage,
    usage,
    queued,
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
    clearQueue,
    refreshMessages,
    renameSession,
    deleteSession,
    switchBranch,
    sendMessage,
    resolveInterrupt,
    resolveConsult,
    cancelRun,
    steerCurrentRun,
    stopStream,
    clearFileChanged,
  }
})
