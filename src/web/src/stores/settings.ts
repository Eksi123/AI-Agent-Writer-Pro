/**
 * 设置 store：通用设置（主题/字号/字体/自动保存间隔/回落城市）。
 * 全部持久化到后端 `user_settings` 单行表。
 * 主题为 深色/浅色/护眼 三种（system 仅兼容旧数据，按浅色渲染）。
 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { healthApi, settingsApi } from '@/api'
import { errorMessage } from '@/api/http'
import type { Health, UserSettings } from '@/types'

export const SETTINGS_DEFAULTS: UserSettings = {
  theme: 'light',
  primary_color: '#409EFF',
  font_size: 18,
  font_family: 'serif',
  reading_width: 42,
  autosave_enabled: true,
  autosave_interval_sec: 30,
  city: '北京',
  pet_idle_minutes: 30,
  export_dir: '',
}

const DEFAULTS = SETTINGS_DEFAULTS

/** 字体族 → CSS font-family */
const FONT_STACKS: Record<string, string> = {
  serif: '"Source Han Serif SC", "Noto Serif SC", "Songti SC", "SimSun", serif',
  hei: '"Source Han Sans SC", "Noto Sans SC", "Heiti SC", "SimHei", sans-serif',
  kai: '"LXGW WenKai", "Kaiti SC", "KaiTi", "STKaiti", serif',
  system: 'system-ui, "Microsoft YaHei", sans-serif',
}

export const useSettingsStore = defineStore('settings', () => {
  const settings = ref<UserSettings>({ ...DEFAULTS })
  const loaded = ref(false)
  const saving = ref(false)
  const error = ref<string | null>(null)
  /** 已持久化到后端的设置快照（本地预览未保存时可回滚到这里） */
  const persisted = ref<UserSettings>({ ...DEFAULTS })

  /** 后端自检：FTS5 可用性 / PDF 依赖（reportlab）是否安装 */
  const health = ref<Health | null>(null)
  const pdfAvailable = computed(() => health.value?.pdf_available ?? false)
  const fts5Available = computed(() => health.value?.fts5_available ?? true)

  /** 三主题（light | dark | eye）；旧数据 system 归一到 light */
  const theme = computed<'light' | 'dark' | 'eye'>(() => {
    const v = settings.value.theme
    return v === 'dark' || v === 'eye' ? v : 'light'
  })
  const isDark = computed(() => theme.value === 'dark')

  const fontFamilyCss = computed(() => FONT_STACKS[settings.value.font_family] ?? FONT_STACKS.serif)

  /** 把设置写成 CSS 变量（手册：CSS 变量驱动主题色/字号/字体） */
  function applyToDom(): void {
    const root = document.documentElement
    root.style.setProperty('--el-color-primary', settings.value.primary_color)
    root.style.setProperty('--novel-font-size', `${settings.value.font_size}px`)
    root.style.setProperty('--novel-font-family', fontFamilyCss.value)
    root.dataset.theme = theme.value
    root.classList.toggle('dark', isDark.value)
  }

  async function load(): Promise<void> {
    try {
      const [s, h] = await Promise.all([settingsApi.get(), healthApi.get()])
      settings.value = { ...DEFAULTS, ...s }
      persisted.value = { ...settings.value }
      health.value = h
      loaded.value = true
    } catch (err) {
      error.value = errorMessage(err)
      // 后端不可用时保留默认值，UI 仍可用（只读降级）
      loaded.value = true
    }
    applyToDom()
  }

  async function save(patch: Partial<UserSettings>): Promise<void> {
    saving.value = true
    error.value = null
    try {
      const next = await settingsApi.save(patch)
      settings.value = { ...DEFAULTS, ...next }
      persisted.value = { ...settings.value }
      applyToDom()
    } catch (err) {
      error.value = errorMessage(err)
      throw err
    } finally {
      saving.value = false
    }
  }

  /** 仅本地即时预览（主题/字号/字体变更立即生效，不落库；点「保存」才持久化） */
  function preview(patch: Partial<UserSettings>): void {
    settings.value = { ...settings.value, ...patch }
    applyToDom()
  }

  /** 丢弃未保存的本地预览，回到已持久化的设置 */
  function revert(): void {
    settings.value = { ...DEFAULTS, ...persisted.value }
    applyToDom()
  }

  /** 恢复默认设置（后端 POST /settings/reset） */
  async function reset(): Promise<void> {
    saving.value = true
    error.value = null
    try {
      const next = await settingsApi.reset()
      settings.value = { ...DEFAULTS, ...next }
      persisted.value = { ...settings.value }
      applyToDom()
    } catch (err) {
      error.value = errorMessage(err)
      throw err
    } finally {
      saving.value = false
    }
  }

  return {
    settings,
    persisted,
    loaded,
    saving,
    error,
    health,
    pdfAvailable,
    fts5Available,
    theme,
    isDark,
    fontFamilyCss,
    load,
    save,
    reset,
    preview,
    revert,
    applyToDom,
  }
})
