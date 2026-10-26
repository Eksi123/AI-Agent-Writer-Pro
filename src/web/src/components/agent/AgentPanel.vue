<script setup lang="ts">
/**
 * Agent 面板（M3 交付）。M1 阶段为占位说明 + 只读展示 SSE 客户端契约。
 *
 * M3 将接入：会话列表/切换、消息流（流式）、工具调用卡片、子代理泳道、
 * PlanTodoList、审批卡片、ConsultCard、GateStatusBar、上下文 Chips、模型切换。
 */
import { useProjectStore } from '@/stores/project'
import { t } from '@/i18n'

const project = useProjectStore()
</script>

<template>
  <div class="agent-panel">
    <div class="panel-head">
      <strong>{{ t('agent.panelTitle') }}</strong>
    </div>
    <div class="panel-body scroll-y">
      <el-empty :image-size="70" :description="t('agent.comingM3')" />
      <div v-if="project.currentNovel" class="vc-hint">
        当前项目：{{ project.currentNovel.title }}
        <div class="muted">会话按小说项目隔离，一个项目可有多个会话。</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.agent-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.panel-head {
  padding: 10px 12px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

.panel-body {
  flex: 1;
  min-height: 0;
  padding: 12px;
}
</style>
