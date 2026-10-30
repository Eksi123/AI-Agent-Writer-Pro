<script setup lang="ts">
/**
 * 目录（v1028 浮层 / v1029 结构）：浮在**中间阅读区域的左上角**，点击可展开/收起。
 * 顶端固定「{小说名称}_大纲」节点（位于第一卷之前），其下按 sort_order 只读渲染卷章树。
 *
 * v1029：
 *   - 大纲节点标题 = 「小说名称_大纲」，左侧换成书本 icon；
 *   - 一级目录 = 卷（格式「第X卷 卷名」，可点击展开/收起）；
 *   - 二级目录 = 章（格式「第X章 章节名」），与一级同在一个目录框内。
 *
 * ★不可拖拽重排——顺序由大纲小节先后决定（铁律 10）。
 * ★无小说项目时由父级（WorkspaceView）不渲染本组件。
 */
import { computed, ref } from 'vue'
import { useProjectStore } from '@/stores/project'
import { useReaderStore } from '@/stores/reader'
import { formatChapterTitle, formatOutlineNode, formatVolumeTitle } from '@/utils/titles'
import { t } from '@/i18n'

const project = useProjectStore()
const reader = useReaderStore()

const volumes = computed(() => project.volumeTree)

/** v1029：大纲节点标题「小说名称_大纲」 */
const outlineTitle = computed(() => formatOutlineNode(project.currentNovel?.title || ''))

/** 卷的展开状态（默认展开当前章所在卷与首卷） */
const expanded = ref<Record<string, boolean>>({})

function isExpanded(volId: string, index: number): boolean {
  if (expanded.value[volId] !== undefined) return expanded.value[volId]
  // 默认：当前打开章所在的卷 + 第一个卷 展开
  const activeVolId = reader.activeChapterId
    ? volumes.value.find((v) => v.chapters.some((c) => c.id === reader.activeChapterId))?.id
    : null
  return volId === activeVolId || index === 0
}

function toggleVolume(volId: string, index: number): void {
  expanded.value = { ...expanded.value, [volId]: !isExpanded(volId, index) }
}

function volLabel(title: string, sortOrder: number, index: number): string {
  return formatVolumeTitle(title, sortOrder, index)
}

function chLabel(title: string, sortOrder: number, index: number): string {
  return formatChapterTitle(title, sortOrder, index)
}
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

    <!-- 展开后的目录（悬浮层，不挤压正文）：一级卷 + 二级章同在一个目录框内 -->
    <transition name="toc-fade">
      <div v-if="reader.tocOpen" class="toc-panel">
        <div class="scroll-y toc-scroll">
          <!-- ★大纲节点固定在第一卷之前（v1029：小说名称_大纲 + 书本 icon） -->
          <div
            class="toc-node outline-node"
            :class="{ active: reader.activeOutline }"
            @click="reader.openOutline()"
          >
            <svg class="ico outline-ico" viewBox="0 0 24 24" width="15" height="15" fill="currentColor" aria-hidden="true">
              <path d="M4 3h7a2 2 0 0 1 1 .3A2 2 0 0 1 13 3h7a1 1 0 0 1 1 1v15a1 1 0 0 1-1 1h-7a2 2 0 0 0-1 .5 2 2 0 0 0-1-.5H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1zm7 15v-2a1 1 0 0 1 1-1h7V5h-6a1 1 0 0 0-1 1v11a1 1 0 0 1-1 1zm-1 0H5V5h6v13z" />
            </svg>
            <span class="outline-label">{{ outlineTitle }}</span>
          </div>

          <div v-if="!volumes.length" class="empty muted text-sm">
            {{ t('common.empty') }}
          </div>

          <div v-for="(vol, vi) in volumes" :key="vol.id" class="vol-block">
            <!-- 一级：卷（可展开/收起）
                 v1030续(1)：★点击卷只做展开/收起，**不跳转内容**——正文/大纲的切换
                 只由「大纲节点」和「具体章节」触发，点卷时右侧仍显示原来的大纲或章节内容。 -->
            <div class="toc-node vol-node" @click="toggleVolume(vol.id, vi)">
              <svg
                class="ico vol-chev"
                :class="{ open: isExpanded(vol.id, vi) }"
                viewBox="0 0 24 24"
                width="13"
                height="13"
                fill="currentColor"
                aria-hidden="true"
              >
                <path d="M7.4 6.3 13.1 12l-5.7 5.7 1.4 1.4L15.9 12 8.8 4.9z" />
              </svg>
              <span class="vol-title">{{ volLabel(vol.title, vol.sort_order, vi) }}</span>
              <span class="muted text-sm">{{ vol.chapter_count }}</span>
            </div>

            <!-- 二级：章 -->
            <template v-if="isExpanded(vol.id, vi)">
              <div
                v-for="(ch, ci) in vol.chapters"
                :key="ch.id"
                class="toc-node chapter-node"
                :class="{ active: reader.activeChapterId === ch.id }"
                @click="reader.openChapter(ch.id)"
              >
                <span class="ch-title">{{ chLabel(ch.title, ch.sort_order, ci) }}</span>
                <el-tag v-if="ch.status === 'finalized'" size="small" type="success" effect="plain">
                  定稿
                </el-tag>
              </div>
            </template>
          </div>
        </div>
      </div>
    </transition>
  </div>
</template>

<style scoped>
.toc-wrap {
  /* v1030：目录按钮改由父级浮动工具栏（.float-toolbar）排布，这里只做展开面板的定位上下文 */
  position: relative;
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
  /* v1030：悬浮展开，不撑高工具栏行 */
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  z-index: 40;
  width: 280px;
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
  font-weight: 600;
}

.outline-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.outline-ico {
  color: var(--el-color-primary);
  flex-shrink: 0;
}

.vol-block {
  margin-bottom: 2px;
}

.vol-node {
  font-weight: 600;
}

.vol-chev {
  flex-shrink: 0;
  color: var(--text-muted);
  transition: transform 0.18s;
}

.vol-chev.open {
  transform: rotate(90deg);
}

.vol-title {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chapter-node {
  padding-left: 30px;
  font-weight: 400;
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
