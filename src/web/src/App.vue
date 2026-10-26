<script setup lang="ts">
/**
 * 应用根组件：装配三栏布局，启动时加载通用设置与项目列表。
 */
import { onMounted } from 'vue'
import { RouterView } from 'vue-router'
import { useSettingsStore } from '@/stores/settings'

const settingsStore = useSettingsStore()

onMounted(async () => {
  // 先应用主题/字号，避免首屏闪烁
  await settingsStore.load()
})
</script>

<template>
  <RouterView />
</template>
