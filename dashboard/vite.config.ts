import { fileURLToPath, URL } from 'node:url'

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'

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
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:60214',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://localhost:60214',
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
