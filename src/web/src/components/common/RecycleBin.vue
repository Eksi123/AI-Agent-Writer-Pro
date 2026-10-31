<script setup lang="ts">
/**
 * ★v1031：回收站抽屉（入口在顶部工具栏「AI写作助手」的左边）。
 *
 * 两级视图：
 *   1) 近 20 本「删除过章节」的小说（也包括整本被删除的小说）；
 *   2) 点某本 → 它的目录：卷 → 该卷下被删除的章节；
 * 点击某章节 → 询问弹窗（左「回收」/ 右「删除」，删除为红色警示）：
 *   - 回收：还原该章节（整本被删的小说会**同时恢复小说项目与它的大纲**）；
 *   - 删除：彻底删除，不可恢复。
 */
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { recycleApi } from '@/api'
import { errorMessage } from '@/api/http'
import { t } from '@/i18n'
import { formatChapterTitle, formatVolumeTitle } from '@/utils/titles'
import type { RecycledContents, RecycledNovel } from '@/types'

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  /** 整本小说被恢复 → 父级刷新左边栏项目列表与目录 */
  (e: 'novel-restored', novelId: string): void
  /** 回收了某个章节 → 父级刷新当前项目的卷章树 */
  (e: 'chapter-restored', novelId: string): void
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const loading = ref(false)
const novels = ref<RecycledNovel[]>([])
/** 当前进入二级视图的小说（null = 还在小说列表） */
const current = ref<RecycledContents | null>(null)
/** 已展开的卷（默认全展开，便于直接看到被删章节） */
const collapsed = ref<Record<string, boolean>>({})

async function loadNovels(): Promise<void> {
  loading.value = true
  try {
    const res = await recycleApi.list()
    // ★v1031 续：后端缺路由时 SPA 回落会返回页面（http.ts 已拦成中文报错）；
    // 这里再兜一层，任何非数组载荷都按「空回收站」展示，绝不崩成白屏。
    novels.value = Array.isArray(res?.items) ? res.items : []
  } catch (err) {
    novels.value = []
    ElMessage.error(errorMessage(err) || t('recycle.loadFailed'))
  } finally {
    loading.value = false
  }
}

async function openNovel(novel: RecycledNovel): Promise<void> {
  loading.value = true
  try {
    const data = await recycleApi.contents(novel.novel_id)
    current.value = { ...data, volumes: Array.isArray(data?.volumes) ? data.volumes : [] }
    collapsed.value = {}
  } catch (err) {
    ElMessage.error(errorMessage(err) || t('recycle.loadFailed'))
  } finally {
    loading.value = false
  }
}

function backToList(): void {
  current.value = null
  void loadNovels()
}

function isCollapsed(volId: string): boolean {
  return collapsed.value[volId] === true
}

function toggleVolume(volId: string): void {
  collapsed.value = { ...collapsed.value, [volId]: !isCollapsed(volId) }
}

/** 重新载入二级视图（回收/删除后），空了则回到小说列表 */
async function reloadCurrent(): Promise<void> {
  const novelId = current.value?.novel_id
  if (!novelId) return
  try {
    // ★v1031 续：先查列表确认该小说还在回收站——直接 GET 详情会在「条目刚被移除」时
    // 产生一次 404（浏览器控制台会记一条网络错误），先查列表可避免这次噪音请求。
    const list = await recycleApi.list()
    if (!list.items.some((n) => n.novel_id === novelId)) {
      backToList()
      return
    }
    const next = await recycleApi.contents(novelId)
    const vols = Array.isArray(next?.volumes) ? next.volumes : []
    if ((next?.total_chapters ?? 0) <= 0 && !next?.novel_deleted) {
      backToList()
      return
    }
    current.value = { ...next, volumes: vols }
  } catch {
    // 整本恢复后该小说已不在回收站里 → 回到列表
    backToList()
  }
}

/**
 * 点击章节 → 询问弹窗：左「回收」，右「删除」（红色警示）。
 * ElMessageBox 的 confirm 按钮在右、cancel 在左，故 cancel=回收、confirm=删除。
 */
async function onChapterClick(chapterId: string, chapterTitle: string): Promise<void> {
  const novel = current.value
  if (!novel) return
  const display = chapterTitle || '未命名章节'
  let action: 'restore' | 'purge'
  try {
    await ElMessageBox.confirm(
      novel.novel_deleted
        ? `${t('recycle.askMessage', { title: display })}\n（这本小说已整本删除，回收任一章节会连同小说项目与大纲一起恢复）`
        : t('recycle.askMessage', { title: display }),
      t('recycle.askTitle'),
      {
        confirmButtonText: t('recycle.purge'),
        cancelButtonText: t('recycle.restore'),
        confirmButtonClass: 'el-button--danger recycle-purge-btn',
        type: 'warning',
        distinguishCancelAndClose: true,
      },
    )
    action = 'purge'
  } catch (err) {
    // cancel = 点了「回收」；close/esc = 取消操作，什么都不做
    if (err !== 'cancel') return
    action = 'restore'
  }

  try {
    if (action === 'restore') {
      const res = await recycleApi.restore(novel.novel_id, chapterId)
      ElMessage.success(
        res.novel_restored
          ? t('recycle.restoredNovel', { title: novel.title })
          : t('recycle.restored', { title: display }),
      )
      // 整本恢复 → 父级刷新左边栏项目；只回收章节 → 父级刷新卷章树
      if (res.novel_restored) emit('novel-restored', novel.novel_id)
      else emit('chapter-restored', novel.novel_id)
    } else {
      await recycleApi.purge(novel.novel_id, chapterId)
      ElMessage.success(t('recycle.purged', { title: display }))
    }
  } catch (err) {
    ElMessage.error(errorMessage(err))
  }
  // 整本恢复后这本小说已离开回收站；否则留在当前小说目录里刷新
  if (current.value?.novel_deleted) backToList()
  else await reloadCurrent()
}

watch(visible, (v) => {
  if (!v) return
  current.value = null
  void loadNovels()
})
</script>

<template>
  <el-drawer v-model="visible" :title="t('recycle.title')" size="380px" direction="rtl">
    <!-- 一级：小说列表 -->
    <template v-if="!current">
      <div v-if="loading && !novels.length" class="recycle-empty muted text-sm">…</div>
      <div v-else-if="!novels.length" class="recycle-empty muted text-sm">
        {{ t('recycle.empty') }}
      </div>
      <div v-else class="recycle-list scroll-y">
        <div
          v-for="n in novels"
          :key="n.novel_id"
          class="recycle-novel"
          @click="openNovel(n)"
        >
          <span class="novel-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
              <path d="M6 2h9a3 3 0 0 1 3 3v16a2 2 0 0 0-2-2H6a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1zm0 18h10v1a1 1 0 0 1-1 1H6a1 1 0 0 1 0-2zm3-15h5v2H9V5z" />
            </svg>
          </span>
          <span class="novel-meta">
            <span class="novel-title">{{ n.title }}</span>
            <span class="novel-sub muted text-sm">
              <el-tag v-if="n.novel_deleted" size="small" type="danger" effect="plain">
                {{ t('recycle.novelDeletedTag') }}
              </el-tag>
              {{ t('recycle.chaptersUnit') }} {{ n.chapter_count }}
            </span>
          </span>
        </div>
      </div>
    </template>

    <!-- 二级：卷 → 被删章节 -->
    <template v-else>
      <div class="recycle-head row">
        <el-button text size="small" @click="backToList">
          <svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor" aria-hidden="true">
            <path d="M15.4 4.6a1 1 0 0 0-1.4 0l-6.7 6.7a1 1 0 0 0 0 1.4l6.7 6.7a1 1 0 0 0 1.4-1.4L9.4 12l6-6a1 1 0 0 0 0-1.4z" />
          </svg>
          {{ t('recycle.back') }}
        </el-button>
        <span class="recycle-novel-title">{{ current.title }}</span>
      </div>
      <div class="muted text-sm recycle-total">
        {{ t('recycle.chapterCount', { n: current.total_chapters }) }}
        <el-tag v-if="current.novel_deleted" size="small" type="danger" effect="plain">
          {{ t('recycle.novelDeletedTag') }}
        </el-tag>
      </div>

      <div v-if="loading" class="recycle-empty muted text-sm">…</div>
      <div v-else-if="!current.volumes.length" class="recycle-empty muted text-sm">
        {{ t('recycle.empty') }}
      </div>
      <div v-else class="recycle-tree scroll-y">
        <div v-for="(vol, vi) in current.volumes" :key="vol.id" class="recycle-vol-block">
          <div class="recycle-node recycle-vol" @click="toggleVolume(vol.id)">
            <svg
              class="chev"
              :class="{ collapsed: isCollapsed(vol.id) }"
              viewBox="0 0 24 24"
              width="13"
              height="13"
              fill="currentColor"
              aria-hidden="true"
            >
              <path d="M8.6 4.6 14.3 10.3l-5.7 5.7-1.4-1.4L11.5 12 7.2 7.7z" />
            </svg>
            <span class="vol-title">
              {{ formatVolumeTitle(vol.title, vol.sort_order, vi) }}
            </span>
            <span class="muted text-sm">{{ vol.chapter_count }}</span>
          </div>

          <template v-if="!isCollapsed(vol.id)">
            <div
              v-for="(ch, ci) in vol.chapters"
              :key="ch.id"
              class="recycle-node recycle-chapter"
              @click="onChapterClick(ch.id, ch.title)"
            >
              <span class="ch-title">
                {{ formatChapterTitle(ch.title, ch.sort_order, ci) }}
              </span>
              <span class="muted text-sm ch-words">{{ ch.word_count }}{{ t('sidebar.wordsUnit') }}</span>
            </div>
          </template>
        </div>
      </div>
    </template>
  </el-drawer>
</template>

<style scoped>
.recycle-empty {
  padding: 24px 4px;
  text-align: center;
}

.recycle-list,
.recycle-tree {
  display: flex;
  flex-direction: column;
  gap: 2px;
  max-height: 100%;
}

.recycle-novel {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s;
}

.recycle-novel:hover {
  background: var(--bg-hover);
}

.novel-icon {
  display: inline-flex;
  color: var(--text-muted);
  flex-shrink: 0;
}

.novel-meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.novel-title {
  font-size: 13.5px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.novel-sub {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.recycle-head {
  gap: 4px;
  flex-shrink: 0;
}

.recycle-novel-title {
  font-size: 14px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.recycle-total {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 0 4px 8px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 8px;
  flex-shrink: 0;
}

.recycle-vol-block {
  display: flex;
  flex-direction: column;
}

.recycle-node {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 8px;
  border-radius: 6px;
  cursor: pointer;
  min-width: 0;
}

.recycle-node:hover {
  background: var(--bg-hover);
}

.recycle-vol {
  font-size: 13px;
  font-weight: 600;
}

.recycle-vol .chev {
  flex-shrink: 0;
  transition: transform 0.15s;
}

.recycle-vol .chev.collapsed {
  transform: rotate(0deg);
}

.vol-title,
.ch-title {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}

.recycle-chapter {
  padding-left: 26px;
  font-size: 12.5px;
}

.ch-words {
  margin-left: auto;
  flex-shrink: 0;
}
</style>

<style>
/* 询问弹窗右侧「删除」按钮：红色字体警示（v1031 需求） */
.recycle-purge-btn {
  color: var(--el-color-danger) !important;
  border-color: var(--el-color-danger) !important;
  font-weight: 600;
}

.recycle-purge-btn:hover,
.recycle-purge-btn:focus {
  color: #fff !important;
  background: var(--el-color-danger) !important;
}
</style>
