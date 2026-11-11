<script setup lang="ts">
/**
 * 顶部工具栏：
 *   左：【阅读模式】/【编辑模式】切换 + 项目名 + 后端提示（目录开关移到阅读区左上角浮层）
 * 右：回收站入口（在 AI 写作助手左边）+ AI写作助手入口 —— 聊天框 icon + 文字；
 *       悬浮提示功能介绍；会话框展开后只保留聊天框 icon，点击可收起。
 */
import { computed } from 'vue'
import { useReaderStore } from '@/stores/reader'
import { useProjectStore } from '@/stores/project'
import { useSettingsStore } from '@/stores/settings'
import { t } from '@/i18n'

defineProps<{
  /** AI 会话框是否展开（展开后只显示聊天框 icon，点击收起） */
  agentOpen: boolean
}>()
const emit = defineEmits<{
  (e: 'toggle-agent'): void
  /** 打开回收站抽屉 */
  (e: 'open-recycle'): void
}>()

const reader = useReaderStore()
const project = useProjectStore()
const settings = useSettingsStore()

const mode = computed({
  get: () => reader.mode,
  set: (v) => reader.setMode(v),
})

/** 后端未连通时给出提示（FTS5 降级）；后端离线由 BackendOfflineBanner 居中图案处理，不再在此显示 */
const backendHint = computed(() => {
  // 后端离线错误已由 BackendOfflineBanner 居中灰色图案处理，此处不再重复显示
  if (settings.error && (settings.error.includes('50001') || settings.error.includes('后端未连接') || settings.error.includes('uvicorn'))) return ''
  if (settings.error) return settings.error
  if (settings.health && !settings.fts5Available) return t('health.fts5Degraded')
  return ''
})
</script>

<template>
  <div class="reader-toolbar row-between">
    <div class="row">
      <el-radio-group v-model="mode" size="small">
        <el-radio-button value="read">{{ t('reader.modeRead') }}</el-radio-button>
        <el-radio-button value="edit">{{ t('reader.modeEdit') }}</el-radio-button>
      </el-radio-group>

      <el-tag v-if="project.currentNovel" size="small" effect="plain">
        {{ project.currentNovel.title }}
      </el-tag>
      <el-tag v-if="backendHint" size="small" type="warning" effect="plain">{{ backendHint }}</el-tag>
    </div>

    <div class="row">
      <!-- 卷章操作条 / 保存 / 自动保存标记已移到「目录」所在工具栏行；
           页面最上方不再显示「已保存 自动保存已开启（30 秒）」标记 -->

      <!-- 回收站入口（放在 AI写作助手的左边）——找回被删除的章节，最多 20 本 -->
      <el-tooltip :content="t('recycle.tooltip')" placement="bottom-end" :show-after="200">
        <button class="recycle-btn" @click="emit('open-recycle')">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true">
            <path d="M9 3h6l1 1.5h3.5v2h-15v-2H8L9 3zm-3.5 5.5h13a1 1 0 0 1 1 1.1l-1 9.9a2 2 0 0 1-2 1.9H7.5a2 2 0 0 1-2-1.9l-1-9.9a1 1 0 0 1 1-1.1zm3.7 2.2-.1 8h1.9l.1-8H9.2zm4 0-.1 8h1.9l.1-8h-1.9z" />
          </svg>
          <span>{{ t('recycle.title') }}</span>
        </button>
      </el-tooltip>

      <!-- AI写作助手（未展开）= 聊天框 icon + 文字，悬浮出功能提示 -->
      <el-tooltip
        v-if="!agentOpen"
        :content="t('agent.panelTooltip')"
        placement="bottom-end"
        :show-after="200"
      >
        <button class="ai-assistant-btn" @click="emit('toggle-agent')">
          <svg class="chat-icon" viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true">
            <path d="M12 3C6.8 3 2.6 6.6 2.6 11c0 2.5 1.3 4.7 3.4 6.2-.2 1-.8 2.3-1.8 3.2-.2.2-.1.6.2.7 1.7.4 3.6-.2 4.9-1 1-.2 1.9-.3 2.7-.3 5.2 0 9.4-3.6 9.4-8S17.2 3 12 3z" />
          </svg>
          <span>{{ t('agent.panelTitle') }}</span>
        </button>
      </el-tooltip>

      <!-- 展开后只出现聊天框 icon，点击收起 AI 会话框 -->
      <el-tooltip v-else :content="t('agent.collapse')" placement="bottom-end">
        <button class="ai-assistant-icon" @click="emit('toggle-agent')">
          <svg class="chat-icon" viewBox="0 0 24 24" width="18" height="18" fill="currentColor" aria-hidden="true">
            <path d="M12 3C6.8 3 2.6 6.6 2.6 11c0 2.5 1.3 4.7 3.4 6.2-.2 1-.8 2.3-1.8 3.2-.2.2-.1.6.2.7 1.7.4 3.6-.2 4.9-1 1-.2 1.9-.3 2.7-.3 5.2 0 9.4-3.6 9.4-8S17.2 3 12 3z" />
          </svg>
        </button>
      </el-tooltip>
    </div>
  </div>
</template>

<style scoped>
.reader-toolbar {
  padding: 8px 16px;
  border-bottom: 1px solid var(--border);
  background: var(--bg-panel);
  flex-shrink: 0;
  flex-wrap: wrap;
  gap: 8px;
}

/* 回收站入口（AI写作助手左边，样式与 AI 助手入口同形制但用中性色） */
.recycle-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border-radius: 16px;
  border: 1px solid var(--border);
  background: var(--bg-panel);
  color: var(--text-muted);
  font-size: 13px;
  font-weight: 600;
  line-height: 1;
  cursor: pointer;
  transition: background 0.15s, color 0.15s, box-shadow 0.15s;
}

.recycle-btn:hover {
  color: var(--el-color-danger);
  border-color: color-mix(in srgb, var(--el-color-danger) 35%, transparent);
  background: color-mix(in srgb, var(--el-color-danger) 8%, var(--bg-panel));
}

/* AI写作助手入口（比原纯文字按钮更有存在感） */
.ai-assistant-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
  border-radius: 16px;
  border: 1px solid color-mix(in srgb, var(--el-color-primary) 35%, transparent);
  background: color-mix(in srgb, var(--el-color-primary) 8%, var(--bg-panel));
  color: var(--el-color-primary);
  font-size: 13px;
  font-weight: 600;
  line-height: 1;
  cursor: pointer;
  transition: background 0.15s, box-shadow 0.15s;
}

.ai-assistant-btn:hover {
  background: color-mix(in srgb, var(--el-color-primary) 16%, var(--bg-panel));
  box-shadow: 0 1px 6px color-mix(in srgb, var(--el-color-primary) 25%, transparent);
}

.ai-assistant-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 50%;
  border: none;
  background: transparent;
  color: var(--el-color-primary);
  cursor: pointer;
  transition: background 0.15s;
}

.ai-assistant-icon:hover {
  background: color-mix(in srgb, var(--el-color-primary) 14%, transparent);
}

.chat-icon {
  flex-shrink: 0;
}
</style>
