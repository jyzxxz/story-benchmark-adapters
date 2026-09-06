export const BYOK_HEADER_NAME = 'X-LLM-API-Key'
export const BYOK_BASE_URL_HEADER_NAME = 'X-LLM-Base-Url'
export const BYOK_MODEL_HEADER_NAME = 'X-LLM-Model'
export const BYOK_API_KEY_MAX_LENGTH = 2048
export const BYOK_BASE_URL_MAX_LENGTH = 512
export const BYOK_MODEL_MAX_LENGTH = 256

const API_KEY_STORAGE_KEY = 'if-line:byok:llm-api-key'
const BASE_URL_STORAGE_KEY = 'if-line:byok:llm-base-url'
const MODEL_STORAGE_KEY = 'if-line:byok:llm-model'
const ENABLED_STORAGE_KEY = 'if-line:byok:enabled'
const AUTH_SESSION_CHANNEL_NAME = 'if-line:auth-session'

let authSessionChannel: BroadcastChannel | null = null

export interface ByokStatus {
  hasKey: boolean
  hasBaseUrl: boolean
  hasModel: boolean
  enabled: boolean
}

function getAuthSessionChannel(): BroadcastChannel | null {
  if (typeof window === 'undefined' || typeof BroadcastChannel === 'undefined') return null
  if (authSessionChannel) return authSessionChannel

  try {
    authSessionChannel = new BroadcastChannel(AUTH_SESSION_CHANNEL_NAME)
    authSessionChannel.addEventListener('message', (event: MessageEvent<unknown>) => {
      const payload = event.data as { type?: unknown } | null
      if (payload?.type === 'auth-session-changed') clearByokApiKey()
    })
    return authSessionChannel
  } catch {
    return null
  }
}

/** Clear BYOK in other tabs when the shared cookie identity changes. */
export function notifyAuthSessionChanged(): void {
  try {
    getAuthSessionChannel()?.postMessage({ type: 'auth-session-changed' })
  } catch {
    // Cross-tab coordination is best effort and never includes the secret.
  }
}

// Subscribe as soon as the security module is loaded in this tab.
getAuthSessionChannel()

function getSessionStorage(): Storage | null {
  if (typeof window === 'undefined') return null

  try {
    return window.sessionStorage
  } catch {
    return null
  }
}

function requireSessionStorage(): Storage {
  const storage = getSessionStorage()
  if (!storage) {
    throw new Error('当前浏览器无法使用会话存储，不能保存 API Key')
  }
  return storage
}

function normalizeApiKey(value: string): string {
  const key = value.trim()
  if (!key) return ''

  if (key.length > BYOK_API_KEY_MAX_LENGTH) {
    throw new Error(`API Key 不能超过 ${BYOK_API_KEY_MAX_LENGTH} 个字符`)
  }

  // Browser header APIs require a safe byte-string. Provider API keys are
  // printable ASCII; rejecting whitespace/non-ASCII also prevents ambiguity.
  if (key.includes(',') || /[^\x21-\x7e]/.test(key)) {
    throw new Error('API Key 只能包含不带逗号的可见 ASCII 字符')
  }

  return key
}

const BASE_URL_RE = /^https?:\/\/[^\s]+$/
const MODEL_RE = /^[A-Za-z0-9._\-/:]{1,256}$/

function normalizeBaseUrl(value: string): string {
  const url = value.trim()
  if (!url) return ''

  if (url.length > BYOK_BASE_URL_MAX_LENGTH) {
    throw new Error(`Base URL 不能超过 ${BYOK_BASE_URL_MAX_LENGTH} 个字符`)
  }

  if (!BASE_URL_RE.test(url)) {
    throw new Error('Base URL 必须以 http:// 或 https:// 开头')
  }

  return url
}

function normalizeModel(value: string): string {
  const model = value.trim()
  if (!model) return ''

  if (model.length > BYOK_MODEL_MAX_LENGTH) {
    throw new Error(`模型名不能超过 ${BYOK_MODEL_MAX_LENGTH} 个字符`)
  }

  if (model.includes(',') || !MODEL_RE.test(model)) {
    throw new Error('模型名只能包含字母、数字、点、下划线、连字符、斜杠和冒号')
  }

  return model
}

function readTrimmed(storage: Storage, key: string): string {
  const raw = storage.getItem(key)
  return raw ? raw.trim() : ''
}

function getValidatedStoredKey(storage: Storage): string | null {
  try {
    const rawKey = storage.getItem(API_KEY_STORAGE_KEY)
    if (!rawKey) {
      if (storage.getItem(ENABLED_STORAGE_KEY) === '1') clearByokApiKey()
      return null
    }

    const key = normalizeApiKey(rawKey)
    if (key) return key
  } catch {
    // Invalid/tampered values are cleared below and are never put in a header.
  }

  clearByokApiKey()
  return null
}

export function getByokStatus(): ByokStatus {
  const storage = getSessionStorage()
  if (!storage) return { hasKey: false, hasBaseUrl: false, hasModel: false, enabled: false }

  try {
    const hasKey = Boolean(getValidatedStoredKey(storage))
    let hasBaseUrl = false
    let hasModel = false
    try {
      const baseUrl = normalizeBaseUrl(readTrimmed(storage, BASE_URL_STORAGE_KEY))
      hasBaseUrl = Boolean(baseUrl)
    } catch {
      hasBaseUrl = false
    }
    try {
      const model = normalizeModel(readTrimmed(storage, MODEL_STORAGE_KEY))
      hasModel = Boolean(model)
    } catch {
      hasModel = false
    }
    return {
      hasKey,
      hasBaseUrl,
      hasModel,
      enabled: hasKey && storage.getItem(ENABLED_STORAGE_KEY) === '1'
    }
  } catch {
    return { hasKey: false, hasBaseUrl: false, hasModel: false, enabled: false }
  }
}

/**
 * Returns the secret only for immediate request-header injection. UI code should
 * use getByokStatus(), which intentionally never exposes the stored value.
 */
export function getActiveByokApiKey(): string | null {
  const storage = getSessionStorage()
  if (!storage) return null

  try {
    if (storage.getItem(ENABLED_STORAGE_KEY) !== '1') return null
    return getValidatedStoredKey(storage)
  } catch {
    clearByokApiKey()
    return null
  }
}

export function getActiveByokBaseUrl(): string | null {
  const storage = getSessionStorage()
  if (!storage) return null

  try {
    if (storage.getItem(ENABLED_STORAGE_KEY) !== '1') return null
    if (!getValidatedStoredKey(storage)) return null
    const baseUrl = normalizeBaseUrl(readTrimmed(storage, BASE_URL_STORAGE_KEY))
    return baseUrl || null
  } catch {
    return null
  }
}

export function getActiveByokModel(): string | null {
  const storage = getSessionStorage()
  if (!storage) return null

  try {
    if (storage.getItem(ENABLED_STORAGE_KEY) !== '1') return null
    if (!getValidatedStoredKey(storage)) return null
    const model = normalizeModel(readTrimmed(storage, MODEL_STORAGE_KEY))
    return model || null
  } catch {
    return null
  }
}

/** Save and enable a key. A blank value means clear. */
export function saveByokApiKey(value: string): ByokStatus {
  const key = normalizeApiKey(value)
  const storage = requireSessionStorage()

  if (!key) {
    storage.removeItem(API_KEY_STORAGE_KEY)
    storage.removeItem(ENABLED_STORAGE_KEY)
    return { hasKey: false, hasBaseUrl: false, hasModel: false, enabled: false }
  }

  try {
    storage.setItem(API_KEY_STORAGE_KEY, key)
    storage.setItem(ENABLED_STORAGE_KEY, '1')
  } catch {
    try {
      storage.removeItem(API_KEY_STORAGE_KEY)
      storage.removeItem(ENABLED_STORAGE_KEY)
    } catch {
      // Best-effort cleanup only; never include the secret in an error.
    }
    throw new Error('API Key 保存失败，请检查浏览器的会话存储设置')
  }

  return getByokStatus()
}

export function saveByokBaseUrl(value: string): ByokStatus {
  const url = normalizeBaseUrl(value)
  const storage = requireSessionStorage()

  try {
    if (url) {
      storage.setItem(BASE_URL_STORAGE_KEY, url)
    } else {
      storage.removeItem(BASE_URL_STORAGE_KEY)
    }
  } catch {
    throw new Error('Base URL 保存失败，请检查浏览器的会话存储设置')
  }

  return getByokStatus()
}

export function saveByokModel(value: string): ByokStatus {
  const model = normalizeModel(value)
  const storage = requireSessionStorage()

  try {
    if (model) {
      storage.setItem(MODEL_STORAGE_KEY, model)
    } else {
      storage.removeItem(MODEL_STORAGE_KEY)
    }
  } catch {
    throw new Error('模型名保存失败，请检查浏览器的会话存储设置')
  }

  return getByokStatus()
}

export function setByokEnabled(enabled: boolean): ByokStatus {
  const storage = requireSessionStorage()
  const hasKey = Boolean(getValidatedStoredKey(storage))

  if (enabled && !hasKey) {
    throw new Error('请先保存 API Key')
  }

  try {
    if (enabled) {
      storage.setItem(ENABLED_STORAGE_KEY, '1')
    } else {
      storage.removeItem(ENABLED_STORAGE_KEY)
    }

    return { ...getByokStatus(), enabled: hasKey && enabled }
  } catch {
    throw new Error('API Key 状态更新失败，请检查浏览器的会话存储设置')
  }
}

/** Best-effort cleanup so logout/401 handling can never be blocked by storage. */
export function clearByokApiKey(): void {
  const storage = getSessionStorage()
  if (!storage) return

  try {
    storage.removeItem(API_KEY_STORAGE_KEY)
    storage.removeItem(BASE_URL_STORAGE_KEY)
    storage.removeItem(MODEL_STORAGE_KEY)
    storage.removeItem(ENABLED_STORAGE_KEY)
  } catch {
    // Intentionally ignored. Do not log storage errors that may carry context.
  }
}
