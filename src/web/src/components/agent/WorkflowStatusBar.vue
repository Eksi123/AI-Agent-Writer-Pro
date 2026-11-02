<script setup lang="ts">
/**
 * 流程状态条（手册「三、(3) 右侧栏」WorkflowStatusBar）：
 * S0 存在性 → S1 衔接预检 → S2 生成 → S3 匹配后检 的流转与结论展示。
 */
import { computed } from 'vue'

const props = defineProps<{
  /** 当前阶段：S0|S1|S2|S3|done|failed|null（非固定流程运行为 null） */
  stage: string | null
  /** S3 重生成次数 */
  retryCount?: number
  /** S1 是否被强制跳过 */
  g1Skipped?: boolean
  /** 各阶段结论（passed/failed/skipped） */
  results?: Record<string, string>
}>()

const STAGES = [
  { key: 'S0', label: '大纲存在性' },
  { key: 'S1', label: '衔接预检' },
  { key: 'S2', label: '生成正文' },
  { key: 'S3', label: '匹配后检' },
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
  return props.results?.S1 ?? ''
})
</script>

<template>
  <div v-if="stage" class="wf-status">
    <div class="wf-track">
      <template v-for="(s, i) in STAGES" :key="s.key">
        <div class="wf-node" :class="`wf-${statusOf(i)}`">
          <span class="wf-icon">{{ iconOf(i) }}</span>
          <span class="wf-label">{{ s.label }}</span>
        </div>
        <div v-if="i < STAGES.length - 1" class="wf-line" :class="{ 'wf-line-done': statusOf(i) === 'done' }" />
      </template>
    </div>

    <div class="wf-meta">
      <el-tag v-if="g1Result" size="small" :type="g1Skipped ? 'warning' : 'info'">
        S1: {{ g1Result }}
      </el-tag>
      <el-tag v-if="results?.S3" size="small" :type="results.S3 === 'passed' ? 'success' : 'warning'">
        S3: {{ results.S3 }}
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
.wf-status {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 8px 10px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  background: var(--el-fill-color-lighter);
}

.wf-track {
  display: flex;
  align-items: center;
  gap: 2px;
}

.wf-node {
  display: flex;
  align-items: center;
  gap: 3px;
  font-size: 11px;
  white-space: nowrap;
}

.wf-icon {
  font-size: 10px;
}

.wf-done {
  color: var(--el-color-success);
}

.wf-current {
  color: var(--el-color-primary);
  font-weight: 600;
}

.wf-current .wf-icon {
  animation: pulse 1.2s infinite ease-in-out;
}

.wf-todo {
  color: var(--el-text-color-placeholder);
}

.wf-failed {
  color: var(--el-color-danger);
}

.wf-line {
  flex: 1;
  height: 1px;
  background: var(--el-border-color);
  min-width: 8px;
}

.wf-line-done {
  background: var(--el-color-success);
}

.wf-meta {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}

@keyframes pulse {
  0%, 100% { opacity: 0.4; }
  50% { opacity: 1; }
}
</style>
