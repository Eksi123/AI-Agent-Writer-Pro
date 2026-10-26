<script setup lang="ts">
/**
 * 章节阅读视图：markdown 渲染 + 进度记忆（scroll_ratio）。
 * 支持上一章/下一章翻页（按 sort_order 展平序列）。
 */
import { computed, nextTick, ref, watch } from 'vue'
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'
import { useProjectStore } from '@/stores/project'
import { useReaderStore } from '@/stores/reader'
import { contentApi, progressApi } from '@/api'
import { errorMessage } from '@/api/http'
import { t } from '@/i18n'

const props = defineProps<{ chapterId: string }>()

const project = useProjectStore()
const reader = useReaderStore()

const md = new MarkdownIt({ html: false, linkify: true, breaks: true })

const title = ref('')
const content = ref('')
const loading = ref(false)
const error = ref<string | null>(null)
const scroller = ref<HTMLElement | null>(null)

const html = computed(() => DOMPurify.sanitize(md.render(content.value || '')))

/** 当前章在展平序列中的位置，用于翻页 */
const position = computed(() => {
  const list = project.flatChapters
  const idx = list.findIndex((x) => x.chapter.id === props.chapterId)
  return {
    index: idx,
    total: list.length,
    prev: idx > 0 ? list[idx - 1].chapter.id : null,
    next: idx >= 0 && idx < list.length - 1 ? list[idx + 1].chapter.id : null,
  }
})

async function load(id: string): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const data = await contentApi.read(id)
    title.value = data.title ?? ''
    content.value = data.content
    reader.scrollRatio = 0
    await nextTick()
    // 进入项目时定位到上次位置（进度记忆）
    if (project.currentNovelId) {
      try {
        const progress = await progressApi.get(project.currentNovelId)
        if (progress.chapter_id === id && progress.scroll_ratio > 0 && scroller.value) {
          const target = progress.scroll_ratio * (scroller.value.scrollHeight - scroller.value.clientHeight)
          scroller.value.scrollTop = Math.max(0, target)
        }
      } catch {
        /* 进度读取失败不阻断阅读 */
      }
    }
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    loading.value = false
  }
}

/** 滚动时记录比例（节流），供进度记忆写回 */
let lastSave = 0
function onScroll(): void {
  const el = scroller.value
  if (!el) return
  const max = el.scrollHeight - el.clientHeight
  reader.scrollRatio = max > 0 ? Math.min(1, Math.max(0, el.scrollTop / max)) : 0
  const now = Date.now()
  if (now - lastSave > 3000 && project.currentNovelId) {
    lastSave = now
    void progressApi.save(project.currentNovelId, props.chapterId, reader.scrollRatio).catch(() => {})
  }
}

function go(id: string | null): void {
  if (id) reader.openChapter(id)
}

watch(() => props.chapterId, (id) => void load(id), { immediate: true })
</script>

<template>
  <div class="chapter-reader">
    <div class="row-between reader-bar">
      <div class="row">
        <el-button size="small" :disabled="!position.prev" @click="go(position.prev)">
          {{ t('reader.prevChapter') }}
        </el-button>
        <el-button size="small" :disabled="!position.next" @click="go(position.next)">
          {{ t('reader.nextChapter') }}
        </el-button>
      </div>
      <div class="row">
        <span class="muted text-sm" v-if="position.total">
          {{ position.index + 1 }} / {{ position.total }}
        </span>
        <el-button size="small" text @click="reader.toggleNight()">
          {{ t('reader.nightMode') }}
        </el-button>
      </div>
    </div>

    <div ref="scroller" class="scroll-y flex-fill reader-scroll" :class="{ night: reader.nightMode }" @scroll="onScroll">
      <el-skeleton v-if="loading" :rows="6" animated class="pad" />
      <el-alert v-else-if="error" :title="error" type="error" :closable="false" class="pad" />
      <article v-else class="novel-prose">
        <h1>{{ title }}</h1>
        <div v-if="!content.trim()" class="muted">{{ t('reader.emptyChapter') }}</div>
        <!-- eslint-disable-next-line vue/no-v-html -->
        <div v-else v-html="html" />
      </article>
    </div>
  </div>
</template>

<style scoped>
.chapter-reader {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.reader-bar {
  padding: 8px 16px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

.reader-scroll {
  background: var(--bg);
}

.pad {
  padding: 24px 32px;
}
</style>
