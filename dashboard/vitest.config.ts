import { fileURLToPath, URL } from 'node:url';

import vue from '@vitejs/plugin-vue';
import { defineConfig } from 'vitest/config';

// 纯函数工具层测试：node 环境即可（无 DOM 依赖）；组件挂载用例按文件首行注解切
// happy-dom，.vue 单文件组件由 vue 插件编译（自动导入插件不接，测试里显式注册组件）
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
});
