<script setup lang="ts">
/**
 * 咨询卡片（consult.request 事件）。
 *
 * 两种形态：
 * - **多问题卡片**（`kind="ask_user"`，AI 思考中间过程询问）：参考问答卡样式——
 *   逐题翻页（‹ › + n/total）、选项单选/多选（选中高亮 + ✓）、末位「输入自定义答案」行、
 *   全部答完后右下角「提交」按钮一次性提交，提交后 AI 继续思考；
 * - **单问卡片**（流程咨询 / 调用预算等旧契约）：选项按钮 + 自由输入框。
 * 决策统一走 resolve 接口：decision=option（choice=答案清单 JSON 或选项值）| text。
 */
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useAgentStore } from '@/stores/agent'
import type { PendingConsult } from '@/stores/agent'
import { t } from '@/i18n'

const props = defineProps<{ consult: PendingConsult }>()
const agent = useAgentStore()

const submitting = ref(false)
const textMode = ref(false)
const freeText = ref('')

/** 多问题形态（ask_user） */
const isMulti = computed(() => (props.consult.questions?.length ?? 0) > 0)
const questions = computed(() => props.consult.questions ?? [])

/** 每题作答：selected = 选中选项 label 列表；text = 自定义答案 */
const answers = ref<{ selected: string[]; text: string }[]>([])
if (isMulti.value) {
  answers.value = questions.value.map(() => ({ selected: [], text: '' }))
}
const page = ref(0)
const total = computed(() => questions.value.length)
const current = computed(() => questions.value[page.value])
const currentAnswer = computed(() => answers.value[page.value])

function answeredAt(i: number): boolean {
  const a = answers.value[i]
  const q = questions.value[i]
  // 无选项且全局不允许文本输入的问题视为自动回答（用户无法操作）
  if (q && (!q.options || q.options.length === 0) && !props.consult.allowText) return true
  return !!a && (a.selected.length > 0 || a.text.trim() !== '')
}
const answeredCount = computed(() => questions.value.filter((_q, i) => answeredAt(i)).length)
const allAnswered = computed(() => total.value > 0 && answeredCount.value === total.value)

function circled(i: number): string {
  const chars = '①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳'
  return i < chars.length ? chars[i] : `${i + 1}.`
}

function toggleOption(label: string): void {
  const a = currentAnswer.value
  const q = current.value
  if (!a || !q) return
  const idx = a.selected.indexOf(label)
  if (q.multi_select) {
    if (idx >= 0) a.selected.splice(idx, 1)
    else a.selected.push(label)
  } else {
    a.selected = idx >= 0 ? [] : [label]
    a.text = '' // 单选：选选项即清空自定义答案
  }
}

function onCustomInput(value: string): void {
  const a = currentAnswer.value
  const q = current.value
  if (!a || !q) return
  a.text = value
  if (!q.multi_select && value.trim()) a.selected = [] // 单选票与自定义答案互斥
}

function prevPage(): void {
  if (page.value > 0) page.value -= 1
}
function nextPage(): void {
  if (page.value < total.value - 1) page.value += 1
}

async function submitAll(): Promise<void> {
  if (!allAnswered.value) return
  submitting.value = true
  try {
    const payload = answers.value.map((a) => ({
      selected: a.selected,
      text: a.text.trim(),
    }))
    await agent.resolveConsult('option', JSON.stringify(payload))
  } catch (err) {
    ElMessage.error(String(err))
  } finally {
    submitting.value = false
  }
}

/** ---- 单问形态（流程咨询 / 调用预算等） ---- */
const normalizedOptions = computed(() =>
  (props.consult.options ?? []).map((opt) =>
    typeof opt === 'string' ? { value: opt, label: opt } : { value: String(opt.value), label: String(opt.label ?? opt.value) },
  ),
)

/** 当前选中的选项值（用于高亮显示） */
const selectedValue = ref('')

function onFreeTextInput(value: string): void {
  freeText.value = value
  if (value.trim()) selectedValue.value = '' // 输入文本时清空选项选中
}

async function submitChoice(): Promise<void> {
  if (!selectedValue.value) return
  submitting.value = true
  try {
    await agent.resolveConsult('option', selectedValue.value)
  } catch (err) {
    ElMessage.error(String(err))
  } finally {
    submitting.value = false
  }
}

async function choose(value: string): Promise<void> {
  selectedValue.value = selectedValue.value === value ? '' : value
  if (selectedValue.value) freeText.value = '' // 选选项时清空自由文本
}

async function submitText(): Promise<void> {
  if (!freeText.value.trim()) return
  submitting.value = true
  try {
    await agent.resolveConsult('text', freeText.value.trim())
  } catch (err) {
    ElMessage.error(String(err))
  } finally {
    submitting.value = false
    textMode.value = false
  }
}
</script>

<template>
  <div class="consult-card">
    <!-- 多问题卡片（ask_user） -->
    <template v-if="isMulti">
      <div class="card-head">
        <el-tag type="primary" size="small">{{ t('agent.askUserTitle') }}</el-tag>
        <el-tag v-if="consult.workflowStage" size="small" type="info">{{ consult.workflowStage }}</el-tag>
      </div>

      <div class="q-block">
        <div class="q-title">
          <span class="q-no">{{ circled(page) }}</span>
          <span class="q-text">{{ current?.question }}</span>
        </div>

        <div class="opt-list">
          <button
            v-for="(opt, oi) in current?.options ?? []"
            :key="opt.label"
            type="button"
            class="opt-row"
            :class="{ active: currentAnswer?.selected.includes(opt.label) }"
            :disabled="submitting"
            @click="toggleOption(opt.label)"
          >
            <span class="opt-no">{{ oi + 1 }}</span>
            <span class="opt-body">
              <span class="opt-label">{{ opt.label }}</span>
              <span v-if="opt.description" class="opt-desc muted">{{ opt.description }}</span>
            </span>
            <span v-if="currentAnswer?.selected.includes(opt.label)" class="opt-check">✓</span>
          </button>

          <div class="opt-row custom-row">
            <span class="opt-no">{{ (current?.options ?? []).length + 1 }}</span>
            <input
              class="custom-input"
              :value="currentAnswer?.text ?? ''"
              :placeholder="t('agent.askUserCustom')"
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
        <el-button type="primary" size="small" :disabled="!allAnswered" :loading="submitting" @click="submitAll">
          {{ t('agent.askUserSubmit') }}
        </el-button>
      </div>
    </template>

    <!-- 单问卡片（流程咨询 / 调用预算等） -->
    <template v-else>
      <div class="card-head">
        <el-tag type="primary" size="small">{{ t('agent.consultTitle') }}</el-tag>
        <el-tag v-if="consult.workflowStage" size="small" type="info">{{ consult.workflowStage }}</el-tag>
      </div>

      <div class="q-block">
        <div class="q-title">
          <span class="q-text">{{ consult.question }}</span>
        </div>

        <div class="opt-list">
          <button
            v-for="(opt, oi) in normalizedOptions"
            :key="opt.value"
            type="button"
            class="opt-row"
            :class="{ active: selectedValue === opt.value }"
            :disabled="submitting"
            @click="choose(opt.value)"
          >
            <span class="opt-no">{{ oi + 1 }}</span>
            <span class="opt-body">
              <span class="opt-label">{{ opt.label }}</span>
            </span>
            <span v-if="selectedValue === opt.value" class="opt-check">✓</span>
          </button>

          <!-- 自由输入行 -->
          <div v-if="consult.allowText" class="opt-row custom-row">
            <span class="opt-no">{{ normalizedOptions.length + 1 }}</span>
            <input
              class="custom-input"
              :value="freeText"
              :placeholder="consult.textPlaceholder || t('agent.consultText') + '…'"
              :disabled="submitting"
              @input="onFreeTextInput(($event.target as HTMLInputElement).value)"
            />
          </div>
        </div>
      </div>

      <div class="card-foot">
        <div class="pager">
          <span class="muted">{{ t('agent.consultHint') }}</span>
        </div>
        <el-button
          v-if="selectedValue"
          type="primary"
          size="small"
          :loading="submitting"
          @click="submitChoice"
        >
          {{ t('agent.consultSendOption') }}
        </el-button>
        <el-button
          v-else-if="freeText.trim()"
          type="primary"
          size="small"
          :loading="submitting"
          @click="submitText"
        >
          {{ t('agent.consultSendText') }}
        </el-button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.consult-card {
  border: 1px solid var(--el-color-primary-light-5);
  background: var(--el-color-primary-light-9);
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
  justify-content: space-between;
}

.muted {
  color: var(--el-text-color-secondary);
}

/* ---- 与审批卡片同排版的问答区 ---- */
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
  background: var(--el-color-primary);
  color: var(--el-color-white);
}

.opt-row.active:hover:not(:disabled) {
  background: var(--el-color-primary);
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
  border-bottom-color: var(--el-color-primary);
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

/* ---- 多问题卡片 ---- */
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
  background: var(--el-color-primary);
  color: var(--el-color-white);
}

.opt-row.active:hover:not(:disabled) {
  background: var(--el-color-primary);
}

.opt-row.active .muted {
  color: var(--el-color-white);
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
  line-height: 1.5;
}

.opt-desc {
  font-size: 12px;
  line-height: 1.4;
}

.opt-check {
  flex: none;
  font-weight: 700;
}

.custom-row {
  cursor: default;
}

.custom-input {
  flex: 1;
  min-width: 0;
  padding: 4px 8px;
  border: 1px solid var(--el-border-color);
  border-radius: 4px;
  background: var(--el-fill-color-blank);
  color: inherit;
  font: inherit;
  font-size: 13px;
}

.custom-input:focus {
  outline: none;
  border-color: var(--el-color-primary);
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
