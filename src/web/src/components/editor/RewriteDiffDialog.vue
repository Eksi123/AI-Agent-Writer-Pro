<script setup lang="ts">
/**
 * AI 改写结果 diff 对话框（手册「三、(3)【正文】」：diff 查看 / 接受 / 回滚）。
 *
 * - 句级 LCS diff：改动的句子高亮标记（删除线=原文被替换，绿底=新文本）；
 * - 「接受」→ 父组件用改写文本替换选区并打 AI 来源标记；
 * - 「放弃」→ 不产生任何改动（改写只读，未写文件）。
 */
import { computed } from 'vue'
import type { RewriteResult } from '@/types'
import { t } from '@/i18n'

const props = defineProps<{
  visible: boolean
  result: RewriteResult | null
  loading: boolean
}>()

const emit = defineEmits<{
  (e: 'update:visible', v: boolean): void
  (e: 'accept', rewritten: string): void
  (e: 'discard'): void
}>()

const MODE_LABELS: Record<string, string> = {
  rewrite: t('editor.aiRewrite'),
  expand: t('editor.aiExpand'),
  condense: t('editor.aiCondense'),
  polish: t('editor.aiPolish'),
}

/** 句子切分（中英标点 + 换行） */
function splitSentences(text: string): string[] {
  return text
    .split(/(?<=[。！？；…\n])/)
    .map((s) => s.trim())
    .filter(Boolean)
}

/** 句级 LCS diff：返回 [{type: same|del|ins, text}] */
interface DiffSeg {
  type: 'same' | 'del' | 'ins'
  text: string
}

const diffSegments = computed<DiffSeg[]>(() => {
  const r = props.result
  if (!r) return []
  const a = splitSentences(r.original)
  const b = splitSentences(r.rewritten)
  // LCS 表（句子级，规模小）
  const n = a.length
  const m = b.length
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0))
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
    }
  }
  const out: DiffSeg[] = []
  let i = 0
  let j = 0
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      out.push({ type: 'same', text: b[j] })
      i++
      j++
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      out.push({ type: 'del', text: a[i] })
      i++
    } else {
      out.push({ type: 'ins', text: b[j] })
      j++
    }
  }
  while (i < n) out.push({ type: 'del', text: a[i++] })
  while (j < m) out.push({ type: 'ins', text: b[j++] })
  return out
})

const changedCount = computed(
  () => diffSegments.value.filter((s) => s.type !== 'same').length,
)

const deltaChars = computed(() => {
  const r = props.result
  if (!r) return 0
  return r.word_counts.rewritten - r.word_counts.original
})

function close(): void {
  emit('update:visible', false)
}

function accept(): void {
  if (props.result) emit('accept', props.result.rewritten)
}
</script>

<template>
  <el-dialog
    :model-value="visible"
    :title="result ? `AI ${MODE_LABELS[result.mode] ?? result.mode} 结果对比` : 'AI 改写'"
    width="720px"
    :close-on-click-modal="false"
    @update:model-value="close"
  >
    <div v-loading="loading" class="diff-body">
      <template v-if="result">
        <div class="diff-meta muted text-sm">
          <span>原文 {{ result.word_counts.original }} 字 → 改写 {{ result.word_counts.rewritten }} 字</span>
          <span :class="deltaChars >= 0 ? 'delta-up' : 'delta-down'">
            （{{ deltaChars >= 0 ? '+' : '' }}{{ deltaChars }}）
          </span>
          <span>· 变更句 {{ changedCount }} 处</span>
          <span>· 模型 {{ result.model_ref.split(':')[1] ?? result.model_ref }}</span>
        </div>

        <div class="diff-view">
          <span
            v-for="(seg, idx) in diffSegments"
            :key="idx"
            :class="{
              'diff-same': seg.type === 'same',
              'diff-del': seg.type === 'del',
              'diff-ins': seg.type === 'ins',
            }"
            >{{ seg.text }}</span
          >
        </div>

        <div class="diff-legend muted text-sm">
          <span class="diff-del">删除</span>
          <span class="diff-ins">新增</span>
          <span>接受后替换选区并标记 AI 来源（可点击标记回滚）；放弃则不改动任何内容。</span>
        </div>
      </template>
    </div>

    <template #footer>
      <el-button @click="close(); emit('discard')">{{ t('common.cancel') }}（放弃）</el-button>
      <el-button type="primary" :disabled="!result || loading" @click="accept">
        接受改写
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.diff-body {
  min-height: 120px;
}

.diff-meta {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}

.delta-up {
  color: var(--el-color-success);
}

.delta-down {
  color: var(--el-color-warning);
}

.diff-view {
  line-height: 1.9;
  font-size: 14px;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 12px 14px;
  max-height: 46vh;
  overflow-y: auto;
  background: var(--el-fill-color-lighter);
}

.diff-same {
  color: var(--el-text-color-regular);
}

.diff-del {
  background: var(--el-color-danger-light-8);
  color: var(--el-color-danger);
  text-decoration: line-through;
  border-radius: 2px;
}

.diff-ins {
  background: var(--el-color-success-light-8);
  color: var(--el-color-success-dark-2);
  border-radius: 2px;
}

.diff-legend {
  margin-top: 10px;
  display: flex;
  gap: 8px;
  align-items: center;
}

.diff-legend .diff-del,
.diff-legend .diff-ins {
  padding: 0 6px;
  text-decoration: none;
}

.muted {
  color: var(--el-text-color-secondary);
}

.text-sm {
  font-size: 12px;
}
</style>
