<script setup lang="ts">
/**
 * 左上角动态组件（v1028 重做）：生肖宠物 + 城市 + 天气卡片。
 *
 * 数据流：浏览器定位 → /widgets/weather?lat&lon&refresh=true（含反向地理编码）；
 * 定位失败/被拒 → /widgets/weather?refresh=true（回落通用设置的回落城市）。
 * ★城市靠定位取得；天气、温度、空气质量在**页面刷新时**都取实时数据（refresh 跳过后端缓存）。
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
const tempLabel = computed(() => {
  const w = weather.value
  if (!w || w.temperature == null) return '--'
  return `${Math.round(w.temperature)}°`
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
      <div class="wx-card" :class="{ 'wx-loading': loading }" :title="weatherError || undefined">
        <div class="wx-head">
          <span v-if="locating" class="wx-city muted">定位中…</span>
          <span v-else class="wx-city">{{ cityLabel }}</span>
          <span v-if="weather" class="wx-desc muted">{{ weather.description }}</span>
        </div>

        <div v-if="weather" class="wx-main">
          <span class="wx-icon">{{ weather.icon }}</span>
          <span class="wx-temp">{{ tempLabel }}</span>
          <span
            v-if="weather.aqi != null"
            class="wx-aqi"
            :class="aqiClass"
            :title="`AQI ${weather.aqi}`"
          >
            {{ weather.aqi_level || weather.aqi }}
          </span>
        </div>
        <div v-else class="wx-main">
          <span class="muted wx-fallback">{{ weatherError || '…' }}</span>
        </div>

        <div v-if="weather" class="wx-meta muted">
          <span v-if="weather.humidity != null">湿度 {{ Math.round(weather.humidity) }}%</span>
          <span v-if="weather.wind_speed != null">风 {{ weather.wind_speed }} km/h</span>
          <span v-if="weather.apparent_temperature != null">
            体感 {{ Math.round(weather.apparent_temperature) }}°
          </span>
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
  gap: 3px;
  padding: 8px 10px;
  border-radius: 10px;
  background: linear-gradient(135deg, var(--bg-hover), color-mix(in srgb, var(--el-color-primary) 8%, var(--bg-hover)));
  border: 1px solid var(--border);
  transition: opacity 0.2s;
}

.wx-card.wx-loading {
  opacity: 0.6;
}

.wx-head {
  display: flex;
  align-items: baseline;
  gap: 6px;
  min-width: 0;
}

.wx-city {
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.wx-desc {
  font-size: 11px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.wx-main {
  display: flex;
  align-items: center;
  gap: 6px;
}

.wx-icon {
  font-size: 20px;
  line-height: 1;
}

.wx-temp {
  font-size: 19px;
  font-weight: 700;
  line-height: 1;
  letter-spacing: 0.5px;
}

.wx-aqi {
  font-size: 10px;
  line-height: 1;
  padding: 3px 6px;
  border-radius: 8px;
  color: #fff;
  white-space: nowrap;
}

.wx-fallback {
  font-size: 12px;
}

.aqi-none { background: var(--text-muted); }
.aqi-good { background: #52c41a; }
.aqi-moderate { background: #d4b106; }
.aqi-light { background: #fa8c16; }
.aqi-mid { background: #f5222d; }
.aqi-heavy { background: #a8071a; }
.aqi-severe { background: #531dab; }

.wx-meta {
  display: flex;
  gap: 8px;
  font-size: 11px;
  flex-wrap: wrap;
}

.muted {
  color: var(--text-muted);
}
</style>
