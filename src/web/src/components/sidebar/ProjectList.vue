<script setup lang="ts">
/**
 * 左侧栏（v1028）：新建小说 + 项目列表。
 *   - 无项目时**不展示任何提示**；
 *   - 有项目时默认选中上次打开的项目（localStorage 记忆）；
 *   - 一行一个项目：书籍 icon + 书名 + 右侧 ··· （重命名 / 删除 / 导出）；
 *   - 悬浮卡片：作者 → 题材 → 简介（无作者与题材时只展示简介）。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useProjectStore } from '@/stores/project'
import { t } from '@/i18n'
import { errorMessage } from '@/api/http'
import type { Novel } from '@/types'
import WeatherWidget from './WeatherWidget.vue'

const emit = defineEmits<{
  (e: 'export', novel: Novel): void
  (e: 'open-settings'): void
  (e: 'open-ai-tools'): void
}>()

const store = useProjectStore()
const dialogVisible = ref(false)
const creating = ref(false)
const form = reactive({ title: '', author_name: '', genre: '', synopsis: '' })
/** 书名规则悬浮提示可见性（v1028：原 placeholder 收进悬浮弹窗） */
const ruleHintVisible = ref(false)

const sortedNovels = computed(() => store.novels)

/** localStorage 记忆的上次打开项目 */
const LAST_NOVEL_KEY = 'aiwriter:lastNovelId'

onMounted(async () => {
  try {
    await store.loadNovels()
    // v1028：默认选中上次打开的项目（失效则回落第一个）
    const lastId = localStorage.getItem(LAST_NOVEL_KEY)
    const target = store.novels.find((n) => n.id === lastId) ?? store.novels[0]
    if (target) await store.selectNovel(target.id)
  } catch {
    /* 错误已由 store 记录，UI 显示后端未连接提示 */
  }
})

function rememberNovel(id: string): void {
  try {
    localStorage.setItem(LAST_NOVEL_KEY, id)
  } catch {
    /* 隐私模式下忽略 */
  }
}

/** 书名校验（v1028 规则）：非空、非全阿拉伯数字、不含 #$% 等特殊字符 */
const SPECIAL_CHARS = /[#\$%&*+=<>@\^|~`\\]/

function validateTitle(raw: string): string {
  const title = raw.trim()
  if (!title) return '书籍名称不允许为空'
  const digits = title.replace(/\s/g, '')
  if (digits && /^\d+$/.test(digits)) return '书籍名称不允许全部为阿拉伯数字'
  const bad = [...new Set(title.split('').filter((c) => SPECIAL_CHARS.test(c)))]
  if (bad.length) return `书籍名称不允许出现特殊字符 ${bad.join(' ')}（可以出现标点符号）`
  return ''
}

const titleError = computed(() => validateTitle(form.title))

async function submitCreate(): Promise<void> {
  if (titleError.value) {
    ElMessage.warning(titleError.value)
    return
  }
  // v1028：简介必填
  if (!form.synopsis.trim()) {
    ElMessage.warning('简介为必填项')
    return
  }
  creating.value = true
  try {
    const author = form.author_name.trim()
    const novel = await store.createNovel({
      title: form.title.trim(),
      author_name: author || undefined,
      genre: form.genre || undefined,
      synopsis: form.synopsis.trim(),
    })
    ElMessage.success(t('novel.created', { title: novel.title }))
    dialogVisible.value = false
    Object.assign(form, { title: '', author_name: '', genre: '', synopsis: '' })
    await store.selectNovel(novel.id)
    rememberNovel(novel.id)
  } catch (err) {
    ElMessage.error(errorMessage(err))
  } finally {
    creating.value = false
  }
}

async function select(novel: Novel): Promise<void> {
  await store.selectNovel(novel.id)
  rememberNovel(novel.id)
}

async function onRename(novel: Novel): Promise<void> {
  try {
    const { value } = await ElMessageBox.prompt(t('novel.titleLabel'), t('novel.renameTitle'), {
      inputValue: novel.title,
      confirmButtonText: t('common.confirm'),
      cancelButtonText: t('common.cancel'),
      inputValidator: (v: string) => validateTitle(v) || true,
    })
    await store.renameNovel(novel.id, value.trim())
    ElMessage.success(t('novel.renamed'))
  } catch (err) {
    if (err === 'cancel' || err === 'close') return
    ElMessage.error(errorMessage(err))
  }
}

async function onDelete(novel: Novel): Promise<void> {
  try {
    await ElMessageBox.confirm(t('novel.deleteConfirm', { title: novel.title }), t('novel.deleteConfirmTitle'), {
      type: 'warning',
      confirmButtonText: t('common.delete'),
      cancelButtonText: t('common.cancel'),
      confirmButtonClass: 'el-button--danger',
    })
    await store.deleteNovel(novel.id)
    if (localStorage.getItem(LAST_NOVEL_KEY) === novel.id) localStorage.removeItem(LAST_NOVEL_KEY)
    ElMessage.success(t('novel.deleted'))
    // 删除当前项目后回落到列表首个
    if (store.novels.length && !store.currentNovelId) {
      await store.selectNovel(store.novels[0].id)
      rememberNovel(store.novels[0].id)
    }
  } catch (err) {
    if (err === 'cancel' || err === 'close') return
    ElMessage.error(errorMessage(err))
  }
}

/** 悬浮卡片：作者 / 题材 / 简介（无作者与题材时只展示简介） */
function hasAuthorOrGenre(novel: Novel): boolean {
  return Boolean(novel.author_name?.trim() || novel.genre?.trim())
}
</script>

<template>
  <div class="project-sidebar">
    <!-- 左上角动态组件：生肖宠物 + 城市 + 天气/空气质量（v1028） -->
    <WeatherWidget />

    <div class="sidebar-head">
      <el-button type="primary" class="w100" @click="dialogVisible = true">
        {{ t('sidebar.newNovel') }}
      </el-button>
    </div>

    <!-- v1028：无项目时不展示任何提示 -->
    <div class="sidebar-body scroll-y">
      <el-tooltip
        v-for="novel in sortedNovels"
        :key="novel.id"
        placement="right"
        :show-after="320"
        :offset="12"
        popper-class="novel-hover-card"
        :disabled="!hasAuthorOrGenre(novel) && !novel.synopsis"
      >
        <template #content>
          <div class="hover-card">
            <template v-if="hasAuthorOrGenre(novel)">
              <div v-if="novel.author_name" class="hc-row">
                <span class="hc-label">{{ t('sidebar.author') }}</span>
                <span class="hc-value">{{ novel.author_name }}</span>
              </div>
              <div v-if="novel.genre" class="hc-row">
                <span class="hc-label">{{ t('sidebar.genre') }}</span>
                <span class="hc-value">{{ novel.genre }}</span>
              </div>
            </template>
            <div v-if="novel.synopsis" class="hc-synopsis">{{ novel.synopsis }}</div>
          </div>
        </template>

        <div
          class="novel-item"
          :class="{ active: novel.id === store.currentNovelId }"
          @click="select(novel)"
        >
          <!-- v1028：书籍 icon + 书名 + ···（重命名/删除/导出） -->
          <span class="novel-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
              <path d="M6 2h9a3 3 0 0 1 3 3v16a2 2 0 0 0-2-2H6a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1zm0 18h10v1a1 1 0 0 1-1 1H6a2 2 0 0 1 0-2zm3-15h5v2H9V5z" />
            </svg>
          </span>
          <span class="novel-title">{{ novel.title }}</span>

          <el-dropdown trigger="click" placement="bottom-end" @command="(cmd: string) => {
            if (cmd === 'rename') onRename(novel)
            else if (cmd === 'export') emit('export', novel)
            else if (cmd === 'delete') onDelete(novel)
          }">
            <span class="novel-more" @click.stop>···</span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="rename">{{ t('common.rename') }}</el-dropdown-item>
                <el-dropdown-item command="delete">{{ t('common.delete') }}</el-dropdown-item>
                <el-dropdown-item command="export" divided>{{ t('common.export') }}</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-tooltip>
    </div>

    <div class="sidebar-foot row">
      <el-button text @click="emit('open-settings')">{{ t('sidebar.settings') }}</el-button>
      <el-button text @click="emit('open-ai-tools')">{{ t('sidebar.aiTools') }}</el-button>
    </div>

    <!-- 新建小说弹窗（v1028：书名必填提示进输入框，规则收进悬浮弹窗；简介必填） -->
    <el-dialog v-model="dialogVisible" :title="t('novel.createTitle')" width="480px">
      <el-form :model="form" label-width="72px">
        <el-form-item>
          <template #label>
            <span class="label-with-hint">
              <el-popover
                v-model:visible="ruleHintVisible"
                placement="top-start"
                :width="300"
                trigger="hover"
              >
                <template #reference>
                  <span class="hint-trigger" @mouseenter="ruleHintVisible = true">
                    <svg viewBox="0 0 16 16" width="13" height="13" fill="currentColor">
                      <path d="M8 1.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13zM7.25 5h1.5v1.5h-1.5V5zm0 3h1.5v4h-1.5V8z" />
                    </svg>
                  </span>
                </template>
                <div class="rule-hint">{{ t('novel.titleRuleHint') }}</div>
              </el-popover>
              {{ t('novel.titleLabel') }}
            </span>
          </template>
          <el-input
            v-model="form.title"
            :placeholder="t('novel.titlePlaceholder')"
            maxlength="200"
            show-word-limit
          />
          <div v-if="form.title && titleError" class="field-error">{{ titleError }}</div>
        </el-form-item>

        <el-form-item :label="t('novel.authorLabel')">
          <el-input v-model="form.author_name" maxlength="100" />
        </el-form-item>

        <el-form-item :label="t('novel.genreLabel')">
          <el-input v-model="form.genre" maxlength="50" />
        </el-form-item>

        <el-form-item :label="t('novel.synopsisLabel')" required>
          <el-input
            v-model="form.synopsis"
            type="textarea"
            :rows="3"
            :placeholder="t('novel.synopsisPlaceholder')"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">{{ t('common.cancel') }}</el-button>
        <el-button
          type="primary"
          :loading="creating"
          :disabled="!!titleError || !form.synopsis.trim()"
          @click="submitCreate"
        >
          {{ t('common.create') }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.project-sidebar {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.sidebar-head {
  padding: 12px;
  border-bottom: 1px solid var(--border);
}

.w100 {
  width: 100%;
}

.sidebar-body {
  flex: 1;
  min-height: 0;
  padding: 6px;
}

/* v1028：一行一个项目 = icon + 书名 + ··· */
.novel-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 10px;
  border-radius: 6px;
  cursor: pointer;
  margin-bottom: 2px;
}

.novel-item:hover {
  background: var(--bg-hover);
}

.novel-item.active {
  background: color-mix(in srgb, var(--el-color-primary) 14%, transparent);
}

.novel-icon {
  flex-shrink: 0;
  display: inline-flex;
  color: var(--el-color-primary);
}

.novel-title {
  flex: 1;
  min-width: 0;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.novel-more {
  flex-shrink: 0;
  width: 24px;
  height: 22px;
  line-height: 16px;
  text-align: center;
  border-radius: 4px;
  color: var(--text-muted);
  font-weight: 700;
  letter-spacing: 1px;
  opacity: 0;
  transition: opacity 0.15s;
}

.novel-item:hover .novel-more,
.novel-item.active .novel-more {
  opacity: 1;
}

.novel-more:hover {
  background: color-mix(in srgb, var(--el-color-primary) 16%, transparent);
  color: var(--el-color-primary);
}

.sidebar-foot {
  padding: 8px 12px;
  border-top: 1px solid var(--border);
  justify-content: space-around;
}

/* 新建弹窗 */
.label-with-hint {
  display: inline-flex;
  align-items: center;
  gap: 3px;
}

.hint-trigger {
  display: inline-flex;
  align-items: center;
  color: var(--text-muted);
  cursor: help;
}

.hint-trigger:hover {
  color: var(--el-color-primary);
}

.rule-hint {
  font-size: 12px;
  line-height: 1.7;
}

.field-error {
  width: 100%;
  font-size: 12px;
  line-height: 1.5;
  color: var(--el-color-danger);
}
</style>

<style>
/* 悬浮卡片（popper 挂到 body，需全局样式） */
.novel-hover-card .hover-card {
  max-width: 260px;
}

.novel-hover-card .hc-row {
  display: flex;
  gap: 8px;
  font-size: 12px;
  line-height: 1.8;
}

.novel-hover-card .hc-label {
  flex-shrink: 0;
  opacity: 0.7;
}

.novel-hover-card .hc-value {
  font-weight: 600;
}

.novel-hover-card .hc-synopsis {
  font-size: 12px;
  line-height: 1.7;
  margin-top: 4px;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 160px;
  overflow: hidden;
}
</style>
