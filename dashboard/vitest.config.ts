import { fileURLToPath, URL } from 'node:url';

import { defineConfig } from 'vitest/config';

// 纯函数工具层测试：node 环境即可（无 DOM 依赖），不引入 vue/自动导入插件以保持轻量
export default defineConfig({
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
