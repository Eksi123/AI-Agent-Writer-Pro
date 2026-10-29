<script setup lang="ts">
/**
 * 写作子代理管理（v1028）：内置 + 用户自建。
 *
 * v1028 变更：
 * - 顶部提示语不再展示，改为「启用/停用」开关的悬浮说明；
 * - 卡片只展示职责（描述），删除技能展示；
 * - 开关旁加「启用/停用」文字；编辑按钮加外框；「恢复出厂」→「恢复默认」（仅内置）；
 * - 弹窗删除顶部标题、删除模型覆盖、删除启用开关（启用在创建后使用）；
 *   名称提示改「如my-writer」；「职责描述」→「描述」并删提示；系统提示词必填；
 *   可用技能改为多选（支持全选），来源为写作技能（含内置工具 read/edit/list/grep/glob/web_search）。
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

/** 内置工具（read/edit/list/grep/glob/web_search）与自建技能分开呈现 */
const builtinTools = computed(() => skills.value.filter((s) => s.is_builtin))
const userSkills = computed(() => skills.value.filter((s) => !s.is_builtin))

const dialogVisible = ref(false)
const saving = ref(false)
const editing = ref<SubagentDto | null>(null)
const form = reactive({
  name: '',
  description: '',
  system_prompt: '',
  mode: 'isolated' as 'isolated' | 'fork',
  /** 已勾选的内置工具真实名（read_file/edit_file/ls/grep/glob/web_search） */
  tools: [] as string[],
  /** 已勾选的自建技能名 */
  skills: [] as string[],
})

/** 弹窗是否编辑内置子代理（内置不可改名） */
const editingBuiltin = computed(() => Boolean(editing.value?.is_builtin))

/** 全部可选项（内置工具 + 自建技能）的合并 key 列表，供全选使用 */
const allOptionKeys = computed(() => [
  ...builtinTools.value.map((s) => `tool:${s.tool_name ?? s.name}`),
  ...userSkills.value.map((s) => `skill:${s.name}`),
])

/** key → 展示名：内置工具展示写作技能页同款名称（read/edit/list/grep/glob/web_search） */
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
    // 旧数据可能是 /skills/user/xxx 或 /skills/builtin/xxx；只回显自建技能
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
  form.mode = s?.mode ?? 'isolated'
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
      mode: form.mode,
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
      <div class="card-head">
        <div class="row">
          <strong class="sa-name">{{ s.name }}</strong>
          <el-tag size="small" :type="s.is_builtin ? 'info' : 'success'" effect="plain">
            {{ s.is_builtin ? t('subagents.builtin') : t('subagents.custom') }}
          </el-tag>
        </div>

        <div class="row">
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

      <!-- v1028：只展示职责，删除技能展示 -->
      <div class="desc">{{ s.description }}</div>
    </div>

    <!-- 弹窗：无顶部标题 -->
    <el-dialog v-model="dialogVisible" width="620px" class="subagent-dialog">
      <el-form :model="form" label-width="100px" label-position="left">
        <el-form-item :label="t('subagents.nameLabel')" required>
          <el-input
            v-model="form.name"
            :disabled="editingBuiltin"
            :placeholder="t('subagents.namePlaceholder')"
            maxlength="64"
          />
        </el-form-item>

        <el-form-item :label="t('subagents.descLabel')" required>
          <el-input v-model="form.description" type="textarea" :rows="2" maxlength="500" />
        </el-form-item>

        <el-form-item :label="t('subagents.promptLabel')" required>
          <el-input v-model="form.system_prompt" type="textarea" :rows="6" />
        </el-form-item>

        <el-form-item label="上下文模式">
          <el-radio-group v-model="form.mode">
            <el-radio value="isolated">isolated（独立上下文）</el-radio>
            <el-radio value="fork">fork（继承父上下文，禁用技能）</el-radio>
          </el-radio-group>
        </el-form-item>

        <!-- v1028：可用技能多选（支持全选），来源为写作技能（含内置工具） -->
        <el-form-item :label="t('subagents.skillsLabel')">
          <el-select
            :model-value="selectedKeys"
            multiple
            collapse-tags
            collapse-tags-tooltip
            :placeholder="t('subagents.skillsPlaceholder')"
            class="full"
            :disabled="form.mode === 'fork'"
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
            <el-button size="small" text :disabled="form.mode === 'fork'" @click="toggleAll">
              {{ allSelected ? '取消全选' : t('subagents.selectAll') }}
            </el-button>
            <span v-if="selectedKeys.length" class="muted text-sm">
              已选 {{ selectedKeys.length }} 项
            </span>
          </div>
          <div v-if="form.mode === 'fork'" class="muted text-sm">fork 模式下技能与工具被禁用</div>
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
  flex-direction: column;
  gap: 6px;
}

.card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
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

.desc {
  font-size: 13px;
  color: var(--el-text-color-regular);
  line-height: 1.6;
}

.full {
  width: 100%;
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
  padding-top: 8px;
}
</style>
