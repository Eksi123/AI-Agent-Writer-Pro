<script setup lang="ts">
/**
 * 通用设置页：主题色（含暗色）/ 字号 / 字体 / 阅读页宽 / 自动保存间隔 / 回落城市 / 宠物阈值。
 * 全部持久化到后端 `user_settings` 单行表。
 *
 * 注：本项目**不设「每日目标字数」**，故无该设置项（手册「三、(3)」）。
 */
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useSettingsStore } from '@/stores/settings'
import { errorMessage } from '@/api/http'
import { t } from '@/i18n'

const router = useRouter()
const store = useSettingsStore()
const saving = ref(false)
const form = reactive({ ...store.settings })

onMounted(async () => {
  if (!store.loaded) await store.load()
  Object.assign(form, store.settings)
})

async function save(): Promise<void> {
  saving.value = true
  try {
    await store.save({
      theme: form.theme,
      primary_color: form.primary_color,
      font_size: form.font_size,
      font_family: form.font_family,
      reading_width: form.reading_width,
      autosave_interval_sec: form.autosave_interval_sec,
      city: form.city,
      pet_idle_minutes: form.pet_idle_minutes,
    })
    Object.assign(form, store.settings)
    ElMessage.success(t('settings.saved'))
  } catch (err) {
    ElMessage.error(errorMessage(err))
  } finally {
    saving.value = false
  }
}

function back(): void {
  void router.push('/')
}
</script>

<template>
  <div class="settings-view">
    <div class="settings-head row-between">
      <div class="row">
        <el-button text @click="back">‹ {{ t('settings.back') }}</el-button>
        <strong>{{ t('settings.title') }}</strong>
      </div>
      <el-button type="primary" :loading="saving" @click="save">{{ t('common.save') }}</el-button>
    </div>

    <div class="settings-body scroll-y">
      <el-form :model="form" label-width="180px" class="form">
        <el-divider content-position="left">外观</el-divider>

        <el-form-item :label="t('settings.theme')">
          <el-radio-group v-model="form.theme">
            <el-radio value="light">{{ t('settings.themeLight') }}</el-radio>
            <el-radio value="dark">{{ t('settings.themeDark') }}</el-radio>
            <el-radio value="system">{{ t('settings.themeSystem') }}</el-radio>
          </el-radio-group>
        </el-form-item>

        <el-form-item :label="t('settings.primaryColor')">
          <el-color-picker v-model="form.primary_color" />
          <span class="muted text-sm preview-text" :style="{ color: form.primary_color }">
            预览文字
          </span>
        </el-form-item>

        <el-form-item :label="t('settings.fontSize')">
          <el-slider v-model="form.font_size" :min="12" :max="32" show-input size="small" />
        </el-form-item>

        <el-form-item :label="t('settings.fontFamily')">
          <el-select v-model="form.font_family">
            <el-option label="宋体" value="serif" />
            <el-option label="黑体" value="hei" />
            <el-option label="楷体" value="kai" />
            <el-option label="系统默认" value="system" />
          </el-select>
        </el-form-item>

        <el-form-item :label="t('settings.readingWidth')">
          <el-slider v-model="form.reading_width" :min="24" :max="70" show-input size="small" />
        </el-form-item>

        <el-divider content-position="left">编辑</el-divider>

        <el-form-item :label="t('settings.autosaveInterval')">
          <el-input-number v-model="form.autosave_interval_sec" :min="5" :max="600" :step="5" />
        </el-form-item>

        <el-divider content-position="left">动态组件</el-divider>

        <el-form-item :label="t('settings.fallbackCity')">
          <el-input v-model="form.city" maxlength="60" style="max-width: 240px" />
          <div class="muted text-sm">{{ t('settings.fallbackCityHint') }}</div>
        </el-form-item>

        <el-form-item :label="t('settings.petIdleMinutes')">
          <el-input-number v-model="form.pet_idle_minutes" :min="1" :max="1440" />
        </el-form-item>

        <el-divider content-position="left">AI 工具</el-divider>
        <el-alert type="info" :closable="false" :title="t('aiTools.comingM3')" />

        <el-divider content-position="left">运行状态</el-divider>
        <el-descriptions v-if="store.health" :column="1" border size="small">
          <el-descriptions-item label="后端版本">{{ store.health.version }}</el-descriptions-item>
          <el-descriptions-item label="SQLite">{{ store.health.sqlite_version }}</el-descriptions-item>
          <el-descriptions-item label="FTS5">
            {{ store.health.fts5_available ? '可用（二元切分 + unicode61）' : `不可用，降级 LIKE：${store.health.fts5_reason}` }}
          </el-descriptions-item>
          <el-descriptions-item label="calibre（mobi 导出）">
            {{ store.calibreAvailable ? '已安装' : '未安装，mobi 选项置灰' }}
          </el-descriptions-item>
          <el-descriptions-item label="Langfuse">
            {{ store.health.langfuse_enabled ? '已配置' : '未配置（降级为本地日志）' }}
          </el-descriptions-item>
          <el-descriptions-item label="数据目录">{{ store.health.data_root }}</el-descriptions-item>
        </el-descriptions>
      </el-form>
    </div>
  </div>
</template>

<style scoped>
.settings-view {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background: var(--bg);
}

.settings-head {
  padding: 12px 20px;
  border-bottom: 1px solid var(--border);
  background: var(--bg-panel);
  flex-shrink: 0;
}

.settings-body {
  flex: 1;
  min-height: 0;
  padding: 20px;
}

.form {
  max-width: 760px;
}

.preview-text {
  margin-left: 12px;
  font-size: 16px;
}
</style>
