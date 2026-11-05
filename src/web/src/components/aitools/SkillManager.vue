<script setup lang="ts">
/**
 * 写作技能管理：内置基础工具（只读置灰）+ 用户自建（CRUD）。
 *
 * 变更：
 * - 技能 = 用户自建：包内不再随包携带写作技能目录，列表只有「内置工具 + 自建技能」两类；
 * - 所有技能都有「启用」设置（自建可切换、内置工具置灰恒启用）；
 * - 去掉「查看」按钮；用户自建技能有「删除」；三个设置顺序 = 启用、编辑、删除；
 * - 未启用的自建技能不可被主 Agent 调用、不可被子代理绑定（后端装配过滤）。
 *
 * 保留：新建弹窗无顶部标题；技能正文必填；自建技能只包含提示词。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { configApi, type SkillDto } from '@/api'
import { errorMessage } from '@/api/http'
import { t } from '@/i18n'

const loading = ref(false)
const skills = ref<SkillDto[]>([])

/** 内置工具在前、自建技能在后 */
const sortedSkills = computed(() =>
  [...skills.value].sort((a, b) => Number(b.is_builtin) - Number(a.is_builtin)),
)

const dialogVisible = ref(false)
const saving = ref(false)
const editing = ref<SkillDto | null>(null)
const form = reactive({
  name: '',
  description: '',
  body: '',
})

const formError = computed(() => {
  if (!form.name.trim()) return `${t('skills.nameLabel')}为必填`
  if (!/^[a-z][a-z0-9-]{0,63}$/.test(form.name.trim())) {
    return '名称须为小写字母开头，仅小写字母/数字/连字符（≤64 字符）'
  }
  if (!form.description.trim()) return `${t('skills.descLabel')}为必填`
  if (!form.body.trim()) return `${t('skills.bodyLabel')}为必填`
  return ''
})

async function load(): Promise<void> {
  loading.value = true
  try {
    skills.value = await configApi.listSkills()
  } catch (err) {
    ElMessage.error(errorMessage(err))
  } finally {
    loading.value = false
  }
}

/** 启用开关：仅用户自建技能可切换；内置工具置灰恒启用 */
async function toggleEnabled(s: SkillDto, enabled: boolean): Promise<void> {
  if (s.is_builtin) return
  try {
    await configApi.updateSkill(s.id, { enabled })
    await load()
  } catch (err) {
    ElMessage.error(errorMessage(err))
    await load()
  }
}

onMounted(load)

function extractBody(contentMd: string): string {
  return contentMd
    .replace(/^---\n[\s\S]*?\n---\n/, '')
    .replace(/^#\s+.*\n+/, '')
    .trim()
}

function openDialog(s?: SkillDto): void {
  editing.value = s && !s.is_builtin ? s : null
  form.name = s && !s.is_builtin ? s.name : ''
  form.description = s && !s.is_builtin ? s.description : ''
  form.body = s && !s.is_builtin ? extractBody(s.content_md) : ''
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
      body: form.body.trim(),
    }
    if (editing.value) {
      await configApi.updateSkill(editing.value.id, payload)
    } else {
      await configApi.createSkill(payload)
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

async function remove(s: SkillDto): Promise<void> {
  try {
    await ElMessageBox.confirm(t('skills.deleteConfirm', { name: s.name }), t('common.delete'), {
      type: 'warning',
      confirmButtonText: t('common.delete'),
      cancelButtonText: t('common.cancel'),
    })
    await configApi.deleteSkill(s.id)
    ElMessage.success('已删除')
    await load()
  } catch {
    /* 取消 */
  }
}
</script>

<template>
  <div v-loading="loading" class="skill-manager">
    <!-- 删除顶部提示语，只保留「新建技能」入口 -->
    <div class="toolbar">
      <div />
      <el-button type="primary" @click="openDialog()">＋ {{ t('skills.add') }}</el-button>
    </div>

    <div v-for="s in sortedSkills" :key="s.id" class="skill-card">
      <div class="card-head">
        <div class="row">
          <strong class="skill-name">{{ s.name }}</strong>
          <el-tag size="small" :type="s.is_builtin ? 'info' : 'success'" effect="plain">
            {{ s.is_builtin ? t('skills.builtin') : t('skills.custom') }}
          </el-tag>
          <el-tag v-if="!s.enabled" size="small" type="info" effect="plain">
            {{ t('skills.disabledTag') }}
          </el-tag>
        </div>

        <!-- 三个设置固定顺序 = 启用、编辑、删除（去掉「查看」）。
             内置工具（kind=tool）启用与编辑均置灰不可改；自建技能三项全可用。 -->
        <div class="row">
          <el-tooltip :content="t('skills.enableHint')" placement="top" :show-after="200">
            <span class="switch-wrap">
              <el-switch
                :model-value="s.enabled"
                size="small"
                :disabled="s.kind === 'tool'"
                @change="toggleEnabled(s, Boolean($event))"
              />
              <span class="switch-label">
                {{ s.enabled ? t('subagents.enabledLabel') : t('subagents.disabledLabel') }}
              </span>
            </span>
          </el-tooltip>

          <el-button size="small" plain :disabled="s.is_builtin" @click="openDialog(s)">
            {{ t('skills.edit') }}
          </el-button>

          <el-button v-if="!s.is_builtin" size="small" type="danger" plain @click="remove(s)">
            {{ t('common.delete') }}
          </el-button>
        </div>
      </div>

      <!-- 有描述才展示（内置工具带简短描述，自建技能带详情） -->
      <div v-if="s.description" class="desc">{{ s.description }}</div>
    </div>

    <!-- 新建/编辑弹窗：无顶部标题；弹窗与字体/输入框等比例放大 -->
    <el-dialog v-model="dialogVisible" width="700px" class="skill-dialog">
      <el-form :model="form" label-width="104px" label-position="left">
        <el-form-item :label="t('skills.nameLabel')" required>
          <el-input v-model="form.name" :disabled="!!editing" maxlength="64" />
        </el-form-item>

        <el-form-item :label="t('skills.descLabel')" required>
          <el-input v-model="form.description" type="textarea" :rows="2" maxlength="1024" show-word-limit />
        </el-form-item>

        <el-form-item :label="t('skills.bodyLabel')" required>
          <el-input v-model="form.body" type="textarea" :rows="12" />
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
.skill-manager {
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

.skill-card {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 5px;
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

/* 启用开关（与子代理页同款） */
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

.skill-name {
  font-size: 14px;
}

.desc {
  font-size: 13px;
  color: var(--el-text-color-regular);
  line-height: 1.6;
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
.skill-dialog .el-dialog__header {
  padding-bottom: 4px;
  margin-right: 0;
}

.skill-dialog .el-dialog__body {
  padding-top: 10px;
  /* 输入框不要长到右上角 × 的位置 */
  padding-right: 52px;
}

/* 设置名字号放大 */
.skill-dialog .el-form-item__label {
  font-size: 15px;
  padding-right: 18px;
}

.skill-dialog .el-form-item {
  margin-bottom: 20px;
}
</style>
