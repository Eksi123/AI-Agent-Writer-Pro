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

/**
 * 将原始 reason 转换为人类可读的中文描述
 * 例如：将 "AI 想读取章节正文内容：/projects/xxx/chapters/v1/c10.md"
 * 转换为 "AI 想读取第一卷第十章的正文内容"
 */
const humanReason = computed(() => {
  const reason = props.interrupt.reason
  if (!reason) return ''

  // 提取路径中的章节信息
  const pathMatch = reason.match(/\/projects\/[^/]+\/chapters\/([^/]+)\/([^/]+)\.md/)
  if (pathMatch) {
    const volume = pathMatch[1] // e.g., "v1"
    const chapter = pathMatch[2] // e.g., "c10"

    // 转换卷号：v1 -> 第一卷, v2 -> 第二卷
    const volumeNum = volume.replace('v', '')
    const volumeNames = ['一', '二', '三', '四', '五', '六', '七', '八', '九', '十']
    const volumeName = volumeNames[parseInt(volumeNum) - 1] || volumeNum

    // 转换章号：c10 -> 第十章
    const chapterNum = chapter.replace('c', '')
    const chapterNames = ['一', '二', '三', '四', '五', '六', '七', '八', '九', '十',
                          '十一', '十二', '十三', '十四', '十五', '十六', '十七', '十八', '十九', '二十']
    const chapterName = chapterNames[parseInt(chapterNum) - 1] || chapterNum

    return reason.replace(/\/projects\/[^/]+\/chapters\/[^/]+\/[^/]+\.md/, `${volumeName}${chapterName}`)
  }

  // 提取大纲路径
  const outlineMatch = reason.match(/\/projects\/[^/]+\/outline\/([^/]+)\.md/)
  if (outlineMatch) {
    return reason.replace(/\/projects\/[^/]+\/outline\/[^/]+\.md/, '小说大纲')
  }

  return reason
})

/**
 * 将原始 args 转换为人类可读的格式
 * 显示真实的文件路径而不是代码路径
 */
const humanArgs = computed(() => {
  const args = props.interrupt.args
  if (!args) return ''

  try {
    const argsObj = typeof args === 'string' ? JSON.parse(args) : args
    const formatted: string[] = []

    // 处理 file_path
    if (argsObj.file_path) {
      const path = argsObj.file_path as string
      // 提取章节信息
      const chapterMatch = path.match(/\/projects\/[^/]+\/chapters\/([^/]+)\/([^/]+)\.md/)
      if (chapterMatch) {
        const volume = chapterMatch[1].replace('v', '')
        const chapter = chapterMatch[2].replace('c', '')
        const volumeNames = ['一', '二', '三', '四', '五', '六', '七', '八', '九', '十']
        const chapterNames = ['一', '二', '三', '四', '五', '六', '七', '八', '九', '十',
                              '十一', '十二', '十三', '十四', '十五', '十六', '十七', '十八', '十九', '二十']
        const volumeName = volumeNames[parseInt(volume) - 1] || volume
        const chapterName = chapterNames[parseInt(chapter) - 1] || chapter
        formatted.push(`文件位置：第${volumeName}卷第${chapterName}章`)
      } else {
        const outlineMatch = path.match(/\/projects\/[^/]+\/outline\/([^/]+)\.md/)
        if (outlineMatch) {
          formatted.push('文件位置：小说大纲')
        } else {
          formatted.push(`文件路径：${path}`)
        }
      }
    }

    // 处理其他参数
    if (argsObj.offset !== undefined) {
      formatted.push(`起始位置：第 ${argsObj.offset} 行`)
    }
    if (argsObj.limit !== undefined) {
      formatted.push(`读取行数：${argsObj.limit} 行`)
    }
    if (argsObj.pattern) {
      formatted.push(`搜索模式：${argsObj.pattern}`)
    }

    return formatted.length > 0 ? formatted.join('\n') : JSON.stringify(argsObj, null, 2)
  } catch {
    return String(args)
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
  decision: 'approve' | 'approve_session' | 'edit' | 'reject' | 'respond',
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
    </div>

    <div class="card-body">
      <div class="field">
        <span class="label">{{ t('agent.approvalTool') }}</span>
        <code>{{ interrupt.toolName ?? '-' }}</code>
      </div>
      <div v-if="humanReason" class="field">
        <span class="label">{{ t('agent.approvalReason') }}</span>
        <span>{{ humanReason }}</span>
      </div>
      <div v-if="humanArgs" class="field">
        <span class="label">{{ t('agent.approvalArgs') }}</span>
        <pre class="args">{{ humanArgs }}</pre>
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
        <el-button
          v-if="decisions.has('approve_session')"
          type="primary"
          plain
          size="small"
          :loading="submitting"
          @click="decide('approve_session')"
        >
          {{ t('agent.approveSession') }}
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
