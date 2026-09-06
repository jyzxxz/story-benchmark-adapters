import api from './index'

export interface AuthUser {
  id: number
  email: string
  display_name: string
  avatar_url: string | null
  quota_total: number
  quota_daily: number
  quota_used_total: number
  quota_used_daily: number
  quota_reset_at: string | null
  created_at: string
  updated_at: string
}

export interface AuthResponse {
  user: AuthUser
}

export interface RegisterPayload {
  email: string
  password: string
  display_name?: string
}

export interface LoginPayload {
  email: string
  password: string
}

export const authApi = {
  guest: () => api.post<AuthResponse>('/auth/guest'),

  register: (data: RegisterPayload) =>
    api.post<AuthResponse>('/auth/register', data),

  login: (data: LoginPayload) =>
    api.post<AuthResponse>('/auth/login', data),

  logout: () => api.post('/auth/logout'),

  me: () => api.get<AuthUser>('/auth/me')
}
