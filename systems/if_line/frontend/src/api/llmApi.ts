import api from './index'

export interface ByokDefaults {
  base_url?: string
  base_url_masked?: string
  model?: string
  model_masked?: string
  [key: string]: unknown
}

export const llmApi = {
  // 获取服务端 BYOK 默认配置（base_url / model）
  getByokDefaults: () =>
    api.get<ByokDefaults>('/llm/byok-defaults')
}
