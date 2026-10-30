<script setup lang="ts">
/**
 * 写作技能管理（v1028）：内置工具（只读）+ 用户自建（CRUD）。
 *
 * v1028 变更：
 * - 删除顶部提示语与「重新物化」按钮；
 * - 内置项改为展示 Agent 基础工具 read / edit / list / grep / glob / web_search（不展示描述）；
 * - 每条技能右侧显示「查看」「编辑」，内置工具两项均置灰；
 * - 新建弹窗：无顶部标题；「技能名」→「名称」；描述/正文删除输入框提示；
 *   技能正文必填；删除 allowed-tools 与启用开关（自建技能只包含提示词）。
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
const previewVisible = ref(false)
const saving = ref(false)
const editing = ref<SkillDto | null>(null)
const previewing = ref<SkillDto | null>(null)
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

function preview(s: SkillDto): void {
  previewing.value = s
  previewVisible.value = true
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
    <!-- v1028：删除顶部提示语，只保留「新建技能」入口 -->
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
          <el-tag v-if="!s.is_builtin && !s.enabled" size="small" type="info" effect="plain">
            已停用
          </el-tag>
        </div>

        <div class="row">
          <!-- v1028：查看 / 编辑；内置工具两项均置灰 -->
          <el-button size="small" text :disabled="s.is_builtin" @click="preview(s)">
            {{ t('skills.view') }}
          </el-button>
          <el-button size="small" plain :disabled="s.is_builtin" @click="openDialog(s)">
            {{ t('skills.edit') }}
          </el-button>
          <el-button v-if="!s.is_builtin" size="small" type="danger" plain @click="remove(s)">
            {{ t('common.delete') }}
          </el-button>
        </div>
      </div>

      <!-- v1028：内置工具不展示描述 -->
      <div v-if="!s.is_builtin && s.description" class="desc">{{ s.description }}</div>
    </div>

    <!-- 新建/编辑弹窗：无顶部标题；v1029 设置名字号更小、弹窗收窄（不挡右上角 ×） -->
    <el-dialog v-model="dialogVisible" width="560px" class="skill-dialog">
      <el-form :model="form" label-width="82px" label-position="left" size="small">
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

    <!-- 查看对话框 -->
    <el-dialog v-model="previewVisible" :title="`SKILL.md · ${previewing?.name ?? ''}`" width="700px">
      <pre class="skill-preview">{{ previewing?.content_md }}</pre>
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

.skill-preview {
  max-height: 60vh;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  background: var(--el-fill-color-light);
  border-radius: 6px;
  padding: 10px;
  margin: 0;
}
</style>

<style>
/* 弹窗无标题时压缩顶部留白 */
.skill-dialog .el-dialog__header {
  padding-bottom: 4px;
  margin-right: 0;
}

.skill-dialog .el-dialog__body {
  padding-top: 8px;
  /* v1029：输入框不要长到右上角 × 的位置 */
  padding-right: 44px;
}

/* v1029：设置名字号更小 */
.skill-dialog .el-form-item__label {
  font-size: 12px;
  padding-right: 16px;
}
</style>
