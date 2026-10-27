<script setup lang="ts">
/**
 * 应用根组件：装配三栏布局，启动时加载通用设置与项目列表。
 * 全局错误边界（M7）：子树渲染异常时显示可恢复的兜底界面，不再白屏。
 */
import { onErrorCaptured, onMounted, ref } from 'vue'
import { RouterView } from 'vue-router'
import { useSettingsStore } from '@/stores/settings'

const settingsStore = useSettingsStore()

const fatalError = ref<string | null>(null)

onErrorCaptured((err) => {
  fatalError.value = err instanceof Error ? `${err.name}: ${err.message}` : String(err)
  return false // 阻止继续向上传播
})

function reload(): void {
  window.location.reload()
}

onMounted(async () => {
  // 先应用主题/字号，避免首屏闪烁
  await settingsStore.load()
})
</script>

<template>
  <div v-if="fatalError" class="fatal-boundary">
    <el-result icon="error" title="页面出现异常" :sub-title="fatalError">
      <template #extra>
        <el-button type="primary" @click="reload">重新加载</el-button>
      </template>
    </el-result>
  </div>
  <RouterView v-else />
</template>

<style scoped>
.fatal-boundary {
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
}
</style>
