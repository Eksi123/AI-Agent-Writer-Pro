<script setup lang="ts">
/** 字数统计条：实时统计当前章正文字数（去除 HTML 标签与空白）。 */
import { computed } from 'vue'
import { useEditorStore } from '@/stores/editor'
import { t } from '@/i18n'

const editorStore = useEditorStore()

/** 从 HTML 草稿中统计实际字数（去标签、去空白） */
const liveCount = computed(() => {
  const text = editorStore.draft.replace(/<[^>]*>/g, '').replace(/&nbsp;/g, ' ')
  return text.replace(/\s+/g, '').length
})
</script>

<template>
  <div class="word-count-bar row-between text-sm">
    <span class="muted">{{ t('editor.wordCount') }}：{{ liveCount }}</span>
    <span v-if="editorStore.status === 'finalized'" class="finalized">定稿</span>
  </div>
</template>

<style scoped>
.word-count-bar {
  padding: 4px 16px;
  border-top: 1px solid var(--border);
  background: var(--bg-panel);
}

.finalized {
  color: var(--el-color-success);
}
</style>
