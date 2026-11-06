<script setup lang="ts">
/**
 * 后端连接失败提示条：当应用其他部分报错导致小说项目不可见时，展示在主页面中间。
 * 
 * 显示条件：agentStore.error 存在且错误码为 INTERNAL_ERROR（50001）
 * 位置：主内容区顶部，ConflictBanner 下方
 */
import { computed } from 'vue'
import { useAgentStore } from '@/stores/agent'
import { t } from '@/i18n'

const agentStore = useAgentStore()

/** 是否为后端连接失败错误 */
const isBackendOffline = computed(() => {
  const err = agentStore.error
  if (!err) return false
  // ErrorCode.INTERNAL_ERROR = 50001
  return err.includes('50001') || err.includes('后端未连接') || err.includes('uvicorn')
})

function dismiss(): void {
  agentStore.clearError()
}
</script>

<template>
  <el-alert
    v-if="isBackendOffline"
    type="error"
    :closable="true"
    class="backend-offline-banner"
    @close="dismiss"
  >
    <template #title>
      <div class="banner-content">
        <strong>{{ t('health.backendOffline') }}</strong>
        <span class="banner-advice">
          请稍后重试；若持续失败，请查看 data/logs/error-*.jsonl 日志或重启后端服务。
        </span>
      </div>
    </template>
  </el-alert>
</template>

<style scoped>
.backend-offline-banner {
  margin: 8px 16px 0;
  flex-shrink: 0;
}

.banner-content {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.banner-advice {
  font-size: 12px;
  color: var(--text-muted);
}
</style>
