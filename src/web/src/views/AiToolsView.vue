<script setup lang="ts">
/**
 * AI 工具配置页（M3 交付大模型 API 设置；子代理/技能 M5、MCP M6 以占位页签呈现）。
 *
 * 密钥交互（手册「三、(3) AI 工具」）：
 * - 编辑态输入框以黑点隐藏；保存后只展示掩码 `sk-****abcd`，不回显明文；
 * - 密钥保存时后端写入 .env（DB 只记变量名）；保存成功后自动触发配置重载；
 * - 环境变量缺失时 Provider 卡片显示「未配置密钥」警示。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { llmApi } from '@/api'
import { errorMessage } from '@/api/http'
import { t } from '@/i18n'
import type { LlmModelDto, LlmOverview, LlmProviderDto, ProviderType } from '@/types'
import SubagentManager from '@/components/aitools/SubagentManager.vue'
import SkillManager from '@/components/aitools/SkillManager.vue'
import McpManager from '@/components/aitools/McpManager.vue'

const router = useRouter()

const loading = ref(false)
const providers = ref<LlmProviderDto[]>([])
const overview = ref<LlmOverview | null>(null)
const activeTab = ref('llm')

// ---- Provider 编辑对话框 ----
const providerDialog = ref(false)
const providerSaving = ref(false)
const providerEditing = ref<LlmProviderDto | null>(null)
const providerForm = reactive({
  name: '',
  type: 'openai_compatible' as ProviderType,
  base_url: '',
  api_key_env: '',
  api_key_value: '',
  priority: 0,
  enabled: true,
})

const providerTypeOptions: { value: ProviderType; label: string }[] = [
  { value: 'openai', label: 'OpenAI' },
  { value: 'openai_compatible', label: 'OpenAI 兼容（DashScope/DeepSeek/Moonshot/智谱/硅基流动/vLLM…）' },
  { value: 'anthropic', label: 'Anthropic Claude' },
  { value: 'google', label: 'Google Gemini' },
  { value: 'azure_openai', label: 'Azure OpenAI' },
  { value: 'openrouter', label: 'OpenRouter' },
]

const suggestedEnvs = computed(() => overview.value?.settings.suggested_key_envs ?? [])

async function load(): Promise<void> {
  loading.value = true
  try {
    const [p, o] = await Promise.all([llmApi.listProviders(), llmApi.overview()])
    providers.value = p
    overview.value = o
  } catch (err) {
    ElMessage.error(errorMessage(err))
  } finally {
    loading.value = false
  }
}

onMounted(load)

function openProviderDialog(p?: LlmProviderDto): void {
  providerEditing.value = p ?? null
  providerForm.name = p?.name ?? ''
  providerForm.type = p?.type ?? 'openai_compatible'
  providerForm.base_url = p?.base_url ?? ''
  providerForm.api_key_env = p?.api_key_env ?? ''
  providerForm.api_key_value = ''
  providerForm.priority = p?.priority ?? 0
  providerForm.enabled = p?.enabled ?? true
  providerDialog.value = true
}

async function saveProvider(): Promise<void> {
  if (!providerForm.name.trim()) {
    ElMessage.warning(t('common.required'))
    return
  }
  providerSaving.value = true
  try {
    const payload = {
      name: providerForm.name.trim(),
      type: providerForm.type,
      base_url: providerForm.base_url || undefined,
      api_key_env: providerForm.api_key_env || undefined,
      api_key_value: providerForm.api_key_value || undefined,
      priority: providerForm.priority,
      enabled: providerForm.enabled,
    }
    if (providerEditing.value) {
      await llmApi.updateProvider(providerEditing.value.id, payload)
    } else {
      await llmApi.createProvider(payload)
    }
    if (providerForm.api_key_value) {
      await llmApi.reloadConfig()
    }
    ElMessage.success(t('aiTools.savedProvider'))
    providerDialog.value = false
    await load()
  } catch (err) {
    ElMessage.error(errorMessage(err))
  } finally {
    providerSaving.value = false
  }
}

async function removeProvider(p: LlmProviderDto): Promise<void> {
  try {
    await ElMessageBox.confirm(
      t('aiTools.deleteProviderConfirm', { name: p.name, count: p.models.length }),
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

// ---- Model 编辑对话框 ----
const modelDialog = ref(false)
const modelSaving = ref(false)
const modelEditing = ref<LlmModelDto | null>(null)
const modelProviderId = ref('')
const modelForm = reactive({
  model_id: '',
  display_name: '',
  max_context: 32768,
  supports_tools: true,
  is_default: false,
  enabled: true,
  fallback_chain_text: '',
  default_params_text: '',
})

const allModelRefs = computed(() =>
  providers.value.flatMap((p) => p.models.map((m) => `${p.name} / ${m.display_name || m.model_id}  →  ${m.model_ref}`)),
)

function openModelDialog(providerId: string, m?: LlmModelDto): void {
  modelEditing.value = m ?? null
  modelProviderId.value = providerId
  modelForm.model_id = m?.model_id ?? ''
  modelForm.display_name = m?.display_name ?? ''
  modelForm.max_context = m?.max_context ?? 32768
  modelForm.supports_tools = m?.supports_tools ?? true
  modelForm.is_default = m?.is_default ?? false
  modelForm.enabled = m?.enabled ?? true
  modelForm.fallback_chain_text = (m?.fallback_chain ?? []).join('\n')
  modelForm.default_params_text = m && Object.keys(m.default_params ?? {}).length
    ? JSON.stringify(m.default_params, null, 2)
    : ''
  modelDialog.value = true
}

async function saveModel(): Promise<void> {
  if (!modelForm.model_id.trim()) {
    ElMessage.warning(t('common.required'))
    return
  }
  let defaultParams: Record<string, unknown> | undefined
  if (modelForm.default_params_text.trim()) {
    try {
      defaultParams = JSON.parse(modelForm.default_params_text)
    } catch {
      ElMessage.error(t('aiTools.invalidJson'))
      return
    }
  }
  const fallback = modelForm.fallback_chain_text
    .split('\n')
    .map((s) => s.trim())
    .filter(Boolean)

  modelSaving.value = true
  try {
    const payload = {
      model_id: modelForm.model_id.trim(),
      display_name: modelForm.display_name || undefined,
      max_context: modelForm.max_context,
      supports_tools: modelForm.supports_tools,
      is_default: modelForm.is_default,
      enabled: modelForm.enabled,
      fallback_chain: fallback,
      default_params: defaultParams,
    }
    if (modelEditing.value) {
      await llmApi.updateModel(modelEditing.value.id, payload)
    } else {
      await llmApi.createModel({ provider_id: modelProviderId.value, ...payload })
    }
    ElMessage.success(t('aiTools.savedModel'))
    modelDialog.value = false
    await load()
  } catch (err) {
    ElMessage.error(errorMessage(err))
  } finally {
    modelSaving.value = false
  }
}

async function removeModel(m: LlmModelDto): Promise<void> {
  try {
    await ElMessageBox.confirm(
      t('aiTools.deleteModelConfirm', { name: m.display_name || m.model_id }),
      t('common.delete'),
      { type: 'warning', confirmButtonText: t('common.delete'), cancelButtonText: t('common.cancel') },
    )
    await llmApi.deleteModel(m.id)
    ElMessage.success(t('aiTools.deletedModel'))
    await load()
  } catch {
    /* 取消 */
  }
}

async function reloadConfig(): Promise<void> {
  try {
    const result = await llmApi.reloadConfig()
    ElMessage.success(t('aiTools.reloaded', { v: result.config_version }))
    await load()
  } catch (err) {
    ElMessage.error(errorMessage(err))
  }
}

function back(): void {
  void router.push('/')
}
</script>

<template>
  <div class="aitools-view">
    <div class="head row-between">
      <div class="row">
        <el-button text @click="back">‹ {{ t('settings.back') }}</el-button>
        <strong>{{ t('aiTools.title') }}</strong>
      </div>
      <div class="row">
        <el-button size="small" @click="reloadConfig">{{ t('aiTools.reloadConfig') }}</el-button>
      </div>
    </div>

    <el-tabs v-model="activeTab" class="body">
      <el-tab-pane :label="t('aiTools.tabLlm')" name="llm">
        <div v-loading="loading" class="tab-body scroll-y">
          <!-- 概览 -->
          <div class="overview">
            <span class="muted">
              {{ t('aiTools.overviewDefaultModel') }}：
              <strong>{{ overview?.default_model_ref ?? t('aiTools.noDefaultModel') }}</strong>
            </span>
          </div>

          <el-empty v-if="!providers.length" :description="t('aiTools.noProviders')" :image-size="70" />

          <!-- Provider 卡片列表 -->
          <div v-for="p in providers" :key="p.id" class="provider-card">
            <div class="provider-head">
              <div class="row">
                <strong>{{ p.name }}</strong>
                <el-tag size="small">{{ p.type }}</el-tag>
                <el-tag v-if="!p.enabled" size="small" type="info">已停用</el-tag>
                <el-tag v-if="!p.api_key_configured" size="small" type="danger">
                  {{ t('aiTools.apiKeyMissing') }}
                </el-tag>
                <el-tag v-else size="small" type="success">
                  {{ t('aiTools.apiKeyConfigured') }} {{ p.api_key_masked }}
                </el-tag>
              </div>
              <div class="row">
                <el-button size="small" :loading="testingId === p.id" @click="testProvider(p)">
                  {{ t('aiTools.testConnection') }}
                </el-button>
                <el-button size="small" @click="openProviderDialog(p)">{{ t('common.rename') }}</el-button>
                <el-button size="small" type="danger" text @click="removeProvider(p)">
                  {{ t('common.delete') }}
                </el-button>
              </div>
            </div>
            <div v-if="p.base_url" class="muted text-sm">base_url: {{ p.base_url }}</div>
            <div v-if="p.api_key_env" class="muted text-sm">key env: {{ p.api_key_env }}</div>

            <!-- 模型列表 -->
            <div class="model-list">
              <div v-for="m in p.models" :key="m.id" class="model-row">
                <div class="model-info">
                  <code>{{ m.model_id }}</code>
                  <span v-if="m.display_name" class="muted">（{{ m.display_name }}）</span>
                  <el-tag v-if="m.is_default" size="small" type="success">默认</el-tag>
                  <el-tag v-if="!m.enabled" size="small" type="info">停用</el-tag>
                  <span class="muted text-sm">ctx {{ m.max_context }}</span>
                  <span v-if="m.fallback_chain.length" class="muted text-sm">
                    降级链 {{ m.fallback_chain.length }}
                  </span>
                </div>
                <div class="row">
                  <el-button text size="small" @click="openModelDialog(p.id, m)">编辑</el-button>
                  <el-button text size="small" type="danger" @click="removeModel(m)">
                    {{ t('common.delete') }}
                  </el-button>
                </div>
              </div>
              <el-button text size="small" @click="openModelDialog(p.id)">
                ＋ {{ t('aiTools.addModel') }}
              </el-button>
            </div>
          </div>

          <el-button type="primary" class="add-provider" @click="openProviderDialog()">
            ＋ {{ t('aiTools.addProvider') }}
          </el-button>
        </div>
      </el-tab-pane>

      <el-tab-pane :label="t('aiTools.tabSubagents')" name="subagents">
        <SubagentManager />
      </el-tab-pane>
      <el-tab-pane :label="t('aiTools.tabSkills')" name="skills">
        <SkillManager />
      </el-tab-pane>
      <el-tab-pane :label="t('aiTools.tabMcp')" name="mcp">
        <McpManager />
      </el-tab-pane>
    </el-tabs>

    <!-- Provider 对话框 -->
    <el-dialog
      v-model="providerDialog"
      :title="providerEditing ? t('aiTools.editProvider') : t('aiTools.addProvider')"
      width="560px"
    >
      <el-form :model="providerForm" label-width="140px">
        <el-form-item :label="t('aiTools.providerName')" required>
          <el-input v-model="providerForm.name" />
        </el-form-item>
        <el-form-item :label="t('aiTools.providerType')">
          <el-select v-model="providerForm.type" style="width: 100%">
            <el-option v-for="o in providerTypeOptions" :key="o.value" :value="o.value" :label="o.label" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('aiTools.baseUrl')">
          <el-input v-model="providerForm.base_url" placeholder="openai_compatible 必填；openai/anthropic 可留空" />
        </el-form-item>
        <el-form-item :label="t('aiTools.apiKeyEnv')">
          <el-select
            v-model="providerForm.api_key_env"
            filterable
            allow-create
            clearable
            style="width: 100%"
            placeholder="如 DASHSCOPE_API_KEY"
          >
            <el-option v-for="e in suggestedEnvs" :key="e" :value="e" :label="e" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('aiTools.apiKeyValue')">
          <el-input
            v-model="providerForm.api_key_value"
            type="password"
            show-password
            :placeholder="providerEditing ? '留空则不修改' : '保存后写入 .env，仅显示掩码'"
          />
          <div class="muted text-sm">{{ t('aiTools.apiKeyHint') }}</div>
        </el-form-item>
        <el-form-item :label="t('aiTools.priority')">
          <el-input-number v-model="providerForm.priority" :min="0" :max="999" />
        </el-form-item>
        <el-form-item :label="t('aiTools.enabled')">
          <el-switch v-model="providerForm.enabled" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="providerDialog = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="providerSaving" @click="saveProvider">
          {{ t('common.save') }}
        </el-button>
      </template>
    </el-dialog>

    <!-- Model 对话框 -->
    <el-dialog
      v-model="modelDialog"
      :title="modelEditing ? t('aiTools.editModel') : t('aiTools.addModel')"
      width="600px"
    >
      <el-form :model="modelForm" label-width="140px">
        <el-form-item :label="t('aiTools.modelId')" required>
          <el-input v-model="modelForm.model_id" placeholder="如 qwen-plus / gpt-4o-mini / claude-sonnet-4-5" />
        </el-form-item>
        <el-form-item :label="t('aiTools.modelDisplayName')">
          <el-input v-model="modelForm.display_name" />
        </el-form-item>
        <el-form-item :label="t('aiTools.maxContext')">
          <el-input-number v-model="modelForm.max_context" :min="1024" :max="2000000" :step="1024" />
        </el-form-item>
        <el-form-item :label="t('aiTools.supportsTools')">
          <el-switch v-model="modelForm.supports_tools" />
        </el-form-item>
        <el-form-item :label="t('aiTools.isDefault')">
          <el-switch v-model="modelForm.is_default" />
        </el-form-item>
        <el-form-item :label="t('aiTools.enabled')">
          <el-switch v-model="modelForm.enabled" />
        </el-form-item>
        <el-form-item :label="t('aiTools.fallbackChain')">
          <el-input
            v-model="modelForm.fallback_chain_text"
            type="textarea"
            :rows="3"
            placeholder="每行一个 model_ref，如 {provider_id}:{model_id}"
          />
          <div class="muted text-sm">{{ t('aiTools.fallbackChainHint') }}</div>
          <div class="chain-refs muted text-sm">
            <div v-for="r in allModelRefs" :key="r">{{ r }}</div>
          </div>
        </el-form-item>
        <el-form-item :label="t('aiTools.defaultParams')">
          <el-input
            v-model="modelForm.default_params_text"
            type="textarea"
            :rows="4"
            :placeholder="t('aiTools.defaultParamsHint')"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="modelDialog = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="modelSaving" @click="saveModel">{{ t('common.save') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.aitools-view {
  height: 100vh;
  display: flex;
  flex-direction: column;
}

.head {
  padding: 10px 16px;
  border-bottom: 1px solid var(--border);
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.row-between {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding: 0 16px;
}

.body :deep(.el-tabs__content) {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.tab-body {
  height: 100%;
  overflow-y: auto;
  padding-bottom: 24px;
}

.overview {
  margin: 8px 0 16px;
}

.provider-card {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.provider-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.model-list {
  margin-top: 8px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  align-items: flex-start;
}

.model-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
  padding: 4px 8px;
  border-radius: 6px;
  background: var(--el-fill-color-light);
}

.model-info {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  font-size: 13px;
}

.add-provider {
  margin-top: 4px;
}

.muted {
  color: var(--el-text-color-secondary);
}

.text-sm {
  font-size: 12px;
}

.chain-refs {
  max-height: 90px;
  overflow-y: auto;
  margin-top: 4px;
}
</style>
