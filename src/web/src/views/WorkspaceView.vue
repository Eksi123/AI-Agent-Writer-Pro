<script setup lang="ts">
/**
 * 工作台（三栏主界面）：
 *   左栏 = 项目列表（+ 设置 / AI 设置入口）
 *   中栏 = 阅读模式（正文/大纲） / 编辑模式（大纲节点 → 大纲编辑器；章节节点 → 正文编辑器）
 *          ★v1028：目录不放在最上面；无小说项目时不展示目录，有项目时浮在阅读区域左上角，可点击展开收起
 *          ★v1029：编辑模式删除【正文】【卷章】页签（页签按钮被目录浮层遮挡且卷章管理已由目录承载），
 *                  改为「大纲节点 → OutlineEditor / 章节节点 → TiptapEditor」；
 *                  主页面右上方新增卷章结构操作条（插入卷/章、重命名、删除），不与目录重叠
 *   右栏 = Agent 面板（可收起，可向左拉伸）
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import ProjectList from '@/components/sidebar/ProjectList.vue'
import ReaderToolbar from '@/components/reader/ReaderToolbar.vue'
import TocDrawer from '@/components/reader/TocDrawer.vue'
import ChapterReader from '@/components/reader/ChapterReader.vue'
import OutlineReader from '@/components/reader/OutlineReader.vue'
import TiptapEditor from '@/components/editor/TiptapEditor.vue'
import WordCountBar from '@/components/editor/WordCountBar.vue'
import ConflictBanner from '@/components/editor/ConflictBanner.vue'
import EditorActionBar from '@/components/editor/EditorActionBar.vue'
import OutlineEditor from '@/components/editor/OutlineEditor.vue'
import AgentPanel from '@/components/agent/AgentPanel.vue'
import ExportDialog from '@/components/common/ExportDialog.vue'
import { errorMessage } from '@/api/http'
import { useAgentStore } from '@/stores/agent'
import { useEditorStore } from '@/stores/editor'
import { useProjectStore } from '@/stores/project'
import { useReaderStore } from '@/stores/reader'
import { useSettingsStore } from '@/stores/settings'
import { t } from '@/i18n'
import { recordRecentChapter } from '@/utils/recent'
import type { Novel } from '@/types'

const router = useRouter()
const project = useProjectStore()
const reader = useReaderStore()
const editorStore = useEditorStore()
const settingsStore = useSettingsStore()
const agentStore = useAgentStore()

const agentOpen = ref(false)
const exportVisible = ref(false)
const exportNovel = ref<Novel | null>(null)

/** v1029：右栏 AI 会话框宽度（px），可向左拉伸；默认沿用 --agent-width，上限 = 主页面宽度的 1/3 */
const AGENT_DEFAULT_WIDTH = 340
const agentWidth = ref(AGENT_DEFAULT_WIDTH)
const layoutRef = ref<HTMLElement | null>(null)

const hasNovel = computed(() => !!project.currentNovelId)

/**
 * 编辑模式下：未打开具体章节时走 OutlineEditor（与阅读模式「无章即看大纲」一致），
 * 打开章节后走正文编辑器。
 */
const editingOutline = computed(
  () => reader.mode === 'edit' && (reader.activeOutline || !reader.activeChapterId),
)

/**
 * v1029：AI 会话框可向左拉伸，**最宽到主页面的 1/3**。
 * 「主页面」= 去掉左栏后的中间工作区宽度（中栏阅读/编辑 + 右栏会话框）。
 * 阅读页与编辑页用 flex:1 + min-width:0，随会话框变宽自动收窄（自适应）。
 */
function maxAgentWidth(): number {
  const host = layoutRef.value
  if (!host) return AGENT_DEFAULT_WIDTH
  const sidebar = host.querySelector('.sidebar') as HTMLElement | null
  const available = host.clientWidth - (sidebar?.offsetWidth ?? 0)
  return Math.max(AGENT_DEFAULT_WIDTH, Math.floor(available / 3))
}

let dragging = false

function onDragStart(e: MouseEvent): void {
  dragging = true
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
  e.preventDefault()
}

function onDragMove(e: MouseEvent): void {
  if (!dragging || !layoutRef.value) return
  const rect = layoutRef.value.getBoundingClientRect()
  const next = rect.right - e.clientX
  // 默认宽度即下限：需求是「默认这个宽度，可向左（变宽）拉伸」
  agentWidth.value = Math.min(Math.max(next, AGENT_DEFAULT_WIDTH), maxAgentWidth())
}

function onDragEnd(): void {
  if (!dragging) return
  dragging = false
  document.body.style.cursor = ''
  document.body.style.userSelect = ''
}

onMounted(async () => {
  await settingsStore.load()
  window.addEventListener('mousemove', onDragMove)
  window.addEventListener('mouseup', onDragEnd)
  // v1028：项目列表加载与「默认选中上次打开的项目」由 ProjectList 统一负责（含 localStorage 记忆）
})

onBeforeUnmount(() => {
  window.removeEventListener('mousemove', onDragMove)
  window.removeEventListener('mouseup', onDragEnd)
})

// 目录里点章：阅读模式直接渲染，编辑模式同时载入正文到编辑器
watch(
  () => reader.activeChapterId,
  async (id) => {
    if (!id) return
    // v1029：记录「最近打开的小说章节」（供添加文件页展示，最多 10 条）
    const found = project.findChapter(id)
    if (found && project.currentNovel) {
      recordRecentChapter({
        novelId: project.currentNovel.id,
        novelTitle: project.currentNovel.title,
        volumeId: found.volume.id,
        volumeTitle: found.volume.title,
        chapterId: found.chapter.id,
        chapterTitle: found.chapter.title,
      })
    }
    try {
      await editorStore.openChapter(id)
    } catch (err) {
      ElMessage.error(errorMessage(err))
    }
  },
)

function onExport(novel: Novel): void {
  exportNovel.value = novel
  exportVisible.value = true
}

async function onOutlineSaved(): Promise<void> {
  // v1029：大纲不再回填卷章顺序，仅需刷新项目统计
  await project.loadVolumes()
}

/** Agent file.changed 热更新（手册「三、(3) 右侧栏」）：
 * - 目录树刷新（增删改章后）；
 * - 若改动的是正在编辑的章：无本地未保存修改 → 静默重载；有未保存修改 → 提示用户选择。 */
watch(
  () => agentStore.fileChanged,
  async (evt) => {
    if (!evt) return
    agentStore.clearFileChanged()
    await project.loadVolumes()

    const openedId = editorStore.chapterId
    if (evt.chapterId && evt.chapterId === openedId) {
      if (!editorStore.dirty) {
        await editorStore.openChapter(openedId)
      } else {
        try {
          await ElMessageBox.confirm(
            `AI 已修改本章正文（${evt.kind}）。你本地有未保存的修改，是否放弃本地修改并载入 AI 版本？`,
            '正文已被 AI 更新',
            { confirmButtonText: '载入 AI 版本', cancelButtonText: '保留本地修改', type: 'warning' },
          )
          await editorStore.openChapter(openedId)
        } catch {
          /* 用户选择保留本地修改；保存时会触发乐观锁 409 冲突流程 */
        }
      }
    }
  },
)

/** 离开页面前把未保存的正文存一次（避免丢稿） */
window.addEventListener('beforeunload', (e) => {
  if (editorStore.dirty) {
    e.preventDefault()
    e.returnValue = ''
  }
})
</script>

<template>
  <div ref="layoutRef" class="app-layout">
    <!-- 左栏 -->
    <aside class="sidebar">
      <ProjectList
        @export="onExport"
        @open-settings="router.push('/settings')"
        @open-ai-tools="router.push('/ai-tools')"
      />
    </aside>

    <!-- 中栏 -->
    <main class="main-panel">
      <ReaderToolbar :agent-open="agentOpen" @toggle-agent="agentOpen = !agentOpen" />

      <!-- v1028：没有小说项目时不展示目录，提示文案改为「点击「新建小说」开始创作」 -->
      <div v-if="!hasNovel" class="empty-state">
        <el-empty :description="t('sidebar.noProjects')" />
      </div>

      <div v-else class="main-body">
        <!-- v1029：编辑模式顶部留出「目录」浮层的高度，避免遮挡编辑器工具条按钮 -->
        <div class="content-area flex-fill">
          <!-- v1030：工具栏（原「目录」入口那一行）＝ 目录 + 卷章结构操作 + 保存 + 自动保存标记。
               阅读模式保持 v1028 的悬浮样式；编辑模式为独占一行，避免遮挡编辑器工具条 -->
          <div class="float-toolbar" :class="{ 'in-flow': reader.mode === 'edit' }">
            <TocDrawer />

            <template v-if="reader.mode === 'edit'">
              <span class="tb-sep" />
              <EditorActionBar v-if="hasNovel" />
              <span class="tb-sep" />

              <!-- 保存（icon） -->
              <el-tooltip :content="t('common.save')" placement="bottom">
                <button
                  class="tb-icon-btn"
                  :disabled="editorStore.saving"
                  @click="editorStore.requestSave()"
                >
                  <svg viewBox="0 0 24 24" width="17" height="17" fill="currentColor" aria-hidden="true">
                    <path d="M5 3h11l3 3v15H5V3zm2 2v4h8V5H7zm3 8a3 3 0 1 0 0 6 3 3 0 0 0 0-6z" />
                  </svg>
                </button>
              </el-tooltip>

              <!-- 自动保存标记：开启=绿色，未开启=灰色 -->
              <span class="autosave-badge" :class="{ on: settingsStore.settings.autosave_enabled }">
                {{ t('settings.autosaveEnabled') }}
              </span>
            </template>
          </div>

          <!-- 阅读模式 -->
          <template v-if="reader.mode === 'read'">
            <OutlineReader v-if="reader.activeOutline || !reader.activeChapterId" />
            <ChapterReader v-else :chapter-id="reader.activeChapterId" />
          </template>

          <!-- 编辑模式（v1029：无页签，按当前节点直接切换编辑器） -->
          <template v-else>
            <OutlineEditor v-if="editingOutline" @saved="onOutlineSaved" />

            <template v-else-if="editorStore.chapterId">
              <ConflictBanner />
              <div class="editor-host">
                <TiptapEditor :chapter-id="editorStore.chapterId" />
                <WordCountBar />
              </div>
            </template>
          </template>
        </div>
      </div>
    </main>

    <!-- 右栏：Agent 面板（可收起；v1029 可向左拉伸，最多到主页面的 1/3） -->
    <template v-if="agentOpen">
      <div class="resize-handle" :title="t('agent.resizeHint')" @mousedown="onDragStart" />
      <aside class="agent-aside" :style="{ width: `${agentWidth}px` }">
        <AgentPanel />
      </aside>
    </template>

    <ExportDialog v-model="exportVisible" :novel="exportNovel" />
  </div>
</template>

<style scoped>
.main-body {
  display: flex;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.content-area {
  display: flex;
  flex-direction: column;
  min-width: 0;
  overflow: hidden;
  /* v1028：目录悬浮层的定位上下文 */
  position: relative;
}

/* v1030：工具栏（「目录」所在那一行）：
   目录 + 插入卷/章 + 重命名 + 删除 + 保存 + 自动保存标记。
   阅读模式悬浮在左上角（v1028 行为）；编辑模式独占一行（in-flow）。 */
.float-toolbar {
  position: absolute;
  top: 8px;
  left: 12px;
  z-index: 30;
  display: flex;
  align-items: center;
  gap: 4px;
}

.float-toolbar.in-flow {
  position: static;
  flex-shrink: 0;
  padding: 5px 12px;
  border-bottom: 1px solid var(--border);
  background: var(--bg-panel);
}

.tb-sep {
  width: 1px;
  height: 16px;
  margin: 0 5px;
  background: var(--border);
}

.tb-icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
  transition: color 0.15s, background 0.15s, border-color 0.15s;
}

.tb-icon-btn:hover:not(:disabled) {
  color: var(--el-color-primary);
  background: color-mix(in srgb, var(--el-color-primary) 12%, transparent);
  border-color: color-mix(in srgb, var(--el-color-primary) 30%, transparent);
}

.tb-icon-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

/* 自动保存标记：文字 + 外框；开启=绿色，未开启=灰色 */
.autosave-badge {
  display: inline-flex;
  align-items: center;
  height: 24px;
  padding: 0 9px;
  border-radius: 12px;
  border: 1px solid var(--border);
  background: var(--bg-panel);
  color: var(--text-muted);
  font-size: 12px;
  line-height: 1;
  white-space: nowrap;
}

.autosave-badge.on {
  color: var(--el-color-success);
  border-color: color-mix(in srgb, var(--el-color-success) 45%, transparent);
  background: color-mix(in srgb, var(--el-color-success) 10%, var(--bg-panel));
}

.empty-state {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}

.editor-host {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

/* v1029：右栏拉伸把手 */
.resize-handle {
  width: 4px;
  flex-shrink: 0;
  cursor: col-resize;
  background: transparent;
  transition: background 0.15s;
}

.resize-handle:hover {
  background: color-mix(in srgb, var(--el-color-primary) 45%, transparent);
}

/* v1029：宽度由拖拽把手控制（内联 style），默认 340px、最多拉到主页面的 1/3 */
.agent-aside {
  flex-shrink: 0;
  min-width: 300px;  display: flex;
  flex-direction: column;
  border-left: 1px solid var(--border);
  background: var(--bg-panel);
  overflow: hidden;
}
</style>
