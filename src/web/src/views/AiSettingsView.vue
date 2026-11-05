<script setup lang="ts">
/**
 * AI 设置页：大模型 / 写作子代理 / 写作技能 三个入口。
 *
 * 变更：
 * - 页面名「AI 工具」→「AI 设置」；删除右上角「重新配置」与「当前默认模型」展示；
 * - 删除 MCP 服务页签（内置天气 MCP 仍由后端自动装配，无需 UI 管理）；
 * - 三个入口之间留出间隔，整体美化。
 */
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { t } from '@/i18n'
import ModelManager from '@/components/aitools/ModelManager.vue'
import SubagentManager from '@/components/aitools/SubagentManager.vue'
import SkillManager from '@/components/aitools/SkillManager.vue'

const router = useRouter()
const activeTab = ref('llm')

function back(): void {
  void router.push('/')
}
</script>

<template>
  <div class="ai-settings-view">
    <!-- 左上角只有返回箭头（与通用设置页一致） -->
    <div class="head">
      <el-button text class="back-btn" :title="t('settings.back')" @click="back">
        <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor" aria-hidden="true">
          <path d="M15.4 4.6a1 1 0 0 0-1.4 0l-6.7 6.7a1 1 0 0 0 0 1.4l6.7 6.7a1 1 0 0 0 1.4-1.4L9.4 12l6-6a1 1 0 0 0 0-1.4z" />
        </svg>
      </el-button>
      <strong class="page-title">{{ t('aiTools.title') }}</strong>
    </div>

    <div class="body scroll-y">
      <!-- 三个入口隔开一点（卡片式分组） -->
      <el-tabs v-model="activeTab" class="settings-tabs">
        <el-tab-pane :label="t('aiTools.tabLlm')" name="llm">
          <section class="pane-card">
            <ModelManager />
          </section>
        </el-tab-pane>

        <el-tab-pane :label="t('aiTools.tabSubagents')" name="subagents">
          <section class="pane-card">
            <SubagentManager />
          </section>
        </el-tab-pane>

        <el-tab-pane :label="t('aiTools.tabSkills')" name="skills">
          <section class="pane-card">
            <SkillManager />
          </section>
        </el-tab-pane>
      </el-tabs>
    </div>
  </div>
</template>

<style scoped>
.ai-settings-view {
  height: 100vh;
  display: flex;
  flex-direction: column;
  background: var(--bg);
}

.head {
  padding: 8px 20px;
  border-bottom: 1px solid var(--border);
  background: var(--bg-panel);
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 10px;
}

.back-btn {
  padding: 4px 8px;
}

.page-title {
  font-size: 15px;
}

.body {
  flex: 1;
  min-height: 0;
  padding: 16px 20px 40px;
}

.settings-tabs {
  max-width: 900px;
}

/* 三个入口隔开一点 */
.settings-tabs :deep(.el-tabs__item) {
  padding: 0 22px;
  font-size: 14px;
}

.settings-tabs :deep(.el-tabs__header) {
  margin-bottom: 18px;
}

.pane-card {
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 18px;
  box-shadow: var(--shadow);
}
</style>
