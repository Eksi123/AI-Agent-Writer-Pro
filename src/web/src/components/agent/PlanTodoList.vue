<script setup lang="ts">
/**
 * 计划待办列表（plan.update 事件 → write_todos 工具产出，手册「三、(3) 右侧栏」）。
 */
import { t } from '@/i18n'

defineProps<{
  todos: { content: string; status: string }[]
}>()

function iconFor(status: string): string {
  switch (status) {
    case 'completed':
      return '✓'
    case 'in_progress':
      return '◐'
    default:
      return '○'
  }
}

function classFor(status: string): string {
  return `todo-item todo-${status}`
}
</script>

<template>
  <div class="plan-todo">
    <div class="plan-title">{{ t('agent.planTitle') }}</div>
    <ul class="todo-list">
      <li v-for="(todo, i) in todos" :key="i" :class="classFor(todo.status)">
        <span class="todo-icon">{{ iconFor(todo.status) }}</span>
        <span class="todo-text">{{ todo.content }}</span>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.plan-todo {
  background: var(--el-fill-color-light);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 8px 10px;
}

.plan-title {
  font-size: 11px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
  margin-bottom: 6px;
}

.todo-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.todo-item {
  display: flex;
  gap: 6px;
  font-size: 12px;
  line-height: 1.5;
}

.todo-icon {
  flex-shrink: 0;
  width: 14px;
  text-align: center;
}

.todo-completed .todo-text {
  color: var(--el-text-color-secondary);
  text-decoration: line-through;
}

.todo-in_progress {
  color: var(--el-color-primary);
  font-weight: 500;
}

.todo-text {
  word-break: break-word;
}
</style>
