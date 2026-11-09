<script setup lang="ts">
/**
 * 大纲编辑器：markdown **源码编辑器**（等宽文本框）。
 *
 * 用源码编辑而非富文本，是为了保证 `# 卷 / ## 章` 层级格式不被破坏。
 * 大纲降级为「参考资料」（AI 生成正文的重要依据），保存**不再回填卷章顺序**；
 *   卷章结构由编辑模式右上角的操作条维护（插入卷/章、重命名、删除）。
 *   保存后仍调用 validate 把「名称不一致 / 序数与顺序差异 / 未登记」以警告条提示（不阻断保存）。
 * 删除正文上方的[编辑]/[预览]切换（编辑模式只有编辑状态）；
 *   [保存] 移到工具栏（icon），这里只监听 editor store 的保存请求。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { outlineApi, volumesApi } from '@/api'
import { errorMessage } from '@/api/http'
import { useEditorStore } from '@/stores/editor'
import { useProjectStore } from '@/stores/project'
import { t } from '@/i18n'
import { registerOutlineSaver } from '@/utils/unsavedGuard'
import type { OutlineValidate } from '@/types'

const emit = defineEmits<{ (e: 'saved'): void }>()

const project = useProjectStore()
const editorStore = useEditorStore()

const content = ref('')
const savedContent = ref('')
const loading = ref(false)
const saving = ref(false)
const validation = ref<OutlineValidate | null>(null)
const error = ref<string | null>(null)

const dirty = computed(() => content.value !== savedContent.value)

/** 大纲回退（与正文同机制）：连续输入停顿 800ms 后把「编辑前内容」压栈 */
const OUTLINE_UNDO_DEBOUNCE_MS = 800
let outlineUndoTimer: ReturnType<typeof setTimeout> | null = null
let outlinePendingSnapshot: string | null = null

function clearOutlineUndoTimer(): void {
  if (outlineUndoTimer) {
    clearTimeout(outlineUndoTimer)
    outlineUndoTimer = null
  }
}

function recordOutlineUndoPoint(previous: string): void {
  if (outlinePendingSnapshot === null) outlinePendingSnapshot = previous
  clearOutlineUndoTimer()
  outlineUndoTimer = setTimeout(() => {
    if (outlinePendingSnapshot !== null) {
      editorStore.pushOutlineUndoSnapshot(outlinePendingSnapshot)
      outlinePendingSnapshot = null
    }
    outlineUndoTimer = null
  }, OUTLINE_UNDO_DEBOUNCE_MS)
}

/** 输入事件：记录回退点 + 置脏 */
function onOutlineInput(): void {
  recordOutlineUndoPoint(savedContent.value)
}

/** 工具栏[回退编辑]（大纲节点）→ 还原最近一份快照 */
watch(
  () => editorStore.outlineUndoTick,
  () => {
    clearOutlineUndoTimer()
    const snapshot = editorStore.popOutlineUndoSnapshot()
    if (snapshot === null) return
    content.value = snapshot
    // 还原视为一次编辑：置脏 + 触发自动保存链路与校验
    editorStore.dirtyOutline = true
    void validateOnly()
  },
)

/**
 * ★ 把落盘函数登记给切换拦截器：大纲正文是本组件的**局部状态**，卸载即销毁，
 *   而导航入口在 `reader` / `project` store 里、拿不到组件实例。登记之后
 *   `utils/unsavedGuard` 才能在用户选「保存并切换」时真正把大纲写回服务端；
 *   卸载时必须注销，避免拦截器拿着已销毁组件的闭包去保存（那会写到错误的项目上）。
 */
onMounted(() => registerOutlineSaver(save))

onBeforeUnmount(() => {
  clearOutlineUndoTimer()
  outlinePendingSnapshot = null
  registerOutlineSaver(null)
})

/** 大纲被 AI 编辑中（锁键 `outline:{projectId}`）→ 只读 + 禁保存 */
const outlineLocked = computed(() => editorStore.isOutlineLocked(project.currentNovelId))

/** 脏状态同步到 editor store（工具栏保存 icon 据此置灰/恢复） */
watch(dirty, (v) => {
  editorStore.dirtyOutline = v
}, { immediate: true })

const warnings = computed(() => validation.value?.issues.filter((i) => i.level === 'warning') ?? [])
const errors = computed(() => validation.value?.issues.filter((i) => i.level === 'error') ?? [])

async function load(): Promise<void> {
  if (!project.currentNovelId) return
  loading.value = true
  error.value = null
  // 换项目/重载大纲 → 回退栈作废（回退只针对当前载入的大纲）
  clearOutlineUndoTimer()
  outlinePendingSnapshot = null
  editorStore.clearOutlineUndo()
  try {
    const data = await outlineApi.read(project.currentNovelId)
    content.value = data.content
    savedContent.value = data.content
    await validateOnly()
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    loading.value = false
  }
}

async function validateOnly(): Promise<void> {
  if (!project.currentNovelId) return
  try {
    validation.value = await outlineApi.validate(project.currentNovelId)
  } catch {
    validation.value = null
  }
}

/** 警告条[重编号修复]：卷/章按当前结构重新连续编号，并同步标题与大纲序数词 */
const renumbering = ref(false)

async function renumber(): Promise<void> {
  if (!project.currentNovelId) return
  renumbering.value = true
  try {
    await volumesApi.renumber(project.currentNovelId)
    ElMessage.success(t('outline.renumbered'))
    // 大纲文件已被后端同步 → 重新载入，避免本地旧内容覆盖
    const data = await outlineApi.read(project.currentNovelId)
    content.value = data.content
    savedContent.value = data.content
    await validateOnly()
    await project.loadVolumes()
    emit('saved')
  } catch (err) {
    ElMessage.error(errorMessage(err))
  } finally {
    renumbering.value = false
  }
}

/**
 * 落盘大纲。返回是否成功——`utils/unsavedGuard` 在「保存并切换」时据此决定
 * 是放行切换还是留在原地（失败时放行等于静默丢稿）。
 */
async function save(): Promise<boolean> {
  if (!project.currentNovelId) return false
  // AI 正在写大纲时拒绝用户保存（后端 PUT 同样 409 兜底）
  if (outlineLocked.value) return false
  saving.value = true
  error.value = null
  try {
    const data = await outlineApi.save(project.currentNovelId, content.value)
    savedContent.value = data.content
    // 大纲是参考资料，保存不再回填卷章顺序（只读差异提示由校验警告条承载）
    ElMessage.success(t('outline.saved'))
    await validateOnly()
    await project.loadVolumes()
    emit('saved')
    return true
  } catch (err) {
    error.value = errorMessage(err)
    ElMessage.error(error.value)
    return false
  } finally {
    saving.value = false
  }
}

function onKeydown(e: KeyboardEvent): void {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
    e.preventDefault()
    void save()
  }
}

/** 工具栏[保存] icon → editor store 保存请求 → 大纲保存 */
watch(
  () => editorStore.saveRequestTick,
  () => {
    if (dirty.value && !saving.value) void save()
  },
)

onMounted(load)
watch(() => project.currentNovelId, load)
</script>

<template>
  <div class="outline-editor-pane" @keydown="onKeydown">
    <!-- 删除「有未保存的修改」提示条（待保存状态改由工具栏保存 icon 置灰表达） -->

    <!-- 校验警告条（不阻断保存） -->
    <el-alert v-if="errors.length" type="error" :closable="false" class="mb8">
      <template #title>{{ t('outline.errors') }}（{{ errors.length }}）</template>
      <ul class="issue-list">
        <li v-for="(e, i) in errors" :key="i">
          <span v-if="e.line" class="muted">{{ t('outline.linePrefix', { line: e.line }) }}：</span>
          {{ e.message }}
        </li>
      </ul>
    </el-alert>

    <el-alert v-if="warnings.length" type="warning" :closable="false" class="mb8">
      <template #title>{{ t('outline.warnings') }}（{{ warnings.length }}）</template>
      <ul class="issue-list">
        <li v-for="(w, i) in warnings" :key="i">
          <span v-if="w.line" class="muted">{{ t('outline.linePrefix', { line: w.line }) }}：</span>
          {{ w.message }}
        </li>
      </ul>
      <el-button size="small" class="mt8" :loading="renumbering" @click="renumber">
        {{ t('outline.renumber') }}
      </el-button>
    </el-alert>

    <el-alert v-if="validation && !validation.ok && !errors.length" type="info" :closable="false" class="mb8">
      {{ t('outline.validateOk', { volumes: validation.volume_count, chapters: validation.chapter_count }) }}
    </el-alert>

    <el-alert v-if="error" :title="error" type="error" :closable="false" class="mb8" />

    <!-- 编辑模式只有编辑状态（预览面板已删除）
    AI 写大纲期间只读，避免并发写坏 -->
    <textarea
      v-model="content"
      class="outline-editor flex-fill"
      spellcheck="false"
      :readonly="outlineLocked"
      @input="onOutlineInput"
    />
  </div>
</template>

<style scoped>
.outline-editor-pane {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  padding: 12px 16px;
}

.mb8 {
  margin-bottom: 8px;
}

.mt8 {
  margin-top: 8px;
}

.issue-list {
  margin: 4px 0 0;
  padding-left: 18px;
  font-size: 12px;
  line-height: 1.7;
  max-height: 140px;
  overflow-y: auto;
}
</style>
