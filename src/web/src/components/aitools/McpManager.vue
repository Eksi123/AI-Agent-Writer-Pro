<script setup lang="ts">
/**
 * MCP 服务管理（M6 实装）：
 * - 内置 Open-Meteo 天气 MCP（只读展示，无需配置）；
 * - 第三方 MCP：stdio（命令白名单 npx/uvx/python + 二次显式确认展示完整命令）/ http / sse；
 * - 探测工具列表、启停、删除；仅接开源免费无认证 MCP。
 */
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { mcpApi, type McpServerDto } from '@/api'
import { errorMessage } from '@/api/http'
import { t } from '@/i18n'

const loading = ref(false)
const servers = ref<McpServerDto[]>([])
const probingId = ref<string | null>(null)

const dialogVisible = ref(false)
const saving = ref(false)
const editing = ref<McpServerDto | null>(null)
const form = reactive({
  name: '',
  transport: 'stdio' as 'stdio' | 'http' | 'sse',
  command: 'npx',
  args_text: '',
  url: '',
})

const COMMAND_WHITELIST = ['npx', 'uvx', 'python']

async function load(): Promise<void> {
  loading.value = true
  try {
    servers.value = await mcpApi.list()
  } catch (err) {
    ElMessage.error(errorMessage(err))
  } finally {
    loading.value = false
  }
}

onMounted(load)

function openDialog(s?: McpServerDto): void {
  editing.value = s ?? null
  form.name = s?.name ?? ''
  form.transport = s?.transport ?? 'stdio'
  form.command = s?.command?.command ?? 'npx'
  form.args_text = (s?.command?.args ?? []).join('\n')
  form.url = s?.url ?? ''
  dialogVisible.value = true
}

function splitArgs(): string[] {
  return form.args_text.split('\n').map((s) => s.trim()).filter(Boolean)
}

async function save(): Promise<void> {
  if (!form.name.trim()) {
    ElMessage.warning(t('common.required'))
    return
  }
  if (form.transport !== 'stdio' && !form.url.trim()) {
    ElMessage.warning('URL 必填')
    return
  }
  saving.value = true
  try {
    if (editing.value) {
      const payload: { command?: { command: string; args: string[] }; url?: string } =
        editing.value.transport === 'stdio'
          ? { command: { command: form.command.trim(), args: splitArgs() } }
          : { url: form.url.trim() }
      await mcpApi.update(editing.value.id, payload)
    } else {
      const payload: Parameters<typeof mcpApi.create>[0] = {
        name: form.name.trim(),
        transport: form.transport,
      }
      if (form.transport === 'stdio') {
        payload.command = { command: form.command.trim(), args: splitArgs() }
      } else {
        payload.url = form.url.trim()
      }
      await mcpApi.create(payload)
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

/** stdio 二次显式确认：展示完整命令，确认后 confirmed_at 落库 + 审计 */
async function confirmServer(s: McpServerDto): Promise<void> {
  const commandFull = `${s.command?.command ?? ''} ${(s.command?.args ?? []).join(' ')}`.trim()
  try {
    await ElMessageBox.confirm(
      `即将允许后端拉起子进程执行以下命令（等价于受限的命令执行）：\n\n${commandFull}\n\n` +
        '请确认该 MCP 来源可信（仅支持开源免费、无需认证的服务）。',
      'stdio MCP 二次确认',
      { type: 'warning', confirmButtonText: '我确认执行此命令', cancelButtonText: t('common.cancel') },
    )
    await mcpApi.confirm(s.id)
    ElMessage.success('已确认，可以启用了')
    await load()
  } catch {
    /* 取消 */
  }
}

async function probe(s: McpServerDto): Promise<void> {
  probingId.value = s.id
  try {
    const dto = await mcpApi.probe(s.id)
    ElMessage.success(`探测成功：发现 ${dto.tools.length} 个工具`)
    await load()
  } catch (err) {
    ElMessage.error(errorMessage(err))
  } finally {
    probingId.value = null
  }
}

async function toggleEnabled(s: McpServerDto, enabled: boolean): Promise<void> {
  try {
    await mcpApi.update(s.id, { enabled })
    await load()
  } catch (err) {
    ElMessage.error(errorMessage(err))
    await load()
  }
}

async function remove(s: McpServerDto): Promise<void> {
  try {
    await ElMessageBox.confirm(`确定删除 MCP「${s.name}」吗？`, t('common.delete'), {
      type: 'warning',
      confirmButtonText: t('common.delete'),
      cancelButtonText: t('common.cancel'),
    })
    await mcpApi.remove(s.id)
    ElMessage.success('已删除')
    await load()
  } catch {
    /* 取消 */
  }
}
</script>

<template>
  <div v-loading="loading" class="mcp-manager">
    <div class="toolbar">
      <span class="muted text-sm">
        仅支持开源免费、无需认证的 MCP。stdio 型命令白名单：{{ COMMAND_WHITELIST.join(' / ') }}，启用前须二次确认。
      </span>
      <el-button type="primary" size="small" @click="openDialog()">＋ 添加 MCP</el-button>
    </div>

    <!-- 内置天气 MCP -->
    <div class="builtin-card">
      <div class="row">
        <strong>open-meteo-weather</strong>
        <el-tag size="small" type="success">内置</el-tag>
        <el-tag size="small">已启用</el-tag>
      </div>
      <div class="desc">
        免费开源天气服务（Open-Meteo）：get_current_weather / get_forecast，同时服务左上角动态组件。
      </div>
    </div>

    <div v-for="s in servers" :key="s.id" class="mcp-card">
      <div class="card-head">
        <div class="row">
          <strong>{{ s.name }}</strong>
          <el-tag size="small">{{ s.transport }}</el-tag>
          <el-tag v-if="s.needs_confirm" size="small" type="danger">待二次确认</el-tag>
          <el-tag v-else-if="s.transport === 'stdio'" size="small" type="success">已确认</el-tag>
          <el-tag v-if="s.tools.length" size="small" type="info">{{ s.tools.length }} 工具</el-tag>
        </div>
        <div class="row">
          <el-button text size="small" :loading="probingId === s.id" @click="probe(s)">探测</el-button>
          <el-button v-if="s.needs_confirm" text size="small" type="warning" @click="confirmServer(s)">
            二次确认
          </el-button>
          <el-switch
            :model-value="s.enabled"
            size="small"
            :disabled="s.needs_confirm"
            @change="toggleEnabled(s, Boolean($event))"
          />
          <el-button text size="small" @click="openDialog(s)">编辑</el-button>
          <el-button text size="small" type="danger" @click="remove(s)">{{ t('common.delete') }}</el-button>
        </div>
      </div>
      <div v-if="s.transport === 'stdio'" class="muted text-sm">
        命令：<code>{{ s.command?.command }} {{ (s.command?.args ?? []).join(' ') }}</code>
      </div>
      <div v-else class="muted text-sm">URL：{{ s.url }}</div>
      <div v-if="s.tools.length" class="tools">
        <el-tag v-for="tool in s.tools" :key="tool.name" size="small" class="tool-tag">
          {{ tool.name }}
        </el-tag>
      </div>
    </div>

    <el-dialog
      v-model="dialogVisible"
      :title="editing ? `编辑 MCP：${editing.name}` : '添加 MCP'"
      width="560px"
    >
      <el-form :model="form" label-width="100px">
        <el-form-item label="名称" required>
          <el-input v-model="form.name" :disabled="!!editing" placeholder="如 my-weather-server" />
        </el-form-item>
        <el-form-item label="接入方式">
          <el-radio-group v-model="form.transport" :disabled="!!editing">
            <el-radio value="stdio">stdio（子进程命令）</el-radio>
            <el-radio value="http">streamable-http</el-radio>
            <el-radio value="sse">SSE</el-radio>
          </el-radio-group>
        </el-form-item>
        <template v-if="form.transport === 'stdio'">
          <el-form-item label="命令">
            <el-select v-model="form.command" :disabled="!!editing" style="width: 100%">
              <el-option v-for="c in COMMAND_WHITELIST" :key="c" :value="c" :label="c" />
            </el-select>
          </el-form-item>
          <el-form-item label="参数">
            <el-input
              v-model="form.args_text"
              type="textarea"
              :rows="3"
              placeholder="每行一个参数，如：-y / @modelcontextprotocol/server-xxx"
            />
          </el-form-item>
        </template>
        <el-form-item v-else label="URL" required>
          <el-input v-model="form.url" placeholder="https://example.com/mcp" />
        </el-form-item>
        <el-alert
          v-if="form.transport === 'stdio' && !editing"
          type="warning"
          :closable="false"
          title="stdio 型 MCP 会由后端拉起子进程执行命令。创建后需「探测 → 二次确认」才可启用；命令全文将记录到审计日志。"
        />
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="saving" @click="save">{{ t('common.save') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.mcp-manager {
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

.builtin-card,
.mcp-card {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.builtin-card {
  background: var(--el-color-success-light-9);
  border-color: var(--el-color-success-light-7);
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
  flex-wrap: wrap;
}

.desc {
  font-size: 13px;
  color: var(--el-text-color-regular);
}

.tools {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}

.tool-tag {
  font-family: monospace;
}

.muted {
  color: var(--el-text-color-secondary);
}

.text-sm {
  font-size: 12px;
}
</style>
