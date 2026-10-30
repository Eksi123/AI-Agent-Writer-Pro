<script setup lang="ts">
/**
 * 左上角动态组件（v1028 重做 / v1029 重排）：生肖宠物 + 天气卡片。
 *
 * 数据流：浏览器定位 → /widgets/weather?lat&lon&refresh=true（含反向地理编码）；
 * 定位失败/被拒 → /widgets/weather?refresh=true（回落通用设置的回落城市）。
 * ★城市靠定位取得；天气、温度、空气质量在**页面刷新时**都取实时数据（refresh 跳过后端缓存）。
 *
 * v1029 天气卡片三排布局：
 *   1) 城市（大字号） + 具体地址（小字号，过长省略号，光标停留显示完整地址）
 *   2) 天气 icon + 天气说明小字 ｜ 空气质量等级 + AQI 数值小字
 *   3) 温度 / 湿度 / 风速（蒲福风力等级），体感温度已删除
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

/** v1029：第一排右侧「具体地址」（反向地理编码给出，可为空） */
const addressLabel = computed(() => weather.value?.address || '')

/** v1029：温度（第三排小字号，与其它指标同字号） */
const tempLabel = computed(() => {
  const w = weather.value
  if (!w || w.temperature == null) return '--'
  return `${Math.round(w.temperature)}°`
})

/** v1029：风速以「风力等级」为单位（蒲福风级 0–12 级） */
const windLabel = computed(() => {
  const level = weather.value?.wind_level
  if (level == null) return '--'
  return `${level}级`
})

const humidityLabel = computed(() => {
  const h = weather.value?.humidity
  if (h == null) return '--'
  return `${Math.round(h)}%`
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
        <!-- 第一排：城市（大字号） + 具体地址（小字号，过长省略，悬停显示完整地址） -->
        <div class="wx-row wx-head">
          <span v-if="locating" class="wx-city muted">定位中…</span>
          <span v-else class="wx-city">{{ cityLabel }}</span>
          <span
            v-if="addressLabel"
            class="wx-address muted"
            :title="addressLabel"
          >{{ addressLabel }}</span>
        </div>

        <!-- 第二排：左 = 天气 icon + 说明小字；右 = 空气质量 + AQI 小字 -->
        <div class="wx-row wx-main">
          <div class="wx-weather">
            <span v-if="weather" class="wx-icon">{{ weather.icon }}</span>
            <span v-if="weather" class="wx-desc muted">{{ weather.description }}</span>
          </div>
          <div v-if="weather && weather.aqi != null" class="wx-aqi-wrap">
            <span class="wx-aqi-text muted">{{ aqiText }}</span>
            <span class="wx-aqi" :class="aqiClass">{{ weather.aqi }}</span>
          </div>
        </div>

        <!-- 第三排：温度 / 湿度 / 风速（风力等级）—— v1029 删除体感温度 -->
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

/* 第一排：城市大字号 + 地址小字号（过长省略号，title 显示完整地址） */
.wx-head {
  align-items: baseline;
}

.wx-city {
  font-size: 15px;
  font-weight: 700;
  letter-spacing: 0.3px;
  white-space: nowrap;
  flex-shrink: 0;
}

.wx-address {
  font-size: 10px;
  flex: 1;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 第二排：左天气 icon+说明，右空气质量+AQI */
.wx-main {
  justify-content: space-between;
}

.wx-weather {
  display: flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
}

.wx-icon {
  font-size: 20px;
  line-height: 1;
}

.wx-desc {
  font-size: 11px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.wx-aqi-wrap {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.wx-aqi-text {
  font-size: 10px;
}

.wx-aqi {
  font-size: 10px;
  line-height: 1;
  padding: 3px 5px;
  border-radius: 8px;
  color: #fff;
  white-space: nowrap;
}

/* 第三排：温度 / 湿度 / 风速（小字号） */
.wx-meta {
  font-size: 11px;
  justify-content: space-between;
  padding-right: 2px;
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

.muted {
  color: var(--text-muted);
}
</style>
