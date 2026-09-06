import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'
import { fileURLToPath } from 'node:url'

const frontendDir = fileURLToPath(new URL('.', import.meta.url))

export default defineConfig(({ mode }) => {
  // 端口等本地配置统一放在 frontend/.env（VITE_DEV_SERVER_PORT、VITE_API_PROXY_TARGET），
  // 未配置时回退到下面的默认值
  const env = loadEnv(mode, frontendDir, 'VITE_')
  const apiProxyTarget = env.VITE_API_PROXY_TARGET || 'http://localhost:61002'
  const devServerPort = Number(env.VITE_DEV_SERVER_PORT) || 5273

  return {
    plugins: [vue()],
    resolve: {
      alias: {
        '@': resolve(frontendDir, 'src')
      }
    },
    server: {
      host: '0.0.0.0',
      port: devServerPort,
      proxy: {
        '/api': {
          target: apiProxyTarget,
          changeOrigin: true
        },
        '/static': {
          target: apiProxyTarget,
          changeOrigin: true
        }
      }
    }
  }
})
