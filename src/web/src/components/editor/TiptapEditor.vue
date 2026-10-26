<script setup lang="ts">
/**
 * Tiptap 正文编辑器：Markdown 快捷输入 + 字数统计 + 自动保存。
 *
 * ★乐观锁：保存携带 base_version，409 时向上抛 conflict 由 ConflictBanner 处理。
 * 自动保存不产生修订快照（autosave=true，防版本爆炸）。
 *
 * 注：选中浮层「AI 改写/扩写/缩写/润色」需 Agent 后端（M3），此处保留工具位并置灰。
 */
import { onBeforeUnmount, watch } from 'vue'
import { useEditor, EditorContent } from '@tiptap/vue-3'
import StarterKit from '@tiptap/starter-kit'
import Placeholder from '@tiptap/extension-placeholder'
import CharacterCount from '@tiptap/extension-character-count'
import { useEditorStore } from '@/stores/editor'
import { useSettingsStore } from '@/stores/settings'
import { t } from '@/i18n'

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
  ],
  onUpdate: ({ editor: ed }) => {
    editorStore.draft = ed.getHTML()
    editorStore.markDirty()
    scheduleAutosave()
  },
})

// 切换章节时把服务端内容灌入编辑器
watch(
  () => [props.chapterId, editorStore.baseVersion] as const,
  () => {
    const ed = editor.value
    if (!ed) return
    if (ed.getHTML() !== editorStore.draft) {
      ed.commands.setContent(editorStore.draft, false)
    }
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

      <!-- AI 选中浮层入口：M3 接入 Agent 后启用 -->
      <el-tooltip :content="t('aiTools.comingM3')" placement="bottom">
        <el-button-group size="small">
          <el-button disabled>{{ t('editor.aiRewrite') }}</el-button>
          <el-button disabled>{{ t('editor.aiExpand') }}</el-button>
          <el-button disabled>{{ t('editor.aiCondense') }}</el-button>
          <el-button disabled>{{ t('editor.aiPolish') }}</el-button>
        </el-button-group>
      </el-tooltip>

      <div class="flex-fill" />
      <el-button size="small" type="primary" :loading="editorStore.saving" @click="saveNow">
        {{ t('common.save') }}
      </el-button>
    </div>

    <EditorContent :editor="editor" class="tiptap-wrap novel-prose" />
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

.tiptap-wrap :deep(.ProseMirror) {
  outline: none;
  min-height: 40vh;
}

.tiptap-wrap :deep(.ProseMirror p) {
  text-indent: 2em;
}
</style>
