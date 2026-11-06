<script setup lang="ts">
/**
 * 通用设置页：
 *   外观：主题（深色 / 浅色 / 护眼）、正文字号、字体
 *   编辑：自动保存间隔（10~60 秒）
 * 已删除：主题色、阅读页宽、动态组件设置、AI 工具入口、运行状态。
 * 去掉「外观」「编辑」两条分界线；功能设置名与设置控件之间拉开间距。
 * 左上角仅返回箭头；「保存」与「恢复默认设置」位于所有设置项下方。
 */
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useSettingsStore } from '@/stores/settings'
import { errorMessage } from '@/api/http'
import { t } from '@/i18n'
import type { UserSettings } from '@/types'

const router = useRouter()
const store = useSettingsStore()
const saving = ref(false)
const resetting = ref(false)
const form = reactive<Pick<UserSettings, 'theme' | 'font_size' | 'font_family' | 'autosave_enabled' | 'autosave_interval_sec' | 'export_dir'>>({
  theme: 'light',
  font_size: 18,
  font_family: 'serif',
  autosave_enabled: true,
  autosave_interval_sec: 30,
  export_dir: '',
})

/** 字体候选：只用开源免费字体 */
const FONT_OPTIONS = [
  { value: 'serif', label: t('settings.fontSerif'), css: '"Source Han Serif SC", "Noto Serif SC", "Songti SC", "SimSun", serif' },
  { value: 'hei', label: t('settings.fontHei'), css: '"Source Han Sans SC", "Noto Sans SC", "Heiti SC", "SimHei", sans-serif' },
  { value: 'kai', label: t('settings.fontKai'), css: '"LXGW WenKai", "Kaiti SC", "KaiTi", "STKaiti", serif' },
  { value: 'system', label: t('settings.fontSystem'), css: 'system-ui, "Microsoft YaHei", sans-serif' },
]

function syncForm(): void {
  // 旧数据的 system 主题按浅色呈现
  const th = store.settings.theme
  form.theme = th === 'dark' || th === 'eye' ? th : 'light'
  form.font_size = store.settings.font_size
  form.font_family = store.settings.font_family
  form.autosave_enabled = store.settings.autosave_enabled
  form.autosave_interval_sec = store.settings.autosave_interval_sec
  form.export_dir = store.settings.export_dir ?? ''
}

onMounted(async () => {
  if (!store.loaded) await store.load()
  syncForm()
})

/** 未保存时也让主题/字号即时生效（选择不同主题整个应用背景随之改变）；点「保存」才持久化 */
function preview(): void {
  store.preview({
    theme: form.theme,
    font_size: form.font_size,
    font_family: form.font_family,
    autosave_enabled: form.autosave_enabled,
    autosave_interval_sec: form.autosave_interval_sec,
  })
}

async function save(): Promise<void> {
  saving.value = true
  try {
    await store.save({
      theme: form.theme,
      font_size: form.font_size,
      font_family: form.font_family,
      autosave_enabled: form.autosave_enabled,
      autosave_interval_sec: form.autosave_interval_sec,
      export_dir: form.export_dir?.trim() || null,
    })
    syncForm()
    ElMessage.success(t('settings.saved'))
  } catch (err) {
    // 保存失败 → 回滚本地预览，避免界面与后端不一致
    store.revert()
    syncForm()
    ElMessage.error(errorMessage(err))
  } finally {
    saving.value = false
  }
}

async function resetDefaults(): Promise<void> {
  try {
    await ElMessageBox.confirm(t('settings.resetConfirm'), t('settings.resetConfirmTitle'), {
      type: 'warning',
      confirmButtonText: t('common.confirm'),
      cancelButtonText: t('common.cancel'),
    })
  } catch {
    return
  }
  resetting.value = true
  try {
    await store.reset()
    syncForm()
    ElMessage.success(t('settings.resetDone'))
  } catch (err) {
    ElMessage.error(errorMessage(err))
  } finally {
    resetting.value = false
  }
}

function back(): void {
  void router.push('/')
}
</script>

<template>
  <div class="settings-view">
    <!-- 左上角只要返回箭头 -->
    <div class="settings-head">
      <el-button text class="back-btn" :title="t('settings.back')" @click="back">
        <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor" aria-hidden="true">
          <path d="M15.4 4.6a1 1 0 0 0-1.4 0l-6.7 6.7a1 1 0 0 0 0 1.4l6.7 6.7a1 1 0 0 0 1.4-1.4L9.4 12l6-6a1 1 0 0 0 0-1.4z" />
        </svg>
      </el-button>
    </div>

    <div class="settings-body scroll-y">
      <!-- 去掉「外观」「编辑」两条分界线；设置名与设置项之间拉开间距 -->
      <el-form :model="form" label-width="150px" class="form">
        <el-form-item :label="t('settings.theme')">
          <el-radio-group v-model="form.theme" @change="preview">
            <el-radio value="light">{{ t('settings.themeLight') }}</el-radio>
            <el-radio value="dark">{{ t('settings.themeDark') }}</el-radio>
            <el-radio value="eye">{{ t('settings.themeEye') }}</el-radio>
          </el-radio-group>
        </el-form-item>

        <el-form-item :label="t('settings.fontSize')">
          <!-- 合理范围（14~28），不会太小也不会太大 -->
          <el-slider
            v-model="form.font_size"
            :min="14"
            :max="28"
            :step="1"
            show-input
            size="small"
            class="font-slider"
            @change="preview"
          />
        </el-form-item>

        <el-form-item :label="t('settings.fontFamily')">
          <el-select v-model="form.font_family" class="font-select" @change="preview">
            <el-option
              v-for="f in FONT_OPTIONS"
              :key="f.value"
              :label="f.label"
              :value="f.value"
            >
              <span :style="{ fontFamily: f.css }">{{ f.label }}</span>
            </el-option>
          </el-select>
          <!-- 预览：修改字号/字体后，阅读与编辑模式的小说显示随之改变 -->
          <div
            class="font-preview"
            :style="{
              fontFamily: FONT_OPTIONS.find((f) => f.value === form.font_family)?.css,
              fontSize: `${form.font_size}px`,
            }"
          >
            星海拾荒人 · 预览
          </div>
        </el-form-item>

        <!-- 新增[自动保存]开关，与[自动保存间隔]联动（关闭后间隔置灰） -->
        <el-form-item :label="t('settings.autosaveEnabled')">
          <el-switch v-model="form.autosave_enabled" @change="preview" />
        </el-form-item>

        <el-form-item :label="t('settings.autosaveInterval')">
          <!-- 最少 10s，最多 60s；关闭自动保存后置灰 -->
          <el-slider
            v-model="form.autosave_interval_sec"
            :min="10"
            :max="60"
            :step="5"
            show-input
            size="small"
            class="font-slider"
            :disabled="!form.autosave_enabled"
            @change="preview"
          />
          <div class="muted text-sm">{{ t('settings.autosaveHint') }}</div>
        </el-form-item>

        <!-- 小说导出地址：默认为桌面；浏览器无法读取文件夹绝对路径，故用路径输入 -->
        <el-form-item :label="t('settings.exportDir')">
          <el-input v-model="form.export_dir" class="export-dir-input" :placeholder="t('settings.exportDirPlaceholder')" />
          <div class="muted text-sm">{{ t('settings.exportDirHint') }}</div>
        </el-form-item>
      </el-form>

      <!-- 保存移到所有设置下面，旁边加恢复默认设置 -->
      <div class="settings-actions">
        <el-button :loading="resetting" @click="resetDefaults">
          {{ t('settings.resetDefault') }}
        </el-button>
        <el-button type="primary" :loading="saving" @click="save">{{ t('common.save') }}</el-button>
      </div>
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
  padding: 8px 16px;
  border-bottom: 1px solid var(--border);
  background: var(--bg-panel);
  flex-shrink: 0;
}

.back-btn {
  padding: 4px 8px;
}

.settings-body {
  flex: 1;
  min-height: 0;
  padding: 20px 20px 40px;
}

.form {
  max-width: 720px;
}

/* 功能设置名与功能设置控件之间拉开距离（默认 12px 太挤） */
.export-dir-input {
  max-width: 420px;
}

.form :deep(.el-form-item__label) {
  padding-right: 32px;
}

.form :deep(.el-form-item) {
  margin-bottom: 24px;
}

.font-slider {
  max-width: 420px;
}

.font-select {
  width: 200px;
}

.font-preview {
  margin-left: 12px;
  color: var(--text);
  line-height: 1.6;
  padding: 4px 10px;
  border: 1px dashed var(--border);
  border-radius: 6px;
}

.settings-actions {
  max-width: 720px;
  margin: 24px 0 0 150px;
  display: flex;
  gap: 12px;
}

.muted {
  color: var(--text-muted);
}

.text-sm {
  font-size: 12px;
}
</style>
