<script setup lang="ts">
/**
 * 左上角动态组件 · 生肖宠物（v1028）：
 * 十二种生肖对应十二时辰（每 2 小时一段），**页面加载时按当前时辰确定一次**，
 * 之后不随时间变化——只有刷新页面才会切换生肖（「如果不刷新就不变」）。
 */
import { computed, onMounted, ref } from 'vue'

interface Zodiac {
  /** 生肖名 */
  name: string
  /** 展示用 emoji */
  emoji: string
  /** 时辰名 */
  hour: string
  /** 时辰起止（含头不含尾，跨零点） */
  range: string
}

/** 十二时辰 → 生肖（子时 23:00–01:00 起） */
const ZODIAC_BY_INDEX: Zodiac[] = [
  { name: '鼠', emoji: '🐭', hour: '子时', range: '23:00-01:00' },
  { name: '牛', emoji: '🐮', hour: '丑时', range: '01:00-03:00' },
  { name: '虎', emoji: '🐯', hour: '寅时', range: '03:00-05:00' },
  { name: '兔', emoji: '🐰', hour: '卯时', range: '05:00-07:00' },
  { name: '龙', emoji: '🐲', hour: '辰时', range: '07:00-09:00' },
  { name: '蛇', emoji: '🐍', hour: '巳时', range: '09:00-11:00' },
  { name: '马', emoji: '🐴', hour: '午时', range: '11:00-13:00' },
  { name: '羊', emoji: '🐑', hour: '未时', range: '13:00-15:00' },
  { name: '猴', emoji: '🐵', hour: '申时', range: '15:00-17:00' },
  { name: '鸡', emoji: '🐔', hour: '酉时', range: '17:00-19:00' },
  { name: '狗', emoji: '🐶', hour: '戌时', range: '19:00-21:00' },
  { name: '猪', emoji: '🐷', hour: '亥时', range: '21:00-23:00' },
]

/** 由小时数求时辰序号（23 点与 0 点同属子时 → index 0） */
function zodiacIndexOf(hour: number): number {
  return Math.floor(((hour + 1) % 24) / 2)
}

/** ★只在组件创建（= 页面刷新）时取一次，之后不再变化 */
const current = ref<Zodiac>(ZODIAC_BY_INDEX[zodiacIndexOf(new Date().getHours())])

onMounted(() => {
  current.value = ZODIAC_BY_INDEX[zodiacIndexOf(new Date().getHours())]
})

const tooltip = computed(() => `${current.value.hour} · ${current.value.name}（${current.value.range}）`)
</script>

<template>
  <div class="zodiac-pet" :title="tooltip" aria-label="生肖宠物">
    <span class="zodiac-emoji">{{ current.emoji }}</span>
    <div class="zodiac-text">
      <div class="zodiac-name">{{ current.name }}</div>
      <div class="zodiac-hour muted">{{ current.hour }}</div>
    </div>
  </div>
</template>

<style scoped>
.zodiac-pet {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.zodiac-emoji {
  font-size: 30px;
  line-height: 1;
  display: inline-block;
  animation: bounce 2.6s infinite ease-in-out;
  transform-origin: center bottom;
}

@keyframes bounce {
  0%, 100% { transform: translateY(0) rotate(0deg); }
  30% { transform: translateY(-3px) rotate(-4deg); }
  60% { transform: translateY(0) rotate(3deg); }
}

.zodiac-text {
  display: flex;
  flex-direction: column;
  line-height: 1.3;
}

.zodiac-name {
  font-size: 13px;
  font-weight: 600;
}

.zodiac-hour {
  font-size: 11px;
}

.muted {
  color: var(--text-muted);
}
</style>
