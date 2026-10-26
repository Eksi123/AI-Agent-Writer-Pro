/**
 * 设置 store：通用设置（主题/字号/字体/页宽/自动保存间隔/回落城市）。
 * 全部持久化到后端 `user_settings` 单行表。
 */
import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'
import { healthApi, settingsApi } from '@/api'
import { errorMessage } from '@/api/http'
import type { Health, UserSettings } from '@/types'

const DEFAULTS: UserSettings = {
  theme: 'system',
  primary_color: '#409EFF',
  font_size: 18,
  font_family: 'serif',
  reading_width: 42,
  autosave_interval_sec: 30,
  city: '北京',
  pet_idle_minutes: 30,
}

/** 字体族 → CSS font-family */
const FONT_STACKS: Record<string, string> = {
  serif: '"Songti SC", "SimSun", "Source Han Serif SC", serif',
  hei: '"Heiti SC", "SimHei", "Source Han Sans SC", sans-serif',
  kai: '"Kaiti SC", "KaiTi", "STKaiti", serif',
  system: 'system-ui, "Microsoft YaHei", sans-serif',
}

export const useSettingsStore = defineStore('settings', () => {
  const settings = ref<UserSettings>({ ...DEFAULTS })
  const loaded = ref(false)
  const saving = ref(false)
  const error = ref<string | null>(null)

  /** 后端自检：FTS5 可用性 / calibre 是否安装（决定 mobi 是否置灰） */
  const health = ref<Health | null>(null)
  const calibreAvailable = computed(() => health.value?.calibre_available ?? false)
  const fts5Available = computed(() => health.value?.fts5_available ?? true)

  /** 实际生效的暗色模式（system 时跟随 prefers-color-scheme） */
  const isDark = computed(() => {
    if (settings.value.theme === 'dark') return true
    if (settings.value.theme === 'light') return false
    return window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false
  })

  const fontFamilyCss = computed(() => FONT_STACKS[settings.value.font_family] ?? FONT_STACKS.serif)

  /** 把设置写成 CSS 变量（手册：CSS 变量驱动主题色/字号/字体） */
  function applyToDom(): void {
    const root = document.documentElement
    root.style.setProperty('--el-color-primary', settings.value.primary_color)
    root.style.setProperty('--novel-font-size', `${settings.value.font_size}px`)
    root.style.setProperty('--novel-font-family', fontFamilyCss.value)
    root.style.setProperty('--novel-reading-width', `${settings.value.reading_width}em`)
    root.dataset.theme = isDark.value ? 'dark' : 'light'
    root.classList.toggle('dark', isDark.value)
  }

  async function load(): Promise<void> {
    try {
      const [s, h] = await Promise.all([settingsApi.get(), healthApi.get()])
      settings.value = { ...DEFAULTS, ...s }
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
      applyToDom()
    } catch (err) {
      error.value = errorMessage(err)
      throw err
    } finally {
      saving.value = false
    }
  }

  // 跟随系统主题变化时实时应用
  watch(isDark, () => applyToDom())

  return {
    settings,
    loaded,
    saving,
    error,
    health,
    calibreAvailable,
    fts5Available,
    isDark,
    fontFamilyCss,
    load,
    save,
    applyToDom,
  }
})
