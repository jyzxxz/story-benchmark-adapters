import { defineStore } from 'pinia'
import { authApi, type AuthUser, type LoginPayload, type RegisterPayload } from '@/api/authApi'
import { clearByokApiKey, notifyAuthSessionChanged } from '@/security/byok'
import { getSafeErrorSummary } from '@/security/safeError'

interface UserState {
  currentUser: AuthUser | null
  loaded: boolean
}

export const useUserStore = defineStore('user', {
  state: (): UserState => ({
    currentUser: null,
    loaded: false
  }),
  getters: {
    isLoggedIn: (state) => !!state.currentUser,
    isGuest: (state) => state.currentUser?.email.endsWith('@guest.ifline.local') ?? false
  },
  actions: {
    async fetchMe() {
      try {
        const response = await authApi.me()
        this.currentUser = response.data
      } catch (error: any) {
        if (error?.response?.status !== 401) {
          console.error('[user] fetchMe failed', getSafeErrorSummary(error))
        } else {
          clearByokApiKey()
          notifyAuthSessionChanged()
        }
        this.currentUser = null
      } finally {
        this.loaded = true
      }
    },

    async login(payload: LoginPayload) {
      // Never carry a previous account's session-only BYOK into a new login.
      clearByokApiKey()
      const response = await authApi.login(payload)
      this.currentUser = response.data.user
      this.loaded = true
      notifyAuthSessionChanged()
      return response.data.user
    },

    async ensureGuest() {
      if (this.currentUser) return this.currentUser
      clearByokApiKey()
      const response = await authApi.guest()
      this.currentUser = response.data.user
      this.loaded = true
      notifyAuthSessionChanged()
      return response.data.user
    },

    async register(payload: RegisterPayload) {
      clearByokApiKey()
      const response = await authApi.register(payload)
      this.currentUser = response.data.user
      this.loaded = true
      notifyAuthSessionChanged()
      return response.data.user
    },

    async logout() {
      try {
        await authApi.logout()
      } catch {
        console.warn('[user] logout api failed, local session cleared')
      }
      this.currentUser = null
      clearByokApiKey()
      notifyAuthSessionChanged()
    },

    clear() {
      this.currentUser = null
      clearByokApiKey()
      notifyAuthSessionChanged()
    }
  }
})
