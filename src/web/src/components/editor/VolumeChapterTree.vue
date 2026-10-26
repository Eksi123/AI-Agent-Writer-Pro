<script setup lang="ts">
/**
 * 卷章树：增删改 / 重命名 + 显式命名校验。
 *
 * ★**只读顺序，不可拖拽**（铁律 10 / 手册「三、(3)【卷章】」）：
 *   - 无拖拽、无上移/下移、无跨卷移动；
 *   - 顺序由大纲小节先后决定，顶部提示 + 「去大纲调整」按钮直达 OutlineEditor；
 *   - 新建卷/章一律追加到末尾（sort_order = max+1）。
 *
 * 删除卷会级联删除其下章节，二次确认弹窗列出将被删除的章名与字数。
 */
import { computed, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { chaptersApi } from '@/api'
import { errorMessage } from '@/api/http'
import { useProjectStore } from '@/stores/project'
import { useReaderStore } from '@/stores/reader'
import { t } from '@/i18n'
import type { Chapter, Volume } from '@/types'

const emit = defineEmits<{ (e: 'goto-outline'): void; (e: 'open-chapter', chapterId: string): void }>()

const project = useProjectStore()
const reader = useReaderStore()

const volumes = computed(() => project.volumeTree)

// ---- 新建对话框 ----
const dialogVisible = ref(false)
const dialogKind = ref<'volume' | 'chapter'>('volume')
const dialogVolId = ref<string | null>(null)
const submitting = ref(false)
const form = ref({ title: '', summary: '' })

function openCreate(kind: 'volume' | 'chapter', volId: string | null = null): void {
  dialogKind.value = kind
  dialogVolId.value = volId
  form.value = { title: '', summary: '' }
  dialogVisible.value = true
}

const dialogTitle = computed(() =>
  dialogKind.value === 'volume' ? t('volumeChapter.newVolume') : t('volumeChapter.newChapter'),
)
const titleLabel = computed(() =>
  dialogKind.value === 'volume' ? t('volumeChapter.volumeTitleLabel') : t('volumeChapter.chapterTitleLabel'),
)

async function submitCreate(): Promise<void> {
  const title = form.value.title.trim()
  if (!title) {
    ElMessage.warning(t('volumeChapter.titlePlaceholder'))
    return
  }
  submitting.value = true
  try {
    if (dialogKind.value === 'volume') {
      const vol = await project.createVolume(title, form.value.summary)
      ElMessage.success(t('volumeChapter.volumeCreated', { title: vol.title }))
    } else if (dialogVolId.value) {
      const ch = await chaptersApi.create(dialogVolId.value, title)
      await project.loadVolumes()
      ElMessage.success(t('volumeChapter.chapterCreated', { title: ch.title }))
    }
    dialogVisible.value = false
  } catch (err) {
    // 42201：显式命名校验失败（空名/默认名/纯序号名）
    ElMessage.error(errorMessage(err))
  } finally {
    submitting.value = false
  }
}

// ---- 重命名 ----
async function renameVolume(vol: Volume): Promise<void> {
  await promptRename(vol.title, t('volumeChapter.volumeTitleLabel'), async (next) => {
    await project.updateVolume(vol.id, { title: next })
  })
}

async function renameChapter(ch: Chapter): Promise<void> {
  await promptRename(ch.title, t('volumeChapter.chapterTitleLabel'), async (next) => {
    await chaptersApi.update(ch.id, { title: next })
    await project.loadVolumes()
  })
}

async function promptRename(
  current: string,
  label: string,
  apply: (next: string) => Promise<void>,
): Promise<void> {
  try {
    const { value } = await ElMessageBox.prompt(label, t('common.rename'), {
      inputValue: current,
      confirmButtonText: t('common.confirm'),
      cancelButtonText: t('common.cancel'),
      inputValidator: (v: string) => (v && v.trim() ? true : t('common.required')),
    })
    await apply(value.trim())
    ElMessage.success(t('volumeChapter.renamedOk'))
  } catch (err) {
    if (err === 'cancel' || err === 'close') return
    ElMessage.error(errorMessage(err))
  }
}

// ---- 删除（卷级联删章，二次确认列出章名与字数）----
const HTML_ESCAPES: Record<string, string> = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;',
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => HTML_ESCAPES[c] ?? c)
}

async function deleteVolume(vol: Volume): Promise<void> {
  const count = vol.chapters.length
  const message = t('volumeChapter.deleteVolumeConfirm', {
    title: vol.title,
    count,
    words: vol.word_count,
  })
  // 级联删除需明确列出将被删除的章名与字数，故用 HTML 渲染（内容已转义）
  const list = vol.chapters.length
    ? `<div style="margin-top:8px;max-height:160px;overflow:auto;text-align:left">
         <div class="muted">${escapeHtml(t('volumeChapter.chaptersToBeDeleted'))}：</div>
         ${vol.chapters
           .map(
             (c) =>
               `<div>· ${escapeHtml(c.title)}（${c.word_count} ${escapeHtml(t('sidebar.wordsUnit'))}）</div>`,
           )
           .join('')}
       </div>`
    : ''

  try {
    await ElMessageBox.confirm(message + list, t('volumeChapter.deleteVolumeTitle'), {
      type: 'warning',
      confirmButtonText: t('common.delete'),
      cancelButtonText: t('common.cancel'),
      confirmButtonClass: 'el-button--danger',
      dangerouslyUseHTMLString: true,
    })
    const deleted = await project.deleteVolume(vol.id)
    ElMessage.success(t('volumeChapter.volumeDeleted', { count: deleted }))
  } catch (err) {
    if (err === 'cancel' || err === 'close') return
    ElMessage.error(errorMessage(err))
  }
}

async function deleteChapter(ch: Chapter): Promise<void> {
  try {
    await ElMessageBox.confirm(
      t('volumeChapter.deleteChapterConfirm', { title: ch.title }),
      t('volumeChapter.deleteChapterTitle'),
      {
        type: 'warning',
        confirmButtonText: t('common.delete'),
        cancelButtonText: t('common.cancel'),
        confirmButtonClass: 'el-button--danger',
      },
    )
    await chaptersApi.remove(ch.id)
    await project.loadVolumes()
    if (reader.activeChapterId === ch.id) reader.activeChapterId = null
    ElMessage.success(t('volumeChapter.chapterDeleted'))
  } catch (err) {
    if (err === 'cancel' || err === 'close') return
    ElMessage.error(errorMessage(err))
  }
}

async function toggleFinalized(ch: Chapter): Promise<void> {
  try {
    const next = ch.status === 'finalized' ? 'draft' : 'finalized'
    await chaptersApi.update(ch.id, { status: next })
    await project.loadVolumes()
  } catch (err) {
    ElMessage.error(errorMessage(err))
  }
}

function openChapter(ch: Chapter): void {
  reader.openChapter(ch.id)
  emit('open-chapter', ch.id)
}
</script>

<template>
  <div class="volume-chapter-tree">
    <!-- ★顺序不可手工调整的说明 + 直达大纲入口 -->
    <div class="vc-hint">
      {{ t('volumeChapter.orderHint') }}
      <el-link type="primary" underline="never" @click="emit('goto-outline')">
        {{ t('volumeChapter.gotoOutline') }}
      </el-link>
      <div class="muted">{{ t('volumeChapter.appendedHint') }}</div>
      <div class="muted">{{ t('volumeChapter.noMoveHint') }}</div>
    </div>

    <div class="row toolbar">
      <el-button size="small" type="primary" plain @click="openCreate('volume')">
        {{ t('volumeChapter.newVolume') }}
      </el-button>
      <span class="muted text-sm">
        {{ project.totalChapters }} {{ t('sidebar.chaptersUnit') }} · {{ project.totalWords }} {{ t('sidebar.wordsUnit') }}
      </span>
    </div>

    <el-empty v-if="!volumes.length" :description="t('common.empty')" :image-size="60" />

    <div class="scroll-y flex-fill tree-body">
      <div v-for="vol in volumes" :key="vol.id" class="vol-block">
        <div class="vc-node vol-row">
          <span class="badge muted">#{{ vol.sort_order }}</span>
          <span class="vol-title">{{ vol.title }}</span>
          <span class="flex-fill" />
          <el-button link size="small" @click="openCreate('chapter', vol.id)">
            {{ t('volumeChapter.newChapter') }}
          </el-button>
          <el-button link size="small" @click="renameVolume(vol)">{{ t('common.rename') }}</el-button>
          <el-button link size="small" type="danger" @click="deleteVolume(vol)">
            {{ t('common.delete') }}
          </el-button>
        </div>

        <div
          v-for="ch in vol.chapters"
          :key="ch.id"
          class="vc-node ch-row"
          :class="{ active: reader.activeChapterId === ch.id }"
          @click="openChapter(ch)"
        >
          <span class="badge muted">{{ vol.sort_order }}.{{ ch.sort_order }}</span>
          <span class="ch-title">{{ ch.title }}</span>
          <el-tag v-if="ch.status === 'finalized'" size="small" type="success" effect="plain">定稿</el-tag>
          <span class="muted text-sm">{{ ch.word_count }}{{ t('sidebar.wordsUnit') }}</span>
          <span class="flex-fill" />
          <el-button link size="small" @click.stop="toggleFinalized(ch)">
            {{ ch.status === 'finalized' ? t('editor.unfinalize') : t('editor.finalize') }}
          </el-button>
          <el-button link size="small" @click.stop="renameChapter(ch)">{{ t('common.rename') }}</el-button>
          <el-button link size="small" type="danger" @click.stop="deleteChapter(ch)">
            {{ t('common.delete') }}
          </el-button>
        </div>

        <div v-if="!vol.chapters.length" class="muted text-sm empty-vol">
          {{ t('common.empty') }}
        </div>
      </div>
    </div>

    <el-dialog v-model="dialogVisible" :title="dialogTitle" width="460px">
      <el-form :model="form" label-width="72px">
        <el-form-item :label="titleLabel" required>
          <el-input
            v-model="form.title"
            :placeholder="t('volumeChapter.titlePlaceholder')"
            maxlength="200"
            show-word-limit
          />
        </el-form-item>
        <el-form-item v-if="dialogKind === 'volume'" :label="t('volumeChapter.summaryLabel')">
          <el-input v-model="form.summary" type="textarea" :rows="3" />
        </el-form-item>
        <div class="muted text-sm">{{ t('volumeChapter.appendedHint') }}</div>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="submitting" @click="submitCreate">
          {{ t('common.create') }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.volume-chapter-tree {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  padding: 12px 16px;
}

.toolbar {
  margin-bottom: 8px;
  justify-content: space-between;
}

.tree-body {
  min-height: 0;
}

.vol-block {
  margin-bottom: 10px;
}

.vol-row {
  font-weight: 600;
  background: var(--bg-hover);
}

.ch-row {
  padding-left: 20px;
}

.badge {
  font-variant-numeric: tabular-nums;
  font-size: 12px;
  min-width: 30px;
}

.vol-title,
.ch-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 320px;
}

.empty-vol {
  padding: 4px 20px;
}
</style>
