<script setup lang="ts">
/** 字数统计条：实时统计当前章正文字数。 */
import { computed } from 'vue'
import { useEditorStore } from '@/stores/editor'
import { countWords } from '@/utils/words'
import { t } from '@/i18n'

const editorStore = useEditorStore()

/** draft 为 Markdown 正文，输入即更新 → 字数实时刷新 */
const liveCount = computed(() => countWords(editorStore.draft))
</script>

<template>
  <div class="word-count-bar row-between text-sm">
    <span class="muted">{{ t('editor.wordCount') }}：{{ liveCount }}</span>
  </div>
</template>

<style scoped>
.word-count-bar {
  padding: 4px 16px;
  border-top: 1px solid var(--border);
  background: var(--bg-panel);
}
</style>
