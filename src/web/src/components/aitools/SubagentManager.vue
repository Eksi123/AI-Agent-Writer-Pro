<script setup lang="ts">
/**
 * 写作子代理管理（M5）：内置 5 个 + 用户自建。
 * 内置不可删除/改名，可停用、改提示词、改模型覆盖、恢复出厂。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { configApi, llmApi, type SubagentDto } from '@/api'
import { errorMessage } from '@/api/http'
import { t } from '@/i18n'

const loading = ref(false)
const subagents = ref<SubagentDto[]>([])
const modelOptions = ref<{ label: string; value: string }[]>([])

const dialogVisible = ref(false)
const saving = ref(false)
const editing = ref<SubagentDto | null>(null)
const form = reactive({
  name: '',
  description: '',
  system_prompt: '',
  model_ref: '',
  mode: 'isolated' as 'isolated' | 'fork',
  skills_text: '',
  enabled: true,
})

const forkNoSkills = computed(() => form.mode === 'fork')

async function load(): Promise<void> {
  loading.value = true
  try {
    const [list, overview] = await Promise.all([configApi.listSubagents(), llmApi.overview()])
    subagents.value = list
    modelOptions.value = overview.models.map((m) => ({
      label: `${m.provider_name} / ${m.display_name}${m.is_default ? ' ★' : ''}`,
      value: m.model_ref,
    }))
  } catch (err) {
    ElMessage.error(errorMessage(err))
  } finally {
    loading.value = false
  }
}

onMounted(load)

function openDialog(s?: SubagentDto): void {
  editing.value = s ?? null
  form.name = s?.name ?? ''
  form.description = s?.description ?? ''
  form.system_prompt = s?.system_prompt ?? ''
  form.model_ref = s?.model_ref ?? ''
  form.mode = s?.mode ?? 'isolated'
  form.skills_text = (s?.skills ?? []).join('\n')
  form.enabled = s?.enabled ?? true
  dialogVisible.value = true
}

async function save(): Promise<void> {
  if (!form.name.trim() || !form.description.trim()) {
    ElMessage.warning(t('common.required'))
    return
  }
  const skills = form.skills_text
    .split('\n')
    .map((s) => s.trim())
    .filter(Boolean)
  if (form.mode === 'fork' && skills.length) {
    ElMessage.warning('fork 模式不能配置技能（继承父上下文，deepagents 限制）')
    return
  }
  saving.value = true
  try {
    const payload = {
      name: form.name.trim(),
      description: form.description.trim(),
      system_prompt: form.system_prompt || undefined,
      model_ref: form.model_ref || undefined,
      mode: form.mode,
      skills,
      enabled: form.enabled,
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

async function reset(s: SubagentDto): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `恢复「${s.name}」为出厂配置？你的自定义提示词与模型覆盖将被清除。`,
      '恢复出厂',
      { type: 'warning', confirmButtonText: t('common.confirm'), cancelButtonText: t('common.cancel') },
    )
    await configApi.resetSubagent(s.id)
    ElMessage.success('已恢复出厂配置')
    await load()
  } catch {
    /* 取消 */
  }
}
</script>

<template>
  <div v-loading="loading" class="subagent-manager">
    <div class="toolbar">
      <span class="muted text-sm">
        子代理供主代理按需委派（策划 / 撰写 / 润色 / 连贯性 / 资料检索）；停用后主代理不再委派。
      </span>
      <el-button type="primary" size="small" @click="openDialog()">＋ 新建子代理</el-button>
    </div>

    <div v-for="s in subagents" :key="s.id" class="subagent-card">
      <div class="card-head">
        <div class="row">
          <strong>{{ s.name }}</strong>
          <el-tag size="small" :type="s.is_builtin ? 'info' : 'success'">
            {{ s.is_builtin ? '内置' : '自建' }}
          </el-tag>
          <el-tag size="small">{{ s.mode }}</el-tag>
          <el-tag v-if="s.model_ref" size="small" type="warning">模型覆盖</el-tag>
        </div>
        <div class="row">
          <el-switch
            :model-value="s.enabled"
            size="small"
            @change="toggleEnabled(s, Boolean($event))"
          />
          <el-button text size="small" @click="openDialog(s)">编辑</el-button>
          <el-button v-if="s.is_builtin" text size="small" @click="reset(s)">恢复出厂</el-button>
          <el-button v-else text size="small" type="danger" @click="remove(s)">
            {{ t('common.delete') }}
          </el-button>
        </div>
      </div>
      <div class="desc">{{ s.description }}</div>
      <div v-if="s.skills.length" class="muted text-sm">技能：{{ s.skills.join('、') }}</div>
    </div>

    <el-dialog
      v-model="dialogVisible"
      :title="editing ? `编辑子代理：${editing.name}` : '新建子代理'"
      width="620px"
    >
      <el-form :model="form" label-width="110px">
        <el-form-item label="名称" required>
          <el-input
            v-model="form.name"
            :disabled="!!editing?.is_builtin"
            placeholder="小写字母/数字/连字符，如 my-reviewer"
          />
        </el-form-item>
        <el-form-item label="职责描述" required>
          <el-input
            v-model="form.description"
            type="textarea"
            :rows="2"
            placeholder="主代理据此决定何时委派给它"
          />
        </el-form-item>
        <el-form-item label="系统提示词">
          <el-input v-model="form.system_prompt" type="textarea" :rows="6" />
        </el-form-item>
        <el-form-item label="模型覆盖">
          <el-select v-model="form.model_ref" clearable placeholder="默认继承主模型" style="width: 100%">
            <el-option v-for="m in modelOptions" :key="m.value" :value="m.value" :label="m.label" />
          </el-select>
        </el-form-item>
        <el-form-item label="上下文模式">
          <el-radio-group v-model="form.mode">
            <el-radio value="isolated">isolated（独立上下文）</el-radio>
            <el-radio value="fork">fork（继承父上下文，禁用技能）</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="可用技能">
          <el-input
            v-model="form.skills_text"
            type="textarea"
            :rows="3"
            :disabled="forkNoSkills"
            placeholder="每行一个虚拟路径，如 /skills/builtin/prose-writing"
          />
          <div v-if="forkNoSkills" class="muted text-sm">fork 模式下技能被禁用</div>
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.enabled" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="saving" @click="save">{{ t('common.save') }}</el-button>
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
  margin-bottom: 4px;
}

.subagent-card {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
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

.desc {
  font-size: 13px;
  color: var(--el-text-color-regular);
}

.muted {
  color: var(--el-text-color-secondary);
}

.text-sm {
  font-size: 12px;
}
</style>
