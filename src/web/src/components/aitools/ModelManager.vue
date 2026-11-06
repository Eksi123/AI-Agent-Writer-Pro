<script setup lang="ts">
/**
 * 大模型管理：一条 = 一个大模型（前端把 Provider + 其唯一伴随模型收敛为一条配置）。
 *
 * 列表：模型名 + 小字 `Time Out / Temperature / Max Token` + 测试连接 / 编辑 / 删除。
 * 弹窗（无标题）：协议类型三选一（OpenAI Chat / Anthropic Messages / Google Gemini）、
 * Base Url、密钥环境变量名（任意命名，仅排除与配置项同名）、API 密钥（存系统级环境变量、
 * 只显示掩码）、Time Out（10~60s）、Temperature（按协议校验、不含 0）、Max Token，全部必填。
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

/** 参数拆成多项分别渲染（用 flex gap 隔开，不再靠空格拼接）
 * 追加 Context Length（伴随模型的 max_context，token → K） */
function paramParts(p: LlmProviderDto): string[] {
  const win = p.models?.[0]?.max_context
  const ctxK = win && win > 0 ? Math.round(win / 1024) : null
  return [
    `${t('aiTools.timeoutSec')}=${p.timeout_sec ?? 30}`,
    `${t('aiTools.temperature')}=${(p.temperature ?? 1).toString()}`,
    `${t('aiTools.maxTokens')}=${Math.round((p.max_tokens ?? 32768) / 1024)}K`,
    ...(ctxK ? [`${t('aiTools.contextLength')}=${ctxK}K`] : []),
  ]
}

// ---------------------------------------------------------------------------
// 配置弹窗
// ---------------------------------------------------------------------------

const dialogVisible = ref(false)
const saving = ref(false)
const editing = ref<LlmProviderDto | null>(null)
/** 编辑时后端返回的掩码值（如 `sk-****abcd`），用于判断用户是否修改了密钥 */
const originalMaskedKey = ref('')

const form = reactive({
  name: '',
  type: 'openai_compatible' as ProviderType,
  base_url: '',
  api_key_env: '',
  api_key_value: '',
  timeout_sec: 30,
  temperature: 1.0,
  /** Max Token 以 K 为单位（1K = 1024 token），默认 32K，最多 128K */
  max_tokens_k: 32,
  /** Context Length（单位 K，1K = 1024 token），默认 256K，取值 100 ~ 1024K */
  context_length_k: 256,
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
  if (!form.api_key_env.trim()) return '密钥名为必填'
  if (!editing.value && !form.api_key_value.trim()) return '密钥API为必填'
  if (form.timeout_sec == null || form.timeout_sec < 10 || form.timeout_sec > 60) {
    return '超时时长须在 10 ~ 60 秒之间'
  }
  if (tempError.value) return tempError.value
  if (form.max_tokens_k == null || form.max_tokens_k < 1 || form.max_tokens_k > 128) {
    return 'Max Token 须在 1 ~ 128 K 之间'
  }
  if (form.context_length_k == null || form.context_length_k < 100 || form.context_length_k > 1024) {
    return 'Context Length 须在 100 ~ 1024 K 之间'
  }
  return ''
})

function openDialog(p?: LlmProviderDto): void {
  editing.value = p ?? null
  // 编辑时名称回填（旧数据的名称与模型 ID 本就一致；若不一致以名称为准）
  form.name = p?.name ?? ''
  form.type = normalizeProtocol(p?.type ?? 'openai_compatible')
  form.base_url = p?.base_url ?? ''
  form.api_key_env = p?.api_key_env ?? ''
  // 编辑时回填掩码（后端不返回明文，只返回脱敏后的值如 `sk-****abcd`）
  form.api_key_value = p?.api_key_masked ?? ''
  originalMaskedKey.value = p?.api_key_masked ?? ''
  form.timeout_sec = p?.timeout_sec ?? 30
  form.temperature = p?.temperature ?? 1.0
  // Max Token 回填（token → K；旧数据缺省 32K）
  form.max_tokens_k = p?.max_tokens ? Math.min(128, Math.max(1, Math.round(p.max_tokens / 1024))) : 32
  // Context Length 回填（token → K；旧数据缺省 256K）
  const win = p?.models?.[0]?.max_context
  form.context_length_k = win && win > 0 ? Math.min(1024, Math.max(100, Math.round(win / 1024))) : 256
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
    // 名称就等于模型 ID（不再有独立的「模型 ID」字段）
    const name = form.name.trim()
    const mid = name
    const payload = {
      name,
      type: form.type,
      base_url: form.base_url.trim() || undefined,
      api_key_env: form.api_key_env.trim(),
      // 编辑时：密钥未修改（仍为掩码）则不发送，避免把掩码当真实密钥写入
      api_key_value:
        editing.value && form.api_key_value === originalMaskedKey.value
          ? undefined
          : form.api_key_value || undefined,
      // 编辑时：用户清空了密钥字段 → 清除密钥
      clear_api_key:
        editing.value && !form.api_key_value && originalMaskedKey.value ? true : undefined,
      timeout_sec: form.timeout_sec,
      temperature: Number(form.temperature),
      max_tokens: form.max_tokens_k * 1024,
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
        // Context Length（K → token）随模型一起保存
        max_context: form.context_length_k * 1024,
      })
    } else {
      await llmApi.createModel({
        provider_id: provider.id,
        model_id: mid,
        display_name: name,
        is_default: existing.length === 0,
        max_context: form.context_length_k * 1024,
      })
    }

    // 密钥实际被修改时才触发后端重载配置
    const keyChanged = form.api_key_value && form.api_key_value !== originalMaskedKey.value
    if (keyChanged) await llmApi.reloadConfig()

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

    <!-- 一条 = 一个大模型；上方模型名称，下方小字参数行 -->
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

    <!-- 配置弹窗：无顶部标题；弹窗与字体/输入框等比例放大 -->
    <el-dialog v-model="dialogVisible" width="600px" :show-close="true" class="provider-dialog">
      <el-form :model="form" label-width="128px" label-position="left">
        <el-form-item required>
        <!-- 名称左边加提示 icon，点击弹出说明（名称必须与模型真实 ID 相同） -->
          <template #label>
            <span class="name-label">
              <el-popover trigger="click" :width="240" placement="right" :teleported="false">
                <template #reference>
                  <svg
                    class="hint-icon"
                    viewBox="0 0 24 24"
                    width="14"
                    height="14"
                    fill="currentColor"
                    aria-hidden="true"
                  >
                    <path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm0 15.5a1.2 1.2 0 1 1 0-2.4 1.2 1.2 0 0 1 0 2.4zm1.5-4.6c-.6.5-1 .9-1 1.6h-1.6c0-1.2.7-1.9 1.4-2.5.6-.5 1.2-1 1.2-1.8 0-.9-.7-1.5-1.6-1.5-.9 0-1.6.6-1.7 1.5H8.6c.1-1.8 1.5-3 3.3-3 1.8 0 3.2 1.2 3.2 2.9 0 1.3-.8 2-1.6 2.8z" />
                  </svg>
                </template>
                <span class="hint-pop-text">{{ t('aiTools.nameHint') }}</span>
              </el-popover>
              <span>{{ t('aiTools.providerName') }}</span>
            </span>
          </template>
          <!-- 名称即模型 ID（不再有单独的「模型 ID」输入框） -->
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

        <el-form-item required>
          <template #label>
            <el-tooltip :content="t('aiTools.timeoutSecHint')" placement="top">
              <span class="label-tip">{{ t('aiTools.timeoutSec') }}</span>
            </el-tooltip>
          </template>
          <!-- 左右带增减按钮的数字框：默认 30s，最少 10s，最多 60s -->
          <el-input-number v-model="form.timeout_sec" :min="10" :max="60" :step="1" class="num" />
          <span class="unit muted">s</span>
        </el-form-item>

        <el-form-item required>
          <template #label>
            <el-tooltip :content="t('aiTools.temperatureHint')" placement="top">
              <span class="label-tip">{{ t('aiTools.temperature') }}</span>
            </el-tooltip>
          </template>
          <el-input v-model.number="form.temperature" class="num" type="number" step="0.1" />
          <span class="hint muted">{{ tempRangeHint }}</span>
        </el-form-item>

        <el-form-item required>
          <template #label>
            <el-tooltip :content="t('aiTools.maxTokensHint')" placement="top">
              <span class="label-tip">{{ t('aiTools.maxTokens') }}</span>
            </el-tooltip>
          </template>
          <!-- Max Token 以 K 为单位（默认 32K，最多 128K）；右侧标明取值范围 -->
          <el-input v-model.number="form.max_tokens_k" class="num" type="number" :min="1" :max="128" />
          <span class="unit muted">K</span>
          <span class="hint muted">{{ t('aiTools.valueRangeHint', { range: '1 ~ 128 K' }) }}</span>
        </el-form-item>

        <!-- Context Length 必填（单位 K，默认 1024K）；悬浮展示简短解释 -->
        <el-form-item required>
          <template #label>
            <el-tooltip :content="t('aiTools.contextLengthHint')" placement="top">
              <span class="label-tip">{{ t('aiTools.contextLength') }}</span>
            </el-tooltip>
          </template>
          <el-input v-model.number="form.context_length_k" class="num" type="number" :min="100" :max="1024" />
          <span class="unit muted">K</span>
          <span class="hint muted">{{ t('aiTools.valueRangeHint', { range: '100 ~ 1024 K' }) }}</span>
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
  /* 条目加高，名称与参数行不再显得局促 */
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

/* 模型名称在上，参数小字在下；两行之间拉开间距 */
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

/* 名称下方小字号显示 Time Out / Temperature / Max Token；三项用 gap 隔开更透气 */
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

/* 弹窗整体等比例放大（宽度 + 字体 + 输入框） */
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
  /* 输入框不要长到右上角 × 的位置 */
  padding-right: 52px;
}

/* 设置名字号放大（一行展示），控件与设置名间距适中
字号调小 + 不换行，保证「Context Length」等长标签一行展示 */
.provider-dialog .el-form-item__label {
  font-size: 13px;
  padding-right: 18px;
  white-space: nowrap;
}

.provider-dialog .el-form-item {
  margin-bottom: 20px;
}

/* 参数名悬浮解释（虚线下划线提示可悬浮） */
.label-tip {
  cursor: help;
  border-bottom: 1px dashed var(--el-border-color);
}

/* 名称左边的提示 icon（点击弹出说明） */
.name-label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.hint-icon {
  color: var(--el-text-color-secondary);
  cursor: pointer;
  flex-shrink: 0;
}

.hint-icon:hover {
  color: var(--el-color-primary);
}

.hint-pop-text {
  font-size: 12px;
  line-height: 1.6;
}
</style>
