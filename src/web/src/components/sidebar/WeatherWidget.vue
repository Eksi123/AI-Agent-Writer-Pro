<script setup lang="ts">
/**
 * 左上角动态组件（M6，手册「三、(3) 左侧栏」）：
 * 宠物动态 icon（SVG 动画状态机，**仅两态**：写作中→敲键盘 / 久无进展→打瞌睡）
 * + 所在城市（浏览器定位，失败回落通用设置的回落城市）+ 当前天气。
 *
 * 数据流：浏览器定位 → /widgets/weather?lat&lon（含反向地理编码）；
 * 定位失败/被拒 → /widgets/weather?city={回落城市}。
 * 宠物状态：/widgets/pet-state?novel_id（project_index.last_updated 距今 ≤ pet_idle_minutes → writing）。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { widgetsApi, type PetStateDto, type WeatherDto } from '@/api'
import { useProjectStore } from '@/stores/project'
import { useSettingsStore } from '@/stores/settings'

const project = useProjectStore()
const settings = useSettingsStore()

const weather = ref<WeatherDto | null>(null)
const pet = ref<PetStateDto | null>(null)
const locating = ref(false)
const weatherError = ref('')

const REFRESH_MS = 5 * 60 * 1000 // 天气 5 分钟刷新
const PET_POLL_MS = 60 * 1000 // 宠物状态 1 分钟轮询
let weatherTimer: ReturnType<typeof setInterval> | null = null
let petTimer: ReturnType<typeof setInterval> | null = null

const petState = computed(() => pet.value?.state ?? 'idle')
const cityLabel = computed(() => weather.value?.city || settings.settings.city || '—')
const tempLabel = computed(() => {
  const w = weather.value
  if (!w || w.temperature == null) return '--'
  return `${Math.round(w.temperature)}°`
})

async function loadWeather(): Promise<void> {
  weatherError.value = ''
  try {
    // 浏览器定位（可能失败/被拒 → 回落设置城市）
    const coords = await geolocate()
    if (coords) {
      weather.value = await widgetsApi.weather({ latitude: coords.lat, longitude: coords.lon })
    } else {
      weather.value = await widgetsApi.weather({})
    }
  } catch {
    weatherError.value = '天气暂不可用'
  }
}

function geolocate(): Promise<{ lat: number; lon: number } | null> {
  return new Promise((resolve) => {
    if (!navigator.geolocation) {
      resolve(null)
      return
    }
    locating.value = true
    const timer = setTimeout(() => {
      locating.value = false
      resolve(null)
    }, 6000)
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        clearTimeout(timer)
        locating.value = false
        resolve({ lat: pos.coords.latitude, lon: pos.coords.longitude })
      },
      () => {
        clearTimeout(timer)
        locating.value = false
        resolve(null) // 被拒/失败 → 回落城市（后端处理）
      },
      { timeout: 5000, maximumAge: 10 * 60 * 1000 },
    )
  })
}

async function loadPet(): Promise<void> {
  try {
    pet.value = await widgetsApi.petState(project.currentNovelId ?? undefined)
  } catch {
    pet.value = null
  }
}

onMounted(async () => {
  await Promise.all([loadWeather(), loadPet()])
  weatherTimer = setInterval(() => void loadWeather(), REFRESH_MS)
  petTimer = setInterval(() => void loadPet(), PET_POLL_MS)
})

onBeforeUnmount(() => {
  if (weatherTimer) clearInterval(weatherTimer)
  if (petTimer) clearInterval(petTimer)
})

// 切换项目时刷新宠物状态
watch(
  () => project.currentNovelId,
  () => void loadPet(),
)
</script>

<template>
  <div class="weather-widget" :title="weatherError || undefined">
    <!-- 宠物：两态 SVG 动画 -->
    <div class="pet" :class="`pet-${petState}`">
      <svg viewBox="0 0 48 48" width="40" height="40" aria-hidden="true">
        <!-- 身体 -->
        <ellipse cx="24" cy="30" rx="14" ry="11" class="pet-body" />
        <!-- 头 -->
        <circle cx="24" cy="17" r="10" class="pet-body" />
        <!-- 耳朵 -->
        <path d="M16 10 L13 3 L20 7 Z" class="pet-body" />
        <path d="M32 10 L35 3 L28 7 Z" class="pet-body" />
        <!-- 眼睛：写作态睁眼 / 瞌睡态闭眼 -->
        <g v-if="petState === 'writing'" class="pet-eyes-open">
          <circle cx="20" cy="16" r="1.6" class="pet-pupil" />
          <circle cx="28" cy="16" r="1.6" class="pet-pupil" />
        </g>
        <g v-else class="pet-eyes-closed">
          <path d="M18 16 q2 2 4 0" class="pet-line" />
          <path d="M26 16 q2 2 4 0" class="pet-line" />
        </g>
        <!-- 鼻子嘴 -->
        <path d="M22 20 q2 2 4 0" class="pet-line" />
        <!-- 写作态：敲键盘的前爪（交替动画） -->
        <g v-if="petState === 'writing'">
          <ellipse cx="15" cy="38" rx="3.4" ry="2.4" class="paw paw-left" />
          <ellipse cx="33" cy="38" rx="3.4" ry="2.4" class="paw paw-right" />
          <rect x="9" y="41" width="30" height="4" rx="2" class="keyboard" />
        </g>
        <!-- 瞌睡态：zzz -->
        <g v-else class="zzz">
          <text x="34" y="10" font-size="7" class="zzz-text">z</text>
          <text x="39" y="5" font-size="9" class="zzz-text zzz-2">Z</text>
        </g>
      </svg>
    </div>

    <!-- 城市 + 天气 -->
    <div class="wx-info">
      <div class="wx-city">
        <span v-if="locating" class="muted">定位中…</span>
        <span v-else>{{ cityLabel }}</span>
      </div>
      <div class="wx-line">
        <template v-if="weather">
          <span class="wx-icon">{{ weather.icon }}</span>
          <span class="wx-temp">{{ tempLabel }}</span>
          <span class="wx-desc">{{ weather.description }}</span>
        </template>
        <span v-else class="muted">{{ weatherError || '…' }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.weather-widget {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

.pet {
  flex-shrink: 0;
  line-height: 0;
}

.pet-body {
  fill: var(--el-color-primary-light-3);
}

.pet-pupil {
  fill: #303133;
}

.pet-line {
  stroke: #303133;
  stroke-width: 1.4;
  fill: none;
  stroke-linecap: round;
}

.keyboard {
  fill: var(--el-fill-color-dark);
}

.paw {
  fill: var(--el-color-primary-light-5);
}

/* 写作态：前爪交替敲键盘 */
.paw-left {
  animation: tap 0.6s infinite ease-in-out;
  transform-origin: center;
}

.paw-right {
  animation: tap 0.6s infinite ease-in-out 0.3s;
  transform-origin: center;
}

@keyframes tap {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(2px); }
}

/* 瞌睡态：轻微呼吸起伏 + zzz 漂浮 */
.pet-idle svg {
  animation: breathe 3s infinite ease-in-out;
  transform-origin: center bottom;
}

@keyframes breathe {
  0%, 100% { transform: scale(1); }
  50% { transform: scale(1.04); }
}

.zzz-text {
  fill: var(--el-text-color-secondary);
  animation: float 2.4s infinite ease-in-out;
}

.zzz-2 {
  animation-delay: 1.2s;
}

@keyframes float {
  0% { opacity: 0; transform: translateY(2px); }
  40% { opacity: 1; }
  100% { opacity: 0; transform: translateY(-4px); }
}

.wx-info {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.wx-city {
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.wx-line {
  display: flex;
  align-items: baseline;
  gap: 4px;
  font-size: 12px;
}

.wx-temp {
  font-weight: 600;
}

.wx-desc {
  color: var(--el-text-color-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.muted {
  color: var(--el-text-color-secondary);
}
</style>
