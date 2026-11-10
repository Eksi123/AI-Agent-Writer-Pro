<script setup lang="ts">
/**
 * 审批卡片（interrupt.request 事件，手册「二、(2) HITL」）。
 *
 * 与询问卡片同排版：**问题 + 选项**（逐条翻页 ‹ › + n/N、选项单选高亮 ✓、
 * 末位「回复补充」输入行、全部决策后右下角「提交」一次提交）；
 * 外框保持橙色（warning）以示风险操作。
 * 同类多条审批合并进同一张卡片（store 的 pendingInterrupts 队列），
 * 提交时按队列顺序逐条 resolve，首个成功后续跑流。
 */
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useAgentStore } from '@/stores/agent'
import type { PendingInterrupt } from '@/stores/agent'
import { t } from '@/i18n'

const props = defineProps<{ interrupts: PendingInterrupt[] }>()
const agent = useAgentStore()

type Decision = 'approve' | 'approve_session' | 'edit' | 'reject' | 'respond'

const submitting = ref(false)
const page = ref(0)
/** 每条审批的决策：selected = 选中的决策；text = 回复补充内容；editedArgs = 编辑后批准的参数 */
const answers = ref<{ selected: Decision | ''; text: string; editedArgs?: Record<string, unknown> }[]>(
  props.interrupts.map(() => ({ selected: '', text: '' })),
)

const total = computed(() => props.interrupts.length)
const current = computed(() => props.interrupts[page.value])
const currentAnswer = computed(() => answers.value[page.value])

// 卡片挂载期间新到的同类审批入队（answers 同步扩容，页码不越界）
watch(
  () => props.interrupts.length,
  (n) => {
    while (answers.value.length < n) answers.value.push({ selected: '', text: '' })
    answers.value.length = n
    if (page.value >= n) page.value = Math.max(0, n - 1)
  },
)

/** 问题文案：后端已转成人话（如「删除第二卷第三章《章名》的正文，删除后不可恢复」） */
function questionOf(it: PendingInterrupt): string {
  return it.reason || it.itemLabel || ''
}

/** 选项行：统一为 批准 / 拒绝 / 回复补充（移除「本次会话允许」，简化用户决策） */
const OPTION_DECISIONS: { value: Decision; labelKey: 'approve' | 'reject' }[] = [
  { value: 'approve', labelKey: 'approve' },
  { value: 'reject', labelKey: 'reject' },
]
const optionsOf = computed(() =>
  OPTION_DECISIONS.filter((o) => (current.value?.decisions ?? []).includes(o.value)),
)

function answeredAt(i: number): boolean {
  const a = answers.value[i]
  return !!a && (a.selected !== '' || a.text.trim() !== '')
}
const answeredCount = computed(() => props.interrupts.filter((_q, i) => answeredAt(i)).length)
const allAnswered = computed(() => total.value > 0 && answeredCount.value === total.value)

function circled(i: number): string {
  const chars = '①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳'
  return i < chars.length ? chars[i] : `${i + 1}.`
}

function pick(value: Decision): void {
  const a = currentAnswer.value
  if (!a) return
  a.selected = a.selected === value ? '' : value
  if (value !== 'respond') a.text = ''
}

function onCustomInput(value: string): void {
  const a = currentAnswer.value
  if (!a) return
  a.text = value
  if (value.trim()) a.selected = '' // 回复补充与选项互斥
}

function prevPage(): void {
  if (page.value > 0) page.value--
}
function nextPage(): void {
  if (page.value < total.value - 1) page.value++
}

async function submitAll(): Promise<void> {
  if (!allAnswered.value || submitting.value) return
  submitting.value = true
  try {
    const decisions: Decision[] = []
    const payloads: ({ edited_args?: Record<string, unknown>; message?: string } | undefined)[] = []
    for (const a of answers.value) {
      if (a.text.trim()) {
        decisions.push('respond')
        payloads.push({ message: a.text.trim() })
      } else {
        decisions.push((a.selected || 'approve') as Decision)
        payloads.push(a.editedArgs ? { edited_args: a.editedArgs } : undefined)
      }
    }
    await agent.resolveInterrupts(decisions, payloads)
  } catch (err) {
    ElMessage.error(String(err))
  } finally {
    submitting.value = false
  }
}

/** 一键批准所有审批（全部设为 approve 并立即提交） */
async function approveAll(): Promise<void> {
  if (submitting.value) return
  submitting.value = true
  try {
    const decisions: Decision[] = props.interrupts.map(() => 'approve')
    const payloads = props.interrupts.map(() => undefined)
    await agent.resolveInterrupts(decisions, payloads)
  } catch (err) {
    ElMessage.error(String(err))
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="approval-card">
    <div class="card-head">
      <el-tag type="warning" size="small">{{ t('agent.approvalTitle') }}</el-tag>
      <el-button
        v-if="total > 1"
        type="warning"
        size="small"
        plain
        :loading="submitting"
        @click="approveAll"
      >
        {{ t('agent.approveAll') }}
      </el-button>
    </div>

    <div class="q-block">
      <div class="q-title">
        <span class="q-no">{{ circled(page) }}</span>
        <span class="q-text">{{ questionOf(current) }}</span>
      </div>

      <div class="opt-list">
        <button
          v-for="(opt, oi) in optionsOf"
          :key="opt.value"
          type="button"
          class="opt-row"
          :class="{ active: currentAnswer?.selected === opt.value }"
          :disabled="submitting"
          @click="pick(opt.value)"
        >
          <span class="opt-no">{{ oi + 1 }}</span>
          <span class="opt-body">
            <span class="opt-label">{{ t(`agent.${opt.labelKey}`) }}</span>
          </span>
          <span v-if="currentAnswer?.selected === opt.value" class="opt-check">✓</span>
        </button>

        <!-- 无选项按钮时显示提示（如后端只允许 respond） -->
        <div v-if="!optionsOf.length && (current?.decisions ?? []).includes('respond')" class="opt-hint">
          <span class="muted">{{ t('agent.approvalRespondHint') }}</span>
        </div>

        <div v-if="(current?.decisions ?? []).includes('respond')" class="opt-row custom-row">
          <span class="opt-no">{{ optionsOf.length + 1 }}</span>
          <input
            class="custom-input"
            :value="currentAnswer?.text ?? ''"
            :placeholder="t('agent.approvalRespondPlaceholder')"
            :disabled="submitting"
            @input="onCustomInput(($event.target as HTMLInputElement).value)"
          />
        </div>
      </div>
    </div>

    <div class="card-foot">
      <div class="pager">
        <button type="button" class="pager-btn" :disabled="page === 0 || submitting" @click="prevPage">‹</button>
        <button type="button" class="pager-btn" :disabled="page >= total - 1 || submitting" @click="nextPage">›</button>
        <span class="muted">{{ page + 1 }}/{{ total }}</span>
      </div>
      <el-button type="warning" size="small" :disabled="!allAnswered" :loading="submitting" @click="submitAll">
        {{ t('agent.approvalSubmit') }}
      </el-button>
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

.muted {
  color: var(--el-text-color-secondary);
}

/* ---- 与询问卡片同排版的问答区 ---- */
.q-block {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.q-title {
  display: flex;
  gap: 6px;
  align-items: baseline;
  font-size: 13px;
  font-weight: 600;
  line-height: 1.5;
}

.q-no {
  flex: none;
}

.q-text {
  white-space: pre-wrap;
}

.opt-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.opt-row {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  width: 100%;
  padding: 6px 8px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.opt-row:hover:not(:disabled) {
  background: var(--el-fill-color-light);
}

.opt-row.active {
  background: var(--el-color-warning);
  color: var(--el-color-white);
}

.opt-row.active:hover:not(:disabled) {
  background: var(--el-color-warning);
}

.opt-row:disabled {
  cursor: not-allowed;
  opacity: 0.7;
}

.opt-no {
  flex: none;
  min-width: 14px;
  font-size: 12px;
  line-height: 1.6;
}

.opt-body {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1;
  min-width: 0;
}

.opt-label {
  font-size: 13px;
  line-height: 1.6;
}

.opt-check {
  flex: none;
  font-size: 12px;
  line-height: 1.6;
}

.custom-row {
  cursor: default;
}

.custom-input {
  flex: 1;
  min-width: 0;
  border: none;
  border-bottom: 1px solid var(--el-border-color);
  background: transparent;
  color: inherit;
  font: inherit;
  font-size: 13px;
  padding: 2px 0;
  outline: none;
}

.custom-input:focus {
  border-bottom-color: var(--el-color-warning);
}

.custom-input:disabled {
  opacity: 0.7;
}

.card-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.pager {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
}

.pager-btn {
  width: 22px;
  height: 22px;
  border: 1px solid var(--el-border-color);
  border-radius: 4px;
  background: transparent;
  color: inherit;
  cursor: pointer;
  line-height: 1;
}

.pager-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
</style>
