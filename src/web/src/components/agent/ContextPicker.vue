<script setup lang="ts">
/**
 * 「＋」添加内容浮层（v1029 需求 7）。
 *
 * 结构（自上而下）：
 *   1) 搜索框：检索定位**应用内小说项目目录**（大纲/摘要全文检索 + 项目名过滤）；
 *   2) [小说项目大纲或文章]：点进去 → 小说列表 → 大纲与卷列表 → 卷下章节列表
 *      （★逐级跳转，跳转过程中不提供返回）；
 *   3) [本地文件]：只允许**单个**文件，格式 Markdown / TXT / JSON，单文件 ≤5MB，
 *      所有已添加文件合计 ≤20MB（超限提示），问答结束后由父级释放缓存；
 *   4) 最近打开的小说章节（最多 10 条）。
 *
 * v1029：删除原「支持文本类文件（txt/md/json/csv/yaml…）」提示（约束改为隐藏校验）。
 */
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { contentApi, outlineApi, searchApi } from '@/api'
import { errorMessage } from '@/api/http'
import { useProjectStore } from '@/stores/project'
import { listRecentChapters } from '@/utils/recent'
import { t } from '@/i18n'
import type { ContextBlock, Novel, Volume } from '@/types'

const props = defineProps<{
  /** 当前已添加的文件字节数合计（父级持有，用于 20MB 上限） */
  attachedBytes: number
}>()
const emit = defineEmits<{
  (e: 'add', block: ContextBlock): void
  (e: 'close'): void
}>()

const project = useProjectStore()

/** 浮层内的层级：novels（小说列表）→ novel（大纲+卷）→ volume（章节列表） */
type Level = 'root' | 'novels' | 'novel' | 'volume'
const level = ref<Level>('root')

// ---------------------------------------------------------------------------
// 搜索（检索定位应用内小说项目目录）
// ---------------------------------------------------------------------------

const keyword = ref('')
const searching = ref(false)
const searchHits = ref<SearchHit[]>([])

interface SearchHit {
  novelTitle: string
  /** 摘要命中的 doc_ref 为 `summaries/ch-{chapterId}.md`，可解析出章节 id */
  chapterId?: string
  title: string
  snippet: string
  kind: 'outline' | 'summary'
}

/** 项目名匹配（无关键字时不显示） */
const novelHits = computed(() => {
  const q = keyword.value.trim().toLowerCase()
  if (!q) return []
  return project.novels.filter(
    (n) =>
      n.title.toLowerCase().includes(q) ||
      (n.author_name || '').toLowerCase().includes(q) ||
      (n.genre || '').toLowerCase().includes(q),
  )
})

/** 从摘要 doc_ref 解析章节 id：`summaries/ch-{id}.md` → `{id}` */
function chapterIdOf(docRef: string): string | undefined {
  const m = /^summaries\/ch-(.+)\.md$/.exec(docRef)
  return m ? m[1] : undefined
}

async function doSearch(): Promise<void> {
  const q = keyword.value.trim()
  if (!q) {
    searchHits.value = []
    return
  }
  searching.value = true
  try {
    const hits: SearchHit[] = []
    // 当前项目内做全文检索（大纲 + 章节摘要）
    if (project.currentNovelId) {
      const res = await searchApi.search(project.currentNovelId, q, 'all', 8)
      for (const item of res.items ?? []) {
        hits.push({
          novelTitle: project.currentNovel?.title ?? '',
          chapterId: item.kind === 'summary' ? chapterIdOf(item.doc_ref) : undefined,
          title: item.title || item.doc_ref,
          snippet: (item.snippet || '').slice(0, 80),
          kind: item.kind,
        })
      }
    }
    searchHits.value = hits
  } catch (err) {
    ElMessage.error(errorMessage(err))
  } finally {
    searching.value = false
  }
}

// ---------------------------------------------------------------------------
// 逐级下钻：小说列表 → 大纲/卷 → 章节
// ---------------------------------------------------------------------------

const activeNovel = ref<Novel | null>(null)
const activeVolumes = ref<Volume[]>([])
const activeVolume = ref<Volume | null>(null)
const drilling = ref(false)

async function enterNovels(): Promise<void> {
  // 需求 7-(1.3)：跳转到小说目录（跳转过程中不需要返回）
  if (!project.novels.length) await project.loadNovels().catch(() => undefined)
  level.value = 'novels'
}

async function enterNovel(novel: Novel): Promise<void> {
  drilling.value = true
  try {
    // 该小说的卷章树（大纲单独作为第一个条目展示）
    const prevId = project.currentNovelId
    if (prevId !== novel.id) await project.selectNovel(novel.id)
    activeNovel.value = novel
    activeVolumes.value = project.volumeTree
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

// ---------------------------------------------------------------------------
// 添加：大纲 / 章节
// ---------------------------------------------------------------------------

async function addOutline(): Promise<void> {
  const novel = activeNovel.value ?? project.currentNovel
  if (!novel) return
  try {
    const o = await outlineApi.read(novel.id)
    emit('add', { kind: 'outline', label: `${novel.title} · ${t('agent.ctxOutline')}`, text: o.content })
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
  if (!hit.chapterId) {
    // 命中大纲（或摘要无法定位到章）：把整份大纲加进来
    const novelId = project.currentNovelId
    if (!novelId) return
    const o = await outlineApi.read(novelId)
    emit('add', { kind: 'outline', label: `${hit.novelTitle} · ${t('agent.ctxOutline')}`, text: o.content })
    emit('close')
    return
  }
  await addChapter(hit.chapterId, hit.title)
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
</script>

<template>
  <div class="ctx-picker">
    <!-- ① 搜索框：检索定位应用内小说项目目录 -->
    <div class="picker-search">
      <el-input
        v-model="keyword"
        size="small"
        :placeholder="t('agent.pickerSearchPlaceholder')"
        clearable
        @input="doSearch"
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
        <div v-if="novelHits.length" class="picker-label">{{ t('agent.pickerNovelMatches') }}</div>
        <button
          v-for="n in novelHits"
          :key="n.id"
          class="picker-item"
          @click="enterNovel(n)"
        >
          <span class="pi-title">{{ n.title }}</span>
          <span v-if="n.genre" class="pi-meta">{{ n.genre }}</span>
        </button>

        <div v-if="searchHits.length" class="picker-label">{{ t('agent.pickerContentMatches') }}</div>
        <button
          v-for="(h, i) in searchHits"
          :key="i"
          class="picker-item"
          @click="addSearchHit(h)"
        >
          <span class="pi-title">{{ h.title }}</span>
          <span v-if="h.snippet" class="pi-meta pi-snippet">{{ h.snippet }}</span>
        </button>

        <div v-if="!novelHits.length && !searchHits.length && !searching" class="muted text-sm picker-empty">
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

      <!-- 小说列表 -->
      <template v-else-if="level === 'novels'">
        <div class="picker-label">{{ t('agent.pickerChooseNovel') }}</div>
        <button
          v-for="n in project.novels"
          :key="n.id"
          class="picker-item"
          :disabled="drilling"
          @click="enterNovel(n)"
        >
          <span class="pi-title">{{ n.title }}</span>
          <span class="pi-meta">{{ n.chapter_count }}{{ t('sidebar.chaptersUnit') }}</span>
        </button>
      </template>

      <!-- 某本小说：大纲 + 卷列表 -->
      <template v-else-if="level === 'novel' && activeNovel">
        <div class="picker-label">{{ activeNovel.title }}</div>
        <button class="picker-item picker-item-strong" @click="addOutline">
          <span class="pi-title">{{ t('agent.ctxOutline') }}</span>
          <span class="pi-meta">{{ t('agent.pickerAddHint') }}</span>
        </button>

        <div class="picker-label">{{ t('agent.pickerVolumes') }}</div>
        <button
          v-for="v in activeVolumes"
          :key="v.id"
          class="picker-item"
          @click="enterVolume(v)"
        >
          <span class="pi-title">第{{ v.sort_order }}卷 {{ v.title }}</span>
          <span class="pi-meta">{{ v.chapter_count }}{{ t('sidebar.chaptersUnit') }}</span>
        </button>
        <div v-if="!activeVolumes.length" class="muted text-sm picker-empty">
          {{ t('common.empty') }}
        </div>
      </template>

      <!-- 某卷：章节列表 -->
      <template v-else-if="level === 'volume' && activeVolume">
        <div class="picker-label">{{ activeVolume.title }}</div>
        <button
          v-for="c in activeVolume.chapters"
          :key="c.id"
          class="picker-item"
          @click="addChapter(c.id, `${activeNovel?.title ?? ''} · ${c.title}`.replace(/^ · /, ''))"
        >
          <span class="pi-title">第{{ c.sort_order }}章 {{ c.title }}</span>
          <span class="pi-meta">{{ c.word_count }}{{ t('sidebar.wordsUnit') }}</span>
        </button>
        <div v-if="!activeVolume.chapters.length" class="muted text-sm picker-empty">
          {{ t('common.empty') }}
        </div>
      </template>
    </template>

    <!-- ④ 最近打开的小说章节（最多 10 条） -->
    <template v-if="level === 'root' && recents.length">
      <div class="picker-label">{{ t('agent.pickerRecent') }}</div>
      <button
        v-for="r in recents"
        :key="r.chapterId"
        class="picker-item"
        @click="addChapter(r.chapterId, `${r.novelTitle} · ${r.chapterTitle}`)"
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
  font-size: 11px;
  color: var(--text-muted);
}

.pi-snippet {
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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
