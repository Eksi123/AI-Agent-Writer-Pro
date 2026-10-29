<script setup lang="ts">
/**
 * 目录（v1028）：不再置于页面最上方，而是浮在**中间阅读区域的左上角**，点击可展开/收起。
 * 顶端固定「大纲」节点（位于第一卷之前），其下按 sort_order 只读渲染卷章树。
 * ★不可拖拽重排——顺序由大纲小节先后决定（铁律 10）。
 * ★无小说项目时由父级（WorkspaceView）不渲染本组件。
 */
import { computed } from 'vue'
import { useProjectStore } from '@/stores/project'
import { useReaderStore } from '@/stores/reader'
import { t } from '@/i18n'

const project = useProjectStore()
const reader = useReaderStore()

const volumes = computed(() => project.volumeTree)
</script>

<template>
  <div class="toc-wrap">
    <!-- 展开/收起触发：固定在阅读区域左上角 -->
    <button class="toc-toggle" :class="{ open: reader.tocOpen }" @click="reader.toggleToc()">
      <svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor" aria-hidden="true">
        <path d="M4 6h16v2H4V6zm0 5h16v2H4v-2zm0 5h10v2H4v-2z" />
      </svg>
      <span>{{ t('reader.toc') }}</span>
      <svg class="chev" viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden="true">
        <path d="M7.4 6.3 13.1 12l-5.7 5.7 1.4 1.4L15.9 12 8.8 4.9z" />
      </svg>
    </button>

    <!-- 展开后的目录（悬浮层，不挤压正文） -->
    <transition name="toc-fade">
      <div v-if="reader.tocOpen" class="toc-panel">
        <div class="scroll-y toc-scroll">
          <!-- ★大纲节点固定在第一卷之前 -->
          <div
            class="toc-node outline-node"
            :class="{ active: reader.activeOutline }"
            @click="reader.openOutline()"
          >
            <span class="ico">≡</span>
            <span>{{ t('reader.outlineNode') }}</span>
          </div>

          <div v-if="!volumes.length" class="empty muted text-sm">
            {{ t('common.empty') }}
          </div>

          <div v-for="vol in volumes" :key="vol.id" class="vol-block">
            <div class="toc-node vol-node">
              <span class="ico">▸</span>
              <span class="vol-title">{{ vol.title }}</span>
              <span class="muted text-sm">{{ vol.chapter_count }}</span>
            </div>
            <div
              v-for="ch in vol.chapters"
              :key="ch.id"
              class="toc-node chapter-node"
              :class="{ active: reader.activeChapterId === ch.id }"
              @click="reader.openChapter(ch.id)"
            >
              <span class="ch-title">{{ ch.title }}</span>
              <el-tag v-if="ch.status === 'finalized'" size="small" type="success" effect="plain">
                定稿
              </el-tag>
            </div>
          </div>
        </div>
      </div>
    </transition>
  </div>
</template>

<style scoped>
.toc-wrap {
  position: absolute;
  top: 10px;
  left: 12px;
  z-index: 30;
}

.toc-toggle {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 5px 11px;
  border: 1px solid var(--border);
  border-radius: 16px;
  background: var(--bg-panel);
  color: var(--text);
  font-size: 12px;
  line-height: 1;
  cursor: pointer;
  box-shadow: var(--shadow);
  transition: color 0.15s, border-color 0.15s;
}

.toc-toggle:hover {
  color: var(--el-color-primary);
  border-color: var(--el-color-primary);
}

.toc-toggle.open {
  color: var(--el-color-primary);
  border-color: color-mix(in srgb, var(--el-color-primary) 45%, transparent);
}

.toc-toggle .chev {
  transition: transform 0.18s;
}

.toc-toggle.open .chev {
  transform: rotate(90deg);
}

.toc-panel {
  margin-top: 6px;
  width: 252px;
  max-height: min(62vh, 470px);
  display: flex;
  flex-direction: column;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 8px;
  box-shadow: var(--shadow);
  overflow: hidden;
}

.toc-scroll {
  padding: 6px;
  min-height: 0;
}

.toc-fade-enter-active,
.toc-fade-leave-active {
  transition: opacity 0.16s ease, transform 0.16s ease;
}

.toc-fade-enter-from,
.toc-fade-leave-to {
  opacity: 0;
  transform: translateY(-6px);
}

.toc-node {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  cursor: pointer;
  border-radius: 4px;
  font-size: 13px;
}

.toc-node:hover {
  background: var(--bg-hover);
}

.toc-node.active {
  background: color-mix(in srgb, var(--el-color-primary) 14%, transparent);
  color: var(--el-color-primary);
  font-weight: 600;
}

.outline-node {
  border-bottom: 1px dashed var(--border);
  border-radius: 4px 4px 0 0;
  margin-bottom: 4px;
}

.vol-block {
  margin-bottom: 2px;
}

.vol-node {
  font-weight: 600;
  cursor: default;
}

.vol-title {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chapter-node {
  padding-left: 26px;
}

.ch-title {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ico {
  color: var(--text-muted);
  font-size: 12px;
}

.empty {
  padding: 16px;
  text-align: center;
}
</style>
