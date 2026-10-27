<script setup lang="ts">
/**
 * Agent 面板（M3 实装）：会话管理 + 流式消息 + 工具卡片 + 审批卡片 + 咨询卡片 + 计划。
 *
 * 数据流：agentApi.postMessage → 202 + run_id → SseClient 订阅 → store.streaming 增量渲染
 * → run.end 后 refreshMessages 落库合并。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAgentStore } from '@/stores/agent'
import { useProjectStore } from '@/stores/project'
import { useReaderStore } from '@/stores/reader'
import { agentApi, llmApi } from '@/api'
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
const modelOverride = ref<string>('')
const overview = ref<LlmOverview | null>(null)

/** 已落库消息 + 流式消息合并渲染 */
const displayMessages = computed(() => {
  const persisted = agent.messages.map((m) => ({
    key: m.id,
    role: m.role,
    content: String(m.content ?? ''),
    toolCalls: m.tool_calls ?? [],
    streaming: false,
  }))
  const live = agent.streaming.map((s) => ({
    key: s.messageId,
    role: s.messageId.startsWith('tool:') ? ('tool' as const) : ('assistant' as const),
    content: s.content,
    toolCalls: s.toolCalls,
    streaming: !s.done,
  }))
  return [...persisted, ...live]
})

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

async function loadOverview(): Promise<void> {
  try {
    overview.value = await llmApi.overview()
  } catch {
    overview.value = null
  }
}

onMounted(async () => {
  await loadOverview()
  if (project.currentNovelId) {
    await agent.loadSessions(project.currentNovelId)
  }
})

onBeforeUnmount(() => {
  agent.stopStream()
})

watch(
  () => project.currentNovelId,
  async (id) => {
    if (id) {
      await agent.loadSessions(id)
    }
  },
)

/** file.changed → 提示（工作台监听 store.fileChanged 做正文/目录树热更新） */
watch(
  () => agent.fileChanged,
  (evt) => {
    if (evt) {
      ElMessage.info(t('agent.fileChanged', { path: evt.path }))
    }
  },
)

async function send(): Promise<void> {
  const content = input.value.trim()
  if (!content || sending.value) return
  if (!agent.currentSessionId) {
    await createSession()
    if (!agent.currentSessionId) return
  }
  sending.value = true
  input.value = ''
  try {
    await agent.sendMessage(content, modelOverride.value || undefined)
  } catch (err) {
    ElMessage.error(errorMessage(err))
  } finally {
    sending.value = false
  }
}

async function createSession(): Promise<void> {
  if (!project.currentNovelId) return
  try {
    await agent.createSession(project.currentNovelId)
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
  } catch {
    /* 取消 */
  }
}

/** 导出会话（手册「三、(3) 右侧栏」：支持打断/重新生成/复制/导出会话） */
function exportSession(): void {
  const sess = agent.currentSession
  if (!sess) return
  const lines: string[] = [
    `# 会话记录 · ${sess.title}`,
    '',
    `- 项目：${sess.project_title || sess.project_id}`,
    `- 会话 ID：${sess.id}`,
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
    if (content) {
      lines.push(content, '')
    }
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

async function onStop(): Promise<void> {
  await agent.cancelRun()
  ElMessage.info(t('agent.cancelledRun'))
}

async function onRegenerate(): Promise<void> {
  await agent.regenerate(modelOverride.value || undefined)
}

/** 门禁生成当前章（G0→G3 四阶段，手册「二、(6)」） */
const activeChapterTitle = computed(() => {
  const found = reader.activeChapterId ? project.findChapter(reader.activeChapterId) : null
  return found?.chapter.title ?? ''
})

// ---- 上下文 Chips（手册「三、(3) 输入窗口」：主动添加子代理/技能/已读章节）----
interface ChipItem {
  kind: 'subagent' | 'skill' | 'chapter'
  id: string
  label: string
}
const chips = ref<ChipItem[]>([])
const chipPicker = ref(false)
const subagentOptions = ref<{ id: string; name: string }[]>([])
const skillOptions = ref<{ id: string; name: string }[]>([])

async function loadChipOptions(): Promise<void> {
  try {
    const { configApi } = await import('@/api')
    const [subs, sks] = await Promise.all([configApi.listSubagents(), configApi.listSkills()])
    subagentOptions.value = subs.filter((s) => s.enabled).map((s) => ({ id: s.id, name: s.name }))
    skillOptions.value = sks.filter((s) => s.enabled).map((s) => ({ id: s.id, name: s.name }))
  } catch {
    /* 选项加载失败不阻断输入 */
  }
}

/** 会话切换时回填已保存的上下文选择 */
watch(
  () => agent.currentSession,
  async (sess) => {
    if (!sess) {
      chips.value = []
      return
    }
    await loadChipOptions()
    const items: ChipItem[] = []
    for (const id of sess.subagent_ids) {
      const found = subagentOptions.value.find((s) => s.id === id)
      if (found) items.push({ kind: 'subagent', id, label: found.name })
    }
    for (const id of sess.skill_ids) {
      const found = skillOptions.value.find((s) => s.id === id)
      if (found) items.push({ kind: 'skill', id, label: found.name })
    }
    for (const id of sess.context_chapter_ids) {
      const found = project.findChapter(id)
      items.push({ kind: 'chapter', id, label: found?.chapter.title ?? id })
    }
    chips.value = items
  },
)

const chapterOptions = computed(() =>
  project.flatChapters.map((fc) => ({
    id: fc.chapter.id,
    name: `${fc.volume.title} · ${fc.chapter.title}`,
  })),
)

async function addChip(kind: ChipItem['kind'], id: string, label: string): Promise<void> {
  if (chips.value.some((c) => c.kind === kind && c.id === id)) return
  chips.value = [...chips.value, { kind, id, label }]
  await persistChips()
}

function removeChip(kind: ChipItem['kind'], id: string): void {
  chips.value = chips.value.filter((c) => !(c.kind === kind && c.id === id))
  void persistChips()
}

async function persistChips(): Promise<void> {
  if (!agent.currentSessionId) return
  try {
    await agentApi.updateContext(agent.currentSessionId, {
      subagent_ids: chips.value.filter((c) => c.kind === 'subagent').map((c) => c.id),
      skill_ids: chips.value.filter((c) => c.kind === 'skill').map((c) => c.id),
      context_chapter_ids: chips.value.filter((c) => c.kind === 'chapter').map((c) => c.id),
    })
  } catch (err) {
    ElMessage.error(errorMessage(err))
  }
}

function chipTagType(kind: ChipItem['kind']): 'primary' | 'success' | 'warning' {
  return kind === 'subagent' ? 'primary' : kind === 'skill' ? 'success' : 'warning'
}

function addCurrentChapter(): void {
  const id = reader.activeChapterId
  if (!id) return
  const found = project.findChapter(id)
  void addChip('chapter', id, found?.chapter.title ?? id)
}

async function generateCurrentChapter(): Promise<void> {
  const chapterId = reader.activeChapterId
  if (!chapterId) {
    ElMessage.warning('请先在目录中选择要生成的章节')
    return
  }
  if (!agent.currentSessionId) {
    await createSession()
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
  await agent.generateChapter(chapterId, activeChapterTitle.value, modelOverride.value || undefined)
}

function copyText(text: string): void {
  void navigator.clipboard.writeText(text)
  ElMessage.success(t('common.copySuccess'))
}

function formatToolCall(tc: { name: string; args?: Record<string, unknown> }): string {
  try {
    const args = JSON.stringify(tc.args ?? {})
    return `${tc.name}(${args.length > 300 ? `${args.slice(0, 300)}…` : args})`
  } catch {
    return tc.name
  }
}

const hasModels = computed(() => (overview.value?.models.length ?? 0) > 0)
</script>

<template>
  <div class="agent-panel">
    <!-- 头部：会话切换 -->
    <div class="panel-head">
      <strong>{{ t('agent.panelTitle') }}</strong>
      <el-select
        v-if="agent.sessions.length"
        :model-value="agent.currentSessionId ?? ''"
        size="small"
        class="session-select"
        @change="(v: string) => agent.selectSession(v)"
      >
        <el-option v-for="s in agent.sessions" :key="s.id" :value="s.id" :label="s.title">
          <span>{{ s.title }}</span>
          <span v-if="s.status !== 'idle'" class="session-status">· {{ s.status }}</span>
        </el-option>
      </el-select>
      <div class="head-actions">
        <el-button text size="small" @click="createSession">＋</el-button>
        <el-dropdown v-if="agent.currentSession" trigger="click">
          <el-button text size="small">⋯</el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item
                @click="renameSession(agent.currentSession!.id, agent.currentSession!.title)"
              >
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
    </div>

    <!-- 未配置模型引导 -->
    <el-alert
      v-if="!hasModels"
      type="warning"
      :closable="false"
      class="model-alert"
      title="尚未配置大模型"
      description="请先到左下角「AI 工具」页添加 Provider 与模型，然后即可开始对话。"
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

    <!-- 消息流 -->
    <div ref="messageHost" class="panel-body scroll-y">
      <div v-if="!project.currentNovel" class="muted">{{ t('sidebar.noProjects') }}</div>
      <div v-else-if="!agent.currentSessionId" class="muted">{{ t('agent.noSession') }}</div>
      <div v-else-if="!displayMessages.length" class="muted">{{ t('agent.noMessages') }}</div>

      <div
        v-for="msg in displayMessages"
        :key="msg.key"
        class="msg"
        :class="[`msg-${msg.role}`, { 'msg-streaming': msg.streaming }]"
      >
        <div class="msg-role">
          {{ msg.role === 'user' ? '我' : msg.role === 'assistant' ? 'AI' : msg.role }}
        </div>
        <div class="msg-content">
          <template v-if="msg.toolCalls?.length">
            <div v-for="(tc, i) in msg.toolCalls" :key="i" class="tool-card">
              <code>{{ formatToolCall(tc) }}</code>
            </div>
          </template>
          <span v-if="msg.content" class="msg-text">{{ msg.content }}</span>
          <span v-if="msg.streaming" class="cursor-blink">▌</span>
        </div>
        <div v-if="msg.role === 'assistant' && msg.content && !msg.streaming" class="msg-actions">
          <el-button text size="small" @click="copyText(msg.content)">
            {{ t('agent.copyMessage') }}
          </el-button>
        </div>
      </div>

      <!-- 计划待办 -->
      <PlanTodoList v-if="agent.todos.length" :todos="agent.todos" />

      <!-- 门禁状态条（M4） -->
      <GateStatusBar
        v-if="agent.gateStage"
        :stage="agent.gateStage"
        :retry-count="agent.gateRetryCount"
        :g1-skipped="agent.gateG1Skipped"
        :results="agent.gateResults"
      />

      <!-- 审批卡片 -->
      <ApprovalCard v-if="agent.pendingInterrupt" :interrupt="agent.pendingInterrupt" />

      <!-- 咨询卡片（M4 门禁） -->
      <ConsultCard v-if="agent.pendingConsult" :consult="agent.pendingConsult" />

      <!-- 用量 -->
      <div v-if="agent.lastUsage.tokens_in || agent.lastUsage.tokens_out" class="usage muted">
        {{ t('agent.usage', { in: agent.lastUsage.tokens_in, out: agent.lastUsage.tokens_out }) }}
      </div>
    </div>

    <!-- 输入区 -->
    <div class="panel-input">
      <!-- 上下文 Chips（手册「三、(3) 输入窗口」） -->
      <div class="chips-row">
        <el-popover v-model:visible="chipPicker" placement="top-start" :width="280" trigger="click">
          <template #reference>
            <el-button size="small" text>＋ 上下文</el-button>
          </template>
          <div class="chip-picker">
            <div class="picker-section">
              <div class="picker-label">子代理</div>
              <el-tag
                v-for="s in subagentOptions"
                :key="s.id"
                size="small"
                class="picker-tag"
                :type="chips.some((c) => c.kind === 'subagent' && c.id === s.id) ? 'primary' : 'info'"
                @click="addChip('subagent', s.id, s.name)"
              >{{ s.name }}</el-tag>
            </div>
            <div class="picker-section">
              <div class="picker-label">技能</div>
              <el-tag
                v-for="s in skillOptions"
                :key="s.id"
                size="small"
                class="picker-tag"
                :type="chips.some((c) => c.kind === 'skill' && c.id === s.id) ? 'success' : 'info'"
                @click="addChip('skill', s.id, s.name)"
              >{{ s.name }}</el-tag>
            </div>
            <div class="picker-section">
              <div class="picker-label">已读章节</div>
              <el-button size="small" text @click="addCurrentChapter" :disabled="!reader.activeChapterId">
                ＋ 当前章
              </el-button>
              <el-select
                size="small"
                placeholder="选择章节"
                style="width:100%;margin-top:4px"
                @change="(v: string) => { const o = chapterOptions.find(c => c.id === v); if (o) addChip('chapter', o.id, o.name) }"
              >
                <el-option
                  v-for="c in chapterOptions"
                  :key="c.id"
                  :value="c.id"
                  :label="c.name"
                  :disabled="chips.some(x => x.kind === 'chapter' && x.id === c.id)"
                />
              </el-select>
            </div>
          </div>
        </el-popover>

        <div v-if="chips.length" class="chips-list">
          <el-tag
            v-for="c in chips"
            :key="`${c.kind}-${c.id}`"
            size="small"
            closable
            :type="chipTagType(c.kind)"
            @close="removeChip(c.kind, c.id)"
          >{{ c.label }}</el-tag>
        </div>
      </div>

      <div v-if="hasModels" class="model-row">
        <el-select
          v-model="modelOverride"
          size="small"
          :placeholder="t('agent.useSessionModel')"
          clearable
          class="model-select"
        >
          <el-option
            v-for="m in overview?.models ?? []"
            :key="m.model_ref"
            :value="m.model_ref"
            :label="`${m.display_name}${m.is_default ? ' ★' : ''}`"
          />
        </el-select>
        <el-button
          v-if="!agent.running && displayMessages.length"
          text
          size="small"
          @click="onRegenerate"
        >
          {{ t('agent.regenerate') }}
        </el-button>
      </div>
      <div class="input-row">
        <el-input
          v-model="input"
          type="textarea"
          :rows="2"
          resize="none"
          :disabled="!project.currentNovelId"
          :placeholder="t('agent.inputPlaceholder')"
          @keydown.enter.exact.prevent="send"
        />
        <div class="send-col">
          <el-tooltip
            v-if="reader.activeChapterId"
            :content="`按大纲生成《${activeChapterTitle}》正文（四阶段门禁）`"
            placement="top"
          >
            <el-button
              size="small"
              type="warning"
              plain
              :disabled="agent.running"
              @click="generateCurrentChapter"
            >
              生成本章
            </el-button>
          </el-tooltip>
          <el-button
            type="primary"
            :loading="sending || agent.running"
            :disabled="!input.trim() || !project.currentNovelId"
            @click="send"
          >
            {{ t('agent.send') }}
          </el-button>
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

.panel-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

.session-select {
  flex: 1;
  min-width: 0;
}

.session-status {
  color: var(--el-color-warning);
  margin-left: 4px;
}

.head-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
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

.panel-body {
  flex: 1;
  min-height: 0;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.msg {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.msg-role {
  font-size: 11px;
  color: var(--el-text-color-secondary);
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
  padding: 6px 8px;
  font-size: 11px;
  overflow-x: auto;
  margin-bottom: 4px;
}

.tool-card code {
  font-family: var(--el-font-family-mono, monospace);
  white-space: pre;
}

.msg-actions {
  display: flex;
  gap: 4px;
}

.usage {
  font-size: 11px;
  text-align: right;
}

.panel-input {
  border-top: 1px solid var(--border);
  padding: 8px 12px 12px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.model-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.model-select {
  flex: 1;
  min-width: 0;
}

.input-row {
  display: flex;
  gap: 8px;
  align-items: flex-end;
}

.chips-row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.chips-list {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
  flex: 1;
  min-width: 0;
}

.chip-picker {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.picker-section {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.picker-label {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.picker-tag {
  cursor: pointer;
  margin-right: 4px;
  margin-bottom: 2px;
}

.send-col {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.input-row :deep(.el-textarea) {
  flex: 1;
}

.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  text-align: center;
  padding: 16px 0;
}
</style>
