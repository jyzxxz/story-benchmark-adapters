import axios from 'axios'
import {
  BYOK_BASE_URL_HEADER_NAME,
  BYOK_HEADER_NAME,
  BYOK_MODEL_HEADER_NAME,
  getActiveByokApiKey,
  getActiveByokBaseUrl,
  getActiveByokModel
} from '@/security/byok'

const api = axios.create({
  baseURL: '/api',
  timeout: 180000,
  withCredentials: true,
  headers: {
    'Content-Type': 'application/json'
  }
})

const SILENT_401_PATH_PATTERNS = [
  /^\/auth\/me/,
  /^\/projects\/public/,
  /^\/projects\/\d+$/ // GET /projects/{id} 用 get_optional_user，游客访问私有项目返回 401 是正常情况
]

function isTrustedApiRequest(config: Parameters<typeof api.getUri>[0]): boolean {
  if (typeof window === 'undefined') return false

  try {
    // getUri applies both config.url and a caller-provided config.baseURL. The
    // resolved request must still target this page's origin and /api namespace.
    const resolvedUrl = new URL(api.getUri(config), window.location.origin)
    const isApiPath = resolvedUrl.pathname === '/api' || resolvedUrl.pathname.startsWith('/api/')
    const isAuthPath = /^\/api\/auth(?:\/|$)/.test(resolvedUrl.pathname)
    return resolvedUrl.origin === window.location.origin && isApiPath && !isAuthPath
  } catch {
    return false
  }
}

const BYOK_HEADER_NAMES = [
  BYOK_HEADER_NAME,
  BYOK_BASE_URL_HEADER_NAME,
  BYOK_MODEL_HEADER_NAME
]

function removeByokHeader(headers: unknown): void {
  if (!headers || typeof headers !== 'object') return

  const headerBag = headers as Record<string, unknown> & {
    delete?: (name: string) => void
  }

  if (typeof headerBag.delete === 'function') {
    for (const name of BYOK_HEADER_NAMES) {
      headerBag.delete(name)
    }
    return
  }

  for (const name of Object.keys(headerBag)) {
    if (BYOK_HEADER_NAMES.some((target) => name.toLowerCase() === target.toLowerCase())) {
      delete headerBag[name]
    }
  }
}

function safeRequestPath(url: unknown): string | undefined {
  if (typeof url !== 'string') return undefined
  return url.split(/[?#]/, 1)[0]
}

api.interceptors.request.use((config) => {
  // Also remove any manually supplied value when BYOK is disabled. This keeps
  // the session setting authoritative and prevents accidental external leaks.
  removeByokHeader(config.headers)

  const apiKey = getActiveByokApiKey()
  if (apiKey && isTrustedApiRequest(config)) {
    config.headers.set(BYOK_HEADER_NAME, apiKey)
    const baseUrl = getActiveByokBaseUrl()
    if (baseUrl) config.headers.set(BYOK_BASE_URL_HEADER_NAME, baseUrl)
    const model = getActiveByokModel()
    if (model) config.headers.set(BYOK_MODEL_HEADER_NAME, model)
  }

  return config
})

function shouldSilence401(url: string | undefined): boolean {
  if (!url) return false
  const path = url.replace(/^\/api/, '')
  return SILENT_401_PATH_PATTERNS.some((pattern) => pattern.test(path))
}

api.interceptors.response.use(
  (response) => {
    // Axios attaches request headers to response.config. Remove the secret so a
    // later console.log(response) cannot reveal it after the request completes.
    removeByokHeader(response?.config?.headers)
    if (response && typeof response === 'object') delete response.request
    return response
  },
  async (error) => {
    // The same config (including headers) is present on Axios errors. Redact it
    // before logging or handing the error to view-level catch blocks.
    removeByokHeader(error?.config?.headers)
    removeByokHeader(error?.response?.config?.headers)
    if (error && typeof error === 'object') delete error.request
    if (error?.response && typeof error.response === 'object') delete error.response.request

    const status = error?.response?.status
    const requestUrl = error?.config?.url
    const requestSilent401 = error?.config?.silent401 === true

    const silenceThis = shouldSilence401(requestUrl) || requestSilent401

    if (status === 401 && !silenceThis) {
      const { useUserStore } = await import('@/stores/user')
      const { default: router } = await import('@/router')
      const userStore = useUserStore()
      try {
        userStore.clear()
      } catch {
        console.warn('[api] clear user session failed')
      }
      const requestConfig = error?.config as (typeof error.config & { guestRetry?: boolean }) | undefined
      if (requestConfig && !requestConfig.guestRetry) {
        try {
          await userStore.ensureGuest()
          requestConfig.guestRetry = true
          return api.request(requestConfig)
        } catch {
          console.warn('[api] automatic guest session failed')
        }
      }
      const current = router.currentRoute.value
      if (current.name !== 'Login') {
        const redirect = current.fullPath || '/'
        router.push({ path: '/login', query: { redirect } }).catch(() => {})
      }
    }

    if (error?.code === 'ECONNABORTED') {
      error.message = '请求超时，AI 正在生成内容，请稍后重试'
    }

    if (status !== 404 && !(status === 401 && silenceThis)) {
      console.error('[api] request failed', {
        status,
        method: typeof error?.config?.method === 'string'
          ? error.config.method.toUpperCase()
          : undefined,
        path: safeRequestPath(requestUrl),
        code: error?.code,
        message: error?.message
      })
    }

    return Promise.reject(error)
  }
)

export default api
