<script setup lang="ts">
/**
 * 编辑模式 · 主页面右上角的卷章结构操作条（v1029 需求 5-(1.4)~(1.7)）。
 *
 * 六个入口（前四个为 icon 按钮，光标停留显示功能说明）：
 *   [往前插入小说卷] [往后插入小说卷] [往前插入小说章节] [往后插入小说章节] [重命名] [删除]
 *
 * - 插入弹窗：必填「序号 + 名称」。序号为正整数且落在 1..N+1（★位置语义：插入到该位置，
 *   其后各项自动顺延重编号）；名称与书名同规则（非空 / 非全阿拉伯数字 / 不含 #$% 等特殊字符）。
 *   弹窗内输入框**不带提示词**。
 * - 重命名弹窗：含「章名 + 卷名」，卷名默认填当前章所属卷名（即默认不修改卷名）。
 * - 删除：二次确认；删的是当前章，**该卷章节删空后卷一并删除**（后端保证）。
 *   当前处于大纲节点时不允许删除（大纲是整本书的创作参考）。
 */
import { computed, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { chaptersApi } from '@/api'
import { errorMessage } from '@/api/http'
import { useProjectStore } from '@/stores/project'
import { useReaderStore } from '@/stores/reader'
import { t } from '@/i18n'
import { validateChapterName, validateOrdinal, validateVolumeName } from '@/utils/naming'
import { formatChapterTitle, formatVolumeTitle } from '@/utils/titles'
import type { Chapter, Volume } from '@/types'

const project = useProjectStore()
const reader = useReaderStore()

const volumes = computed(() => project.volumeTree)

/** 当前打开的章（及其所属卷）；在大纲节点时为 null */
const current = computed<{ volume: Volume; chapter: Chapter; index: number } | null>(() => {
  const id = reader.activeChapterId
  if (!id) return null
  for (const volume of volumes.value) {
    const index = volume.chapters.findIndex((c) => c.id === id)
    if (index >= 0) return { volume, chapter: volume.chapters[index], index }
  }
  return null
})

/** 插入章的目标卷：当前章所属卷 → 否则第一个卷 */
const targetVolume = computed<Volume | null>(() => current.value?.volume ?? volumes.value[0] ?? null)

const onOutline = computed(() => reader.activeOutline || !reader.activeChapterId)

// ---------------------------------------------------------------------------
// 插入弹窗
// ---------------------------------------------------------------------------

type InsertKind = 'volume' | 'chapter'
type InsertDir = 'before' | 'after'

const dialogVisible = ref(false)
const submitting = ref(false)
const kind = ref<InsertKind>('volume')
const dir = ref<InsertDir>('before')
const form = ref({ ordinal: '', title: '' })

const dialogTitle = computed(() => {
  if (kind.value === 'volume') {
    return dir.value === 'before'
      ? t('volumeChapter.insertBeforeVolume')
      : t('volumeChapter.insertAfterVolume')
  }
  return dir.value === 'before'
    ? t('volumeChapter.insertBeforeChapter')
    : t('volumeChapter.insertAfterChapter')
})

/** 当前已有数量（卷总数 / 目标卷内章总数） */
const count = computed(() =>
  kind.value === 'volume' ? volumes.value.length : (targetVolume.value?.chapters.length ?? 0),
)

/** 默认序号：往前 = 当前项序号；往后 = 当前项序号 + 1（无当前项则追加末尾） */
function defaultOrdinal(k: InsertKind, d: InsertDir): number {
  const total = k === 'volume' ? volumes.value.length : (targetVolume.value?.chapters.length ?? 0)
  if (k === 'volume') {
    const at = current.value ? current.value.volume.sort_order : total
    return d === 'before' ? Math.max(1, at) : Math.min(total + 1, at + 1)
  }
  const at = current.value ? current.value.chapter.sort_order : total
  return d === 'before' ? Math.max(1, at) : Math.min(total + 1, at + 1)
}

function openInsert(k: InsertKind, d: InsertDir): void {
  if (k === 'chapter' && !targetVolume.value) {
    ElMessage.warning(t('volumeChapter.editNoTarget'))
    return
  }
  kind.value = k
  dir.value = d
  form.value = { ordinal: String(defaultOrdinal(k, d)), title: '' }
  dialogVisible.value = true
}

const ordinalError = computed(() =>
  validateOrdinal(form.value.ordinal, count.value, kind.value === 'volume' ? '卷序号' : '章节序号'),
)

const titleError = computed(() => {
  const raw = form.value.title
  if (!raw.trim()) return kind.value === 'volume' ? '卷名不允许为空' : '章节名不允许为空'
  return kind.value === 'volume' ? validateVolumeName(raw) : validateChapterName(raw)
})

const canSubmit = computed(() => !ordinalError.value && !titleError.value && !submitting.value)

async function submitInsert(): Promise<void> {
  if (!canSubmit.value) return
  const ordinal = Number.parseInt(form.value.ordinal.trim(), 10)
  const title = form.value.title.trim()
  submitting.value = true
  try {
    if (kind.value === 'volume') {
      if (!project.currentNovelId) return
      const vol = await project.createVolume(title, '', ordinal)
      ElMessage.success(t('volumeChapter.volumeInserted', { ordinal: vol.sort_order, title: vol.title }))
    } else {
      const vol = targetVolume.value
      if (!vol) return
      const ch = await chaptersApi.create(vol.id, title, '', ordinal)
      await project.loadVolumes()
      ElMessage.success(t('volumeChapter.chapterInserted', { ordinal: ch.sort_order, title: ch.title }))
      // 新建章后打开它，便于直接撰写
      reader.openChapter(ch.id)
    }
    dialogVisible.value = false
  } catch (err) {
    ElMessage.error(errorMessage(err))
  } finally {
    submitting.value = false
  }
}

// ---------------------------------------------------------------------------
// 重命名（章名 + 卷名，卷名默认不变）
// ---------------------------------------------------------------------------

const renameVisible = ref(false)
const renameForm = ref({ chapter: '', volume: '' })

const renameChapterError = computed(() =>
  renameForm.value.chapter.trim() ? validateChapterName(renameForm.value.chapter) : '章节名不允许为空',
)
const renameVolumeError = computed(() =>
  renameForm.value.volume.trim() ? validateVolumeName(renameForm.value.volume) : '卷名不允许为空',
)
const canRename = computed(
  () => !renameChapterError.value && !renameVolumeError.value && !submitting.value,
)

function openRename(): void {
  if (!current.value) {
    ElMessage.warning(t('volumeChapter.editNoTarget'))
    return
  }
  renameForm.value = {
    chapter: current.value.chapter.title,
    // v1029：卷名默认填入当前章所对应卷名（也就是默认不修改卷名）
    volume: current.value.volume.title,
  }
  renameVisible.value = true
}

async function submitRename(): Promise<void> {
  if (!canRename.value || !current.value) return
  submitting.value = true
  try {
    await chaptersApi.update(current.value.chapter.id, {
      title: renameForm.value.chapter.trim(),
      volume_title: renameForm.value.volume.trim(),
    })
    await project.loadVolumes()
    renameVisible.value = false
    ElMessage.success(t('volumeChapter.renamedOk'))
  } catch (err) {
    ElMessage.error(errorMessage(err))
  } finally {
    submitting.value = false
  }
}

// ---------------------------------------------------------------------------
// 删除（二次确认；删空卷 → 卷一并删除）
// ---------------------------------------------------------------------------

async function onDelete(): Promise<void> {
  if (onOutline.value || !current.value) {
    ElMessage.warning(t('volumeChapter.deleteOutlineDenied'))
    return
  }
  const { volume, chapter } = current.value
  const isLast = volume.chapters.length <= 1
  const message = isLast
    ? t('volumeChapter.deleteLastChapterConfirm', {
        title: formatChapterTitle(chapter.title, chapter.sort_order, 0),
        volume: formatVolumeTitle(volume.title, volume.sort_order, 0),
      })
    : t('volumeChapter.deleteChapterConfirm', {
        title: formatChapterTitle(chapter.title, chapter.sort_order, 0),
      })

  try {
    await ElMessageBox.confirm(message, t('volumeChapter.deleteChapterTitle'), {
      type: 'warning',
      confirmButtonText: t('common.delete'),
      cancelButtonText: t('common.cancel'),
      confirmButtonClass: 'el-button--danger',
    })
  } catch {
    return // 用户取消
  }

  try {
    const res = await chaptersApi.remove(chapter.id)
    await project.loadVolumes()
    reader.activeChapterId = null
    reader.openOutline()
    ElMessage.success(
      res.volume_deleted
        ? t('volumeChapter.chapterDeletedVolumeGone', {
            volume: formatVolumeTitle(volume.title, volume.sort_order, 0),
          })
        : t('volumeChapter.chapterDeleted'),
    )
  } catch (err) {
    ElMessage.error(errorMessage(err))
  }
}
</script>

<template>
  <div class="editor-actions">
    <!-- 往前插入小说卷 -->
    <el-tooltip :content="t('volumeChapter.insertBeforeVolume')" placement="bottom">
      <button class="act-btn" @click="openInsert('volume', 'before')">
        <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true">
          <path d="M4 5h16v2H4V5zm0 4h7v2H4V9zm0 4h7v2H4v-2zm0 4h16v2H4v-2zm11-8h2v3h3v2h-3v3h-2v-3h-3v-2h3V9z" />
        </svg>
      </button>
    </el-tooltip>

    <!-- 往后插入小说卷 -->
    <el-tooltip :content="t('volumeChapter.insertAfterVolume')" placement="bottom">
      <button class="act-btn" @click="openInsert('volume', 'after')">
        <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true">
          <path d="M4 5h16v2H4V5zm0 4h16v2H4V9zm0 8h7v2H4v-2zm11-4h2v3h3v2h-3v3h-2v-3h-3v-2h3v-3zM4 17h7v2H4v-2z" />
        </svg>
      </button>
    </el-tooltip>

    <span class="act-sep" />

    <!-- 往前插入小说章节 -->
    <el-tooltip :content="t('volumeChapter.insertBeforeChapter')" placement="bottom">
      <button class="act-btn" :disabled="!targetVolume" @click="openInsert('chapter', 'before')">
        <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true">
          <path d="M3 5h11v2H3V5zm0 4h8v2H3V9zm0 8h11v2H3v-2zm0-4h8v2H3v-2zm12 0h2v3h3v2h-3v3h-2v-3h-3v-2h3v-3z" />
        </svg>
      </button>
    </el-tooltip>

    <!-- 往后插入小说章节 -->
    <el-tooltip :content="t('volumeChapter.insertAfterChapter')" placement="bottom">
      <button class="act-btn" :disabled="!targetVolume" @click="openInsert('chapter', 'after')">
        <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true">
          <path d="M3 5h11v2H3V5zm0 4h11v2H3V9zm0 4h8v2H3v-2zm12 0h2v3h3v2h-3v3h-2v-3h-3v-2h3v-3zM3 17h8v2H3v-2z" />
        </svg>
      </button>
    </el-tooltip>

    <span class="act-sep" />

    <!-- 重命名 -->
    <el-tooltip :content="t('common.rename')" placement="bottom">
      <button class="act-btn" :disabled="!current" @click="openRename">
        <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true">
          <path d="M3 17.2V21h3.8L17.9 9.9l-3.8-3.8L3 17.2zM20.7 7.1a1 1 0 0 0 0-1.4l-2.4-2.4a1 1 0 0 0-1.4 0l-1.8 1.8 3.8 3.8 1.8-1.8z" />
        </svg>
      </button>
    </el-tooltip>

    <!-- 删除 -->
    <el-tooltip :content="t('common.delete')" placement="bottom">
      <button class="act-btn danger" :disabled="onOutline || !current" @click="onDelete">
        <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true">
          <path d="M6 7h12v13a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V7zm3 3v7h2v-7H9zm4 0v7h2v-7h-2zM9 4h6l1 1h3v2H5V5h3l1-1z" />
        </svg>
      </button>
    </el-tooltip>

    <!-- ============ 插入弹窗（序号 + 名称，输入框无提示词） ============ -->
    <el-dialog v-model="dialogVisible" :title="dialogTitle" width="440px">
      <el-form label-width="86px" @submit.prevent>
        <el-form-item
          :label="kind === 'volume' ? t('volumeChapter.ordinalLabel') : t('volumeChapter.chapterOrdinalLabel')"
          required
        >
          <el-input v-model="form.ordinal" class="ordinal-input" />
          <div v-if="ordinalError" class="field-error">{{ ordinalError }}</div>
          <div v-else class="field-hint">
            {{ t('volumeChapter.ordinalHint', { kind: kind === 'volume' ? '卷' : '章' }) }}
          </div>
        </el-form-item>

        <el-form-item
          :label="kind === 'volume' ? t('volumeChapter.volumeTitleLabel') : t('volumeChapter.chapterTitleLabel')"
          required
        >
          <el-input v-model="form.title" maxlength="200" />
          <div v-if="form.title && titleError" class="field-error">{{ titleError }}</div>
        </el-form-item>

        <div v-if="kind === 'chapter' && targetVolume" class="muted text-sm target-hint">
          {{ t('volumeChapter.volumeTitleLabel') }}：{{
            formatVolumeTitle(targetVolume.title, targetVolume.sort_order, 0)
          }}
        </div>
      </el-form>

      <template #footer>
        <el-button @click="dialogVisible = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="submitting" :disabled="!canSubmit" @click="submitInsert">
          {{ t('common.confirm') }}
        </el-button>
      </template>
    </el-dialog>

    <!-- ============ 重命名弹窗（章名 + 卷名） ============ -->
    <el-dialog v-model="renameVisible" :title="t('volumeChapter.renameDialogTitle')" width="440px">
      <el-form label-width="86px" @submit.prevent>
        <el-form-item :label="t('volumeChapter.chapterTitleLabel')" required>
          <el-input v-model="renameForm.chapter" maxlength="200" />
          <div v-if="renameChapterError" class="field-error">{{ renameChapterError }}</div>
        </el-form-item>

        <el-form-item :label="t('volumeChapter.volumeTitleLabel')" required>
          <el-input v-model="renameForm.volume" maxlength="200" />
          <div v-if="renameVolumeError" class="field-error">{{ renameVolumeError }}</div>
          <div v-else class="field-hint">{{ t('volumeChapter.renameVolumeHint') }}</div>
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="renameVisible = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="submitting" :disabled="!canRename" @click="submitRename">
          {{ t('common.confirm') }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.editor-actions {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
}

.act-btn {
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

.act-btn:hover:not(:disabled) {
  color: var(--el-color-primary);
  background: color-mix(in srgb, var(--el-color-primary) 12%, transparent);
  border-color: color-mix(in srgb, var(--el-color-primary) 30%, transparent);
}

.act-btn.danger:hover:not(:disabled) {
  color: var(--el-color-danger);
  background: color-mix(in srgb, var(--el-color-danger) 12%, transparent);
  border-color: color-mix(in srgb, var(--el-color-danger) 30%, transparent);
}

.act-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

.act-sep {
  width: 1px;
  height: 16px;
  margin: 0 5px;
  background: var(--border);
}

.ordinal-input {
  width: 130px;
}

.field-error {
  width: 100%;
  font-size: 12px;
  line-height: 1.5;
  color: var(--el-color-danger);
}

.field-hint {
  width: 100%;
  font-size: 12px;
  line-height: 1.5;
  color: var(--text-muted);
}

.target-hint {
  padding-left: 86px;
}
</style>
