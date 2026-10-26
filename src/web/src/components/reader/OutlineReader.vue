<script setup lang="ts">
/**
 * 大纲阅读视图：markdown 渲染（只读）。编辑入口在编辑模式的 OutlineEditor。
 */
import { computed, onMounted, ref, watch } from 'vue'
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'
import { outlineApi } from '@/api'
import { errorMessage } from '@/api/http'
import { useProjectStore } from '@/stores/project'
import { t } from '@/i18n'

const project = useProjectStore()
const md = new MarkdownIt({ html: false, linkify: true })

const content = ref('')
const loading = ref(false)
const error = ref<string | null>(null)

const html = computed(() => DOMPurify.sanitize(md.render(content.value || '')))

async function load(): Promise<void> {
  if (!project.currentNovelId) return
  loading.value = true
  error.value = null
  try {
    const data = await outlineApi.read(project.currentNovelId)
    content.value = data.content
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(() => project.currentNovelId, load)
</script>

<template>
  <div class="outline-reader scroll-y">
    <el-skeleton v-if="loading" :rows="8" animated class="novel-prose" />
    <el-alert v-else-if="error" :title="error" type="error" :closable="false" class="pad" />
    <article v-else class="novel-prose">
      <div v-if="!content.trim()" class="muted">{{ t('common.empty') }}</div>
      <!-- eslint-disable-next-line vue/no-v-html -->
      <div v-else v-html="html" />
    </article>
  </div>
</template>

<style scoped>
.outline-reader {
  height: 100%;
  min-height: 0;
}

.pad {
  margin: 24px 32px;
}
</style>
