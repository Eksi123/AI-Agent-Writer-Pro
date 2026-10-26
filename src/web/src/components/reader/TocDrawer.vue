<script setup lang="ts">
/**
 * 目录抽屉：顶端固定「大纲」节点（位于第一卷之前），其下按 sort_order 只读渲染卷章树。
 * ★不可拖拽重排——顺序由大纲小节先后决定（铁律 10）。
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
  <div class="toc-drawer">
    <div class="row-between toc-head">
      <strong>{{ t('reader.toc') }}</strong>
      <el-button text size="small" @click="reader.toggleToc()">‹</el-button>
    </div>

    <div class="scroll-y flex-fill">
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
</template>

<style scoped>
.toc-drawer {
  display: flex;
  flex-direction: column;
  height: 100%;
  border-right: 1px solid var(--border);
  background: var(--bg-panel);
  width: 260px;
}

.toc-head {
  padding: 10px 12px;
  border-bottom: 1px solid var(--border);
}

.toc-node {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  cursor: pointer;
  border-radius: 4px;
  font-size: 14px;
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
  border-radius: 0;
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
  padding-left: 28px;
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
