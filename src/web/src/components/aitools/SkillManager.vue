<script setup lang="ts">
/**
 * 写作技能管理（M5）：内置（只读，可查看）+ 用户自建（CRUD）。
 * 技能存 skill_configs 并物化到 data/skills/{name}/SKILL.md，经 /skills/user/ 供 Agent 只读加载。
 * frontmatter 约束：name == 目录名（小写连字符 ≤64），description ≤1024。
 */
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { configApi, type SkillDto } from '@/api'
import { errorMessage } from '@/api/http'
import { t } from '@/i18n'

const loading = ref(false)
const skills = ref<SkillDto[]>([])

const dialogVisible = ref(false)
const previewVisible = ref(false)
const saving = ref(false)
const editing = ref<SkillDto | null>(null)
const previewing = ref<SkillDto | null>(null)
const form = reactive({
  name: '',
  description: '',
  body: '',
  allowed_tools_text: '',
  enabled: true,
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

function openDialog(s?: SkillDto): void {
  editing.value = s && !s.is_builtin ? s : null
  form.name = s?.name ?? ''
  form.description = s?.description ?? ''
  form.body = s && !s.is_builtin ? extractBody(s.content_md) : ''
  form.allowed_tools_text = (s?.allowed_tools ?? []).join('\n')
  form.enabled = s?.enabled ?? true
  dialogVisible.value = true
}

function extractBody(contentMd: string): string {
  return contentMd
    .replace(/^---\n[\s\S]*?\n---\n/, '')
    .replace(/^#\s+.*\n+/, '')
    .trim()
}

function preview(s: SkillDto): void {
  previewing.value = s
  previewVisible.value = true
}

async function save(): Promise<void> {
  if (!form.name.trim() || !form.description.trim()) {
    ElMessage.warning(t('common.required'))
    return
  }
  const allowedTools = form.allowed_tools_text
    .split('\n')
    .map((s) => s.trim())
    .filter(Boolean)
  saving.value = true
  try {
    const payload = {
      name: form.name.trim(),
      description: form.description.trim(),
      body: form.body,
      allowed_tools: allowedTools,
      enabled: form.enabled,
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

async function toggleEnabled(s: SkillDto, enabled: boolean): Promise<void> {
  try {
    await configApi.updateSkill(s.id, { enabled })
    await load()
  } catch (err) {
    ElMessage.error(errorMessage(err))
    await load()
  }
}

async function remove(s: SkillDto): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `删除技能「${s.name}」会同时移除 data/skills/ 下的物化目录。确定吗？`,
      t('common.delete'),
      { type: 'warning', confirmButtonText: t('common.delete'), cancelButtonText: t('common.cancel') },
    )
    await configApi.deleteSkill(s.id)
    ElMessage.success('已删除')
    await load()
  } catch {
    /* 取消 */
  }
}

async function resync(): Promise<void> {
  try {
    const res = await configApi.resyncSkills()
    ElMessage.success(`已重新物化 ${res.materialized} 个技能`)
    await load()
  } catch (err) {
    ElMessage.error(errorMessage(err))
  }
}
</script>

<template>
  <div v-loading="loading" class="skill-manager">
    <div class="toolbar">
      <span class="muted text-sm">
        技能以 SKILL.md 渐进披露给 Agent（模型按需 read_file）；启用即物化到 data/skills/。
      </span>
      <div class="row">
        <el-button size="small" @click="resync">重新物化</el-button>
        <el-button type="primary" size="small" @click="openDialog()">＋ 新建技能</el-button>
      </div>
    </div>

    <div v-for="s in skills" :key="s.id" class="skill-card">
      <div class="card-head">
        <div class="row">
          <strong>{{ s.name }}</strong>
          <el-tag size="small" :type="s.is_builtin ? 'info' : 'success'">
            {{ s.is_builtin ? '内置' : '自建' }}
          </el-tag>
          <el-tag v-if="!s.is_builtin" size="small" :type="s.materialized ? 'success' : 'danger'">
            {{ s.materialized ? '已物化' : '未物化' }}
          </el-tag>
          <el-tag v-if="s.allowed_tools.length" size="small">
            工具：{{ s.allowed_tools.join(' ') }}
          </el-tag>
        </div>
        <div class="row">
          <el-button text size="small" @click="preview(s)">查看</el-button>
          <template v-if="!s.is_builtin">
            <el-switch
              :model-value="s.enabled"
              size="small"
              @change="toggleEnabled(s, Boolean($event))"
            />
            <el-button text size="small" @click="openDialog(s)">编辑</el-button>
            <el-button text size="small" type="danger" @click="remove(s)">
              {{ t('common.delete') }}
            </el-button>
          </template>
          <el-tag v-else size="small" type="info">只读</el-tag>
        </div>
      </div>
      <div class="desc">{{ s.description }}</div>
    </div>

    <!-- 编辑对话框 -->
    <el-dialog
      v-model="dialogVisible"
      :title="editing ? `编辑技能：${editing.name}` : '新建技能'"
      width="640px"
    >
      <el-form :model="form" label-width="110px">
        <el-form-item label="技能名" required>
          <el-input
            v-model="form.name"
            :disabled="!!editing"
            placeholder="小写字母/数字/连字符，须与物化目录名一致"
          />
          <div class="muted text-sm">改名会迁移物化目录；创建后不可再改。</div>
        </el-form-item>
        <el-form-item label="描述" required>
          <el-input
            v-model="form.description"
            type="textarea"
            :rows="2"
            maxlength="1024"
            show-word-limit
            placeholder="Agent 据此决定何时加载本技能（写清适用场景）"
          />
        </el-form-item>
        <el-form-item label="技能正文">
          <el-input
            v-model="form.body"
            type="textarea"
            :rows="10"
            placeholder="Markdown 正文：工作流程、检查表、示例、禁止事项…"
          />
        </el-form-item>
        <el-form-item label="allowed-tools">
          <el-input
            v-model="form.allowed_tools_text"
            type="textarea"
            :rows="2"
            placeholder="每行一个工具名（可选），如 web_search"
          />
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
  margin-bottom: 4px;
}

.skill-card {
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
