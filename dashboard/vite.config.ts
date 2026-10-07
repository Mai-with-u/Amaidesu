import { fileURLToPath, URL } from 'node:url'

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'

// 端口由后端启动 Vite 时经环境变量注入（与 infra.dashboard 配置同源）；单独运行 pnpm dev 时回落默认值
const backendPort = process.env.DASHBOARD_BACKEND_PORT ?? '60214'
const backendHost = process.env.DASHBOARD_BACKEND_HOST ?? 'localhost'
const vitePort = Number(process.env.DASHBOARD_VITE_PORT ?? 60315)

export default defineConfig({
  plugins: [
    vue(),
    AutoImport({
      resolvers: [ElementPlusResolver({ importStyle: 'css', icons: true })],
    }),
    Components({
      resolvers: [ElementPlusResolver({ importStyle: 'css', icons: true })],
    }),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    }
  },
  server: {
    port: vitePort,
    host: true,
    proxy: {
      '/api': {
        target: `http://${backendHost}:${backendPort}`,
        changeOrigin: true,
        ws: true,
      },
      '/ws': {
        target: `ws://${backendHost}:${backendPort}`,
        ws: true,
      },
    },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          'vue-vendor': ['vue', 'vue-router', 'pinia', '@vueuse/core'],
          'element-plus': ['element-plus', '@element-plus/icons-vue'],
          'highlight': ['highlight.js'],
        },
      },
    },
    chunkSizeWarningLimit: 600,
  },
})
