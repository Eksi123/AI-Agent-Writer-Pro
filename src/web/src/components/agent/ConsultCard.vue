<script setup lang="ts">
/**
 * 咨询卡片（consult.request 事件，M4 门禁使用；M3 先落地组件与契约）。
 * 选项按钮 + 自由输入框；decision=option|text 走同一 resolve 接口。
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

/** 选项统一为 {value,label} */
const normalizedOptions = computed(() =>
  (props.consult.options ?? []).map((opt) =>
    typeof opt === 'string' ? { value: opt, label: opt } : { value: String(opt.value), label: String(opt.label ?? opt.value) },
  ),
)

async function choose(value: string): Promise<void> {
  submitting.value = true
  try {
    await agent.resolveConsult('option', value)
  } catch (err) {
    ElMessage.error(String(err))
  } finally {
    submitting.value = false
  }
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
    <div class="card-head">
      <el-tag type="primary" size="small">{{ t('agent.consultTitle') }}</el-tag>
      <el-tag v-if="consult.gateStage" size="small" type="info">{{ consult.gateStage }}</el-tag>
    </div>

    <div class="question">{{ consult.question }}</div>

    <div class="options">
      <el-button
        v-for="opt in normalizedOptions"
        :key="opt.value"
        size="small"
        :loading="submitting"
        @click="choose(opt.value)"
      >
        {{ opt.label }}
      </el-button>
      <el-button v-if="consult.allowText" size="small" text @click="textMode = !textMode">
        {{ t('agent.consultText') }}…
      </el-button>
    </div>

    <div v-if="textMode && consult.allowText" class="free-text">
      <el-input v-model="freeText" type="textarea" :rows="3" />
      <el-button type="primary" size="small" :loading="submitting" @click="submitText">
        {{ t('agent.consultSendText') }}
      </el-button>
    </div>
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
}

.question {
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
}

.options {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.free-text {
  display: flex;
  flex-direction: column;
  gap: 6px;
  align-items: flex-start;
}
</style>
