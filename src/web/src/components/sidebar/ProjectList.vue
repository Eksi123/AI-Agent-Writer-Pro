<script setup lang="ts">
/**
 * 左侧栏：新建/删除小说 + 项目列表（右键菜单：重命名、导出、复制、删除）。
 * 删除为软删除（移入 data/trash/，列表不再显示）。
 */
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useProjectStore } from '@/stores/project'
import { t } from '@/i18n'
import { errorMessage } from '@/api/http'
import type { Novel } from '@/types'

const emit = defineEmits<{
  (e: 'export', novel: Novel): void
  (e: 'open-settings'): void
  (e: 'open-ai-tools'): void
}>()

const store = useProjectStore()
const dialogVisible = ref(false)
const creating = ref(false)
const form = ref({ title: '', author_name: '', genre: '', synopsis: '' })

const sortedNovels = computed(() => store.novels)

onMounted(async () => {
  try {
    await store.loadNovels()
  } catch {
    /* 错误已由 store 记录，UI 显示后端未连接提示 */
  }
})

async function submitCreate(): Promise<void> {
  if (!form.value.title.trim()) {
    ElMessage.warning(t('novel.titlePlaceholder'))
    return
  }
  creating.value = true
  try {
    const novel = await store.createNovel({
      title: form.value.title.trim(),
      author_name: form.value.author_name || undefined,
      genre: form.value.genre || undefined,
      synopsis: form.value.synopsis || undefined,
    })
    ElMessage.success(t('novel.created', { title: novel.title }))
    dialogVisible.value = false
    form.value = { title: '', author_name: '', genre: '', synopsis: '' }
    await store.selectNovel(novel.id)
  } catch (err) {
    ElMessage.error(errorMessage(err))
  } finally {
    creating.value = false
  }
}

async function select(novel: Novel): Promise<void> {
  await store.selectNovel(novel.id)
}

async function onRename(novel: Novel): Promise<void> {
  try {
    const { value } = await ElMessageBox.prompt(t('novel.titleLabel'), t('novel.renameTitle'), {
      inputValue: novel.title,
      confirmButtonText: t('common.confirm'),
      cancelButtonText: t('common.cancel'),
      inputValidator: (v: string) => (v && v.trim() ? true : t('common.required')),
    })
    await store.renameNovel(novel.id, value.trim())
    ElMessage.success(t('novel.renamed'))
  } catch (err) {
    if (err === 'cancel' || err === 'close') return
    ElMessage.error(errorMessage(err))
  }
}

async function onDuplicate(novel: Novel): Promise<void> {
  try {
    const { value } = await ElMessageBox.prompt(t('novel.duplicateLabel'), t('novel.duplicateTitle'), {
      inputValue: `${novel.title}（副本）`,
      confirmButtonText: t('common.confirm'),
      cancelButtonText: t('common.cancel'),
      inputValidator: (v: string) => (v && v.trim() ? true : t('common.required')),
    })
    await store.duplicateNovel(novel.id, value.trim())
    ElMessage.success(t('common.saved'))
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
    ElMessage.success(t('novel.deleted'))
  } catch (err) {
    if (err === 'cancel' || err === 'close') return
    ElMessage.error(errorMessage(err))
  }
}

function formatWords(n: number): string {
  if (n >= 10000) return `${(n / 10000).toFixed(1)} 万${t('sidebar.wordsUnit')}`
  return `${n} ${t('sidebar.wordsUnit')}`
}
</script>

<template>
  <div class="project-sidebar">
    <div class="sidebar-head">
      <el-button type="primary" class="w100" @click="dialogVisible = true">
        {{ t('sidebar.newNovel') }}
      </el-button>
    </div>

    <div class="sidebar-body scroll-y">
      <div v-if="!sortedNovels.length" class="empty-hint">
        {{ t('sidebar.noProjects') }}
      </div>
      <el-dropdown
        v-for="novel in sortedNovels"
        :key="novel.id"
        trigger="contextmenu"
        placement="bottom-start"
        @command="(cmd: string) => {
          if (cmd === 'rename') onRename(novel)
          else if (cmd === 'export') emit('export', novel)
          else if (cmd === 'duplicate') onDuplicate(novel)
          else if (cmd === 'delete') onDelete(novel)
        }"
      >
        <div
          class="novel-item"
          :class="{ active: novel.id === store.currentNovelId }"
          @click="select(novel)"
        >
          <div class="novel-title">{{ novel.title }}</div>
          <div class="novel-meta muted text-sm">
            {{ novel.chapter_count }} {{ t('sidebar.chaptersUnit') }} ·
            {{ formatWords(novel.total_words) }}
          </div>
        </div>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="rename">{{ t('common.rename') }}</el-dropdown-item>
            <el-dropdown-item command="export">{{ t('common.export') }}</el-dropdown-item>
            <el-dropdown-item command="duplicate">{{ t('common.duplicate') }}</el-dropdown-item>
            <el-dropdown-item command="delete" divided>{{ t('common.delete') }}</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>

    <div class="sidebar-foot row">
      <el-button text @click="emit('open-settings')">{{ t('sidebar.settings') }}</el-button>
      <el-button text @click="emit('open-ai-tools')">{{ t('sidebar.aiTools') }}</el-button>
    </div>

    <el-dialog v-model="dialogVisible" :title="t('novel.createTitle')" width="480px">
      <el-form :model="form" label-width="80px">
        <el-form-item :label="t('novel.titleLabel')" required>
          <el-input
            v-model="form.title"
            :placeholder="t('novel.titlePlaceholder')"
            maxlength="200"
            show-word-limit
          />
        </el-form-item>
        <el-form-item :label="t('novel.authorLabel')">
          <el-input v-model="form.author_name" maxlength="100" />
        </el-form-item>
        <el-form-item :label="t('novel.genreLabel')">
          <el-input v-model="form.genre" maxlength="50" />
        </el-form-item>
        <el-form-item :label="t('novel.synopsisLabel')">
          <el-input v-model="form.synopsis" type="textarea" :rows="3" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="creating" @click="submitCreate">
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

.empty-hint {
  padding: 24px 12px;
  color: var(--text-muted);
  font-size: 13px;
  line-height: 1.7;
  text-align: center;
}

.novel-item {
  padding: 8px 10px;
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

.novel-title {
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.novel-meta {
  margin-top: 2px;
}

.sidebar-foot {
  padding: 8px 12px;
  border-top: 1px solid var(--border);
  justify-content: space-around;
}
</style>
