<script setup lang="ts">
/**
 * 「工具」浮层：首层两个入口 [写作子代理] / [写作技能]，无内容时整项不显示。
 *
 * 点击入口 → 对应目录（逐级跳转，不提供返回）；选中后由父级在输入框开头展示
 * `@工具名` 标记并持久化到会话（后端装配进 agent，模型主动使用）。
 * 关闭后重新打开回到初始界面，便于连续添加。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { configApi, type SkillDto, type SubagentDto } from '@/api'
import { errorMessage } from '@/api/http'
import { ElMessage } from 'element-plus'
import { t } from '@/i18n'

const emit = defineEmits<{
  /** 选中/取消选中一个工具（父级负责更新 chips 与会话持久化） */
  (e: 'toggle', kind: 'subagent' | 'skill', id: string, label: string): void
  (e: 'close'): void
}>()

const props = defineProps<{
  /** 当前已选中的工具 id（用于高亮） */
  selectedSubagentIds: string[]
  selectedSkillIds: string[]
  /** 浮层是否打开：关闭再打开时重置到初始界面 */
  open: boolean
}>()

type Level = 'root' | 'subagents' | 'skills'
const level = ref<Level>('root')
const loading = ref(false)

const subagents = ref<SubagentDto[]>([])
const skills = ref<SkillDto[]>([])

/**
 * 只列启用项；子代理供主代理委派。
 * [写作技能] = 用户自建技能 + 会话级内置写作技能；
 * 其余 Agent 基础工具 read/edit/list/… 对主代理恒可用，不作为会话级选项。
 */
const subagentList = computed(() => subagents.value.filter((s) => s.enabled))
const skillList = computed(() =>
  skills.value.filter((s) => s.enabled && (s.kind !== 'tool' || s.session_builtin === true)),
)

async function load(): Promise<void> {
  loading.value = true
  try {
    // picker=true：内置项只列 web_search（会话级写作技能），其余基础工具对主代理恒可用
    const [subs, sks] = await Promise.all([configApi.listSubagents(), configApi.listSkills(true)])
    subagents.value = subs
    skills.value = sks
  } catch (err) {
    // 加载失败不阻断输入，但给出可读提示
    ElMessage.error(errorMessage(err))
  } finally {
    loading.value = false
  }
}

onMounted(load)

function isSelected(kind: 'subagent' | 'skill', id: string): boolean {
  return kind === 'subagent'
    ? props.selectedSubagentIds.includes(id)
    : props.selectedSkillIds.includes(id)
}

function onPick(kind: 'subagent' | 'skill', id: string, name: string): void {
  emit('toggle', kind, id, name)
  emit('close')
}

/** 关闭后重新打开 → 回到初始界面（可继续添加其它工具） */
watch(
  () => props.open,
  (open) => {
    if (open) level.value = 'root'
  },
)
</script>

<template>
  <div class="tools-picker">
    <!-- 根层级：两个入口，没有内容的那项直接不显示 -->
    <template v-if="level === 'root'">
      <button
        v-if="subagentList.length"
        class="tp-entry"
        @click="level = 'subagents'"
      >
        <svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor" aria-hidden="true">
          <path d="M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zm0 2c-3.9 0-7 2.2-7 5v1h14v-1c0-2.8-3.1-5-7-5z" />
        </svg>
        <span>{{ t('agent.toolsWritingSubagents') }}</span>
        <span class="tp-count">{{ subagentList.length }}</span>
      </button>

      <button
        v-if="skillList.length"
        class="tp-entry"
        @click="level = 'skills'"
      >
        <svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor" aria-hidden="true">
          <path d="M12 2 9.2 8.6 2 9.3l5.5 4.8L5.8 21 12 17.3 18.2 21l-1.7-6.9L22 9.3l-7.2-.7L12 2z" />
        </svg>
        <span>{{ t('agent.toolsWritingSkills') }}</span>
        <span class="tp-count">{{ skillList.length }}</span>
      </button>

      <!-- 两类都没有时不显示任何提示（浮层留空即可） -->
      <div v-if="loading" class="tp-loading muted text-sm">{{ t('common.loading') }}</div>
    </template>

    <!-- 写作子代理目录（跳转过程中不需要返回） -->
    <template v-else-if="level === 'subagents'">
      <div class="tp-label">{{ t('agent.toolsWritingSubagents') }}</div>
      <button
        v-for="s in subagentList"
        :key="s.id"
        class="tp-item"
        :class="{ selected: isSelected('subagent', s.id) }"
        @click="onPick('subagent', s.id, s.name)"
      >
        <span class="tp-name">#{{ s.name }}</span>
        <span class="tp-desc">{{ s.description }}</span>
      </button>
    </template>

    <!-- 写作技能目录 -->
    <template v-else>
      <div class="tp-label">{{ t('agent.toolsWritingSkills') }}</div>
      <button
        v-for="s in skillList"
        :key="s.id"
        class="tp-item"
        :class="{ selected: isSelected('skill', s.id) }"
        @click="onPick('skill', s.id, s.name)"
      >
        <span class="tp-name">@{{ s.name }}</span>
        <span class="tp-desc">{{ s.description }}</span>
      </button>
    </template>
  </div>
</template>

<style scoped>
.tools-picker {
  display: flex;
  flex-direction: column;
  gap: 3px;
  max-height: 320px;
  overflow-y: auto;
}

.tp-entry {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 9px 10px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--text);
  font-size: 13px;
  font-weight: 600;
  text-align: left;
  cursor: pointer;
}

.tp-entry:hover {
  background: var(--bg-hover);
  color: var(--el-color-primary);
}

.tp-count {
  margin-left: auto;
  font-size: 11px;
  font-weight: 400;
  color: var(--text-muted);
}

.tp-label {
  padding: 4px 10px 2px;
  font-size: 11px;
  color: var(--text-muted);
}

.tp-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  width: 100%;
  padding: 7px 10px;
  border: none;
  border-radius: 5px;
  background: transparent;
  color: var(--text);
  text-align: left;
  cursor: pointer;
}

.tp-item:hover {
  background: var(--bg-hover);
}

.tp-item.selected {
  background: color-mix(in srgb, var(--el-color-primary) 14%, transparent);
}

.tp-item.selected .tp-name {
  color: var(--el-color-primary);
}

.tp-name {
  font-size: 13px;
  font-weight: 600;
}

.tp-desc {
  font-size: 11px;
  color: var(--text-muted);
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.tp-loading {
  padding: 8px 10px;
}

.muted {
  color: var(--text-muted);
}

.text-sm {
  font-size: 12px;
}
</style>
