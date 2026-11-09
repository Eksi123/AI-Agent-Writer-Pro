<script setup lang="ts">
/**
 * 409 版本冲突提示条：AI 或其它端已修改本章，用户三选一——
 * 「查看差异」（只读对比，供手动取舍）、「采用服务端版本（放弃本地修改）」、
 * 「覆盖」（用本地内容盖掉服务端）。
 *
 * ★ 这里**没有自动合并**。旧文案写「查看差异合并」且按钮叫「合并」，
 *   但对应动作是 `acceptServerVersion()` —— 整体丢弃本地 draft，
 *   用户会误以为两边都保住了。文案已改为如实描述，见 i18n `conflict.acceptServer`。
 *
 * 覆盖语义（手册「六」）：强制覆盖需再次带**最新** base_version，
 * 故这里用冲突响应回传的 version 重试，而不是继续用旧的 base_version。
 */
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useEditorStore } from '@/stores/editor'
import { t } from '@/i18n'

const emit = defineEmits<{ (e: 'resolved'): void }>()

const editorStore = useEditorStore()
const showDiff = ref(false)

const conflict = computed(() => editorStore.conflict)

/** 极简行级 diff：用于「查看差异」弹窗，不引入重型依赖 */
const diffRows = computed(() => {
  if (!conflict.value) return []
  const localLines = strip(editorStore.draft).split('\n')
  const serverLines = strip(conflict.value.content).split('\n')
  const max = Math.max(localLines.length, serverLines.length)
  const rows: { i: number; local: string; server: string; same: boolean }[] = []
  for (let i = 0; i < max; i++) {
    const l = localLines[i] ?? ''
    const s = serverLines[i] ?? ''
    rows.push({ i, local: l, server: s, same: l === s })
  }
  return rows.filter((r) => !r.same)
})

function strip(html: string): string {
  return html.replace(/<[^>]*>/g, '').replace(/&nbsp;/g, ' ').trim()
}

async function onOverwrite(): Promise<void> {
  const okOverwritten = await editorStore.overwriteWithLocal()
  if (okOverwritten) {
    ElMessage.success(t('conflict.overwritten'))
    emit('resolved')
  }
}

function onAcceptServer(): void {
  editorStore.acceptServerVersion()
  ElMessage.info(t('conflict.acceptedServer'))
  emit('resolved')
}
</script>

<template>
  <el-alert v-if="conflict" type="warning" :closable="false" class="conflict-banner">
    <template #title>
      <div class="row-between">
        <div>
          <strong>{{ t('conflict.title') }}</strong>
          <span class="muted text-sm"> {{ t('conflict.message') }}</span>
        </div>
        <div class="row">
          <el-button size="small" @click="showDiff = true">{{ t('conflict.viewDiff') }}</el-button>
          <el-button size="small" @click="onAcceptServer">{{ t('conflict.acceptServer') }}</el-button>
          <el-button size="small" type="warning" @click="onOverwrite">
            {{ t('conflict.overwrite') }}
          </el-button>
        </div>
      </div>
    </template>
    <div class="text-sm muted">
      {{ t('conflict.localVersion') }} v{{ conflict.base_version }} →
      {{ t('conflict.serverVersion') }} v{{ conflict.version }}
      · {{ t('conflict.lastWriter') }}：
      {{ conflict.last_writer === 'agent' ? t('conflict.writerAgent') : t('conflict.writerManual') }}
    </div>
  </el-alert>

  <el-dialog v-model="showDiff" :title="t('conflict.viewDiff')" width="860px">
    <table class="diff-table">
      <thead>
        <tr>
          <th>#</th>
          <th>{{ t('conflict.localVersion') }}</th>
          <th>{{ t('conflict.serverVersion') }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in diffRows" :key="row.i">
          <td class="muted">{{ row.i + 1 }}</td>
          <td class="del">{{ row.local || '—' }}</td>
          <td class="ins">{{ row.server || '—' }}</td>
        </tr>
        <tr v-if="!diffRows.length">
          <td colspan="3" class="muted">{{ t('common.empty') }}</td>
        </tr>
      </tbody>
    </table>
    <template #footer>
      <el-button @click="showDiff = false">{{ t('common.close') }}</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.conflict-banner {
  margin: 8px 16px 0;
  flex-shrink: 0;
}

.diff-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
  table-layout: fixed;
}

.diff-table th,
.diff-table td {
  border: 1px solid var(--border);
  padding: 4px 8px;
  text-align: left;
  word-break: break-all;
  vertical-align: top;
}

.diff-table td.del {
  background: color-mix(in srgb, #f56c6c 12%, transparent);
}

.diff-table td.ins {
  background: color-mix(in srgb, #67c23a 12%, transparent);
}
</style>
