<script setup lang="ts">
/**
 * 大模型管理（v1028 扁平化 / v1029 展示调整）：一条 = 一个大模型。
 *
 * 列表（v1029）：上方显示模型名称，下方用小字号显示 `Time Out=X  Temperature=X  Max Token=X`；
 *       右侧 测试连接 / 编辑 / 删除 三个按钮。
 * 弹窗：无顶部标题；v1029 设置名字号更小、一行展示，输入框收窄（不再挡住右上角 ×）。
 *       协议类型只三种（OpenAI Chat Completions / Anthropic Messages / Google Gemini）；
 *       Base Url；密钥环境变量名（可任意命名，仅排除与配置项同名的保留键）；
 *       API 密钥（保存后写入系统级环境变量，仅显示掩码）；
 *       Time Out（10~60s，默认 30）；Temperature（按协议类型校验范围，不含 0）；
 *       Max Token（默认 2048）。全部必填。
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

/** v1030：参数拆成三项分别渲染（用 flex gap 隔开，不再靠空格拼接） */
function paramParts(p: LlmProviderDto): string[] {
  return [
    `${t('aiTools.timeoutSec')}=${p.timeout_sec ?? 30}`,
    `${t('aiTools.temperature')}=${(p.temperature ?? 1).toString()}`,
    `${t('aiTools.maxTokens')}=${p.max_tokens ?? 2048}`,
  ]
}

// ---------------------------------------------------------------------------
// 配置弹窗
// ---------------------------------------------------------------------------

const dialogVisible = ref(false)
const saving = ref(false)
const editing = ref<LlmProviderDto | null>(null)

const form = reactive({
  name: '',
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

/** 全部配置必填（v1029：模型 ID 即名称，不再单独填写；v1030：[密钥环境变量名]→[密钥名]、[API密钥]→[密钥API]） */
const formError = computed(() => {
  if (!form.name.trim()) return '名称为必填'
  if (!form.api_key_env.trim()) return '密钥名为必填'
  if (!editing.value && !form.api_key_value.trim()) return '密钥API为必填'
  if (form.timeout_sec == null || form.timeout_sec < 10 || form.timeout_sec > 60) {
    return '超时时长须在 10 ~ 60 秒之间'
  }
  if (tempError.value) return tempError.value
  if (form.max_tokens == null || form.max_tokens < 1) return 'Max Token 须为正整数'
  return ''
})

function openDialog(p?: LlmProviderDto): void {
  editing.value = p ?? null
  // v1029：编辑时名称回填（旧数据的名称与模型 ID 本就一致；若不一致以名称为准）
  form.name = p?.name ?? ''
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
    // v1029：名称就等于模型 ID（不再有独立的「模型 ID」字段）
    const name = form.name.trim()
    const mid = name
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

    // 维护唯一伴随模型（名称/模型 ID 变化时先删旧的）
    const existing: LlmModelDto[] = provider.models ?? []
    for (const m of existing) {
      if (m.model_id !== mid) await llmApi.deleteModel(m.id)
    }
    const keep = existing.find((m) => m.model_id === mid)
    if (keep) {
      await llmApi.updateModel(keep.id, {
        model_id: mid,
        display_name: name,
        is_default: keep.is_default,
      })
    } else {
      await llmApi.createModel({
        provider_id: provider.id,
        model_id: mid,
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

    <!-- v1029：一条 = 一个大模型；上方模型名称，下方小字参数行 -->
    <div v-for="p in providers" :key="p.id" class="model-row">
      <div class="row-left">
        <div class="model-text">
          <div class="model-name">
            {{ p.name }}
            <el-tag v-if="!p.api_key_configured" size="small" type="danger" effect="plain">
              {{ t('aiTools.apiKeyMissing') }}
            </el-tag>
          </div>
          <div class="model-params">
            <span v-for="part in paramParts(p)" :key="part">{{ part }}</span>
          </div>
        </div>
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

    <!-- 配置弹窗：无顶部标题；v1030 弹窗与字体/输入框等比例放大 -->
    <el-dialog v-model="dialogVisible" width="600px" :show-close="true" class="provider-dialog">
      <el-form :model="form" label-width="128px" label-position="left">
        <el-form-item :label="t('aiTools.providerName')" required>
          <!-- v1029：名称即模型 ID（不再有单独的「模型 ID」输入框） -->
          <el-input v-model="form.name" class="field" maxlength="100" />
        </el-form-item>

        <el-form-item :label="t('aiTools.providerType')" required>
          <el-select v-model="form.type" class="field" @change="onProtocolChange">
            <el-option v-for="o in PROTOCOLS" :key="o.value" :value="o.value" :label="o.label" />
          </el-select>
        </el-form-item>

        <el-form-item :label="t('aiTools.baseUrl')" required>
          <el-input v-model="form.base_url" class="field" maxlength="300" />
        </el-form-item>

        <el-form-item :label="t('aiTools.apiKeyEnv')" required>
          <el-input v-model="form.api_key_env" class="field" maxlength="80" />
        </el-form-item>

        <el-form-item :label="t('aiTools.apiKeyValue')" :required="!editing">
          <el-input
            v-model="form.api_key_value"
            class="field"
            type="password"
            show-password
            :placeholder="t('aiTools.apiKeyValuePlaceholder')"
          />
        </el-form-item>

        <el-form-item :label="t('aiTools.timeoutSec')" required>
          <!-- 左右带增减按钮的数字框：默认 30s，最少 10s，最多 60s -->
          <el-input-number v-model="form.timeout_sec" :min="10" :max="60" :step="1" class="num" />
          <span class="unit muted">s</span>
        </el-form-item>

        <el-form-item :label="t('aiTools.temperature')" required>
          <el-input v-model.number="form.temperature" class="num" type="number" step="0.1" />
          <span class="hint muted">{{ tempRangeHint }}</span>
        </el-form-item>

        <el-form-item :label="t('aiTools.maxTokens')" required>
          <el-input v-model.number="form.max_tokens" class="num" type="number" :min="1" />
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
  /* v1030：条目加高，名称与参数行不再显得局促 */
  padding: 18px 16px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-panel);
}

.row-left {
  display: flex;
  align-items: baseline;
  gap: 10px;
  min-width: 0;
  flex: 1;
}

/* v1030：模型名称在上，参数小字在下；两行之间拉开间距 */
.model-text {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

.model-name {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 700;
  min-width: 0;
}

/* v1030：名称下方小字号显示 Time Out / Temperature / Max Token；三项用 gap 隔开更透气 */
.model-params {
  display: flex;
  align-items: center;
  gap: 22px;
  font-size: 11px;
  color: var(--text-muted);
  overflow: hidden;
}

.model-params > span {
  white-space: nowrap;
}

.row-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

/* v1030：弹窗整体等比例放大（宽度 + 字体 + 输入框） */
.field {
  width: 100%;
  max-width: 380px;
}

.num {
  width: 150px;
}

.unit,
.hint {
  margin-left: 10px;
  font-size: 13px;
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
  padding-top: 10px;
  /* v1030：输入框不要长到右上角 × 的位置 */
  padding-right: 52px;
}

/* v1030：设置名字号放大（一行展示），控件与设置名间距适中 */
.provider-dialog .el-form-item__label {
  font-size: 15px;
  padding-right: 18px;
}

.provider-dialog .el-form-item {
  margin-bottom: 20px;
}
</style>
