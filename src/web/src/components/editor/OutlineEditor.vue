<script setup lang="ts">
/**
 * 大纲编辑器：markdown **源码编辑器**（等宽文本框）+ 编辑/预览切换。
 *
 * 用源码编辑而非富文本，是为了保证 `# 卷 / ## 章` 层级格式不被破坏。
 * ★v1029：大纲降级为「参考资料」（AI 生成正文的重要依据），保存**不再回填卷章顺序**；
 *   卷章结构由编辑模式右上角的操作条维护（插入卷/章、重命名、删除）。
 *   保存后仍调用 validate 把「名称不一致 / 序数与顺序差异 / 未登记」以警告条提示（不阻断保存）。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'
import { outlineApi } from '@/api'
import { errorMessage } from '@/api/http'
import { useProjectStore } from '@/stores/project'
import { t } from '@/i18n'
import type { OutlineValidate } from '@/types'

const emit = defineEmits<{ (e: 'saved'): void }>()

const project = useProjectStore()
const md = new MarkdownIt({ html: false, linkify: true })

const content = ref('')
const savedContent = ref('')
const previewMode = ref(false)
const loading = ref(false)
const saving = ref(false)
const validation = ref<OutlineValidate | null>(null)
const error = ref<string | null>(null)

const dirty = computed(() => content.value !== savedContent.value)
const previewHtml = computed(() => DOMPurify.sanitize(md.render(content.value || '')))

const warnings = computed(() => validation.value?.issues.filter((i) => i.level === 'warning') ?? [])
const errors = computed(() => validation.value?.issues.filter((i) => i.level === 'error') ?? [])

async function load(): Promise<void> {
  if (!project.currentNovelId) return
  loading.value = true
  error.value = null
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

async function save(): Promise<void> {
  if (!project.currentNovelId) return
  saving.value = true
  error.value = null
  try {
    const data = await outlineApi.save(project.currentNovelId, content.value)
    savedContent.value = data.content
    // v1029：大纲是参考资料，保存不再回填卷章顺序（只读差异提示由校验警告条承载）
    ElMessage.success(t('outline.saved'))
    await validateOnly()
    await project.loadVolumes()
    emit('saved')
  } catch (err) {
    error.value = errorMessage(err)
    ElMessage.error(error.value)
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

onMounted(load)
watch(() => project.currentNovelId, load)
</script>

<template>
  <div class="outline-editor-pane" @keydown="onKeydown">
    <div class="row-between toolbar">
      <div class="row">
        <el-radio-group v-model="previewMode" size="small">
          <el-radio-button :value="false">{{ t('outline.edit') }}</el-radio-button>
          <el-radio-button :value="true">{{ t('outline.preview') }}</el-radio-button>
        </el-radio-group>
        <!-- v1029：删除「请保持层级：# 为卷（或附录），## 为章…」提示 -->
      </div>
      <div class="row">
        <el-tag v-if="dirty" type="warning" size="small" effect="plain">{{ t('editor.unsaved') }}</el-tag>
        <el-button size="small" type="primary" :loading="saving" :disabled="!dirty" @click="save">
          {{ t('common.save') }}
        </el-button>
      </div>
    </div>

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
    </el-alert>

    <el-alert v-if="validation && !validation.ok && !errors.length" type="info" :closable="false" class="mb8">
      {{ t('outline.validateOk', { volumes: validation.volume_count, chapters: validation.chapter_count }) }}
    </el-alert>

    <el-alert v-if="error" :title="error" type="error" :closable="false" class="mb8" />

    <textarea
      v-show="!previewMode"
      v-model="content"
      class="outline-editor flex-fill"
      spellcheck="false"
    />

    <div v-if="previewMode" class="scroll-y flex-fill preview">
      <!-- eslint-disable-next-line vue/no-v-html -->
      <article class="novel-prose" v-html="previewHtml" />
    </div>
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

.toolbar {
  margin-bottom: 8px;
  flex-wrap: wrap;
  gap: 8px;
}

.mb8 {
  margin-bottom: 8px;
}

.issue-list {
  margin: 4px 0 0;
  padding-left: 18px;
  font-size: 12px;
  line-height: 1.7;
  max-height: 140px;
  overflow-y: auto;
}

.preview {
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-panel);
}

.preview .novel-prose {
  padding: 16px 24px;
  max-width: none;
}
</style>
