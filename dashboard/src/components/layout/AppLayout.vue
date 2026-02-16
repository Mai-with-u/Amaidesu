<template>
  <el-container class="app-layout">
    <!-- 顶部栏 -->
    <el-header class="app-header" height="var(--header-height)">
      <div class="header-left">
        <div class="brand">
          <span class="brand-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="12" cy="12" r="10" />
              <path d="M8 14s1.5 2 4 2 4-2 4-2" />
              <line x1="9" y1="9" x2="9.01" y2="9" />
              <line x1="15" y1="9" x2="15.01" y2="9" />
            </svg>
          </span>
          <span class="brand-text">Amaidesu</span>
        </div>
      </div>

      <div class="header-right">
        <!-- 连接状态 -->
        <div class="connection-status" :class="{ connected: isConnected }">
          <span class="status-dot" :class="isConnected ? 'running' : 'stopped'"></span>
          <span class="status-label">{{ isConnected ? '已连接' : '离线' }}</span>
        </div>

        <!-- 主题切换 -->
        <el-tooltip
          :content="themeStore.theme === 'light' ? '切换到深色模式' : '切换到浅色模式'"
          placement="bottom"
        >
          <el-button
            class="theme-toggle"
            :icon="themeStore.theme === 'light' ? Moon : Sunny"
            circle
            @click="themeStore.toggleTheme()"
          />
        </el-tooltip>
      </div>
    </el-header>

    <el-container class="app-body">
      <!-- 侧边栏 -->
      <el-aside width="var(--sidebar-width)" class="app-aside">
        <Sidebar />
      </el-aside>

      <!-- 主内容区 -->
      <el-main class="app-main">
        <slot />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { Sunny, Moon } from '@element-plus/icons-vue';
import Sidebar from './Sidebar.vue';
import { useThemeStore, useWebSocketStore } from '@/stores';
import { storeToRefs } from 'pinia';

const themeStore = useThemeStore();
const wsStore = useWebSocketStore();
const { isConnected } = storeToRefs(wsStore);
</script>

<style scoped>
.app-layout {
  height: 100vh;
  width: 100%;
  flex-direction: column;
  background-color: var(--bg-app);
}

/* 顶部栏 */
.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 var(--spacing-lg);
  background-color: var(--bg-header);
  border-bottom: 1px solid var(--border-color-light);
  box-shadow: var(--shadow-sm);
  z-index: 100;
}

.header-left {
  display: flex;
  align-items: center;
  gap: var(--spacing-md);
}

.brand {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
}

.brand-icon {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--color-primary);
}

.brand-icon svg {
  width: 24px;
  height: 24px;
}

.brand-text {
  font-size: 18px;
  font-weight: 600;
  color: var(--text-primary);
  letter-spacing: -0.5px;
}

.header-right {
  display: flex;
  align-items: center;
  gap: var(--spacing-md);
}

/* 连接状态 */
.connection-status {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  padding: var(--spacing-sm) var(--spacing-md);
  background-color: var(--bg-elevated);
  border-radius: var(--radius-lg);
  font-size: 13px;
}

.connection-status .status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.connection-status .status-dot.running {
  background-color: var(--color-success);
  box-shadow: 0 0 8px var(--color-success);
  animation: pulse 2s infinite;
}

.connection-status .status-dot.stopped {
  background-color: var(--color-danger);
}

.connection-status .status-label {
  color: var(--text-secondary);
}

.connection-status.connected .status-label {
  color: var(--color-success);
}

/* 主题切换按钮 */
.theme-toggle {
  border: 1px solid var(--border-color);
  background-color: var(--bg-elevated);
  color: var(--text-secondary);
}

.theme-toggle:hover {
  border-color: var(--color-primary);
  color: var(--color-primary);
  background-color: var(--bg-active);
}

/* 主体区域 */
.app-body {
  flex: 1;
  overflow: hidden;
}

.app-aside {
  background-color: var(--bg-sidebar);
  border-right: 1px solid var(--border-color-light);
  overflow: hidden;
  flex-shrink: 0;
}

.app-main {
  padding: var(--spacing-lg);
  background-color: var(--bg-app);
  overflow-y: auto;
}

@keyframes pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.5;
  }
}

/* 响应式 */
@media (max-width: 768px) {
  .app-header {
    padding: 0 var(--spacing-md);
  }

  .brand-text {
    display: none;
  }

  .connection-status .status-label {
    display: none;
  }
}
</style>
