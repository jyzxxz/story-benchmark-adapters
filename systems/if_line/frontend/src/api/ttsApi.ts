import axios from 'axios'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || ''

/**
 * TTS 专用 axios 子实例。
 *
 * 为什么不用主实例 `@/api/index.ts`:
 * 主实例的拦截器会做两件对 TTS 试听有害的事:
 * 1. 自动注入 BYOK 头(X-LLM-API-Key / Base-Url / Model)—— 但 TTS 引擎(aliyun/xunfei/minimax)不消费这些头,Key 全部来自后端环境变量。
 * 2. 401 自动刷 guest + 重放 + 跳转 /login —— 试听场景下用户可能就是游客或 session 刚过期,
 *    试听失败应该直接报错,而不是偷偷刷登录态把用户工作流打乱。
 *
 * 因此本子实例刻意:
 * - 不带 withCredentials(不送 cookie,与试听无状态语义一致)
 * - 不挂任何拦截器(完全不进 BYOK / 401 链)
 *
 * 项目级受控生成仍通过主实例处理登录态和项目所有权校验。
 */
const ttsClient = axios.create({
  baseURL: `${API_BASE_URL}/api/tts`,
  timeout: 60000,
  withCredentials: false,
  headers: {
    'Content-Type': 'application/json'
  }
})

export interface TTSSynthesizeRequest {
  text: string
  character_name?: string
  character_voice?: string
  gender?: string
  age?: string
  emotion?: string
  speaker?: string
  emotion_prompt?: string
  /**
   * 项目 ID。传了之后会走项目级音色绑定：同项目内不同角色优先独占不同 vcn，
   * 不够再走参数差异化。character_name 也必须传，character_id 由后端从 name+project_id 派生。
   */
  project_id?: number
}

export interface TTSSynthesizeResponse {
  success: boolean
  audio_url?: string
  cached?: boolean
  speaker?: string
  emotion_prompt?: string
  error?: string
}

export const ttsApi = {
  /**
   * 合成语音（单句试听，走 TTS 子实例绕过主拦截器）
   */
  async synthesize(request: TTSSynthesizeRequest): Promise<{ data: TTSSynthesizeResponse }> {
    return ttsClient.post('/synthesize', request)
  },

  /**
   * 播放音频
   */
  playAudio(audioUrl: string): Promise<void> {
    return new Promise((resolve, reject) => {
      const fullUrl = audioUrl.startsWith('http') ? audioUrl : `${API_BASE_URL}${audioUrl}`
      const audio = new Audio(fullUrl)

      audio.onended = () => resolve()
      audio.onerror = (e) => reject(new Error('音频播放失败'))

      audio.play().catch(reject)
    })
  }
}
