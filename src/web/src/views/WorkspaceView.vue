<script setup lang="ts">
/**
 * 工作台（三栏主界面）：
 *   左栏 = 项目列表（+ 设置 / AI 设置入口）
 *   中栏 = 阅读模式（正文/大纲） / 编辑模式（【正文】【卷章】两页签，大纲经目录「大纲」节点编辑）
 *          ★v1028：目录不放在最上面；无小说项目时不展示目录，有项目时浮在阅读区域左上角，可点击展开收起
 *   右栏 = Agent 面板（可收起）
 */
import { computed, onMounted, ref, watch } from 'vue'
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
import VolumeChapterTree from '@/components/editor/VolumeChapterTree.vue'
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

const hasNovel = computed(() => !!project.currentNovelId)

/** 编辑模式下：大纲节点走 OutlineEditor，其余走正文/卷章页签 */
const editingOutline = computed(() => reader.mode === 'edit' && reader.activeOutline)

onMounted(async () => {
  await settingsStore.load()
  // v1028：项目列表加载与「默认选中上次打开的项目」由 ProjectList 统一负责（含 localStorage 记忆）
})

// 目录里点章：阅读模式直接渲染，编辑模式同时载入正文到编辑器
watch(
  () => reader.activeChapterId,
  async (id) => {
    if (!id) return
    reader.editTab = 'content'
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

function gotoOutlineNode(): void {
  reader.openOutline()
}

/** 卷章树里点章 → 编辑模式切到【正文】页签 */
function onOpenChapter(id: string): void {
  reader.editTab = 'content'
  reader.openChapter(id)
}

async function onOutlineSaved(): Promise<void> {
  // 大纲保存会回填卷章顺序，卷章树需刷新（已由 OutlineEditor 内部 loadVolumes 完成）
  await project.loadVolumes()
}

/** Agent file.changed 热更新（手册「三、(3) 右侧栏」）：
 * - 目录树刷新（增删改章 / 大纲顺序回填后）；
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
  <div class="app-layout">
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
        <div class="content-area flex-fill">
          <!-- v1028：目录浮在阅读区域左上角，点击可展开收起 -->
          <TocDrawer />

          <!-- 阅读模式 -->
          <template v-if="reader.mode === 'read'">
            <OutlineReader v-if="reader.activeOutline || !reader.activeChapterId" />
            <ChapterReader v-else :chapter-id="reader.activeChapterId" />
          </template>

          <!-- 编辑模式 -->
          <template v-else>
            <!-- 大纲编辑归一到目录「大纲」节点，不单设页签 -->
            <OutlineEditor v-if="editingOutline" @saved="onOutlineSaved" />

            <template v-else>
              <el-tabs v-model="reader.editTab" class="edit-tabs">
                <el-tab-pane :label="t('editor.tabContent')" name="content">
                  <ConflictBanner />
                  <div v-if="editorStore.chapterId" class="editor-host">
                    <TiptapEditor :chapter-id="editorStore.chapterId" />
                    <WordCountBar />
                  </div>
                  <el-empty v-else :description="t('common.empty')" :image-size="70" />
                </el-tab-pane>

                <el-tab-pane :label="t('editor.tabVolumes')" name="volumes">
                  <VolumeChapterTree @goto-outline="gotoOutlineNode" @open-chapter="onOpenChapter" />
                </el-tab-pane>
              </el-tabs>
            </template>
          </template>
        </div>
      </div>
    </main>

    <!-- 右栏：Agent 面板（可收起） -->
    <aside v-if="agentOpen" class="agent-panel">
      <AgentPanel />
    </aside>

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

.empty-state {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}

.edit-tabs {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.edit-tabs :deep(.el-tabs__header) {
  margin: 0;
  padding: 0 16px;
  flex-shrink: 0;
}

.edit-tabs :deep(.el-tabs__content) {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.edit-tabs :deep(.el-tab-pane) {
  height: 100%;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.editor-host {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
</style>
