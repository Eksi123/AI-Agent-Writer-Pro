<script setup lang="ts">
/**
 * 导出对话框：正文格式单选（markdown/epub/mobi/txt）+ 附大纲原 md。
 * mobi 由 calibre 转换，未安装则置灰（503/50301）。
 */
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { downloadExport, exportApi } from '@/api'
import { ApiError, errorMessage } from '@/api/http'
import { useSettingsStore } from '@/stores/settings'
import { t } from '@/i18n'
import type { ExportFormat, ExportTask, Novel } from '@/types'

const props = defineProps<{ modelValue: boolean; novel: Novel | null }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void }>()

const settings = useSettingsStore()

const format = ref<ExportFormat>('markdown')
const busy = ref(false)
const task = ref<ExportTask | null>(null)

const visible = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
})

/** calibre 未安装时 mobi 置灰并提示安装 */
const mobiDisabled = computed(() => !settings.calibreAvailable)

const formatOptions = computed(() => [
  { value: 'markdown' as const, label: t('export.fmtMarkdown') },
  { value: 'epub' as const, label: t('export.fmtEpub') },
  { value: 'mobi' as const, label: t('export.fmtMobi') },
  { value: 'txt' as const, label: t('export.fmtTxt') },
])

watch(visible, (v) => {
  if (v) {
    task.value = null
    busy.value = false
    if (mobiDisabled.value && format.value === 'mobi') format.value = 'markdown'
  }
})

async function startExport(): Promise<void> {
  if (!props.novel) return
  busy.value = true
  task.value = null
  try {
    const created = await exportApi.start(props.novel.id, format.value)
    task.value = created
    const done = await exportApi.waitForDone(props.novel.id, created.task_id)
    task.value = done
    if (done.status === 'done') {
      ElMessage.success(t('export.done'))
    } else {
      ElMessage.error(`${t('export.failed')}: ${done.error ?? ''}`)
    }
  } catch (err) {
    // 503/50301：calibre 缺失等依赖不可用
    if (err instanceof ApiError && err.isDependencyUnavailable) {
      ElMessage.warning(err.message)
    } else {
      ElMessage.error(errorMessage(err))
    }
    task.value = null
  } finally {
    busy.value = false
  }
}

async function download(): Promise<void> {
  if (!props.novel || !task.value) return
  try {
    await downloadExport(props.novel.id, task.value.task_id)
  } catch (err) {
    ElMessage.error(errorMessage(err))
  }
}
</script>

<template>
  <el-dialog v-model="visible" :title="t('export.title')" width="520px">
    <el-form label-width="96px">
      <el-form-item :label="t('export.formatLabel')">
        <el-radio-group v-model="format">
          <el-radio v-for="opt in formatOptions" :key="opt.value" :value="opt.value" :disabled="opt.value === 'mobi' && mobiDisabled">
            {{ opt.label }}
          </el-radio>
        </el-radio-group>
      </el-form-item>

      <el-alert v-if="mobiDisabled" type="info" :closable="false" class="mb8">
        {{ t('export.calibreMissing') }}
      </el-alert>

      <el-form-item>
        <span class="muted text-sm">{{ t('export.includeOutline') }}</span>
      </el-form-item>

      <el-form-item v-if="task">
        <div class="task-state">
          <el-progress
            v-if="task.status === 'running' || task.status === 'pending'"
            :percentage="task.progress"
          />
          <el-tag v-else-if="task.status === 'done'" type="success">{{ t('export.done') }}</el-tag>
          <el-tag v-else type="danger">{{ t('export.failed') }}</el-tag>
          <div v-if="task.error" class="muted text-sm">{{ task.error }}</div>
          <div v-if="task.filename" class="muted text-sm">{{ task.filename }}</div>
        </div>
      </el-form-item>
    </el-form>

    <template #footer>
      <el-button @click="visible = false">{{ t('common.close') }}</el-button>
      <el-button
        v-if="task?.status === 'done'"
        type="success"
        @click="download"
      >
        {{ t('export.download') }}
      </el-button>
      <el-button type="primary" :loading="busy" @click="startExport">
        {{ t('export.start') }}
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.mb8 {
  margin-bottom: 8px;
}

.task-state {
  width: 100%;
}
</style>
