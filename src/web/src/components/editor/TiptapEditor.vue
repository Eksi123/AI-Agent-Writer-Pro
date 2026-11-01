<script setup lang="ts">
/**
 * Tiptap 正文编辑器：Markdown 快捷输入 + 字数统计 + 自动保存 + 选区加入 AI 对话。
 *
 * ★乐观锁：保存携带 base_version，409 时向上抛 conflict 由 ConflictBanner 处理。
 * 自动保存不产生修订快照（autosave=true，防版本爆炸）。
 *
 * ★v1101 精简：写小说不需要富文本排版——移除 B/I/H1/H2/列表 格式组与
 *   「AI 改写/扩写/缩写/润色」四按钮（及 diff 对话框）。
 *   保留选区悬浮「AI 润色」：把选中文本作为上下文块加入模型对话，
 *   具体怎么改由用户在对话里描述。
 *
 * ★v1101 AI 编辑锁：AI 修改本章期间编辑器只读，保存（手动/自动）被 store 拒绝，
 *   工具栏旁由 WorkspaceView 展示灰色提示「AI编辑中，请稍后再试……」。
 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useEditor, EditorContent } from '@tiptap/vue-3'
import type { Editor } from '@tiptap/core'
import StarterKit from '@tiptap/starter-kit'
import Placeholder from '@tiptap/extension-placeholder'
import CharacterCount from '@tiptap/extension-character-count'
import { ElMessage } from 'element-plus'
import { useEditorStore } from '@/stores/editor'
import { useSettingsStore } from '@/stores/settings'
import { t } from '@/i18n'

const props = defineProps<{ chapterId: string }>()
const emit = defineEmits<{
  (e: 'autosaved'): void
  (e: 'conflict'): void
  /** v1031 / v1030续(6)：选区悬浮[AI润色] → 选中文本作为上下文块加入模型对话 */
  (e: 'polish-selection', payload: {
    label: string
    text: string
    chapterId: string
    chapterTitle: string
    rangeLabel: string
    before: string
    after: string
  }): void
}>()

const editorStore = useEditorStore()
const settingsStore = useSettingsStore()

// ---------------------------------------------------------------------------
// [回退编辑]（工具栏 icon，最多回退 5 次，类似 Word 的撤销）
// ---------------------------------------------------------------------------

/** 一次「连续输入」停顿多久算结束（结束后才把编辑前的正文压入回退栈） */
const UNDO_DEBOUNCE_MS = 800
let undoTimer: ReturnType<typeof setTimeout> | null = null
/** 本轮连续编辑开始前的正文（尚未压栈） */
let pendingSnapshot: string | null = null

function clearUndoTimer(): void {
  if (undoTimer) {
    clearTimeout(undoTimer)
    undoTimer = null
  }
}

/**
 * 记录一个可回退点：连续输入视为一次编辑——
 * 编辑开始时抓住「编辑前的正文」，停顿 `UNDO_DEBOUNCE_MS` 后压入回退栈（栈深由 store 限制为 5）。
 */
function recordUndoPoint(previousHtml: string): void {
  if (pendingSnapshot === null) pendingSnapshot = previousHtml
  clearUndoTimer()
  undoTimer = setTimeout(() => {
    if (pendingSnapshot !== null) {
      editorStore.pushUndoSnapshot(pendingSnapshot)
      pendingSnapshot = null
    }
    undoTimer = null
  }, UNDO_DEBOUNCE_MS)
}

/** 立即把「本轮未停顿完的编辑」压栈（点击[回退编辑]等整块变更时用） */
function flushUndoPoint(previousHtml?: string): void {
  clearUndoTimer()
  const snapshot = pendingSnapshot ?? previousHtml
  pendingSnapshot = null
  if (snapshot !== undefined && snapshot !== null) editorStore.pushUndoSnapshot(snapshot)
}

const editor = useEditor({
  content: editorStore.draft,
  extensions: [
    StarterKit.configure({ heading: { levels: [1, 2, 3] } }),
    Placeholder.configure({ placeholder: () => t('reader.emptyChapter') }),
    CharacterCount,
  ],
  editable: !editorStore.isChapterLocked(props.chapterId),
  onUpdate: ({ editor: ed }) => {
    // 本次更新前的正文（editorStore.draft 此刻还没被覆盖）
    const previous = editorStore.draft
    editorStore.draft = ed.getHTML()
    recordUndoPoint(previous)
    editorStore.markDirty()
    scheduleAutosave()
  },
  onSelectionUpdate: ({ editor: ed }) => {
    // 记录选区（含空选区收起悬浮按钮）
    const { from, to } = ed.state.selection
    savedRange.value = from === to ? null : { from, to }
    selectedText.value = from === to ? '' : ed.state.doc.textBetween(from, to, ' ')
    updatePolishButton(ed)
  },
})

// ---- 选区（供悬浮[AI润色]使用） ----
const savedRange = ref<{ from: number; to: number } | null>(null)
const selectedText = ref('')

// ---- 选区上方悬浮[AI润色]按钮（点击后选中文本加入模型对话，限定修改范围） ----
const polishBtn = ref({ top: 0, left: 0, visible: false })

/** 悬浮按钮定位到选区首行上方（相对编辑容器）；空选区 / AI 编辑中隐藏 */
function updatePolishButton(ed: Editor): void {
  const { from, to } = ed.state.selection
  if (from === to || !selectedText.value.trim() || editorStore.isChapterLocked(props.chapterId)) {
    polishBtn.value.visible = false
    return
  }
  const host = (ed.view.dom as HTMLElement).closest('.editor-host') as HTMLElement | null
  if (!host) {
    polishBtn.value.visible = false
    return
  }
  const start = ed.view.coordsAtPos(from)
  const end = ed.view.coordsAtPos(to)
  const hostRect = host.getBoundingClientRect()
  const top = Math.max(4, Math.min(start.top, end.top) - hostRect.top - 34)
  const left = Math.max(48, Math.min((start.left + end.left) / 2 - hostRect.left, hostRect.width - 48))
  polishBtn.value = { top, left, visible: true }
}

/** 编辑区滚动时同步悬浮按钮位置（选区坐标随滚动变化） */
function onEditorScroll(): void {
  if (polishBtn.value.visible && editor.value) updatePolishButton(editor.value)
}

let scrollHost: HTMLElement | null = null

onMounted(() => {
  // 滚动容器 = EditorContent 根节点（.tiptap-wrap），ProseMirror dom 的父元素
  scrollHost = editor.value?.view.dom.parentElement ?? null
  scrollHost?.addEventListener('scroll', onEditorScroll, { passive: true })
})

onBeforeUnmount(() => {
  scrollHost?.removeEventListener('scroll', onEditorScroll)
  scrollHost = null
})

/**
 * 点击悬浮[AI润色]：把选中文本（含所在行范围标签）作为上下文块加入模型对话，
 * 与「添加文件」同形制——后续提问即限定 AI 只润色这些行的内容。
 * v1030续(6)：附带章节 id + 前后文，让大模型知道这段文本在文章中的所在范围再修改。
 */
function onPolishClick(): void {
  const ed = editor.value
  if (!ed || !savedRange.value || !selectedText.value.trim()) return
  const { from, to } = savedRange.value
  // 选区落在正文的第几段（行）：遍历顶层节点定位起止行号
  let line = 0
  let lineStart = 1
  let lineEnd = 1
  ed.state.doc.forEach((node, offset) => {
    line += 1
    const s = offset
    const e = offset + node.nodeSize
    if (s <= from && from < e) lineStart = line
    if (s < to && to <= e) lineEnd = line
  })
  const title = editorStore.title || '章节正文'
  const rangeLabel = `第${lineStart}-${lineEnd}段（全章共${line}段）`
  const ctx = selectionContext()
  // 聊天框上方的润色标签要短——`章节名_节选X`（X 由 store 按同章已有选区数补齐），
  // 详细的段落范围放在消息正文里给模型看。
  emit('polish-selection', {
    label: title,
    text: selectedText.value,
    chapterId: props.chapterId,
    chapterTitle: title,
    rangeLabel,
    before: ctx.before,
    after: ctx.after,
  })
  polishBtn.value.visible = false
}

/** 取选区前后文（各 ≤600 字，帮助模型衔接） */
function selectionContext(): { before: string; after: string } {
  const ed = editor.value
  if (!ed || !savedRange.value) return { before: '', after: '' }
  const { from, to } = savedRange.value
  const before = ed.state.doc.textBetween(Math.max(0, from - 600), from, ' ')
  const after = ed.state.doc.textBetween(to, Math.min(ed.state.doc.content.size, to + 600), ' ')
  return { before, after }
}

// ★v1101：AI 编辑锁 → 编辑器只读（用户编辑过的内容也保存不出去，store 层同样拒绝）
watch(
  () => editorStore.isChapterLocked(props.chapterId),
  (locked) => {
    editor.value?.setEditable(!locked)
    if (locked) polishBtn.value.visible = false
  },
)

// 切换章节时把服务端内容灌入编辑器
watch(
  () => [props.chapterId, editorStore.baseVersion] as const,
  () => {
    const ed = editor.value
    if (!ed) return
    if (ed.getHTML() !== editorStore.draft) {
      ed.commands.setContent(editorStore.draft, false)
    }
    ed.setEditable(!editorStore.isChapterLocked(props.chapterId))
    savedRange.value = null
    selectedText.value = ''
    polishBtn.value.visible = false
    // 换章/重载后回退栈与未压入的编辑点都作废（回退只针对当前章节）
    clearUndoTimer()
    pendingSnapshot = null
  },
)

onBeforeUnmount(() => {
  editor.value?.destroy()
  clearTimer()
  clearUndoTimer()
})

// ---- 自动保存（间隔见通用设置）----
let timer: ReturnType<typeof setTimeout> | null = null

function clearTimer(): void {
  if (timer) {
    clearTimeout(timer)
    timer = null
  }
}

function scheduleAutosave(): void {
  clearTimer()
  // 通用设置[自动保存]开关关闭时不再定时保存（只保留手动保存）
  if (!settingsStore.settings.autosave_enabled) return
  const sec = settingsStore.settings.autosave_interval_sec
  if (!sec || sec <= 0) return
  timer = setTimeout(() => void doAutosave(), sec * 1000)
}

async function doAutosave(): Promise<void> {
  // ★v1101：AI 编辑中不自动保存（store.save 内部同样拒绝，双保险）
  if (!editorStore.dirty || editorStore.saving) return
  if (editorStore.isChapterLocked(props.chapterId)) return
  const okSaved = await editorStore.save(true) // autosave=true：不产生快照
  if (!okSaved && editorStore.hasConflict) emit('conflict')
  if (okSaved) emit('autosaved')
}

/** 手动保存（Ctrl+S）：产生修订快照 */
async function saveNow(): Promise<void> {
  clearTimer()
  // ★v1101：AI 编辑中手动保存同样被拒（提示由工具栏灰色文案承载）
  if (editorStore.isChapterLocked(props.chapterId)) return
  const okSaved = await editorStore.save(false)
  if (!okSaved && editorStore.hasConflict) emit('conflict')
  else if (okSaved) emit('autosaved')
}

/** v1030：工具栏[保存] icon → editor store 保存请求 → 立即保存 */
watch(
  () => editorStore.saveRequestTick,
  () => void saveNow(),
)

/**
 * 工具栏[回退编辑] icon → editor store 回退请求 → 还原最近一份快照（最多 5 次）。
 * 还原后同样标记脏状态并触发自动保存，与 Word 的「撤销」一致（撤销也是一次编辑）。
 */
watch(
  () => editorStore.undoEditTick,
  () => {
    const ed = editor.value
    if (!ed) return
    // 正在输入未停顿的一轮编辑先补压栈，避免「回退一次跳掉整轮输入」
    flushUndoPoint()
    const snapshot = editorStore.popUndoSnapshot()
    if (snapshot === null) return
    ed.commands.setContent(snapshot, false)
    editorStore.draft = snapshot
    editorStore.markDirty()
    scheduleAutosave()
    ElMessage.success(t('editor.undoDone'))
  },
)

function onKeydown(e: KeyboardEvent): void {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
    e.preventDefault()
    void saveNow()
  }
}

// 暴露给父组件调用
defineExpose({ saveNow })
</script>

<template>
  <div class="tiptap-editor" @keydown="onKeydown">
    <div class="editor-host">
      <EditorContent :editor="editor" class="tiptap-wrap novel-prose" />

      <!-- 选中文本时悬浮[AI润色]按钮；点击把选中文本加入模型对话（限定修改范围） -->
      <button
        v-if="polishBtn.visible"
        class="polish-float-btn"
        :style="{ top: `${polishBtn.top}px`, left: `${polishBtn.left}px` }"
        title="把选中文本加入 AI 对话，限定润色范围"
        @mousedown.prevent
        @click.stop="onPolishClick"
      >
        ✨ AI 润色
      </button>
    </div>
  </div>
</template>

<style scoped>
.tiptap-editor {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.tiptap-wrap {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  outline: none;
}

.editor-host {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  position: relative;
}

/* 选区上方悬浮的 AI 润色按钮 */
.polish-float-btn {
  position: absolute;
  transform: translateX(-50%);
  z-index: 40;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  border: none;
  border-radius: 14px;
  background: var(--el-color-primary);
  color: #fff;
  font-size: 12px;
  line-height: 1.4;
  cursor: pointer;
  box-shadow: var(--shadow);
  white-space: nowrap;
}

.polish-float-btn:hover {
  background: var(--el-color-primary-light-3);
}

.tiptap-wrap :deep(.ProseMirror) {
  outline: none;
  min-height: 40vh;
}

.tiptap-wrap :deep(.ProseMirror p) {
  text-indent: 2em;
}
</style>
