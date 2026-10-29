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
import { agentApi, configApi, llmApi, outlineApi } from '@/api'
import { errorMessage } from '@/api/http'
import { t } from '@/i18n'
import type { LlmOverview } from '@/types'
import ApprovalCard from './ApprovalCard.vue'
import ConsultCard from './ConsultCard.vue'
import PlanTodoList from './PlanTodoList.vue'
import GateStatusBar from './GateStatusBar.vue'

const agent = useAgentStore()
const project = useProjectStore()
const reader = useReaderStore()

const input = ref('')
const sending = ref(false)
const messageHost = ref<HTMLElement | null>(null)
const overview = ref<LlmOverview | null>(null)
/** v1028：选中的模型（未选中或无模型 → Model Off） */
const modelRef = ref<string>('')
/** 发送方式三选项弹层 */
const sendMenu = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

/** v1028：会话记录最多展示 50 条 */
const MAX_SESSIONS = 50
const sessionList = computed(() => agent.sessions.slice(0, MAX_SESSIONS))

// ---------------------------------------------------------------------------
// 头部：会话数量 + 项目 token 使用量
// ---------------------------------------------------------------------------

const sessionCountLabel = computed(() => t('agent.sessionCount', { n: agent.usage.session_count }))

/** token 使用量（K 为单位） */
const tokenLabel = computed(() => `${(agent.usage.tokens_total / 1000).toFixed(1)}K`)

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
  const live: DisplayMessage[] = agent.streaming.map((s) => ({
    key: s.messageId,
    role: s.messageId.startsWith('tool:') ? 'tool' : 'assistant',
    content: s.content,
    toolCalls: s.toolCalls,
    streaming: !s.done,
    createdAt: null,
  }))
  return [...persisted, ...live]
})

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
    // 默认选中后端的默认模型；否则留空（显示 Model Off）
    if (!modelRef.value) {
      const def = overview.value.models.find((m) => m.is_default)
      modelRef.value = def?.model_ref ?? ''
    }
  } catch {
    overview.value = null
  }
}

const hasModels = computed(() => (overview.value?.models.length ?? 0) > 0)

onMounted(async () => {
  await loadOverview()
  if (project.currentNovelId) await agent.loadSessions(project.currentNovelId)
})

onBeforeUnmount(() => agent.stopStream())

watch(
  () => project.currentNovelId,
  async (id) => {
    agent.backToList()
    if (id) await agent.loadSessions(id)
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
    await agent.deleteSession(id)
    agent.backToList()
  } catch {
    /* 取消 */
  }
}

function exportSession(): void {
  const sess = agent.currentSession
  if (!sess) return
  const lines: string[] = [
    `# 会话记录 · ${sess.title}`,
    '',
    `- 项目：${sess.project_title || sess.project_id}`,
    `- 导出时间：${new Date().toLocaleString('zh-CN')}`,
    '',
    '---',
    '',
  ]
  for (const msg of agent.messages) {
    const who = msg.role === 'user' ? '我' : msg.role === 'assistant' ? 'AI' : msg.role
    lines.push(`### ${who}`, '')
    if (msg.tool_calls?.length) {
      for (const tc of msg.tool_calls) {
        let argsText = ''
        try {
          argsText = JSON.stringify(tc.args ?? {})
          if (argsText.length > 200) argsText = `${argsText.slice(0, 200)}…`
        } catch {
          argsText = ''
        }
        lines.push(`> 🔧 ${tc.name}${argsText ? ` ${argsText}` : ''}`)
      }
      lines.push('')
    }
    const content = String(msg.content ?? '').trim()
    if (content) lines.push(content, '')
  }
  const blob = new Blob([lines.join('\n')], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  const stamp = new Date().toISOString().slice(0, 19).replace(/[T:]/g, '-')
  a.href = url
  a.download = `${sess.title.replace(/[\\/:*?"<>|]/g, '_')}-${stamp}.md`
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
  ElMessage.success('会话已导出为 Markdown')
}

// ---------------------------------------------------------------------------
// 发送 / 运行中三选项（停止并发送 / 通过消息引导发送 / 添加到队列）
// ---------------------------------------------------------------------------

const canSend = computed(() => input.value.trim().length > 0 && !!project.currentNovelId)

async function send(): Promise<void> {
  const content = input.value.trim()
  if (!content || sending.value) return
  if (!agent.currentSessionId) {
    await newSession()
    if (!agent.currentSessionId) return
  }
  sending.value = true
  input.value = ''
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
    ElMessage.success('已把引导内容发送给正在思考的 AI')
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
  ElMessage.success('已添加到队列，当前回答结束后自动发送')
}

async function onStop(): Promise<void> {
  await agent.cancelRun()
  ElMessage.info(t('agent.cancelledRun'))
}

async function onRegenerate(): Promise<void> {
  await agent.regenerate(modelRef.value || undefined)
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
// 「+」添加内容：大纲 / 章节 / 本地文件
// ---------------------------------------------------------------------------

/** 已添加的上下文块（随下一条消息一起发送） */
interface ContextBlock {
  kind: 'outline' | 'chapter' | 'file'
  label: string
  text: string
}
const blocks = ref<ContextBlock[]>([])
const addPopover = ref(false)

const chapterOptions = computed(() =>
  project.flatChapters.map((fc) => ({
    id: fc.chapter.id,
    name: `${fc.volume.title} · ${fc.chapter.title}`,
  })),
)

async function addOutline(): Promise<void> {
  if (!project.currentNovelId) return
  try {
    const o = await outlineApi.read(project.currentNovelId)
    blocks.value = [...blocks.value, { kind: 'outline', label: '大纲', text: o.content }]
    addPopover.value = false
    ElMessage.success('已添加大纲')
  } catch (err) {
    ElMessage.error(errorMessage(err))
  }
}

async function addChapter(id: string): Promise<void> {
  const found = project.findChapter(id)
  const label = found ? `${found.volume.title} · ${found.chapter.title}` : id
  if (blocks.value.some((b) => b.kind === 'chapter' && b.label === label)) {
    addPopover.value = false
    return
  }
  try {
    const { contentApi } = await import('@/api')
    const c = await contentApi.read(id)
    blocks.value = [...blocks.value, { kind: 'chapter', label, text: c.content }]
    addPopover.value = false
    ElMessage.success(`已添加「${label}」`)
  } catch (err) {
    ElMessage.error(errorMessage(err))
  }
}

/** 本地文件：读取文本后作为上下文（限大模型可理解的文本类文件） */
const MAX_FILE_CHARS = 40_000
const ACCEPT_EXT = '.txt,.md,.markdown,.json,.csv,.yaml,.yml,.log,.html,.xml,.py,.js,.ts,.vue,.css'

function pickFile(): void {
  addPopover.value = false
  fileInput.value?.click()
}

async function onFilePicked(e: Event): Promise<void> {
  const el = e.target as HTMLInputElement
  const file = el.files?.[0]
  el.value = ''
  if (!file) return
  if (file.size > 2 * 1024 * 1024) {
    ElMessage.warning('文件过大（>2MB），请选择较小的文本文件')
    return
  }
  try {
    const text = await file.text()
    const clipped = text.length > MAX_FILE_CHARS ? `${text.slice(0, MAX_FILE_CHARS)}\n…（已截断）` : text
    blocks.value = [...blocks.value, { kind: 'file', label: file.name, text: clipped }]
    ElMessage.success(`已添加「${file.name}」`)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '读取文件失败')
  }
}

function removeBlock(idx: number): void {
  blocks.value = blocks.value.filter((_, i) => i !== idx)
}

/** 组装最终消息：上下文块 + 用户输入 */
function composeMessage(): string {
  if (!blocks.value.length) return input.value.trim()
  const parts = blocks.value.map((b) => `【${b.label}】\n${b.text}`)
  return `${parts.join('\n\n---\n\n')}\n\n---\n\n【我的问题】\n${input.value.trim()}`
}

watch(blocks, () => {
  // 上下文块变化不影响输入框；仅用于发送时组装
})

// ---------------------------------------------------------------------------
// 「工具」：子代理 / 技能（MCP 服务入口已按 v1028 移除）
// ---------------------------------------------------------------------------

interface ChipItem {
  kind: 'subagent' | 'skill'
  id: string
  label: string
}
const chips = ref<ChipItem[]>([])
const toolPopover = ref(false)
const subagentOptions = ref<{ id: string; name: string }[]>([])
const skillOptions = ref<{ id: string; name: string }[]>([])

async function loadToolOptions(): Promise<void> {
  try {
    const [subs, sks] = await Promise.all([configApi.listSubagents(), configApi.listSkills()])
    subagentOptions.value = subs.filter((s) => s.enabled).map((s) => ({ id: s.id, name: s.name }))
    // v1028：内置「工具」（read/edit/list/grep/glob/web_search）对主代理恒可用，
    // 会话级只需勾选自建写作技能
    skillOptions.value = sks
      .filter((s) => s.enabled && !s.is_builtin)
      .map((s) => ({ id: s.id, name: s.name }))
  } catch {
    /* 选项加载失败不阻断输入 */
  }
}

watch(
  () => agent.currentSession,
  async (sess) => {
    if (!sess) {
      chips.value = []
      return
    }
    await loadToolOptions()
    const items: ChipItem[] = []
    for (const id of sess.subagent_ids) {
      const found = subagentOptions.value.find((s) => s.id === id)
      if (found) items.push({ kind: 'subagent', id, label: found.name })
    }
    for (const id of sess.skill_ids) {
      const found = skillOptions.value.find((s) => s.id === id)
      if (found) items.push({ kind: 'skill', id, label: found.name })
    }
    chips.value = items
  },
)

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
      subagent_ids: chips.value.filter((c) => c.kind === 'subagent').map((c) => c.id),
      skill_ids: chips.value.filter((c) => c.kind === 'skill').map((c) => c.id),
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

/** 回车/点击发送：把上下文块合并进 input，再走正常发送或运行中三选项 */
function onSubmit(): void {
  if (!project.currentNovelId) return
  if (blocks.value.length && input.value.trim()) {
    input.value = composeMessage()
    blocks.value = []
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
</script>

<template>
  <div class="agent-panel">
    <!-- 头部：当前会话数量 + 当前小说项目 token 使用量（v1028 删除「AI助手」与 + 号） -->
    <div class="panel-head">
      <span class="head-stat">
        <svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden="true">
          <path d="M12 3C6.8 3 2.6 6.6 2.6 11c0 2.5 1.3 4.7 3.4 6.2-.2 1-.8 2.3-1.8 3.2-.2.2-.1.6.2.7 1.7.4 3.6-.2 4.9-1 1-.2 1.9-.3 2.7-.3 5.2 0 9.4-3.6 9.4-8S17.2 3 12 3z" />
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
      <el-button v-if="agent.currentSession" text size="small" @click="newSession">
        {{ t('agent.newSession') }}
      </el-button>
    </div>

    <!-- 无模型引导 -->
    <el-alert
      v-if="!hasModels"
      type="warning"
      :closable="false"
      class="model-alert"
      :title="t('agent.noModelsHint')"
    />

    <!-- 运行状态条 -->
    <div v-if="agent.running || agent.activeSubagents.length || agent.gateStage" class="status-bar">
      <span v-if="agent.running" class="dot-pulse" />
      <span v-if="agent.running">{{ t('agent.running') }}</span>
      <el-tag v-for="sa in agent.activeSubagents" :key="sa" size="small" type="info">
        {{ t('agent.subagentLane') }}: {{ sa }}
      </el-tag>
      <el-tag v-if="agent.gateStage" size="small" type="warning">
        {{ t('agent.gateStage') }}: {{ agent.gateStage }}
      </el-tag>
      <el-button v-if="agent.running" size="small" type="danger" text @click="onStop">
        {{ t('agent.stop') }}
      </el-button>
    </div>

    <!-- ============ 无小说项目 ============ -->
    <div v-if="!project.currentNovel" class="panel-body center-body">
      <div class="empty-tip">{{ t('agent.noNovel') }}</div>
    </div>

    <!-- ============ 会话记录列表 ============ -->
    <div v-else-if="!agent.currentSessionId" class="panel-body scroll-y">
      <div v-if="!sessionList.length" class="center-body">
        <div class="empty-tip">{{ t('agent.noSession') }}</div>
      </div>
      <template v-else>
        <div class="list-head">
          <span class="muted text-sm">{{ t('agent.sessionListTitle') }}</span>
          <span class="muted text-sm">{{ sessionList.length }}/{{ MAX_SESSIONS }}</span>
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
                <el-dropdown-item command="delete">{{ t('agent.deleteSession') }}</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </template>
    </div>

    <!-- ============ 会话对话 ============ -->
    <template v-else>
      <div class="chat-head">
        <el-button text size="small" class="back-btn" @click="backToList">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true">
            <path d="M15.4 4.6a1 1 0 0 0-1.4 0l-6.7 6.7a1 1 0 0 0 0 1.4l6.7 6.7a1 1 0 0 0 1.4-1.4L9.4 12l6-6a1 1 0 0 0 0-1.4z" />
          </svg>
        </el-button>
        <span class="chat-title">{{ agent.currentSession?.title }}</span>
        <el-dropdown trigger="click" placement="bottom-end">
          <el-button text size="small">⋯</el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item @click="renameSession(agent.currentSession!.id, agent.currentSession!.title)">
                {{ t('agent.renameSession') }}
              </el-dropdown-item>
              <el-dropdown-item :disabled="!agent.messages.length" @click="exportSession">
                {{ t('agent.exportSession') }}
              </el-dropdown-item>
              <el-dropdown-item divided @click="removeSession(agent.currentSession!.id)">
                {{ t('agent.deleteSession') }}
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>

      <div ref="messageHost" class="panel-body scroll-y">
        <div v-if="!displayMessages.length" class="center-body">
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
      <!-- 已添加的上下文块 -->
      <div v-if="blocks.length" class="blocks-row">
        <el-tag
          v-for="(b, i) in blocks"
          :key="`${b.kind}-${i}`"
          size="small"
          closable
          :type="b.kind === 'outline' ? 'warning' : b.kind === 'chapter' ? 'success' : 'info'"
          @close="removeBlock(i)"
        >
          {{ b.label }}
        </el-tag>
      </div>

      <!-- 已选工具（子代理/技能） -->
      <div v-if="chips.length" class="chips-row">
        <el-tag
          v-for="c in chips"
          :key="`${c.kind}-${c.id}`"
          size="small"
          closable
          :type="c.kind === 'subagent' ? 'primary' : 'success'"
          @close="removeChip(c.kind, c.id)"
        >
          {{ c.label }}
        </el-tag>
      </div>

      <!-- 队列提示 -->
      <div v-if="agent.queued.length" class="queue-hint">
        <span class="muted text-sm">队列中 {{ agent.queued.length }} 条待发送</span>
        <el-button text size="small" @click="agent.clearQueue()">清空</el-button>
      </div>

      <div class="input-box" :class="{ focused: false }">
        <el-input
          v-model="input"
          type="textarea"
          :rows="2"
          resize="none"
          :disabled="!project.currentNovelId"
          :placeholder="project.currentNovelId ? t('agent.inputPlaceholder') : t('agent.noNovel')"
          class="chat-input"
          @keydown.enter.exact.prevent="onSubmit"
        />

        <!-- 输入框内工具条：+ / 工具 / Model / 上下文使用情况 / 发送箭头 -->
        <div class="input-bar">
          <!-- +：添加大纲、章节、本地文件 -->
          <el-popover v-model:visible="addPopover" placement="top-start" :width="260" trigger="click">
            <template #reference>
              <button class="bar-btn" :disabled="!project.currentNovelId" :title="t('agent.addContext')">＋</button>
            </template>
            <div class="picker">
              <div class="picker-label">{{ t('agent.ctxOutline') }}</div>
              <el-button size="small" text :disabled="!project.currentNovelId" @click="addOutline">
                ＋ {{ t('agent.ctxOutline') }}
              </el-button>

              <div class="picker-label">{{ t('agent.ctxChapters') }}</div>
              <el-button
                size="small"
                text
                :disabled="!reader.activeChapterId"
                @click="addChapter(reader.activeChapterId!)"
              >
                ＋ {{ t('agent.ctxCurrentChapter') }}
              </el-button>
              <el-select
                size="small"
                :placeholder="t('agent.ctxChapters')"
                class="picker-select"
                @change="(v: string) => addChapter(v)"
              >
                <el-option v-for="c in chapterOptions" :key="c.id" :value="c.id" :label="c.name" />
              </el-select>

              <div class="picker-label">{{ t('agent.ctxLocalFile') }}</div>
              <el-button size="small" text @click="pickFile">＋ {{ t('agent.ctxLocalFile') }}</el-button>
              <div class="muted text-sm picker-hint">支持文本类文件（txt/md/json/csv/yaml…）</div>
            </div>
          </el-popover>

          <!-- 工具：子代理 / 技能 -->
          <el-popover v-model:visible="toolPopover" placement="top-start" :width="280" trigger="click">
            <template #reference>
              <button class="bar-btn" :disabled="!project.currentNovelId">{{ t('agent.tools') }}</button>
            </template>
            <div class="picker">
              <div class="picker-label">{{ t('agent.toolsSubagents') }}</div>
              <div class="tag-wrap">
                <el-tag
                  v-for="s in subagentOptions"
                  :key="s.id"
                  size="small"
                  class="picker-tag"
                  :type="chips.some((c) => c.kind === 'subagent' && c.id === s.id) ? 'primary' : 'info'"
                  @click="toggleChip('subagent', s.id, s.name)"
                >
                  {{ s.name }}
                </el-tag>
                <span v-if="!subagentOptions.length" class="muted text-sm">暂无可用子代理</span>
              </div>

              <div class="picker-label">{{ t('agent.toolsSkills') }}</div>
              <div class="tag-wrap">
                <el-tag
                  v-for="s in skillOptions"
                  :key="s.id"
                  size="small"
                  class="picker-tag"
                  :type="chips.some((c) => c.kind === 'skill' && c.id === s.id) ? 'success' : 'info'"
                  @click="toggleChip('skill', s.id, s.name)"
                >
                  {{ s.name }}
                </el-tag>
                <span v-if="!skillOptions.length" class="muted text-sm">暂无可用技能</span>
              </div>
            </div>
          </el-popover>

          <!-- Model：未选中或无模型 → Model Off -->
          <el-dropdown trigger="click" placement="top-start" @command="(v: string) => (modelRef = v)">
            <button class="bar-btn model-btn" :class="{ off: !selectedModel }">
              <svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor" aria-hidden="true">
                <path d="M12 2a3 3 0 0 1 3 3v1h2a2 2 0 0 1 2 2v2h1a3 3 0 0 1 0 6h-1v2a2 2 0 0 1-2 2h-2v1a3 3 0 0 1-6 0v-1H7a2 2 0 0 1-2-2v-2H4a3 3 0 0 1 0-6h1V8a2 2 0 0 1 2-2h2V5a3 3 0 0 1 3-3z" />
              </svg>
              {{ selectedModel ? selectedModel.display_name : t('agent.modelOff') }}
            </button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item v-if="!overview?.models.length" disabled>
                  {{ t('agent.noModelsHint') }}
                </el-dropdown-item>
                <el-dropdown-item
                  v-for="m in overview?.models ?? []"
                  :key="m.model_ref"
                  :command="m.model_ref"
                  :class="{ 'is-selected': m.model_ref === modelRef }"
                >
                  {{ m.display_name }}{{ m.is_default ? ' ★' : '' }}
                  <span class="muted text-sm"> · {{ Math.round(m.max_context / 1000) }}K</span>
                </el-dropdown-item>
                <el-dropdown-item divided command="">Model Off</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>

          <!-- 上下文使用情况：{已使用}/{窗口}，K 为单位 -->
          <span class="bar-ctx" :title="t('agent.contextUsage')">{{ contextLabel }}</span>

          <div class="bar-spacer" />

          <el-button
            v-if="!agent.running && displayMessages.length"
            text
            size="small"
            class="regen-btn"
            @click="onRegenerate"
          >
            {{ t('agent.regenerate') }}
          </el-button>
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

          <!-- 发送箭头；运行中变为 ↵ 并给三种选项 -->
          <el-popover
            v-if="agent.running"
            v-model:visible="sendMenu"
            placement="top-end"
            :width="190"
            trigger="click"
          >
            <template #reference>
              <button class="send-btn running" :title="t('agent.running')">
                <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <path d="M19 7H8" />
                  <path d="M11 3 7 7l4 4" />
                  <path d="M7 17h10" />
                </svg>
              </button>
            </template>
            <div class="send-menu">
              <button class="send-menu-item" @click="stopAndSend">{{ t('agent.sendAnyway') }}</button>
              <button class="send-menu-item" @click="steerSend">{{ t('agent.steer') }}</button>
              <button class="send-menu-item" @click="queueSend">{{ t('agent.queue') }}</button>
            </div>
          </el-popover>

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

    <input
      ref="fileInput"
      type="file"
      :accept="ACCEPT_EXT"
      class="hidden-file"
      @change="onFilePicked"
    />
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

.model-alert {
  margin: 8px 12px 0;
}

.status-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  border-bottom: 1px dashed var(--border);
  flex-shrink: 0;
  flex-wrap: wrap;
}

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
.list-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 2px 4px 6px;
  border-bottom: 1px solid var(--border);
}

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

.model-btn.off {
  color: var(--text-muted);
  font-style: italic;
}

.bar-ctx {
  font-size: 11px;
  color: var(--text-muted);
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

.bar-spacer {
  flex: 1;
}

.regen-btn,
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

.send-menu {
  display: flex;
  flex-direction: column;
}

.send-menu-item {
  border: none;
  background: transparent;
  text-align: left;
  padding: 7px 8px;
  font-size: 12px;
  color: var(--text);
  cursor: pointer;
  border-radius: 4px;
}

.send-menu-item:hover {
  background: var(--bg-hover);
  color: var(--el-color-primary);
}

.picker {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.picker-label {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  margin-top: 2px;
}

.picker-select {
  width: 100%;
}

.picker-hint {
  line-height: 1.5;
}

.tag-wrap {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.picker-tag {
  cursor: pointer;
}

.hidden-file {
  display: none;
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
