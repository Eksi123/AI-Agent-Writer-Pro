<script setup lang="ts">
/**
 * 后端连接失败提示：当后端不可用时，在页面中间展示灰色服务异常图案。
 * 
 * 显示条件：agentStore.error 存在且错误码为 INTERNAL_ERROR（50001）
 * 样式：全屏居中灰色图案 + 文字说明，不遮挡左侧项目栏
 */
import { computed } from 'vue'
import { useAgentStore } from '@/stores/agent'
import { t } from '@/i18n'

const agentStore = useAgentStore()

/** 是否为后端连接失败错误 */
const isBackendOffline = computed(() => {
  const err = agentStore.error
  if (!err) return false
  return err.includes('50001') || err.includes('后端未连接') || err.includes('uvicorn')
})

function dismiss(): void {
  agentStore.clearError()
}
</script>

<template>
  <div v-if="isBackendOffline" class="backend-offline-overlay" @click="dismiss">
    <div class="offline-content">
      <!-- 灰色断连图标 -->
      <svg class="offline-icon" viewBox="0 0 120 120" fill="none" xmlns="http://www.w3.org/2000/svg">
        <!-- 服务器主体 -->
        <rect x="20" y="25" width="80" height="70" rx="6" stroke="#b0b0b0" stroke-width="3" fill="none" />
        <!-- 服务器分层线 -->
        <line x1="20" y1="48" x2="100" y2="48" stroke="#b0b0b0" stroke-width="2" />
        <line x1="20" y1="72" x2="100" y2="72" stroke="#b0b0b0" stroke-width="2" />
        <!-- 指示灯 -->
        <circle cx="35" cy="36" r="4" fill="#d0d0d0" />
        <circle cx="35" cy="60" r="4" fill="#d0d0d0" />
        <circle cx="35" cy="83" r="4" fill="#d0d0d0" />
        <!-- 断连斜线 -->
        <line x1="30" y1="20" x2="90" y2="100" stroke="#c0c0c0" stroke-width="3" stroke-linecap="round" />
        <!-- 顶部天线 -->
        <line x1="60" y1="25" x2="60" y2="12" stroke="#b0b0b0" stroke-width="3" stroke-linecap="round" />
        <circle cx="60" cy="10" r="3" fill="#d0d0d0" />
      </svg>
      <p class="offline-title">{{ t('health.backendOffline') }}</p>
      <p class="offline-hint">
        请稍后重试；若持续失败，请查看 data/logs/error-*.jsonl 日志或重启后端服务。
      </p>
    </div>
  </div>
</template>

<style scoped>
.backend-offline-overlay {
  position: absolute;
  inset: 0;
  z-index: 50;
  display: flex;
  align-items: center;
  justify-content: center;
  background: color-mix(in srgb, var(--bg) 85%, transparent);
  backdrop-filter: blur(1px);
  cursor: pointer;
}

.offline-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
  user-select: none;
}

.offline-icon {
  width: 120px;
  height: 120px;
  opacity: 0.6;
}

.offline-title {
  font-size: 16px;
  font-weight: 500;
  color: var(--text-muted);
  margin: 0;
}

.offline-hint {
  font-size: 13px;
  color: var(--text-muted);
  opacity: 0.7;
  margin: 0;
  text-align: center;
  max-width: 360px;
  line-height: 1.5;
}
</style>
