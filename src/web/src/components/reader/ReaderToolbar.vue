<script setup lang="ts">
/**
 * 顶部工具栏：【阅读模式】/【编辑模式】切换 + 目录抽屉开关 + 自动保存状态 + Agent 面板开关。
 */
import { computed } from 'vue'
import { useReaderStore } from '@/stores/reader'
import { useProjectStore } from '@/stores/project'
import { useSettingsStore } from '@/stores/settings'
import AutosaveIndicator from '@/components/editor/AutosaveIndicator.vue'
import { t } from '@/i18n'

const emit = defineEmits<{ (e: 'toggle-agent'): void }>()

const reader = useReaderStore()
const project = useProjectStore()
const settings = useSettingsStore()

const mode = computed({
  get: () => reader.mode,
  set: (v) => reader.setMode(v),
})

/** 后端未连通时给出提示（FTS5 降级 / 服务未启动） */
const backendHint = computed(() => {
  if (settings.error) return settings.error
  if (settings.health && !settings.fts5Available) return t('health.fts5Degraded')
  return ''
})
</script>

<template>
  <div class="reader-toolbar row-between">
    <div class="row">
      <el-button v-if="reader.tocOpen" text size="small" @click="reader.toggleToc()">≡</el-button>
      <el-button v-else text size="small" @click="reader.toggleToc()">☰ {{ t('reader.toc') }}</el-button>

      <el-radio-group v-model="mode" size="small">
        <el-radio-button value="read">{{ t('reader.modeRead') }}</el-radio-button>
        <el-radio-button value="edit">{{ t('reader.modeEdit') }}</el-radio-button>
      </el-radio-group>

      <el-tag v-if="project.currentNovel" size="small" effect="plain">
        {{ project.currentNovel.title }}
      </el-tag>
      <el-tag v-if="backendHint" size="small" type="warning" effect="plain">{{ backendHint }}</el-tag>
    </div>

    <div class="row">
      <AutosaveIndicator v-if="reader.mode === 'edit'" />
      <el-button text size="small" @click="emit('toggle-agent')">{{ t('agent.panelTitle') }}</el-button>
    </div>
  </div>
</template>

<style scoped>
.reader-toolbar {
  padding: 8px 16px;
  border-bottom: 1px solid var(--border);
  background: var(--bg-panel);
  flex-shrink: 0;
  flex-wrap: wrap;
  gap: 8px;
}
</style>
