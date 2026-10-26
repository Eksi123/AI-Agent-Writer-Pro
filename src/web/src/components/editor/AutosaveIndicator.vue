<script setup lang="ts">
/** 自动保存状态指示器：显示「保存中… / 有未保存修改 / 上次保存时间」。 */
import { computed } from 'vue'
import { useEditorStore } from '@/stores/editor'
import { useSettingsStore } from '@/stores/settings'
import { t } from '@/i18n'

const editorStore = useEditorStore()
const settingsStore = useSettingsStore()

const label = computed(() => {
  if (editorStore.saving) return t('common.saving')
  if (editorStore.dirty) return t('editor.unsaved')
  if (editorStore.lastSavedAt) {
    const d = new Date(editorStore.lastSavedAt)
    return t('editor.lastSaved', { time: d.toLocaleTimeString('zh-CN') })
  }
  return t('common.saved')
})

const type = computed<'success' | 'warning' | 'info'>(() => {
  if (editorStore.dirty) return 'warning'
  if (editorStore.saving) return 'info'
  return 'success'
})
</script>

<template>
  <div class="autosave-indicator row">
    <el-tag :type="type" size="small" effect="plain">
      {{ label }}
    </el-tag>
    <span class="muted text-sm">
      v{{ editorStore.baseVersion }} · {{ t('editor.autosaveOn', { sec: settingsStore.settings.autosave_interval_sec }) }}
    </span>
  </div>
</template>

<style scoped>
.autosave-indicator {
  gap: 8px;
}
</style>
