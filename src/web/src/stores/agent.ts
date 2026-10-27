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
import { errorMessage } from '@/api/http'
import { SseClient, type SseEnvelope } from '@/api/sse'
import type { AgentMessageDto, AgentRunDto, AgentSessionDto, MessageTree } from '@/types'

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

  let sseClient: SseClient | null = null
  let currentRunId: string | null = null

  const currentSession = computed(
    () => sessions.value.find((s) => s.id === currentSessionId.value) ?? null,
  )

  async function loadSessions(novelId: string): Promise<void> {
    loading.value = true
    error.value = null
    try {
      sessions.value = await agentApi.listSessions(novelId)
      // 自动选中第一个会话（没有则不选，由 UI 提供新建入口）
      if (sessions.value.length > 0 && !sessions.value.some((s) => s.id === currentSessionId.value)) {
        await selectSession(sessions.value[0].id)
      } else if (sessions.value.length === 0) {
        currentSessionId.value = null
        messages.value = []
      }
    } catch (err) {
      error.value = errorMessage(err)
    } finally {
      loading.value = false
    }
  }

  async function createSession(novelId: string, title?: string, modelRef?: string): Promise<AgentSessionDto> {
    const sess = await agentApi.createSession({ novel_id: novelId, title, model_ref: modelRef })
    sessions.value.unshift(sess)
    await selectSession(sess.id)
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
    try {
      const tree: MessageTree = await agentApi.getMessages(sessionId)
      messages.value = tree.items.filter((m) => m.in_active_path)
      activeLeafId.value = tree.active_leaf_id ?? null
    } catch (err) {
      error.value = errorMessage(err)
    }
  }

  async function refreshMessages(): Promise<void> {
    if (!currentSessionId.value) return
    try {
      const tree = await agentApi.getMessages(currentSessionId.value)
      messages.value = tree.items.filter((m) => m.in_active_path)
      activeLeafId.value = tree.active_leaf_id ?? null
    } catch (err) {
      error.value = errorMessage(err)
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
    if (currentSessionId.value === id) {
      currentSessionId.value = null
      messages.value = []
    }
  }

  async function switchBranch(messageId: string): Promise<void> {
    if (!currentSessionId.value) return
    await agentApi.switchBranch(currentSessionId.value, messageId)
    await refreshMessages()
  }

  async function sendMessage(content: string, modelRef?: string): Promise<void> {
    if (!currentSessionId.value) {
      error.value = '请先选择或新建会话'
      return
    }
    error.value = null
    try {
      const { run_id: runId } = await agentApi.postMessage(currentSessionId.value, {
        content,
        model_ref: modelRef,
      })
      currentRunId = runId
      running.value = true
      lastUsage.value = { tokens_in: 0, tokens_out: 0 }
      startStream(runId)
    } catch (err) {
      error.value = errorMessage(err)
    }
  }

  /** 门禁生成指定章正文（手册「二、(6)」四阶段） */
  async function generateChapter(chapterId: string, chapterTitle: string, modelRef?: string): Promise<void> {
    if (!currentSessionId.value) {
      error.value = '请先选择或新建会话'
      return
    }
    error.value = null
    gateStage.value = 'G0'
    gateRetryCount.value = 0
    gateG1Skipped.value = false
    gateResults.value = {}
    try {
      const { run_id: runId } = await agentApi.postMessage(currentSessionId.value, {
        content: `请按大纲生成《${chapterTitle}》的正文。`,
        model_ref: modelRef,
        target_chapter_id: chapterId,
      })
      currentRunId = runId
      running.value = true
      lastUsage.value = { tokens_in: 0, tokens_out: 0 }
      startStream(runId)
    } catch (err) {
      error.value = errorMessage(err)
      gateStage.value = null
    }
  }

  /** 重新生成：以最后一条用户消息的父节点重发（形成兄弟分支） */
  async function regenerate(modelRef?: string): Promise<void> {
    const lastUser = [...messages.value].reverse().find((m) => m.role === 'user')
    if (!lastUser) return
    await sendMessage(String(lastUser.content), modelRef)
  }

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
        error.value = err.message
      },
      onClose: () => {
        // run.end 后收尾：把流式消息合入正式列表
        running.value = false
        void refreshMessages()
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
        error.value = String(data.message ?? '运行出错')
        break
      case 'run.end':
        running.value = false
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
        startStream(currentRunId, sseClient?.currentSeq ?? undefined)
      }
    } catch (err) {
      error.value = errorMessage(err)
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
        startStream(currentRunId, sseClient?.currentSeq ?? undefined)
      }
    } catch (err) {
      error.value = errorMessage(err)
    }
  }

  async function cancelRun(): Promise<void> {
    if (!currentRunId) return
    try {
      await agentApi.cancelRun(currentRunId)
      running.value = false
      await refreshMessages()
    } catch (err) {
      error.value = errorMessage(err)
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
    fileChanged,
    todos,
    gateStage,
    gateRetryCount,
    gateG1Skipped,
    gateResults,
    activeSubagents,
    lastUsage,
    loadSessions,
    createSession,
    selectSession,
    refreshMessages,
    renameSession,
    deleteSession,
    switchBranch,
    sendMessage,
    generateChapter,
    regenerate,
    resolveInterrupt,
    resolveConsult,
    cancelRun,
    stopStream,
    clearFileChanged,
  }
})
