<script setup lang="ts">
/**
 * 写作子代理管理：内置 + 用户自建。
 *
 * 卡片只展示职责描述 + 启用/停用开关（悬浮说明）；编辑按钮带外框；内置可「恢复默认」。
 * 弹窗（无标题）：名称（如 my-writer）、描述、系统提示词（必填）、可用技能多选
 * （含内置工具 read/write/edit/list/grep/glob/web_search，支持全选）；启用在创建后使用。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { configApi, type SkillDto, type SubagentDto } from '@/api'
import { errorMessage } from '@/api/http'
import { t } from '@/i18n'

/** 用户技能 → deepagents 虚拟路径 */
const USER_SKILL_PREFIX = '/skills/user/'

const loading = ref(false)
const subagents = ref<SubagentDto[]>([])
const skills = ref<SkillDto[]>([])

/** 内置工具（read/write/edit/list/grep/glob/web_search）与自建技能分开呈现 */
const builtinTools = computed(() => skills.value.filter((s) => s.kind === 'tool'))
const userSkills = computed(() => skills.value.filter((s) => s.kind !== 'tool'))

const dialogVisible = ref(false)
const saving = ref(false)
const editing = ref<SubagentDto | null>(null)
const form = reactive({
  name: '',
  description: '',
  system_prompt: '',
  /** 已勾选的内置工具真实名（read_file/edit_file/ls/grep/glob/web_search） */
  tools: [] as string[],
  /** 已勾选的自建技能名 */
  skills: [] as string[],
})

/**
 * v1029：删除[上下文模式]，**默认采用独立上下文**（isolated）。
 * 子代理用量仍与主 Agent 一起计入 Token（后端 `project_usage` 按 run 汇总）。
 */
const SUBAGENT_MODE = 'isolated' as const

/** 弹窗是否编辑内置子代理（内置不可改名） */
const editingBuiltin = computed(() => Boolean(editing.value?.is_builtin))

/** 全部可选项（内置工具 + 自建技能）的合并 key 列表，供全选使用 */
const allOptionKeys = computed(() => [
  ...builtinTools.value.map((s) => `tool:${s.tool_name ?? s.name}`),
  ...userSkills.value.map((s) => `skill:${s.name}`),
])

/** key → 展示名：内置工具展示写作技能页同款名称（read/write/edit/list/grep/glob/web_search） */
const optionLabel = computed<Record<string, string>>(() => {
  const map: Record<string, string> = {}
  for (const s of builtinTools.value) map[`tool:${s.tool_name ?? s.name}`] = s.name
  for (const s of userSkills.value) map[`skill:${s.name}`] = s.name
  return map
})

/** 勾选集合（含全选）：key = `tool:{tool_name}` | `skill:{name}` */
const selectedKeys = computed<string[]>(() => [
  ...form.tools.map((x) => `tool:${x}`),
  ...form.skills.map((x) => `skill:${x}`),
])

const allSelected = computed(
  () => allOptionKeys.value.length > 0 && selectedKeys.value.length === allOptionKeys.value.length,
)

function toggleAll(): void {
  if (allSelected.value) {
    form.tools = []
    form.skills = []
    return
  }
  form.tools = builtinTools.value.map((s) => s.tool_name ?? s.name)
  form.skills = userSkills.value.map((s) => s.name)
}

/** 多选变化 → 拆回 tools / skills 两组 */
function onSelectionChange(keys: string[]): void {
  form.tools = keys.filter((k) => k.startsWith('tool:')).map((k) => k.slice('tool:'.length))
  form.skills = keys.filter((k) => k.startsWith('skill:')).map((k) => k.slice('skill:'.length))
}

const formError = computed(() => {
  if (!form.name.trim()) return `${t('subagents.nameLabel')}为必填`
  if (!form.description.trim()) return `${t('subagents.descLabel')}为必填`
  if (!form.system_prompt.trim()) return `${t('subagents.promptLabel')}为必填`
  return ''
})

async function load(): Promise<void> {
  loading.value = true
  try {
    const [list, sks] = await Promise.all([configApi.listSubagents(), configApi.listSkills()])
    subagents.value = list
    skills.value = sks
  } catch (err) {
    ElMessage.error(errorMessage(err))
  } finally {
    loading.value = false
  }
}

onMounted(load)

/** 把已存的 skills/tools 反解为弹窗勾选 key */
function keysOf(s: SubagentDto): string[] {
  const keys: string[] = []
  for (const name of s.tools ?? []) keys.push(`tool:${name}`)
  for (const path of s.skills ?? []) {
    // 技能 = 用户自建（/skills/user/xxx）；历史数据里的 /skills/builtin/xxx 绑定已失效，不回显
    const m = path.startsWith(USER_SKILL_PREFIX) ? path.slice(USER_SKILL_PREFIX.length) : ''
    if (m) keys.push(`skill:${m}`)
  }
  return keys
}

function openDialog(s?: SubagentDto): void {
  editing.value = s ?? null
  form.name = s?.name ?? ''
  form.description = s?.description ?? ''
  form.system_prompt = s?.system_prompt ?? ''
  // v1029：不再有[上下文模式]选项，一律按独立上下文（isolated）保存
  form.tools = []
  form.skills = []
  if (s) onSelectionChange(keysOf(s))
  dialogVisible.value = true
}

async function save(): Promise<void> {
  if (formError.value) {
    ElMessage.warning(formError.value)
    return
  }
  saving.value = true
  try {
    const payload = {
      name: form.name.trim(),
      description: form.description.trim(),
      system_prompt: form.system_prompt.trim(),
      mode: SUBAGENT_MODE,
      tools: form.tools,
      skills: form.skills.map((n) => `${USER_SKILL_PREFIX}${n}`),
    }
    if (editing.value) {
      await configApi.updateSubagent(editing.value.id, payload)
    } else {
      await configApi.createSubagent(payload)
    }
    ElMessage.success(t('common.saved'))
    dialogVisible.value = false
    await load()
  } catch (err) {
    ElMessage.error(errorMessage(err))
  } finally {
    saving.value = false
  }
}

async function toggleEnabled(s: SubagentDto, enabled: boolean): Promise<void> {
  try {
    await configApi.updateSubagent(s.id, { enabled })
    await load()
  } catch (err) {
    ElMessage.error(errorMessage(err))
    await load()
  }
}

async function remove(s: SubagentDto): Promise<void> {
  try {
    await ElMessageBox.confirm(`确定删除子代理「${s.name}」吗？`, t('common.delete'), {
      type: 'warning',
      confirmButtonText: t('common.delete'),
      cancelButtonText: t('common.cancel'),
    })
    await configApi.deleteSubagent(s.id)
    ElMessage.success('已删除')
    await load()
  } catch {
    /* 取消 */
  }
}

async function resetDefault(s: SubagentDto): Promise<void> {
  try {
    await ElMessageBox.confirm(
      t('subagents.resetConfirm', { name: s.name }),
      t('subagents.resetConfirmTitle'),
      { type: 'warning', confirmButtonText: t('common.confirm'), cancelButtonText: t('common.cancel') },
    )
    await configApi.resetSubagent(s.id)
    ElMessage.success(t('subagents.resetDone'))
    await load()
  } catch {
    /* 取消 */
  }
}
</script>

<template>
  <div v-loading="loading" class="subagent-manager">
    <div class="toolbar">
      <div />
      <el-button type="primary" @click="openDialog()">＋ {{ t('subagents.add') }}</el-button>
    </div>

    <div v-for="s in subagents" :key="s.id" class="subagent-card">
      <!-- v1029：左侧文案列（名称 + 描述，描述单行省略）｜右侧操作列（启用/编辑/删除）
           描述不会越过[启用]所在的右侧操作列 -->
      <div class="card-text">
        <div class="row">
          <strong class="sa-name">{{ s.name }}</strong>
          <el-tag size="small" :type="s.is_builtin ? 'info' : 'success'" effect="plain">
            {{ s.is_builtin ? t('subagents.builtin') : t('subagents.custom') }}
          </el-tag>
        </div>

        <!-- v1028：只展示职责；v1029：过长用省略号截断（悬停看全文） -->
        <div class="desc" :title="s.description">{{ s.description }}</div>
      </div>

      <div class="card-actions">
        <!-- v1028：启用/停用开关 + 文字；顶部提示语改为此处的悬浮说明 -->
        <el-tooltip :content="t('subagents.enableHint')" placement="top" :show-after="200">
          <span class="switch-wrap">
            <el-switch
              :model-value="s.enabled"
              size="small"
              @change="toggleEnabled(s, Boolean($event))"
            />
            <span class="switch-label">
              {{ s.enabled ? t('subagents.enabledLabel') : t('subagents.disabledLabel') }}
            </span>
          </span>
        </el-tooltip>

        <!-- v1028：编辑按钮加外框 -->
        <el-button size="small" plain @click="openDialog(s)">{{ t('skills.edit') }}</el-button>

        <!-- v1028：恢复出厂 → 恢复默认（仅内置） -->
        <el-button v-if="s.is_builtin" size="small" text @click="resetDefault(s)">
          {{ t('subagents.resetDefault') }}
        </el-button>
        <el-button v-else size="small" type="danger" plain @click="remove(s)">
          {{ t('common.delete') }}
        </el-button>
      </div>
    </div>

    <!-- 弹窗：无顶部标题；v1030 弹窗与字体/输入框等比例放大 -->
    <el-dialog v-model="dialogVisible" width="700px" class="subagent-dialog">
      <el-form :model="form" label-width="116px" label-position="left">
        <el-form-item :label="t('subagents.nameLabel')" required>
          <el-input
            v-model="form.name"
            :disabled="editingBuiltin"
            :placeholder="t('subagents.namePlaceholder')"
            maxlength="64"
          />
          <!-- ★v1102（SubAgents(4)）：新建时小红字提示，避免与内置子代理功能重复 -->
          <div v-if="!editing" class="name-dup-hint">{{ t('subagents.nameDupHint') }}</div>
        </el-form-item>

        <el-form-item :label="t('subagents.descLabel')" required>
          <el-input v-model="form.description" type="textarea" :rows="2" maxlength="500" />
        </el-form-item>

        <el-form-item :label="t('subagents.promptLabel')" required>
          <el-input v-model="form.system_prompt" type="textarea" :rows="6" />
        </el-form-item>

        <!-- v1029：删除[上下文模式]——默认独立上下文（isolated），Token 与主 Agent 合并计算 -->
        <!-- v1028：可用技能多选（支持全选），来源为写作技能（含内置工具） -->
        <!-- v1029：选中的技能在输入框内展示，最多显示 6 个（超出折叠为 +N） -->
        <el-form-item :label="t('subagents.skillsLabel')">
          <el-select
            :model-value="selectedKeys"
            multiple
            collapse-tags
            collapse-tags-tooltip
            :max-collapse-tags="6"
            :placeholder="t('subagents.skillsPlaceholder')"
            class="full"
            @update:model-value="onSelectionChange"
          >
            <el-option
              v-for="key in allOptionKeys"
              :key="key"
              :value="key"
              :label="optionLabel[key] ?? key"
            >
              <span>{{ optionLabel[key] ?? key }}</span>
              <span class="opt-kind muted">
                {{ key.startsWith('tool:') ? t('subagents.builtinToolsLabel') : t('skills.custom') }}
              </span>
            </el-option>
          </el-select>

          <div class="select-actions">
            <el-button size="small" text @click="toggleAll">
              {{ allSelected ? '取消全选' : t('subagents.selectAll') }}
            </el-button>
            <span v-if="selectedKeys.length" class="muted text-sm">
              已选 {{ selectedKeys.length }} 项
            </span>
          </div>
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="dialogVisible = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="saving" :disabled="!!formError" @click="save">
          {{ t('common.save') }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.subagent-manager {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}

.subagent-card {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 14px;
  display: flex;
  align-items: center;
  gap: 14px;
}

/* v1029：左侧文案列（弹性收缩，描述超长省略） */
.card-text {
  display: flex;
  flex-direction: column;
  gap: 6px;
  flex: 1;
  min-width: 0;
}

/* v1029：右侧操作列（启用开关 + 编辑 + 删除），不被描述挤压 */
.card-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.sa-name {
  font-size: 15px;
}

.switch-wrap {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
}

.switch-label {
  font-size: 12px;
  color: var(--text-muted);
}

/* v1029：描述单行省略，不越过[启用]（右侧操作列） */
.desc {
  font-size: 13px;
  color: var(--el-text-color-regular);
  line-height: 1.6;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.full {
  width: 100%;
}

/* ★v1102：新建子代理弹窗，名称下方的小红字提示 */
.name-dup-hint {
  color: var(--el-color-danger);
  font-size: 12px;
  line-height: 1.4;
  margin-top: 4px;
}

.select-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 4px;
}

.opt-kind {
  float: right;
  font-size: 11px;
}

.muted {
  color: var(--text-muted);
}

.text-sm {
  font-size: 12px;
}
</style>

<style>
/* 弹窗无标题时压缩顶部留白 */
.subagent-dialog .el-dialog__header {
  padding-bottom: 4px;
  margin-right: 0;
}

.subagent-dialog .el-dialog__body {
  padding-top: 10px;
  /* v1030：输入框不要长到右上角 × 的位置 */
  padding-right: 52px;
}

/* v1030：设置名字号放大，控件不挡住右上角 × */
.subagent-dialog .el-form-item__label {
  font-size: 15px;
  padding-right: 18px;
}

.subagent-dialog .el-form-item {
  margin-bottom: 20px;
}

.subagent-dialog .el-dialog__headerbtn {
  z-index: 2;
}
</style>
