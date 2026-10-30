<script setup lang="ts">
/**
 * Tiptap 正文编辑器：Markdown 快捷输入 + 字数统计 + 自动保存 + 选区 AI 改写。
 *
 * ★乐观锁：保存携带 base_version，409 时向上抛 conflict 由 ConflictBanner 处理。
 * 自动保存不产生修订快照（autosave=true，防版本爆炸）。
 *
 * ★选区 AI 改写（手册「三、(3)【正文】」）：选中文字后「改写/扩写/缩写/润色」四按钮启用，
 * 调 POST /chapters/{id}/rewrite 取结果 → RewriteDiffDialog 做 diff 预览 →
 * 接受后替换选区并打 AI 来源标记（AiSource mark，可回滚），放弃则不改动。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useEditor, EditorContent } from '@tiptap/vue-3'
import StarterKit from '@tiptap/starter-kit'
import Placeholder from '@tiptap/extension-placeholder'
import CharacterCount from '@tiptap/extension-character-count'
import { ElMessage } from 'element-plus'
import { contentApi } from '@/api'
import { errorMessage } from '@/api/http'
import { useEditorStore } from '@/stores/editor'
import { useSettingsStore } from '@/stores/settings'
import { t } from '@/i18n'
import type { RewriteResult } from '@/types'
import { AiSource } from './aiSourceMark'
import RewriteDiffDialog from './RewriteDiffDialog.vue'

const props = defineProps<{ chapterId: string }>()
const emit = defineEmits<{
  (e: 'autosaved'): void
  (e: 'conflict'): void
}>()

const editorStore = useEditorStore()
const settingsStore = useSettingsStore()

const editor = useEditor({
  content: editorStore.draft,
  extensions: [
    StarterKit.configure({ heading: { levels: [1, 2, 3] } }),
    Placeholder.configure({ placeholder: () => t('reader.emptyChapter') }),
    CharacterCount,
    AiSource,
  ],
  onUpdate: ({ editor: ed }) => {
    editorStore.draft = ed.getHTML()
    editorStore.markDirty()
    scheduleAutosave()
  },
  onSelectionUpdate: ({ editor: ed }) => {
    // 记录选区（含空选区收起 AI 按钮）
    const { from, to } = ed.state.selection
    savedRange.value = from === to ? null : { from, to }
    selectedText.value = from === to ? '' : ed.state.doc.textBetween(from, to, ' ')
  },
})

// ---- 选区 AI 改写 ----
const savedRange = ref<{ from: number; to: number } | null>(null)
const selectedText = ref('')
const aiLoading = ref(false)
const diffVisible = ref(false)
const rewriteResult = ref<RewriteResult | null>(null)

const hasSelection = computed(() => !!selectedText.value.trim() && !editorStore.saving)

/** 取选区前后文（各 ≤600 字，帮助模型衔接） */
function selectionContext(): { before: string; after: string } {
  const ed = editor.value
  if (!ed || !savedRange.value) return { before: '', after: '' }
  const { from, to } = savedRange.value
  const before = ed.state.doc.textBetween(Math.max(0, from - 600), from, ' ')
  const after = ed.state.doc.textBetween(to, Math.min(ed.state.doc.content.size, to + 600), ' ')
  return { before, after }
}

async function runRewrite(mode: 'rewrite' | 'expand' | 'condense' | 'polish'): Promise<void> {
  const ed = editor.value
  if (!ed || !savedRange.value || !selectedText.value.trim()) return
  aiLoading.value = true
  try {
    const ctx = selectionContext()
    const result = await contentApi.rewrite(props.chapterId, {
      selected_text: selectedText.value,
      mode,
      context_before: ctx.before,
      context_after: ctx.after,
    })
    rewriteResult.value = result
    diffVisible.value = true
  } catch (err) {
    ElMessage.error(errorMessage(err))
  } finally {
    aiLoading.value = false
  }
}

/** 接受改写：替换选区 + 打 AI 来源标记（可回滚） */
function acceptRewrite(rewritten: string): void {
  const ed = editor.value
  if (!ed || !savedRange.value) return
  const mode = rewriteResult.value?.mode ?? 'rewrite'
  const original = rewriteResult.value?.original ?? ''
  ed
    .chain()
    .focus()
    .setTextSelection(savedRange.value)
    .insertContent(rewritten)
    .run()
  // 对刚插入的内容打标记：插入后选区即为新内容范围
  const { from, to } = ed.state.selection
  if (to > from) {
    ed.chain().setTextSelection({ from, to }).setAiSource({ mode, original }).run()
  }
  editorStore.draft = ed.getHTML()
  editorStore.markDirty()
  scheduleAutosave()
  diffVisible.value = false
  rewriteResult.value = null
  savedRange.value = null
  selectedText.value = ''
  ElMessage.success('已应用 AI 改写（标记处可点击回滚）')
}

function discardRewrite(): void {
  rewriteResult.value = null
}

/** 点击 AI 标记 → 回滚为原文 */
function onEditorClick(event: MouseEvent): void {
  const target = (event.target as HTMLElement).closest('span[data-ai-source]')
  if (!target) return
  const ed = editor.value
  if (!ed) return
  const pos = ed.view.posAtDOM(target, 0)
  const { state } = ed
  const $pos = state.doc.resolve(pos)
  const marks = $pos.marks()
  const aiMark = marks.find((m) => m.type.name === 'aiSource')
  if (!aiMark) return
  event.preventDefault()
  const original = (aiMark.attrs.original as string) ?? ''
  // 选中整个标记范围并替换回原文：向后扫描同一 mark 找结束位置
  let to = pos
  state.doc.nodesBetween(pos, state.doc.content.size, (node, nodePos) => {
    if (node.marks.some((m) => m.type.name === 'aiSource')) {
      to = nodePos + node.nodeSize
      return true
    }
    return false
  })
  ed.chain()
    .focus()
    .setTextSelection({ from: pos, to })
    .insertContent(original || '')
    .run()
  editorStore.draft = ed.getHTML()
  editorStore.markDirty()
  scheduleAutosave()
  ElMessage.success('已回滚该处 AI 改写')
}

// 切换章节时把服务端内容灌入编辑器
watch(
  () => [props.chapterId, editorStore.baseVersion] as const,
  () => {
    const ed = editor.value
    if (!ed) return
    if (ed.getHTML() !== editorStore.draft) {
      ed.commands.setContent(editorStore.draft, false)
    }
    savedRange.value = null
    selectedText.value = ''
  },
)

onBeforeUnmount(() => {
  editor.value?.destroy()
  clearTimer()
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
  // v1030：通用设置[自动保存]开关关闭时不再定时保存（只保留手动保存）
  if (!settingsStore.settings.autosave_enabled) return
  const sec = settingsStore.settings.autosave_interval_sec
  if (!sec || sec <= 0) return
  timer = setTimeout(() => void doAutosave(), sec * 1000)
}

async function doAutosave(): Promise<void> {
  if (!editorStore.dirty || editorStore.saving) return
  const okSaved = await editorStore.save(true) // autosave=true：不产生快照
  if (!okSaved && editorStore.hasConflict) emit('conflict')
  if (okSaved) emit('autosaved')
}

/** 手动保存（Ctrl+S）：产生修订快照 */
async function saveNow(): Promise<void> {
  clearTimer()
  const okSaved = await editorStore.save(false)
  if (!okSaved && editorStore.hasConflict) emit('conflict')
  else if (okSaved) emit('autosaved')
}

/** v1030：工具栏[保存] icon → editor store 保存请求 → 立即保存 */
watch(
  () => editorStore.saveRequestTick,
  () => void saveNow(),
)

/** v1029：标记定稿 / 取消定稿（原【卷章】页签的入口收敛到此） */
async function toggleFinalize(): Promise<void> {
  try {
    await editorStore.setStatus(editorStore.status === 'finalized' ? 'draft' : 'finalized')
  } catch (err) {
    ElMessage.error(errorMessage(err))
  }
}

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
    <div v-if="editor" class="editor-toolbar row">
      <el-button-group size="small">
        <el-button
          :type="editor.isActive('bold') ? 'primary' : 'default'"
          @click="editor.chain().focus().toggleBold().run()"
        >B</el-button>
        <el-button
          :type="editor.isActive('italic') ? 'primary' : 'default'"
          @click="editor.chain().focus().toggleItalic().run()"
        ><i>I</i></el-button>
        <el-button @click="editor.chain().focus().toggleHeading({ level: 1 }).run()">H1</el-button>
        <el-button @click="editor.chain().focus().toggleHeading({ level: 2 }).run()">H2</el-button>
        <el-button @click="editor.chain().focus().toggleBulletList().run()">• 列表</el-button>
      </el-button-group>

      <el-divider direction="vertical" />

      <!-- 选区 AI 改写（选中文字后启用） -->
      <el-tooltip
        :content="hasSelection ? '对选中文字执行 AI 处理' : '先选中一段文字，再选择改写方式'"
        placement="bottom"
      >
        <el-button-group size="small">
          <el-button :disabled="!hasSelection" :loading="aiLoading" @click="runRewrite('rewrite')">
            {{ t('editor.aiRewrite') }}
          </el-button>
          <el-button :disabled="!hasSelection" :loading="aiLoading" @click="runRewrite('expand')">
            {{ t('editor.aiExpand') }}
          </el-button>
          <el-button :disabled="!hasSelection" :loading="aiLoading" @click="runRewrite('condense')">
            {{ t('editor.aiCondense') }}
          </el-button>
          <el-button :disabled="!hasSelection" :loading="aiLoading" @click="runRewrite('polish')">
            {{ t('editor.aiPolish') }}
          </el-button>
        </el-button-group>
      </el-tooltip>
      <span v-if="hasSelection" class="sel-hint muted text-sm">
        已选 {{ selectedText.length }} 字
      </span>

      <div class="flex-fill" />
      <!-- v1029：卷章页签移除后，定稿入口收敛到正文工具栏 -->
      <el-button
        size="small"
        :type="editorStore.status === 'finalized' ? 'success' : 'default'"
        :plain="editorStore.status !== 'finalized'"
        @click="toggleFinalize"
      >
        {{ editorStore.status === 'finalized' ? t('editor.unfinalize') : t('editor.finalize') }}
      </el-button>
      <!-- v1030：[保存] 已移到「目录」所在工具栏（icon），正文工具栏不再重复 -->
    </div>

    <div class="editor-host" @click="onEditorClick">
      <EditorContent :editor="editor" class="tiptap-wrap novel-prose" />
    </div>

    <!-- AI 改写 diff 对话框 -->
    <RewriteDiffDialog
      v-model:visible="diffVisible"
      :result="rewriteResult"
      :loading="aiLoading"
      @accept="acceptRewrite"
      @discard="discardRewrite"
    />
  </div>
</template>

<style scoped>
.tiptap-editor {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.editor-toolbar {
  padding: 8px 16px;
  border-bottom: 1px solid var(--border);
  flex-wrap: wrap;
  flex-shrink: 0;
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
}

.sel-hint {
  align-self: center;
}

/* AI 来源标记：底色 + 左侧色条，可点击回滚 */
.tiptap-wrap :deep(span[data-ai-source]) {
  background: var(--el-color-primary-light-9);
  border-bottom: 2px solid var(--el-color-primary-light-3);
  cursor: pointer;
  border-radius: 2px;
}

.tiptap-wrap :deep(span[data-ai-source]:hover) {
  background: var(--el-color-primary-light-8);
}

.tiptap-wrap :deep(.ProseMirror) {
  outline: none;
  min-height: 40vh;
}

.tiptap-wrap :deep(.ProseMirror p) {
  text-indent: 2em;
}
</style>
