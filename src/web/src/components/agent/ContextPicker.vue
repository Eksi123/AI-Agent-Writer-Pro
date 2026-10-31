<script setup lang="ts">
/**
 * 「＋」添加内容浮层：搜索 + 逐级目录 + 本地文件 + 最近打开章节。
 *
 * - 搜索框直接搜大纲 / 章节（关键字匹配，小字标所属项目或卷），清空即回初始界面；
 * - 目录逐级跳转（项目 → 大纲/各卷 → 章节），不提供返回，无卷则不显示卷；
 * - 本地文件限单个 Markdown / TXT / JSON ≤5MB，合计 ≤20MB，问答结束后由父级释放；
 * - 最近打开章节最多 10 条；点击即添加并收起，输入框上方出现已添加标记；
 *   每次重新打开都回到初始界面。
 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { contentApi, outlineApi, searchApi, volumesApi } from '@/api'
import { errorMessage } from '@/api/http'
import { useProjectStore } from '@/stores/project'
import { listRecentChapters } from '@/utils/recent'
import { formatChapterTitle, formatOutlineNode, formatVolumeTitle } from '@/utils/titles'
import { t } from '@/i18n'
import type { ContextBlock, Novel, Volume } from '@/types'

const props = defineProps<{
  /** 当前已添加的文件字节数合计（父级持有，用于 20MB 上限） */
  attachedBytes: number
  /** 浮层是否打开：关闭再打开时重置到初始界面 */
  open: boolean
}>()
const emit = defineEmits<{
  (e: 'add', block: ContextBlock): void
  (e: 'close'): void
}>()

const project = useProjectStore()

/** 浮层内的层级：novels（小说项目）→ novel（大纲+卷）→ volume（章节列表） */
type Level = 'root' | 'novels' | 'novel' | 'volume'
const level = ref<Level>('root')

// ---------------------------------------------------------------------------
// 搜索：关键字匹配小说大纲 / 小说章节（跨项目）
// ---------------------------------------------------------------------------

const keyword = ref('')
const searching = ref(false)
const searchHits = ref<SearchHit[]>([])

interface SearchHit {
  key: string
  /** 展示标题：大纲 = 「小说名_大纲」；章节 = 「第X章 章名」 */
  title: string
  /** 小字归属标记：大纲 = 小说项目；章节 = 小说项目/卷 */
  meta: string
  kind: 'outline' | 'chapter'
  novelId: string
  chapterId?: string
}

const SEARCH_LIMIT_PER_NOVEL = 12

/** 摘要命中的 doc_ref 为 `summaries/ch-{chapterId}.md`，可解析出章节 id */
function chapterIdOf(docRef: string): string | undefined {
  const m = /^summaries\/ch-(.+)\.md$/.exec(docRef)
  return m ? m[1] : undefined
}

/** 单个项目内的关键字匹配：大纲正文 + 章节标题 + 章节摘要 */
async function searchNovel(novel: Novel, q: string): Promise<SearchHit[]> {
  const hits: SearchHit[] = []
  const seen = new Set<string>()
  const lower = q.toLowerCase()

  // 卷章树：章节标题匹配 + 摘要命中回落到章节
  let volumes: Volume[] = []
  try {
    volumes = await volumesApi.list(novel.id)
  } catch {
    /* 卷章读取失败时仍可用大纲检索结果 */
  }

  const chapterById = new Map<string, { vol: Volume; ch: Volume['chapters'][number]; index: number }>()
  volumes.forEach((vol, vi) => {
    vol.chapters.forEach((ch, ci) => chapterById.set(ch.id, { vol, ch, index: ci }))
    // 章节标题关键字匹配
    vol.chapters.forEach((ch, ci) => {
      if (!ch.title.toLowerCase().includes(lower)) return
      if (seen.has(ch.id)) return
      seen.add(ch.id)
      hits.push({
        key: `${novel.id}:${ch.id}`,
        title: formatChapterTitle(ch.title, ch.sort_order, ci),
        meta: `${novel.title}/${formatVolumeTitle(vol.title, vol.sort_order, vi)}`,
        kind: 'chapter',
        novelId: novel.id,
        chapterId: ch.id,
      })
    })
  })

  // 大纲正文 / 章节摘要（后端 FTS5，不可用时 LIKE 降级）
  try {
    const res = await searchApi.search(novel.id, q, 'all', SEARCH_LIMIT_PER_NOVEL)
    for (const item of res.items ?? []) {
      if (item.kind === 'outline') {
        if (seen.has('outline')) continue
        seen.add('outline')
        hits.unshift({
          key: `${novel.id}:outline`,
          title: formatOutlineNode(novel.title),
          meta: novel.title,
          kind: 'outline',
          novelId: novel.id,
        })
        continue
      }
      const cid = chapterIdOf(item.doc_ref)
      const found = cid ? chapterById.get(cid) : undefined
      if (!cid || !found || seen.has(cid)) continue
      seen.add(cid)
      const vi = volumes.findIndex((v) => v.id === found.vol.id)
      hits.push({
        key: `${novel.id}:${cid}`,
        title: formatChapterTitle(found.ch.title, found.ch.sort_order, found.index),
        meta: `${novel.title}/${formatVolumeTitle(found.vol.title, found.vol.sort_order, Math.max(vi, 0))}`,
        kind: 'chapter',
        novelId: novel.id,
        chapterId: cid,
      })
    }
  } catch {
    /* 检索失败不阻断（章节标题匹配结果仍可用） */
  }

  return hits
}

/** 搜索防抖：关键字输入停顿后再发请求（避免每键一次跨项目检索） */
let searchTimer: ReturnType<typeof setTimeout> | null = null

function cancelPendingSearch(): void {
  if (searchTimer) {
    clearTimeout(searchTimer)
    searchTimer = null
  }
}

function doSearch(): void {
  const q = keyword.value.trim()
  cancelPendingSearch()
  if (!q) {
    // v1030：清空关键字 → 回到原来的状态（不留在搜索结果列表）
    searchHits.value = []
    searching.value = false
    return
  }
  searching.value = true
  searchTimer = setTimeout(() => void runSearch(q), 250)
}

async function runSearch(q: string): Promise<void> {
  searchTimer = null
  // 防抖期间用户已改关键字 / 清空 → 丢弃这次结果
  if (keyword.value.trim() !== q) return
  try {
    if (!project.novels.length) await project.loadNovels().catch(() => undefined)
    const perNovel = await Promise.all(project.novels.map((n) => searchNovel(n, q)))
    if (keyword.value.trim() === q) searchHits.value = perNovel.flat().slice(0, 30)
  } catch (err) {
    ElMessage.error(errorMessage(err))
  } finally {
    if (keyword.value.trim() === q) searching.value = false
  }
}

/** 搜索结果里的「清空」：手动删除内容或点清空 icon 都回到初始界面 */
function clearSearch(): void {
  cancelPendingSearch()
  keyword.value = ''
  searchHits.value = []
  searching.value = false
}

// ---------------------------------------------------------------------------
// 逐级下钻：小说项目 → 大纲/卷 → 章节
// ---------------------------------------------------------------------------

const activeNovel = ref<Novel | null>(null)
const activeVolumes = ref<Volume[]>([])
const activeVolume = ref<Volume | null>(null)
const drilling = ref(false)

async function enterNovels(): Promise<void> {
  if (!project.novels.length) await project.loadNovels().catch(() => undefined)
  level.value = 'novels'
}

async function enterNovel(novel: Novel): Promise<void> {
  drilling.value = true
  try {
    const volumes = await volumesApi.list(novel.id)
    activeNovel.value = novel
    // v1030：有卷代表至少有一章节内容——空卷不展示，避免进入后无内容可选
    activeVolumes.value = [...volumes]
      .sort((a, b) => a.sort_order - b.sort_order)
      .filter((v) => v.chapters.length > 0)
    level.value = 'novel'
  } catch (err) {
    ElMessage.error(errorMessage(err))
  } finally {
    drilling.value = false
  }
}

function enterVolume(vol: Volume): void {
  activeVolume.value = vol
  level.value = 'volume'
}

/** 章节按 sort_order 只读排序（★顺序不可拖拽重排） */
function sortedChapters(vol: Volume): Volume['chapters'] {
  return [...vol.chapters].sort((a, b) => a.sort_order - b.sort_order)
}

// ---------------------------------------------------------------------------
// 添加：大纲 / 章节
// ---------------------------------------------------------------------------

async function addOutline(novel: Novel): Promise<void> {
  try {
    const o = await outlineApi.read(novel.id)
    emit('add', { kind: 'outline', label: formatOutlineNode(novel.title), text: o.content })
    emit('close')
  } catch (err) {
    ElMessage.error(errorMessage(err))
  }
}

async function addChapter(chapterId: string, label: string): Promise<void> {
  try {
    const c = await contentApi.read(chapterId)
    emit('add', { kind: 'chapter', label, text: c.content })
    emit('close')
  } catch (err) {
    ElMessage.error(errorMessage(err))
  }
}

async function addSearchHit(hit: SearchHit): Promise<void> {
  if (hit.kind === 'outline') {
    const novel = project.novels.find((n) => n.id === hit.novelId)
    if (novel) await addOutline(novel)
    return
  }
  if (hit.chapterId) await addChapter(hit.chapterId, hit.title)
}

// ---------------------------------------------------------------------------
// 本地文件（单文件 / md·txt·json / ≤5MB / 合计 ≤20MB）
// ---------------------------------------------------------------------------

const MAX_SINGLE_BYTES = 5 * 1024 * 1024
const MAX_TOTAL_BYTES = 20 * 1024 * 1024
const ACCEPT_EXT = '.md,.markdown,.txt,.json'
const fileInput = ref<HTMLInputElement | null>(null)

function pickFile(): void {
  fileInput.value?.click()
}

async function onFilePicked(e: Event): Promise<void> {
  const el = e.target as HTMLInputElement
  const file = el.files?.[0]
  el.value = '' // 允许重复选择同一文件
  if (!file) return

  const ext = file.name.slice(file.name.lastIndexOf('.')).toLowerCase()
  if (!['.md', '.markdown', '.txt', '.json'].includes(ext)) {
    ElMessage.warning('只能添加 Markdown / TXT / JSON 文件')
    return
  }
  if (file.size > MAX_SINGLE_BYTES) {
    ElMessage.warning(`单个文件不能超过 5MB（当前 ${(file.size / 1024 / 1024).toFixed(1)}MB）`)
    return
  }
  if (props.attachedBytes + file.size > MAX_TOTAL_BYTES) {
    ElMessage.warning('所有添加的文件合计不能超过 20MB，请先移除部分文件')
    return
  }
  try {
    const text = await file.text()
    emit('add', { kind: 'file', label: file.name, text, bytes: file.size })
    emit('close')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '读取文件失败')
  }
}

// ---------------------------------------------------------------------------
// 最近打开的小说章节（最多 10 条）
// ---------------------------------------------------------------------------

const recents = ref(listRecentChapters())

onMounted(() => {
  recents.value = listRecentChapters()
})
onBeforeUnmount(cancelPendingSearch)

/** v1030：关闭后重新打开 → 回到初始界面（可继续添加其它文件） */
function resetToRoot(): void {
  level.value = 'root'
  clearSearch()
  activeNovel.value = null
  activeVolumes.value = []
  activeVolume.value = null
  recents.value = listRecentChapters()
}

watch(
  () => props.open,
  (open) => {
    if (open) resetToRoot()
  },
)
</script>

<template>
  <div class="ctx-picker">
    <!-- ① 搜索框：关键字匹配小说大纲 / 小说章节 -->
    <div class="picker-search">
      <el-input
        v-model="keyword"
        size="small"
        :placeholder="t('agent.pickerSearchPlaceholder')"
        clearable
        @input="doSearch"
        @clear="clearSearch"
        @keyup.enter="doSearch"
      >
        <template #prefix>
          <svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden="true">
            <path d="M10 3a7 7 0 1 0 4.2 12.6l4.1 4.1 1.4-1.4-4.1-4.1A7 7 0 0 0 10 3zm0 2a5 5 0 1 1 0 10 5 5 0 0 1 0-10z" />
          </svg>
        </template>
      </el-input>

      <div v-if="searching" class="muted text-sm picker-loading">{{ t('common.loading') }}</div>

      <template v-if="keyword.trim()">
        <button
          v-for="h in searchHits"
          :key="h.key"
          class="picker-item picker-item-col"
          @click="addSearchHit(h)"
        >
          <span class="pi-title">{{ h.title }}</span>
          <span class="pi-meta">{{ h.meta }}</span>
        </button>

        <div v-if="!searchHits.length && !searching" class="muted text-sm picker-empty">
          {{ t('agent.pickerNoMatch') }}
        </div>
      </template>
    </div>

    <!-- ② 主体：未搜索时按层级展示 -->
    <template v-if="!keyword.trim()">
      <!-- 根层级：两个入口 -->
      <template v-if="level === 'root'">
        <button class="picker-entry" @click="enterNovels">
          <svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor" aria-hidden="true">
            <path d="M4 3h7a2 2 0 0 1 1 .3A2 2 0 0 1 13 3h7a1 1 0 0 1 1 1v15a1 1 0 0 1-1 1h-7a2 2 0 0 0-1 .5 2 2 0 0 0-1-.5H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1zm7 15v-2a1 1 0 0 1 1-1h7V5h-6a1 1 0 0 0-1 1v11a1 1 0 0 1-1 1zm-1 0H5V5h6v13z" />
          </svg>
          <span>{{ t('agent.ctxNovelContent') }}</span>
        </button>

        <button class="picker-entry" @click="pickFile">
          <svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor" aria-hidden="true">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6zm-1 7V3.5L18.5 9H13z" />
          </svg>
          <span>{{ t('agent.ctxLocalFile') }}</span>
        </button>
      </template>

      <!-- 小说项目（v1030：不显示「选择小说项目」提示，也不显示「X章」） -->
      <template v-else-if="level === 'novels'">
        <button
          v-for="n in project.novels"
          :key="n.id"
          class="picker-item"
          :disabled="drilling"
          @click="enterNovel(n)"
        >
          <span class="pi-title">{{ n.title }}</span>
        </button>
        <!-- 没有小说项目：居中提示 -->
        <div v-if="!project.novels.length" class="picker-centered muted">
          {{ t('agent.pickerNoNovels') }}
        </div>
      </template>

      <!-- 某本小说：最上面「小说名_大纲」，下面各卷（无卷则不显示，不加多余内容） -->
      <template v-else-if="level === 'novel' && activeNovel">
        <button class="picker-item picker-item-strong" @click="addOutline(activeNovel)">
          <span class="pi-title">{{ formatOutlineNode(activeNovel.title) }}</span>
        </button>

        <button
          v-for="(v, vi) in activeVolumes"
          :key="v.id"
          class="picker-item"
          @click="enterVolume(v)"
        >
          <span class="pi-title">{{ formatVolumeTitle(v.title, v.sort_order, vi) }}</span>
        </button>
      </template>

      <!-- 某卷：章节列表（点击即添加该章节） -->
      <template v-else-if="level === 'volume' && activeVolume">
        <button
          v-for="(c, ci) in sortedChapters(activeVolume)"
          :key="c.id"
          class="picker-item"
          @click="addChapter(c.id, formatChapterTitle(c.title, c.sort_order, ci))"
        >
          <span class="pi-title">{{ formatChapterTitle(c.title, c.sort_order, ci) }}</span>
        </button>
      </template>
    </template>

    <!-- ④ 最近打开的小说章节（最多 10 条） -->
    <template v-if="level === 'root' && !keyword.trim() && recents.length">
      <div class="picker-label">{{ t('agent.pickerRecent') }}</div>
      <button
        v-for="r in recents"
        :key="r.chapterId"
        class="picker-item"
        @click="addChapter(r.chapterId, r.chapterTitle)"
      >
        <span class="pi-title">{{ r.chapterTitle }}</span>
        <span class="pi-meta">{{ r.novelTitle }}</span>
      </button>
    </template>

    <input
      ref="fileInput"
      type="file"
      :accept="ACCEPT_EXT"
      class="hidden-file"
      @change="onFilePicked"
    />
  </div>
</template>

<style scoped>
.ctx-picker {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 340px;
  overflow-y: auto;
}

.picker-search {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding-bottom: 6px;
  border-bottom: 1px dashed var(--border);
}

.picker-entry {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 10px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--text);
  font-size: 13px;
  text-align: left;
  cursor: pointer;
}

.picker-entry:hover {
  background: var(--bg-hover);
  color: var(--el-color-primary);
}

.picker-label {
  padding: 6px 10px 2px;
  font-size: 11px;
  color: var(--text-muted);
  letter-spacing: 0.3px;
}

.picker-item {
  display: flex;
  align-items: baseline;
  gap: 8px;
  width: 100%;
  padding: 6px 10px;
  border: none;
  border-radius: 5px;
  background: transparent;
  color: var(--text);
  font-size: 13px;
  text-align: left;
  cursor: pointer;
}

/* 搜索结果：标题在上，所属「小说项目 / 小说项目/卷」小字在下 */
.picker-item-col {
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
}

.picker-item:hover:not(:disabled) {
  background: var(--bg-hover);
}

.picker-item:disabled {
  opacity: 0.5;
  cursor: progress;
}

.picker-item-strong .pi-title {
  font-weight: 600;
  color: var(--el-color-primary);
}

.pi-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pi-meta {
  flex-shrink: 0;
  max-width: 100%;
  font-size: 11px;
  color: var(--text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 没有小说项目：居中提示 */
.picker-centered {
  padding: 22px 10px;
  text-align: center;
  font-size: 13px;
}

.picker-empty,
.picker-loading {
  padding: 6px 10px;
}

.hidden-file {
  display: none;
}

.muted {
  color: var(--text-muted);
}

.text-sm {
  font-size: 12px;
}
</style>
