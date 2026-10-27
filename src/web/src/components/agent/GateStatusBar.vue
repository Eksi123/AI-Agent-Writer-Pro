<script setup lang="ts">
/**
 * 门禁状态条（手册「三、(3) 右侧栏」GateStatusBar）：
 * G0 存在性 → G1 衔接预检 → G2 生成 → G3 匹配后检 的流转与结论展示。
 */
import { computed } from 'vue'

const props = defineProps<{
  /** 当前阶段：G0|G1|G2|G3|done|failed|null（非门禁运行为 null） */
  stage: string | null
  /** G3 重生成次数 */
  retryCount?: number
  /** G1 是否被强制跳过 */
  g1Skipped?: boolean
  /** 各阶段结论（passed/failed/skipped） */
  results?: Record<string, string>
}>()

const STAGES = [
  { key: 'G0', label: '大纲存在性' },
  { key: 'G1', label: '衔接预检' },
  { key: 'G2', label: '生成正文' },
  { key: 'G3', label: '匹配后检' },
]

const stageIndex = computed(() => {
  if (!props.stage) return -1
  if (props.stage === 'done') return STAGES.length
  if (props.stage === 'failed') return -1
  return STAGES.findIndex((s) => s.key === props.stage)
})

function statusOf(index: number): 'done' | 'current' | 'todo' | 'failed' {
  if (props.stage === 'failed') return 'failed'
  if (props.stage === 'done') return 'done'
  if (index < stageIndex.value) return 'done'
  if (index === stageIndex.value) return 'current'
  return 'todo'
}

function iconOf(index: number): string {
  const status = statusOf(index)
  if (status === 'done') return '✓'
  if (status === 'current') return '●'
  if (status === 'failed') return '✕'
  return '○'
}

const g1Result = computed(() => {
  if (props.g1Skipped) return '强制跳过'
  return props.results?.G1 ?? ''
})
</script>

<template>
  <div v-if="stage" class="gate-status">
    <div class="gate-track">
      <template v-for="(s, i) in STAGES" :key="s.key">
        <div class="gate-node" :class="`gate-${statusOf(i)}`">
          <span class="gate-icon">{{ iconOf(i) }}</span>
          <span class="gate-label">{{ s.label }}</span>
        </div>
        <div v-if="i < STAGES.length - 1" class="gate-line" :class="{ 'gate-line-done': statusOf(i) === 'done' }" />
      </template>
    </div>

    <div class="gate-meta">
      <el-tag v-if="g1Result" size="small" :type="g1Skipped ? 'warning' : 'info'">
        G1: {{ g1Result }}
      </el-tag>
      <el-tag v-if="results?.G3" size="small" :type="results.G3 === 'passed' ? 'success' : 'warning'">
        G3: {{ results.G3 }}
      </el-tag>
      <el-tag v-if="(retryCount ?? 0) > 0" size="small" type="warning">
        重生成 {{ retryCount }}/{{ 2 }}
      </el-tag>
      <el-tag v-if="stage === 'done'" size="small" type="success">已完成</el-tag>
      <el-tag v-if="stage === 'failed'" size="small" type="danger">未完成</el-tag>
    </div>
  </div>
</template>

<style scoped>
.gate-status {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 8px 10px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  background: var(--el-fill-color-lighter);
}

.gate-track {
  display: flex;
  align-items: center;
  gap: 2px;
}

.gate-node {
  display: flex;
  align-items: center;
  gap: 3px;
  font-size: 11px;
  white-space: nowrap;
}

.gate-icon {
  font-size: 10px;
}

.gate-done {
  color: var(--el-color-success);
}

.gate-current {
  color: var(--el-color-primary);
  font-weight: 600;
}

.gate-current .gate-icon {
  animation: pulse 1.2s infinite ease-in-out;
}

.gate-todo {
  color: var(--el-text-color-placeholder);
}

.gate-failed {
  color: var(--el-color-danger);
}

.gate-line {
  flex: 1;
  height: 1px;
  background: var(--el-border-color);
  min-width: 8px;
}

.gate-line-done {
  background: var(--el-color-success);
}

.gate-meta {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}

@keyframes pulse {
  0%, 100% { opacity: 0.4; }
  50% { opacity: 1; }
}
</style>
