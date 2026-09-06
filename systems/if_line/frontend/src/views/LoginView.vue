<template>
  <div class="login-container">
    <div class="login-card">
      <div class="login-header">
        <h2>AI 小说 Agent</h2>
        <p class="subtitle">沉浸式小说创作平台</p>
      </div>

      <el-tabs v-model="activeTab" class="login-tabs" stretch>
        <el-tab-pane label="登录" name="login">
          <el-form
            ref="loginFormRef"
            :model="loginForm"
            :rules="loginRules"
            label-position="top"
            @submit.prevent="handleLogin"
          >
            <el-form-item label="邮箱" prop="email">
              <el-input v-model="loginForm.email" type="email" placeholder="you@example.com" autocomplete="email" />
            </el-form-item>
            <el-form-item label="密码" prop="password">
              <el-input v-model="loginForm.password" type="password" show-password placeholder="请输入密码" autocomplete="current-password" />
            </el-form-item>
            <el-button type="primary" :loading="submitting" @click="handleLogin" class="submit-btn">登录</el-button>
          </el-form>
        </el-tab-pane>

        <el-tab-pane label="注册" name="register">
          <el-form
            ref="registerFormRef"
            :model="registerForm"
            :rules="registerRules"
            label-position="top"
            @submit.prevent="handleRegister"
          >
            <el-form-item label="邮箱" prop="email">
              <el-input v-model="registerForm.email" type="email" placeholder="you@example.com" autocomplete="email" />
            </el-form-item>
            <el-form-item label="显示名（可选）" prop="display_name">
              <el-input v-model="registerForm.display_name" placeholder="留空则使用邮箱前缀" maxlength="100" />
            </el-form-item>
            <el-form-item label="密码（至少 8 位）" prop="password">
              <el-input v-model="registerForm.password" type="password" show-password placeholder="8-128 位" autocomplete="new-password" />
            </el-form-item>
            <el-form-item label="确认密码" prop="confirm">
              <el-input v-model="registerForm.confirm" type="password" show-password placeholder="再次输入密码" autocomplete="new-password" />
            </el-form-item>
            <el-button type="primary" :loading="submitting" @click="handleRegister" class="submit-btn">注册</el-button>
          </el-form>
        </el-tab-pane>
      </el-tabs>

      <div class="guest-entry">
        <router-link to="/">浏览公开作品（无需登录）</router-link>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import { useUserStore } from '@/stores/user'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

const activeTab = ref<'login' | 'register'>('login')
const submitting = ref(false)

const loginFormRef = ref<FormInstance>()
const registerFormRef = ref<FormInstance>()

const loginForm = reactive({ email: '', password: '' })
const registerForm = reactive({ email: '', display_name: '', password: '', confirm: '' })

const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
// 简单的弱密码检测：纯数字、纯字母、或常见弱密码
const weakPasswordPattern = /^(\d+|[a-z]+)$/i
const commonWeakPasswords = new Set([
  'password', 'password1', '12345678', '11111111', '00000000',
  'qwerty12', 'abc12345', 'passw0rd'
])

const loginRules: FormRules = {
  email: [
    { required: true, message: '请输入邮箱', trigger: 'blur' },
    { pattern: emailPattern, message: '邮箱格式不正确', trigger: 'blur' }
  ],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }]
}

const registerRules: FormRules = {
  email: [
    { required: true, message: '请输入邮箱', trigger: 'blur' },
    { pattern: emailPattern, message: '邮箱格式不正确', trigger: 'blur' }
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 8, max: 128, message: '密码长度 8-128 位', trigger: 'blur' },
    {
      validator: (_rule, value: string, callback) => {
        if (!value) return callback()
        if (commonWeakPasswords.has(value.toLowerCase())) {
          return callback(new Error('该密码过于常见，请更换'))
        }
        if (weakPasswordPattern.test(value)) {
          return callback(new Error('密码不能为纯数字或纯字母，建议混合字母与数字'))
        }
        callback()
      },
      trigger: 'blur'
    }
  ],
  confirm: [
    { required: true, message: '请再次输入密码', trigger: 'blur' },
    {
      validator: (_rule, value: string, callback) => {
        if (value !== registerForm.password) callback(new Error('两次输入的密码不一致'))
        else callback()
      },
      trigger: 'blur'
    }
  ]
}

const redirectAfterAuth = () => {
  const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/'
  router.replace(redirect)
}

const handleLogin = async () => {
  if (!loginFormRef.value) return
  const valid = await loginFormRef.value.validate().catch(() => false)
  if (!valid) return
  submitting.value = true
  try {
    await userStore.login({ email: loginForm.email.trim().toLowerCase(), password: loginForm.password })
    ElMessage.success('登录成功')
    redirectAfterAuth()
  } catch (error: any) {
    const detail = error?.response?.data?.detail
    ElMessage.error(detail || '登录失败，请检查邮箱和密码')
  } finally {
    submitting.value = false
  }
}

const handleRegister = async () => {
  if (!registerFormRef.value) return
  const valid = await registerFormRef.value.validate().catch(() => false)
  if (!valid) return
  submitting.value = true
  try {
    const payload = {
      email: registerForm.email.trim().toLowerCase(),
      password: registerForm.password,
      display_name: registerForm.display_name.trim() || undefined
    }
    await userStore.register(payload)
    ElMessage.success('注册成功，已自动登录')
    redirectAfterAuth()
  } catch (error: any) {
    const detail = error?.response?.data?.detail
    ElMessage.error(detail || '注册失败，请稍后重试')
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.login-container {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  padding: 24px;
}

.login-card {
  width: 100%;
  max-width: 420px;
  background: #fff;
  border-radius: 12px;
  padding: 32px 36px 24px;
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.18);
}

.login-header {
  text-align: center;
  margin-bottom: 16px;
}

.login-header h2 {
  margin: 0 0 6px;
  color: #2c3e50;
}

.login-header .subtitle {
  color: #7f8c8d;
  font-size: 14px;
}

.login-tabs {
  margin-top: 8px;
}

.submit-btn {
  width: 100%;
  margin-top: 8px;
}

.guest-entry {
  margin-top: 16px;
  text-align: center;
  font-size: 13px;
}

.guest-entry a {
  color: #667eea;
  text-decoration: none;
}

.guest-entry a:hover {
  text-decoration: underline;
}
</style>
