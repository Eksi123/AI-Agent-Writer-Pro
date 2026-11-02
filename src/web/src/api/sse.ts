/**
 * SSE 客户端（手册「六 · SSE 契约」）：
 * - 信封 `{event, run_id, session_id, seq, ts, data}`，seq 每 run 内单调递增；
 * - 断线重连带 `Last-Event-ID: {seq}` 从服务端环形缓冲补发；
 * - 收到 `error` 且 code=41001（缓冲丢失）→ 回落重拉消息树；
 * - 先建流再发消息也支持（POST messages 返回 202 后事件由缓冲补齐）。
 *
 * M1 阶段 Agent 面板未交付，此客户端先行落地供 M3 接线。
 */

export interface SseEnvelope<T = unknown> {
  event: string
  run_id: string
  session_id: string
  seq: number
  ts: string
  data: T
}

export type SseHandler = (envelope: SseEnvelope) => void

export interface SseClientOptions {
  /** run 级流地址：/api/v1/agent/runs/{run_id}/stream */
  url: string
  onEvent: SseHandler
  /** 缓冲丢失（41001）：调用方应重拉 GET /agent/sessions/{sid}/messages */
  onBufferLost?: () => void
  onError?: (err: Error) => void
  onClose?: () => void
  /** 断线自动重连（指数退避），默认 true */
  autoReconnect?: boolean
  maxReconnectAttempts?: number
}

export class SseClient {
  private source: EventSource | null = null
  private lastSeq = 0
  private attempts = 0
  private closedByUser = false
  private readonly opts: Required<Pick<SseClientOptions, 'autoReconnect' | 'maxReconnectAttempts'>> &
    SseClientOptions

  constructor(options: SseClientOptions) {
    this.opts = { autoReconnect: true, maxReconnectAttempts: 5, ...options }
  }

  /** 建流。若此前已收到过事件，带 Last-Event-ID 续传。 */
  connect(): void {
    this.closedByUser = false
    const url = this.lastSeq > 0 ? `${this.opts.url}?last_event_id=${this.lastSeq}` : this.opts.url
    const source = new EventSource(url)
    this.source = source

    source.onmessage = (ev: MessageEvent<string>) => this.handleRaw(ev.data)

    // 服务端以具名事件下发（event: xxx），这里统一注册常见事件类型
    for (const name of [
      'run.start',
      'message.delta',
      'message.complete',
      'tool.start',
      'tool.result',
      'subagent.start',
      'subagent.delta',
      'subagent.end',
      'plan.update',
      'file.changed',
      'context.compact',
      'workflow.update',
      'consult.request',
      'interrupt.request',
      'usage',
      'error',
      'run.end',
    ]) {
      source.addEventListener(name, (ev: MessageEvent<string>) => this.handleRaw(ev.data))
    }

    source.onerror = () => {
      source.close()
      this.source = null
      if (this.closedByUser) {
        this.opts.onClose?.()
        return
      }
      if (this.opts.autoReconnect && this.attempts < this.opts.maxReconnectAttempts) {
        this.attempts += 1
        const delay = Math.min(1000 * 2 ** this.attempts, 15_000)
        setTimeout(() => this.connect(), delay)
      } else {
        this.opts.onError?.(new Error('SSE 连接失败且重连次数已用尽'))
        this.opts.onClose?.()
      }
    }
  }

  private handleRaw(raw: string): void {
    if (!raw) return
    let envelope: SseEnvelope
    try {
      envelope = JSON.parse(raw) as SseEnvelope
    } catch {
      return
    }
    if (typeof envelope.seq === 'number' && envelope.seq > this.lastSeq) {
      this.lastSeq = envelope.seq
      this.attempts = 0 // 收到新事件即认为链路健康
    }
    if (envelope.event === 'error') {
      const data = envelope.data as { code?: number; message?: string }
      // 41001 = 缓冲丢失，不可续传 → 回落重拉消息树
      if (data?.code === 41001) {
        this.opts.onBufferLost?.()
        this.close()
        return
      }
    }
    this.opts.onEvent(envelope)
    if (envelope.event === 'run.end') {
      this.close()
    }
  }

  /** 主动关闭（不再重连） */
  close(): void {
    this.closedByUser = true
    this.source?.close()
    this.source = null
    this.opts.onClose?.()
  }

  get currentSeq(): number {
    return this.lastSeq
  }
}
