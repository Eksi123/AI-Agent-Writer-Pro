<script setup lang="ts">
/**
 * 左上角动态组件 · 生肖宠物：十二时辰对应十二生肖，页面加载时按当前时辰确定一次，
 * 只有刷新页面才会切换。不显示生肖名与时辰名；头像下方显示年月日 + 星期几。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

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

const WEEKDAYS = ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六']

/** 由小时数求时辰序号（23 点与 0 点同属子时 → index 0） */
function zodiacIndexOf(hour: number): number {
  return Math.floor(((hour + 1) % 24) / 2)
}

/** 只在组件创建（= 页面刷新）时取一次，之后不再变化 */
const current = ref<Zodiac>(ZODIAC_BY_INDEX[zodiacIndexOf(new Date().getHours())])
const now = ref(new Date())

let timer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  current.value = ZODIAC_BY_INDEX[zodiacIndexOf(new Date().getHours())]
  now.value = new Date()
  // 日期每天要跨零点，兜底每分钟校正一次（生肖仍只在刷新时变）
  timer = setInterval(() => {
    now.value = new Date()
  }, 60 * 1000)
})

onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
})

/** 头像下方第一行 —— 年月日 */
const dateLabel = computed(
  () => `${now.value.getFullYear()}年${now.value.getMonth() + 1}月${now.value.getDate()}日`,
)

/** 头像下方第二行 —— 星期几 */
const weekdayLabel = computed(() => WEEKDAYS[now.value.getDay()])

/** 生肖与时辰信息收进 title */
const tooltip = computed(() => `${current.value.hour} · ${current.value.name}（${current.value.range}）`)
</script>

<template>
  <div class="zodiac-pet" :title="tooltip" aria-label="生肖宠物与日期">
    <span class="zodiac-emoji">{{ current.emoji }}</span>
    <div class="zodiac-date">{{ dateLabel }}</div>
    <div class="zodiac-week muted">{{ weekdayLabel }}</div>
  </div>
</template>

<style scoped>
/* 头像上移（居中），下方两行日期文字 */
.zodiac-pet {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
  line-height: 1.25;
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

.zodiac-date {
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
}

.zodiac-week {
  font-size: 11px;
  white-space: nowrap;
}

.muted {
  color: var(--text-muted);
}
</style>
