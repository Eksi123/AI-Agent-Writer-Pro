<script setup lang="ts">
/**
 * 左上角动态组件：生肖宠物 + 天气卡片。
 *
 * 数据流：浏览器定位 → /widgets/weather?lat&lon&refresh=true（含反向地理编码）；
 * 定位失败/被拒 → /widgets/weather?refresh=true（回落通用设置的回落城市）。
 * 天气、温度、空气质量在页面刷新时取实时数据；组件自身 10 分钟兜底刷新。
 *
 * 卡片三排：1) 城市居中独占一行；2) 天气组（icon+说明）在排开头、空气质量组
 * （等级+数值）居中在排宽 3/5 处，四元素同一水平线；3) 温度/湿度/风速（带单位小字）。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { widgetsApi, type WeatherDto } from '@/api'
import { useSettingsStore } from '@/stores/settings'
import ZodiacPet from './ZodiacPet.vue'

const settings = useSettingsStore()

const weather = ref<WeatherDto | null>(null)
const locating = ref(false)
const loading = ref(true)
const weatherError = ref('')

const REFRESH_MS = 10 * 60 * 1000 // 组件自身 10 分钟兜底刷新（实时性主要由刷新页面触发）
let timer: ReturnType<typeof setInterval> | null = null

const cityLabel = computed(() => weather.value?.city || settings.settings.city || '—')

/** v1030：第三排三项都带单位前缀（温度 X° / 湿度 Y% / 风速 Z 级） */
const tempLabel = computed(() => {
  const w = weather.value
  if (!w || w.temperature == null) return '温度 --'
  return `温度 ${Math.round(w.temperature)}°`
})

/** v1030：风速以「风力等级」为单位（蒲福风级 0–12 级） */
const windLabel = computed(() => {
  const level = weather.value?.wind_level
  if (level == null) return '风速 --'
  return `风速 ${level}级`
})

const humidityLabel = computed(() => {
  const h = weather.value?.humidity
  if (h == null) return '湿度 --'
  return `湿度 ${Math.round(h)}%`
})

/** 空气质量等级 → 语义色 */
const aqiClass = computed(() => {
  const aqi = weather.value?.aqi
  if (aqi == null) return 'aqi-none'
  if (aqi <= 50) return 'aqi-good'
  if (aqi <= 100) return 'aqi-moderate'
  if (aqi <= 150) return 'aqi-light'
  if (aqi <= 200) return 'aqi-mid'
  if (aqi <= 300) return 'aqi-heavy'
  return 'aqi-severe'
})

/** v1029：第二排右侧「空气质量等级」（无 AQI 时展示占位） */
const aqiText = computed(() => weather.value?.aqi_level || '空气质量 --')

async function loadWeather(force = false): Promise<void> {
  weatherError.value = ''
  loading.value = !weather.value
  try {
    const coords = await geolocate()
    weather.value = coords
      ? await widgetsApi.weather({ latitude: coords.lat, longitude: coords.lon, refresh: force })
      : await widgetsApi.weather({ refresh: force })
  } catch {
    weatherError.value = '天气暂不可用'
  } finally {
    loading.value = false
  }
}

function geolocate(): Promise<{ lat: number; lon: number } | null> {
  return new Promise((resolve) => {
    if (!navigator.geolocation) {
      resolve(null)
      return
    }
    locating.value = true
    const t = setTimeout(() => {
      locating.value = false
      resolve(null)
    }, 6000)
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        clearTimeout(t)
        locating.value = false
        resolve({ lat: pos.coords.latitude, lon: pos.coords.longitude })
      },
      () => {
        clearTimeout(t)
        locating.value = false
        resolve(null)
      },
      { timeout: 5000, maximumAge: 10 * 60 * 1000 },
    )
  })
}

onMounted(async () => {
  // 页面加载（刷新）即取一次实时数据
  await loadWeather(true)
  timer = setInterval(() => void loadWeather(false), REFRESH_MS)
})

onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
})
</script>

<template>
  <div class="weather-widget">
    <div class="widget-row">
      <ZodiacPet />
      <div class="wx-card" :class="{ 'wx-loading': loading }">
        <!-- 第一排：城市居中独占一行（★v1031：不再悬浮展示完整地址） -->
        <div class="wx-row wx-head">
          <span
            v-if="locating"
            class="wx-city muted"
          >定位中…</span>
          <span
            v-else
            class="wx-city"
          >{{ cityLabel }}</span>
        </div>

        <!-- 第二排：★v1031 续：天气组在排**开头**、空气质量组居中在排宽 **3/5** 处（不挤在一起）；
             icon / 天气情况 / 等级 / AQI 数值四个元素在同一条水平线上 -->
        <div class="wx-main">
          <span v-if="weather" class="wx-group wx-group-weather">
            <span class="wx-icon">{{ weather.icon }}</span>
            <span class="wx-sub muted">{{ weather.description }}</span>
          </span>
          <span v-if="weather && weather.aqi != null" class="wx-group wx-group-air">
            <span class="wx-aqi-level" :class="aqiClass">{{ aqiText }}</span>
            <span class="wx-sub muted">{{ weather.aqi }}</span>
          </span>
        </div>

        <!-- 第三排：温度 X° / 湿度 Y% / 风速 Z 级（v1030 带单位） -->
        <div v-if="weather" class="wx-row wx-meta muted">
          <span>{{ tempLabel }}</span>
          <span>{{ humidityLabel }}</span>
          <span>{{ windLabel }}</span>
        </div>
        <div v-else class="wx-row wx-meta">
          <span class="muted wx-fallback">{{ weatherError || '…' }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.weather-widget {
  padding: 12px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

.widget-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.wx-card {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 10px;
  border-radius: 10px;
  background: linear-gradient(135deg, var(--bg-hover), color-mix(in srgb, var(--el-color-primary) 8%, var(--bg-hover)));
  border: 1px solid var(--border);
  transition: opacity 0.2s;
}

.wx-card.wx-loading {
  opacity: 0.6;
}

.wx-row {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

/* 第一排：城市居中独占一行（光标停留显示完整地址） */
.wx-head {
  justify-content: center;
}

.wx-city {
  font-size: 15px;
  font-weight: 700;
  letter-spacing: 0.3px;
  white-space: nowrap;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 第二排：天气组在排**开头**；空气质量组绝对定位、居中在排宽 **3/5** 处，两组不挤在一起（★v1031 续） */
.wx-main {
  position: relative;
  display: flex;
  align-items: center;
  min-height: 18px;
}

.wx-group-weather {
  min-width: 0;
  max-width: 55%;
  overflow: hidden;
}

/* 水平中心 = 排宽 60%（3/5），垂直方向与天气组同一条水平线 */
.wx-group-air {
  position: absolute;
  left: 60%;
  top: 50%;
  transform: translate(-50%, -50%);
}

/* 组内：icon / 文字 / 等级 / 数值**同一条水平线**，垂直居中对齐 */
.wx-group {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  line-height: 1;
  white-space: nowrap;
}

.wx-group-weather .wx-sub {
  overflow: hidden;
  text-overflow: ellipsis;
}

.wx-icon {
  font-size: 14px;
  line-height: 1;
  flex-shrink: 0;
}

.wx-sub {
  font-size: 10px;
  line-height: 1;
}

.wx-aqi-level {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.2px;
  line-height: 1;
  white-space: nowrap;
  color: var(--text);
}

/* 第三排：温度 / 湿度 / 风速（小字号 + 均分，避免超出卡片边框） */
.wx-meta {
  font-size: 10px;
  justify-content: space-between;
  gap: 4px;
  padding: 0 1px;
}

.wx-meta > span {
  white-space: nowrap;
}

.wx-fallback {
  font-size: 12px;
}

/* AQI 等级语义色（v1030：染色文字，不再是小色块） */
.aqi-none { color: var(--text-muted); }
.aqi-good { color: #52c41a; }
.aqi-moderate { color: #d4b106; }
.aqi-light { color: #fa8c16; }
.aqi-mid { color: #f5222d; }
.aqi-heavy { color: #a8071a; }
.aqi-severe { color: #531dab; }

.muted {
  color: var(--text-muted);
}
</style>
