<script setup lang="ts">
/**
 * 大模型管理（v1028）：扁平化「一条 = 一个大模型」。
 *
 * 列表：左侧模型名称（加粗大字）+ 名称右边小字灰色显示 超时时长 / Temperature / Max Token；
 *       右侧 测试连接 / 编辑 / 删除 三个按钮。其余多余信息不展示。
 * 弹窗：无顶部标题，填写框整体左移；协议类型只三种（OpenAI Chat Completions /
 *       Anthropic Messages / Google Gemini，默认前者）；Base Url；密钥环境变量名（手动输入）；
 *       API 密钥（保存后写入环境变量，仅显示掩码）；超时时长（10~60s，默认 30）；
 *       Temperature（按协议类型校验范围，不含 0）；Max Token（默认 2048，不限上限）。全部必填。
 *
 * ★后端仍是 Provider + Model 两级：这里把二者收敛为一条配置——保存 Provider 的同时
 *   维护其**唯一伴随模型**（model_id 必填，否则无法真正调用大模型）。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { llmApi } from '@/api'
import { errorMessage } from '@/api/http'
import { t } from '@/i18n'
import type { LlmModelDto, LlmProviderDto, ProviderType } from '@/types'

const loading = ref(false)
const providers = ref<LlmProviderDto[]>([])

/** 协议类型：UI 三选项 ↔ 后端 provider.type */
const PROTOCOLS: { value: ProviderType; label: string; tempMax: number }[] = [
  { value: 'openai_compatible', label: 'OpenAI Chat Completions', tempMax: 2 },
  { value: 'anthropic', label: 'Anthropic Messages', tempMax: 1 },
  { value: 'google', label: 'Google Gemini', tempMax: 2 },
]

/** 旧数据的 type 归一到三选项之一（编辑时回填） */
function normalizeProtocol(type: string): ProviderType {
  if (type === 'anthropic') return 'anthropic'
  if (type === 'google') return 'google'
  return 'openai_compatible'
}

async function load(): Promise<void> {
  loading.value = true
  try {
    providers.value = await llmApi.listProviders()
  } catch (err) {
    ElMessage.error(errorMessage(err))
  } finally {
    loading.value = false
  }
}

onMounted(load)

function paramSummary(p: LlmProviderDto): string {
  return t('aiTools.paramSummary', {
    timeout: p.timeout_sec ?? 30,
    temp: (p.temperature ?? 1).toString(),
    max: p.max_tokens ?? 2048,
  })
}

// ---------------------------------------------------------------------------
// 配置弹窗
// ---------------------------------------------------------------------------

const dialogVisible = ref(false)
const saving = ref(false)
const editing = ref<LlmProviderDto | null>(null)

const form = reactive({
  name: '',
  model_id: '',
  type: 'openai_compatible' as ProviderType,
  base_url: '',
  api_key_env: '',
  api_key_value: '',
  timeout_sec: 30,
  temperature: 1.0,
  max_tokens: 2048,
})

const tempMax = computed(
  () => PROTOCOLS.find((p) => p.value === form.type)?.tempMax ?? 2,
)
const tempRangeHint = computed(() =>
  t('aiTools.tempRangeHint', { range: `0 ~ ${tempMax.value}` }),
)

/** Temperature 校验：不含 0，上限随协议类型变化 */
const tempError = computed(() => {
  const v = Number(form.temperature)
  if (!Number.isFinite(v)) return 'Temperature 必须是数字'
  if (v <= 0) return `Temperature 不包括 0，取值范围 0 ~ ${tempMax.value}`
  if (v > tempMax.value) return `Temperature 取值范围 0 ~ ${tempMax.value}`
  return ''
})

/** 全部配置必填 */
const formError = computed(() => {
  if (!form.name.trim()) return '名称为必填'
  if (!form.model_id.trim()) return '模型 ID 为必填'
  if (!form.api_key_env.trim()) return '密钥环境变量名为必填'
  if (!editing.value && !form.api_key_value.trim()) return 'API 密钥为必填'
  if (form.timeout_sec == null || form.timeout_sec < 10 || form.timeout_sec > 60) {
    return '超时时长须在 10 ~ 60 秒之间'
  }
  if (tempError.value) return tempError.value
  if (form.max_tokens == null || form.max_tokens < 1) return 'Max Token 须为正整数'
  return ''
})

function openDialog(p?: LlmProviderDto): void {
  editing.value = p ?? null
  form.name = p?.name ?? ''
  form.model_id = p?.models?.[0]?.model_id ?? ''
  form.type = normalizeProtocol(p?.type ?? 'openai_compatible')
  form.base_url = p?.base_url ?? ''
  form.api_key_env = p?.api_key_env ?? ''
  form.api_key_value = ''
  form.timeout_sec = p?.timeout_sec ?? 30
  form.temperature = p?.temperature ?? 1.0
  form.max_tokens = p?.max_tokens ?? 2048
  dialogVisible.value = true
}

/** 协议类型切换后，Temperature 超出新范围则回落到合法默认值 */
function onProtocolChange(): void {
  const v = Number(form.temperature)
  if (!Number.isFinite(v) || v <= 0 || v > tempMax.value) {
    form.temperature = form.type === 'anthropic' ? 1.0 : 1.0
  }
}

async function save(): Promise<void> {
  if (formError.value) {
    ElMessage.warning(formError.value)
    return
  }
  saving.value = true
  try {
    const name = form.name.trim()
    const modelId = form.model_id.trim()
    const payload = {
      name,
      type: form.type,
      base_url: form.base_url.trim() || undefined,
      api_key_env: form.api_key_env.trim(),
      api_key_value: form.api_key_value || undefined,
      timeout_sec: form.timeout_sec,
      temperature: Number(form.temperature),
      max_tokens: form.max_tokens,
    }

    let provider: LlmProviderDto
    if (editing.value) {
      provider = await llmApi.updateProvider(editing.value.id, payload)
    } else {
      provider = await llmApi.createProvider(payload)
    }

    // 维护唯一伴随模型（model_id 变化时先删旧的）
    const existing: LlmModelDto[] = provider.models ?? []
    for (const m of existing) {
      if (m.model_id !== modelId) await llmApi.deleteModel(m.id)
    }
    const keep = existing.find((m) => m.model_id === modelId)
    if (keep) {
      await llmApi.updateModel(keep.id, {
        model_id: modelId,
        display_name: name,
        is_default: keep.is_default,
      })
    } else {
      await llmApi.createModel({
        provider_id: provider.id,
        model_id: modelId,
        display_name: name,
        is_default: existing.length === 0,
      })
    }

    if (form.api_key_value) await llmApi.reloadConfig()

    ElMessage.success(t('aiTools.savedProvider'))
    dialogVisible.value = false
    await load()
  } catch (err) {
    ElMessage.error(errorMessage(err))
  } finally {
    saving.value = false
  }
}

const testingId = ref<string | null>(null)

async function testProvider(p: LlmProviderDto): Promise<void> {
  testingId.value = p.id
  try {
    const result = await llmApi.testProvider(p.id)
    if (result.ok) {
      ElMessage.success(t('aiTools.testOk', { ms: result.latency_ms ?? 0 }))
    } else {
      ElMessageBox.alert(result.message, t('aiTools.testFail'), { type: 'error' })
    }
  } catch (err) {
    ElMessage.error(errorMessage(err))
  } finally {
    testingId.value = null
  }
}

async function removeProvider(p: LlmProviderDto): Promise<void> {
  try {
    await ElMessageBox.confirm(
      t('aiTools.deleteProviderConfirm', { name: p.name }),
      t('common.delete'),
      { type: 'warning', confirmButtonText: t('common.delete'), cancelButtonText: t('common.cancel') },
    )
    await llmApi.deleteProvider(p.id)
    ElMessage.success(t('aiTools.deletedProvider'))
    await load()
  } catch {
    /* 取消 */
  }
}
</script>

<template>
  <div v-loading="loading" class="model-manager">
    <!-- 右上角「添加大模型」 -->
    <div class="toolbar">
      <el-button type="primary" @click="openDialog()">＋ {{ t('aiTools.addProvider') }}</el-button>
    </div>

    <el-empty v-if="!providers.length" :description="t('aiTools.noProviders')" :image-size="80" />

    <!-- 扁平列表：一条 = 一个大模型 -->
    <div v-for="p in providers" :key="p.id" class="model-row">
      <div class="row-left">
        <span class="model-name">{{ p.name }}</span>
        <span class="model-params">{{ paramSummary(p) }}</span>
        <el-tag v-if="!p.api_key_configured" size="small" type="danger" effect="plain">
          {{ t('aiTools.apiKeyMissing') }}
        </el-tag>
      </div>
      <div class="row-actions">
        <el-button size="small" :loading="testingId === p.id" @click="testProvider(p)">
          {{ t('aiTools.testConnection') }}
        </el-button>
        <el-button size="small" @click="openDialog(p)">{{ t('skills.edit') }}</el-button>
        <el-button size="small" type="danger" plain @click="removeProvider(p)">
          {{ t('common.delete') }}
        </el-button>
      </div>
    </div>

    <!-- 配置弹窗：无顶部标题，填写框整体左移 -->
    <el-dialog v-model="dialogVisible" width="560px" :show-close="true" class="provider-dialog">
      <el-form :model="form" label-width="118px" label-position="left">
        <el-form-item :label="t('aiTools.providerName')" required>
          <el-input v-model="form.name" maxlength="100" />
        </el-form-item>

        <el-form-item :label="t('aiTools.modelId')" required>
          <el-input v-model="form.model_id" :placeholder="t('aiTools.modelIdPlaceholder')" maxlength="120" />
        </el-form-item>

        <el-form-item :label="t('aiTools.providerType')" required>
          <el-select v-model="form.type" class="full" @change="onProtocolChange">
            <el-option v-for="o in PROTOCOLS" :key="o.value" :value="o.value" :label="o.label" />
          </el-select>
        </el-form-item>

        <el-form-item :label="t('aiTools.baseUrl')" required>
          <el-input v-model="form.base_url" maxlength="300" />
        </el-form-item>

        <el-form-item :label="t('aiTools.apiKeyEnv')" required>
          <el-input v-model="form.api_key_env" placeholder="如 QWEN_API_KEY" maxlength="80" />
        </el-form-item>

        <el-form-item :label="t('aiTools.apiKeyValue')" :required="!editing">
          <el-input
            v-model="form.api_key_value"
            type="password"
            show-password
            :placeholder="t('aiTools.apiKeyValuePlaceholder')"
          />
        </el-form-item>

        <el-form-item :label="t('aiTools.timeoutSec')" required>
          <!-- 左右带增减按钮的数字框：默认 30s，最少 10s，最多 60s -->
          <el-input-number v-model="form.timeout_sec" :min="10" :max="60" :step="1" />
          <span class="unit muted">s</span>
        </el-form-item>

        <el-form-item :label="t('aiTools.temperature')" required>
          <el-input v-model.number="form.temperature" class="temp-input" type="number" step="0.1" />
          <span class="hint muted">{{ tempRangeHint }}</span>
        </el-form-item>

        <el-form-item :label="t('aiTools.maxTokens')" required>
          <el-input v-model.number="form.max_tokens" class="temp-input" type="number" :min="1" />
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
.model-manager {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.toolbar {
  display: flex;
  justify-content: flex-end;
}

.model-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-panel);
}

.row-left {
  display: flex;
  align-items: baseline;
  gap: 10px;
  min-width: 0;
  flex-wrap: wrap;
}

/* v1028：模型名称加粗、字号大一点 */
.model-name {
  font-size: 15px;
  font-weight: 700;
  white-space: nowrap;
}

/* 名称右边小字灰色显示参数 */
.model-params {
  font-size: 12px;
  color: var(--text-muted);
  white-space: nowrap;
}

.row-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

.full {
  width: 100%;
}

.unit,
.hint {
  margin-left: 8px;
  font-size: 12px;
}

.temp-input {
  width: 150px;
}

.muted {
  color: var(--text-muted);
}
</style>

<style>
/* 弹窗无标题时压缩顶部留白，填写框整体更靠左 */
.provider-dialog .el-dialog__header {
  padding-bottom: 4px;
  margin-right: 0;
}

.provider-dialog .el-dialog__body {
  padding-top: 8px;
}
</style>
