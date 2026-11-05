<script setup lang="ts">
/**
 * 导出对话框：正文格式单选（markdown/epub/pdf/txt）+ 附大纲原 md。
 * PDF 由 reportlab 生成（依赖缺失时任务 failed 并提示安装）。
 *
 * 导出进行中：弹出**不可取消**的进度弹窗（盖在应用上层），显示进度条 + [停止导出]；
 * - 完成 → 产物已由后端写入「通用设置-小说导出地址」（默认桌面），弹窗自动消失；
 * - 失败 → 弹窗消失 + toast 失败原因；
 * - 停止 → 二次确认后调 cancel 接口，任务置 cancelled，弹窗消失。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { exportApi } from '@/api'
import { errorMessage } from '@/api/http'
import { t } from '@/i18n'
import type { ExportFormat, ExportTask, Novel } from '@/types'

const props = defineProps<{ modelValue: boolean; novel: Novel | null }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void }>()

const format = ref<ExportFormat>('markdown')
const busy = ref(false)

/** 进度弹窗状态（导出期间独立于配置弹窗存在） */
const progressVisible = ref(false)
const task = ref<ExportTask | null>(null)
let pollTimer: ReturnType<typeof setInterval> | null = null

const visible = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
})

const formatOptions = computed(() => [
  { value: 'markdown' as const, label: t('export.fmtMarkdown') },
  { value: 'epub' as const, label: t('export.fmtEpub') },
  { value: 'pdf' as const, label: t('export.fmtPdf') },
  { value: 'txt' as const, label: t('export.fmtTxt') },
])

watch(visible, (v) => {
  if (v) {
    task.value = null
    busy.value = false
  }
})

function stopPolling(): void {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

onBeforeUnmount(stopPolling)

/** 轮询任务状态：done/failed/cancelled → 收尾 */
function startPolling(novelId: string, taskId: string): void {
  stopPolling()
  pollTimer = setInterval(async () => {
    try {
      const cur = await exportApi.status(novelId, taskId)
      task.value = cur
      if (cur.status === 'done') {
        stopPolling()
        progressVisible.value = false
        ElMessage.success(t('export.savedToDir'))
      } else if (cur.status === 'failed') {
        stopPolling()
        progressVisible.value = false
        ElMessage.error(`${t('export.failed')}: ${cur.error ?? ''}`)
      } else if (cur.status === 'cancelled') {
        stopPolling()
        progressVisible.value = false
        ElMessage.info(t('export.cancelled'))
      }
    } catch (err) {
      stopPolling()
      progressVisible.value = false
      ElMessage.error(errorMessage(err))
    }
  }, 400)
}

async function startExport(): Promise<void> {
  if (!props.novel) return
  busy.value = true
  task.value = null
  try {
    const created = await exportApi.start(props.novel.id, format.value)
    task.value = created
    visible.value = false // 关闭配置弹窗，进度弹窗接管
    progressVisible.value = true
    startPolling(props.novel.id, created.task_id)
  } catch (err) {
    ElMessage.error(errorMessage(err))
  } finally {
    busy.value = false
  }
}

/** 停止导出：二次确认 → cancel 接口（执行体在进度检查点中止） */
async function stopExport(): Promise<void> {
  if (!props.novel || !task.value) return
  try {
    await ElMessageBox.confirm(t('export.stopConfirm'), t('export.stopTitle'), {
      type: 'warning',
      confirmButtonText: t('export.stop'),
      cancelButtonText: t('common.cancel'),
      confirmButtonClass: 'el-button--danger',
    })
  } catch {
    return
  }
  try {
    await exportApi.cancel(props.novel.id, task.value.task_id)
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
          <el-radio v-for="opt in formatOptions" :key="opt.value" :value="opt.value">
            {{ opt.label }}
          </el-radio>
        </el-radio-group>
      </el-form-item>

      <el-form-item>
        <span class="muted text-sm">{{ t('export.includeOutline') }}</span>
      </el-form-item>
    </el-form>

    <template #footer>
      <el-button @click="visible = false">{{ t('common.close') }}</el-button>
      <el-button type="primary" :loading="busy" @click="startExport">
        {{ t('export.start') }}
      </el-button>
    </template>
  </el-dialog>

  <!-- 导出进度弹窗：不可取消（无关闭按钮、遮罩不可点关、ESC 无效） -->
  <el-dialog
    v-model="progressVisible"
    :title="t('export.progressTitle')"
    width="420px"
    :show-close="false"
    :close-on-click-modal="false"
    :close-on-press-escape="false"
    modal-class="export-progress-modal"
  >
    <div class="progress-body">
      <el-progress :percentage="task?.progress ?? 0" :stroke-width="10" />
      <div class="muted text-sm progress-hint">
        {{ task?.status === 'running' ? t('export.running') : t('export.pending') }}
      </div>
    </div>
    <template #footer>
      <el-button type="danger" plain :disabled="!task || task.status !== 'running'" @click="stopExport">
        {{ t('export.stop') }}
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.progress-body {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 6px 2px;
}

.progress-hint {
  text-align: center;
}
</style>
