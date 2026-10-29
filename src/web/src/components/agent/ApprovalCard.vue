<script setup lang="ts">
/**
 * 审批卡片（interrupt.request 事件，手册「二、(2) HITL」）。
 * 支持 批准 / 编辑后批准 / 拒绝 / 回复补充 四种决策（allowed_decisions 由后端下发）。
 */
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useAgentStore } from '@/stores/agent'
import type { PendingInterrupt } from '@/stores/agent'
import { t } from '@/i18n'

const props = defineProps<{ interrupt: PendingInterrupt }>()
const agent = useAgentStore()

const submitting = ref(false)
const editMode = ref(false)
const respondMode = ref(false)
const editedArgsText = ref('')
const replyText = ref('')

const decisions = computed(() => new Set(props.interrupt.decisions))

const argsPreview = computed(() => {
  try {
    return JSON.stringify(props.interrupt.args ?? {}, null, 2)
  } catch {
    return String(props.interrupt.args)
  }
})

function openEdit(): void {
  editedArgsText.value = argsPreview.value
  editMode.value = true
  respondMode.value = false
}

function openRespond(): void {
  replyText.value = ''
  respondMode.value = true
  editMode.value = false
}

async function decide(
  decision: 'approve' | 'edit' | 'reject' | 'respond',
  payload?: { edited_args?: Record<string, unknown>; message?: string },
): Promise<void> {
  submitting.value = true
  try {
    await agent.resolveInterrupt(decision, payload)
  } catch (err) {
    ElMessage.error(String(err))
  } finally {
    submitting.value = false
    editMode.value = false
    respondMode.value = false
  }
}

async function submitEdit(): Promise<void> {
  let parsed: Record<string, unknown>
  try {
    parsed = JSON.parse(editedArgsText.value)
  } catch {
    ElMessage.error(t('common.invalidJson'))
    return
  }
  await decide('edit', { edited_args: parsed })
}

async function submitReject(): Promise<void> {
  await decide('reject', { message: replyText.value || undefined })
}
</script>

<template>
  <div class="approval-card">
    <div class="card-head">
      <el-tag type="warning" size="small">{{ t('agent.approvalTitle') }}</el-tag>
      <el-tag v-if="interrupt.risk" type="danger" size="small">
        {{ t('agent.approvalRisk') }}: {{ interrupt.risk }}
      </el-tag>
    </div>

    <div class="card-body">
      <div class="field">
        <span class="label">{{ t('agent.approvalTool') }}</span>
        <code>{{ interrupt.toolName ?? '-' }}</code>
      </div>
      <div v-if="interrupt.reason" class="field">
        <span class="label">{{ t('agent.approvalReason') }}</span>
        <span>{{ interrupt.reason }}</span>
      </div>
      <div class="field">
        <span class="label">{{ t('agent.approvalArgs') }}</span>
        <pre class="args">{{ argsPreview }}</pre>
      </div>
    </div>

    <!-- 编辑参数 -->
    <div v-if="editMode" class="edit-area">
      <div class="label">{{ t('agent.editedArgsLabel') }}</div>
      <el-input v-model="editedArgsText" type="textarea" :rows="5" />
    </div>

    <!-- 回复补充 / 拒绝理由 -->
    <div v-if="respondMode" class="edit-area">
      <div class="label">
        {{ respondMode ? t('agent.respondMessageLabel') : t('agent.rejectMessageLabel') }}
      </div>
      <el-input v-model="replyText" type="textarea" :rows="2" />
    </div>

    <div class="card-actions">
      <template v-if="editMode">
        <el-button type="primary" size="small" :loading="submitting" @click="submitEdit">
          {{ t('agent.editThenApprove') }}
        </el-button>
        <el-button size="small" @click="editMode = false">{{ t('common.cancel') }}</el-button>
      </template>
      <template v-else-if="respondMode">
        <el-button type="primary" size="small" :loading="submitting" @click="submitReject">
          {{ t('agent.respond') }}
        </el-button>
        <el-button size="small" @click="respondMode = false">{{ t('common.cancel') }}</el-button>
      </template>
      <template v-else>
        <el-button
          v-if="decisions.has('approve')"
          type="primary"
          size="small"
          :loading="submitting"
          @click="decide('approve')"
        >
          {{ t('agent.approve') }}
        </el-button>
        <el-button v-if="decisions.has('edit')" size="small" @click="openEdit">
          {{ t('agent.editThenApprove') }}
        </el-button>
        <el-button
          v-if="decisions.has('reject')"
          type="danger"
          size="small"
          plain
          @click="decide('reject')"
        >
          {{ t('agent.reject') }}
        </el-button>
        <el-button v-if="decisions.has('respond')" size="small" @click="openRespond">
          {{ t('agent.respond') }}
        </el-button>
      </template>
    </div>
  </div>
</template>

<style scoped>
.approval-card {
  border: 1px solid var(--el-color-warning-light-5);
  background: var(--el-color-warning-light-9);
  border-radius: 8px;
  padding: 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.card-head {
  display: flex;
  gap: 6px;
  align-items: center;
}

.card-body {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.label {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.args {
  margin: 0;
  background: var(--el-fill-color);
  border-radius: 4px;
  padding: 6px 8px;
  font-size: 11px;
  max-height: 160px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}

.edit-area {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.card-actions {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
</style>
