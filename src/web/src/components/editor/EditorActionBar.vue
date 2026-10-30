<script setup lang="ts">
/**
 * 编辑模式 · 工具栏上的卷章结构操作条（v1029 引入，v1030续 重写插入逻辑）。
 *
 * v1030续 插入规则：
 * - **大纲节点**：大纲在最前头 → 只支持「往后插入卷」（新卷落在第 1 位，原有各卷顺延 +1），
 *   不支持插入章节、不支持往前插入；
 * - **章节节点**：支持往前 / 往后插入「卷」与「章节」；
 *   · 插入卷 → 填「卷序号（自动算好，可改）+ 卷名 + 第一章章节名」，★不允许产生空卷；
 *   · 插入章节 → 只填「章节名」，章节序号由插入方向自动推算；
 * - 序号自动增减：插入到第 k 位 → 第 k 位及其后各项 +1；删除 → 其后各项重新连续编号（后端保证）。
 *
 * 其余入口：
 * - 重命名弹窗：含「章名 + 卷名」，卷名默认填当前章所属卷名（即默认不修改卷名）；
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

/** 插入章的目标卷 = 当前章所属卷（大纲节点上没有「当前章」，故不支持插入章节） */
const targetVolume = computed<Volume | null>(() => current.value?.volume ?? null)

/** 是否停在大纲节点（大纲或尚未打开任何章节） */
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
/** volume：ordinal=卷序号、title=卷名、firstChapter=第一章章节名；chapter：只用 title */
const form = ref({ ordinal: '', title: '', firstChapter: '' })

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

/** 卷总数（插入卷时的序号上限 = N+1） */
const volumeCount = computed(() => volumes.value.length)

/**
 * 自动推算「插入卷」的落位序号：
 * - 大纲节点 → 大纲在最前头，往后插入即落在第 1 位；
 * - 章节节点 → 往前 = 当前卷序号，往后 = 当前卷序号 + 1。
 */
function volumeOrdinalFor(d: InsertDir): number {
  if (onOutline.value) return 1
  const vol = current.value?.volume
  if (!vol) return volumeCount.value + 1
  const at = vol.sort_order > 0 ? vol.sort_order : volumeCount.value
  return d === 'before' ? Math.max(1, at) : Math.min(volumeCount.value + 1, at + 1)
}

/**
 * 自动推算「插入章节」的落位序号（★界面上不让用户填，按插入方向直接算）：
 * 往前 = 当前章序号，往后 = 当前章序号 + 1。
 */
function chapterOrdinalFor(d: InsertDir): number | null {
  const cur = current.value
  if (!cur) return null
  const total = cur.volume.chapters.length
  const at = cur.chapter.sort_order > 0 ? cur.chapter.sort_order : cur.index + 1
  return d === 'before' ? Math.max(1, at) : Math.min(total + 1, at + 1)
}

function openInsert(k: InsertKind, d: InsertDir): void {
  if (k === 'chapter' && (!targetVolume.value || !current.value)) {
    ElMessage.warning(t('volumeChapter.insertChapterNeedsChapter'))
    return
  }
  if (k === 'volume' && onOutline.value && d === 'before') {
    // 大纲在最前头，不存在「往前插入卷」
    ElMessage.warning(t('volumeChapter.insertVolumeOutlineAfterOnly'))
    return
  }
  kind.value = k
  dir.value = d
  form.value = {
    ordinal: k === 'volume' ? String(volumeOrdinalFor(d)) : '',
    title: '',
    firstChapter: '',
  }
  dialogVisible.value = true
}

const ordinalError = computed(() =>
  kind.value === 'volume'
    ? validateOrdinal(form.value.ordinal, volumeCount.value, '卷序号')
    : '',
)

const titleError = computed(() => {
  const raw = form.value.title
  if (!raw.trim()) return kind.value === 'volume' ? '卷名不允许为空' : '章节名不允许为空'
  return kind.value === 'volume' ? validateVolumeName(raw) : validateChapterName(raw)
})

/** v1030续：插入卷必须同时给出第一章章节名（★不支持空卷） */
const firstChapterError = computed(() => {
  if (kind.value !== 'volume') return ''
  const raw = form.value.firstChapter
  if (!raw.trim()) return t('volumeChapter.firstChapterRequired')
  return validateChapterName(raw)
})

const canSubmit = computed(
  () => !ordinalError.value && !titleError.value && !firstChapterError.value && !submitting.value,
)

async function submitInsert(): Promise<void> {
  if (!canSubmit.value) return
  const title = form.value.title.trim()
  submitting.value = true
  try {
    if (kind.value === 'volume') {
      if (!project.currentNovelId) return
      const ordinal = Number.parseInt(form.value.ordinal.trim(), 10)
      const vol = await project.createVolume(title, '', ordinal, form.value.firstChapter.trim())
      // 新卷的第一章：直接打开它，便于接着写
      const firstCh = vol.chapters?.[0]
      if (firstCh) reader.openChapter(firstCh.id)
      ElMessage.success(
        t('volumeChapter.volumeInsertedWithChapter', {
          ordinal: vol.sort_order,
          title: vol.title,
          chapter: firstCh?.title ?? form.value.firstChapter.trim(),
        }),
      )
    } else {
      const vol = targetVolume.value
      const ordinal = chapterOrdinalFor(dir.value)
      if (!vol || ordinal == null) return
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
    <!-- 往前插入小说卷（v1030续：大纲节点不支持，只有定位到章节时可用） -->
    <el-tooltip
      v-if="!onOutline"
      :content="t('volumeChapter.insertBeforeVolume')"
      placement="bottom"
    >
      <button class="act-btn" @click="openInsert('volume', 'before')">
        <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true">
          <path d="M4 5h16v2H4V5zm0 4h7v2H4V9zm0 4h7v2H4v-2zm0 4h16v2H4v-2zm11-8h2v3h3v2h-3v3h-2v-3h-3v-2h3V9z" />
        </svg>
      </button>
    </el-tooltip>

    <!-- 往后插入小说卷（大纲节点也支持：大纲在最前头，往后插入即成为第一卷） -->
    <el-tooltip :content="t('volumeChapter.insertAfterVolume')" placement="bottom">
      <button class="act-btn" @click="openInsert('volume', 'after')">
        <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true">
          <path d="M4 5h16v2H4V5zm0 4h16v2H4V9zm0 8h7v2H4v-2zm11-4h2v3h3v2h-3v3h-2v-3h-3v-2h3v-3zM4 17h7v2H4v-2z" />
        </svg>
      </button>
    </el-tooltip>

    <!-- v1030续：插入章节只在定位到某一章时可用 -->
    <template v-if="!onOutline">
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
    </template>

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

    <!-- ============ 插入弹窗（输入框无提示词） ============ -->
    <el-dialog v-model="dialogVisible" :title="dialogTitle" width="440px">
      <el-form label-width="96px" @submit.prevent>
        <!-- 插入卷：卷序号（自动算好，可改）+ 卷名 + 第一章章节名 -->
        <template v-if="kind === 'volume'">
          <el-form-item :label="t('volumeChapter.ordinalLabel')" required>
            <el-input v-model="form.ordinal" class="ordinal-input" />
            <div v-if="ordinalError" class="field-error">{{ ordinalError }}</div>
            <div v-else class="field-hint">{{ t('volumeChapter.ordinalAutoHint') }}</div>
          </el-form-item>

          <el-form-item :label="t('volumeChapter.volumeTitleLabel')" required>
            <el-input v-model="form.title" maxlength="200" />
            <div v-if="form.title && titleError" class="field-error">{{ titleError }}</div>
          </el-form-item>

          <el-form-item :label="t('volumeChapter.firstChapterLabel')" required>
            <el-input v-model="form.firstChapter" maxlength="200" />
            <div v-if="firstChapterError" class="field-error">{{ firstChapterError }}</div>
            <div v-else class="field-hint">{{ t('volumeChapter.noEmptyVolumeHint') }}</div>
          </el-form-item>
        </template>

        <!-- 插入章节：只填章节名，序号自动增减 -->
        <template v-else>
          <el-form-item :label="t('volumeChapter.chapterTitleLabel')" required>
            <el-input v-model="form.title" maxlength="200" />
            <div v-if="form.title && titleError" class="field-error">{{ titleError }}</div>
          </el-form-item>

          <div v-if="targetVolume" class="muted text-sm target-hint">
            {{ t('volumeChapter.volumeTitleLabel') }}：{{
              formatVolumeTitle(targetVolume.title, targetVolume.sort_order, 0)
            }}
          </div>
          <div class="muted text-sm target-hint">
            {{ t('volumeChapter.chapterOrdinalAutoHint') }}
          </div>
        </template>
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
      <el-form label-width="96px" @submit.prevent>
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
  padding-left: 96px;
  line-height: 1.7;
}
</style>
