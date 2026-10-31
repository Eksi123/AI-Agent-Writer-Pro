/**
 * axios 封装：统一解包 `{code, message, data}` 信封 + 错误码归一化。
 *
 * 约定（手册「六、通用约定」）：
 * - 成功 code=0，直接返回 data；
 * - 失败抛 ApiError，携带 code / message / data（409 冲突时 data 含最新内容）；
 * - /api 由 vite 代理到后端（见 vite.config.ts），生产环境单体部署同源。
 */
import axios, { AxiosError, type AxiosInstance, type AxiosRequestConfig } from 'axios'
import { t } from '@/i18n'
import { ErrorCode } from '@/types'

/** 统一的业务错误：由信封 code != 0 或非 2xx 响应产生 */
export class ApiError extends Error {
  code: number
  data: unknown
  httpStatus: number

  constructor(code: number, message: string, data: unknown = null, httpStatus = 0) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.data = data
    this.httpStatus = httpStatus
  }

  /** 是否为乐观锁版本冲突（409 / 40901） */
  get isVersionConflict(): boolean {
    return this.code === ErrorCode.VERSION_CONFLICT
  }

  /** 是否为显式命名校验失败（422 / 42201） */
  get isNamingInvalid(): boolean {
    return this.code === ErrorCode.NAMING_INVALID
  }

  /** 是否为依赖不可用（503 / 50301，如无 calibre） */
  get isDependencyUnavailable(): boolean {
    return this.code === ErrorCode.DEPENDENCY_UNAVAILABLE
  }
}

interface BackendEnvelope<T> {
  code: number
  message: string
  data: T
}

/**
 * ★v1031：HTTP 状态码 → 简短中文文案。
 * axios 的 `error.message` 是英文（如 `Request failed with status code 500`），
 * 直接展示会把英文抛给用户，故统一在这里换成中文。
 */
function httpErrorText(status: number): string {
  switch (true) {
    case status === 400:
      return t('httpError.badRequest')
    case status === 401:
      return t('httpError.unauthorized')
    case status === 403:
      return t('httpError.forbidden')
    case status === 404:
      return t('httpError.notFound')
    case status === 408:
      return t('httpError.timeout')
    case status === 409:
      return t('httpError.conflict')
    case status >= 500:
      return t('httpError.serverError')
    default:
      return t('httpError.unknown')
  }
}

export const http: AxiosInstance = axios.create({
  baseURL: '/api/v1',
  timeout: 60_000,
  headers: { 'Content-Type': 'application/json' },
})

/** 响应拦截：解信封，code!=0 抛 ApiError */
http.interceptors.response.use(
  (response) => {
    const body = response.data as BackendEnvelope<unknown>
    // ★v1031 续：后端**缺少该路由**时（如后端进程是旧版本），单体部署的 SPA 回落会把
    // index.html 页面以 200 返回，body 是 HTML 字符串。若不拦，调用方拿到字符串后
    // 取属性（如回收站的 `res.items`）直接崩成白屏报错。这里统一转成中文 ApiError。
    if (typeof body === 'string') {
      return Promise.reject(
        new ApiError(ErrorCode.INTERNAL_ERROR, t('httpError.htmlResponse'), null, response.status),
      )
    }
    // 二进制流（导出下载）直接放行
    if (body == null || typeof body !== 'object' || !('code' in body)) {
      return response
    }
    if (body.code !== ErrorCode.OK) {
      throw new ApiError(body.code, body.message || t('common.unknownError'), body.data, response.status)
    }
    // 把 data 挂到 response.data，调用方拿到的就是业务数据
    response.data = body.data
    return response
  },
  (error: AxiosError<BackendEnvelope<unknown>>) => {
    const status = error.response?.status ?? 0
    const body = error.response?.data
    if (body && typeof body === 'object' && 'code' in body) {
      return Promise.reject(
        new ApiError(body.code, body.message || httpErrorText(status), body.data, status),
      )
    }
    // 网络层错误（后端未启动 / 超时）
    if (!error.response) {
      return Promise.reject(new ApiError(ErrorCode.INTERNAL_ERROR, t('health.backendOffline'), null, 0))
    }
    // ★v1031：axios 的 error.message 是英文（如 "Request failed with status code 500"），
    // 统一换成简短中文，避免把英文错误抛给用户。
    return Promise.reject(new ApiError(ErrorCode.INTERNAL_ERROR, httpErrorText(status), null, status))
  },
)

/** GET 并返回解包后的 data */
export async function get<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
  const res = await http.get<T>(url, config)
  return res.data as T
}

/** POST 并返回解包后的 data */
export async function post<T>(url: string, body?: unknown, config?: AxiosRequestConfig): Promise<T> {
  const res = await http.post<T>(url, body, config)
  return res.data as T
}

/** PUT 并返回解包后的 data */
export async function put<T>(url: string, body?: unknown, config?: AxiosRequestConfig): Promise<T> {
  const res = await http.put<T>(url, body, config)
  return res.data as T
}

/** PATCH 并返回解包后的 data */
export async function patch<T>(url: string, body?: unknown, config?: AxiosRequestConfig): Promise<T> {
  const res = await http.patch<T>(url, body, config)
  return res.data as T
}

/** DELETE 并返回解包后的 data */
export async function del<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
  const res = await http.delete<T>(url, config)
  return res.data as T
}

/**
 * 把 ApiError 转成适合 ElMessage 展示的中文文案。
 * 命名类错误（42201）单独提示，其余回落到后端 message。
 *
 * ★v1031：非后端错误（原生 JS Error，message 多为英文）一律换成简短中文，
 * 避免把 `Cannot read properties of undefined` 这类英文抛给用户。
 */
export function errorMessage(err: unknown, fallback?: string): string {
  if (err instanceof ApiError) {
    // v1028：命名类错误优先展示后端具体原因（如「不允许全部为阿拉伯数字」）
    if (err.isNamingInvalid) return err.message || t('novel.namingRejected')
    if (err.isDependencyUnavailable) return err.message
    if (err.isVersionConflict) return t('conflict.message')
    return err.message || fallback || t('common.unknownError')
  }
  if (err instanceof Error) {
    // 后端文案是中文，可直接展示；原生英文异常改为统一中文提示
    const msg = (err.message ?? '').trim()
    return msg && /[\u4e00-\u9fff]/.test(msg) ? msg : (fallback ?? t('common.unknownError'))
  }
  return fallback ?? t('common.unknownError')
}
