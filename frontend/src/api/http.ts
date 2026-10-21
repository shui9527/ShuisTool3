// 统一 HTTP 请求封装：所有后端接口保持原有路径与语义

export class ApiError extends Error {
  constructor(message: string, public status?: number) {
    super(message)
    this.name = 'ApiError'
  }
}

// 默认请求超时（毫秒）：防止后端异常无响应时前端永久 pending——
// 典型场景：SSE 长连接挤占开发服务器线程、连接按钮请求排队时，
// fetch 无超时会一直挂起，导致 connecting 永不复位、按钮一直 loading。
const DEFAULT_TIMEOUT = 20000

async function request<T = any>(url: string, options: RequestInit = {}): Promise<T> {
  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), DEFAULT_TIMEOUT)
  try {
    const response = await fetch(url, {
      headers: { 'Content-Type': 'application/json' },
      ...options,
      signal: options.signal ?? controller.signal,
    })
    if (!response.ok) {
    // 优先透出后端 error/message 字段，便于前端展示真实失败原因（如密钥未配置）
    let detail = ''
    try {
      const t = await response.text()
      const j = JSON.parse(t)
      detail = (j && (j.error || j.message)) || ''
    } catch {
      // 非 JSON 错误体，忽略
    }
    throw new ApiError(detail || `请求失败 HTTP ${response.status}: ${url}`, response.status)
    }
    const text = await response.text()
    if (!text) return undefined as T
    // 风险7：统一校验非 JSON 响应——SPA 回退/死接口在 GET 下会返回 index.html(200)，
    // 若当作 JSON 静默解析必然在业务层失效；非 JSON 一律抛 ApiError 明确提示。
    const ct = response.headers.get('content-type') || ''
    const looksJson = ct.includes('application/json') || /^\s*[{[]/.test(text)
    if (looksJson) {
      try {
        return JSON.parse(text) as T
      } catch {
        throw new ApiError(`接口返回非法 JSON（HTTP ${response.status}）: ${url}`)
      }
    }
    throw new ApiError(`接口返回非 JSON 响应（Content-Type: ${ct || 'unknown'}，HTTP ${response.status}）: ${url}，请确认后端路由存在且返回 JSON`, response.status)
  } catch (e: any) {
    // 统一超时语义：AbortError -> ApiError(请求超时)，避免上层只看到 DOMException
    if (e && e.name === 'AbortError') {
      throw new ApiError(`请求超时（${DEFAULT_TIMEOUT / 1000} 秒）: ${url}`)
    }
    throw e
  } finally {
    clearTimeout(timeoutId)
  }
}

export const http = {
  get<T = any>(url: string) {
    return request<T>(url)
  },
  post<T = any>(url: string, data?: any) {
    return request<T>(url, { method: 'POST', body: data ? JSON.stringify(data) : undefined })
  },
  postForm<T = any>(url: string, params: Record<string, string>) {
    const qs = new URLSearchParams(params).toString()
    return request<T>(url, { method: 'POST', body: qs, headers: { 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8' } })
  },
}
