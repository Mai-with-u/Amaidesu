<template>
  <el-container class="app-layout">
    <!-- 顶部栏 -->
    <el-header class="app-header" height="var(--header-height)">
      <div class="header-left">
        <div class="brand">
          <span class="brand-icon">
            <img src="/icon.png" alt="Amaidesu" width="30" height="30" />
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

        <!-- 重启按钮 -->
        <el-button
          class="restart-btn"
          :icon="SwitchButton"
          text
          :disabled="restarting"
          @click="handleRestart"
        >
          重启
        </el-button>

        <!-- 主题切换 -->
        <el-tooltip
          :content="themeStore.theme === 'light' ? '切换到深色模式' : '切换到浅色模式'"
          placement="bottom"
        >
          <el-button class="theme-toggle" circle aria-label="切换深浅主题" @click="onToggleTheme">
            <!-- 图标随主题旋转换场：月亮转出、太阳转入 -->
            <transition name="theme-icon" mode="out-in">
              <el-icon :key="themeStore.theme">
                <Moon v-if="themeStore.theme === 'light'" />
                <Sunny v-else />
              </el-icon>
            </transition>
          </el-button>
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

  <!-- 重启全屏遮罩（fixed定位，放在最外层） -->
  <RestartOverlay
    :visible="overlayVisible"
    :status="overlayStatus"
    :progress="overlayProgress"
    :elapsed="overlayElapsed"
    :check-attempts="overlayCheckAttempts"
    :max-attempts="overlayMaxAttempts"
    @retry="handleRetry"
    @refresh-page="handleRefreshPage"
  />
</template>

<script setup lang="ts">
import { Sunny, Moon, SwitchButton } from '@element-plus/icons-vue';
import { confirmAction } from '@/utils/confirmAction';
import Sidebar from './Sidebar.vue';
import RestartOverlay from './RestartOverlay.vue';
import { useThemeStore, useWebSocketStore } from '@/stores';
import { useAppRestartStore } from '@/stores/appRestart';
import { storeToRefs } from 'pinia';
import { useSystemNotifications } from '@/composables/useSystemNotifications';

const themeStore = useThemeStore();
const wsStore = useWebSocketStore();
const { isConnected } = storeToRefs(wsStore);

// 系统级事件（core.error / core.startup / core.shutdown）右上角显式通知
useSystemNotifications();

// ---- 重启（流程与遮罩状态在全局 appRestart store，任何页面可触发） ----
const appRestart = useAppRestartStore();
const {
  restarting,
  overlayVisible,
  overlayStatus,
  overlayProgress,
  overlayElapsed,
  overlayCheckAttempts,
  overlayMaxAttempts,
} = storeToRefs(appRestart);

async function handleRestart() {
  const ok = await confirmAction(
    '重启后服务将短暂不可用，WebSocket 会自动重连。确定要重启吗？',
    '重启服务',
    {
      confirmButtonText: '确认重启',
      confirmButtonClass: 'el-button--danger',
    },
  );
  if (!ok) return;
  void appRestart.beginRestart();
}

function handleRetry() {
  appRestart.retryHealthCheck();
}

function handleRefreshPage() {
  window.location.reload();
}

/** 以按钮中心为圆心切换主题，新主题从按钮处圆形铺满全屏 */
function onToggleTheme(event: MouseEvent): void {
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
  void themeStore.toggleTheme({ x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 });
}
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
  width: 30px;
  height: 30px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 9px;
  transition: transform 0.4s var(--ease-spring);
}

.brand:hover .brand-icon {
  transform: rotate(-8deg) scale(1.06);
}

.brand-icon img {
  width: 100%;
  height: 100%;
  display: block;
  border-radius: inherit;
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
  padding: 6px 12px;
  background-color: var(--bg-elevated);
  border: 1px solid var(--border-color-light);
  border-radius: 999px;
  font-size: 13px;
  transition:
    background-color var(--transition-normal),
    border-color var(--transition-normal);
}

.connection-status.connected {
  background-color: var(--color-success-bg);
  border-color: transparent;
}

.connection-status .status-dot {
  position: relative;
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

/* 在线：实心点外扩一圈渐隐的涟漪，像心跳；离线：红点静止 */
.connection-status .status-dot.running {
  background-color: var(--color-success);
  box-shadow: none;
  animation: none;
}

.connection-status .status-dot.running::after {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: 50%;
  background-color: var(--color-success);
  animation: status-ripple 2s var(--ease-out) infinite;
}

.connection-status .status-dot.stopped {
  background-color: var(--color-danger);
}

@keyframes status-ripple {
  from {
    opacity: 0.6;
    transform: scale(1);
  }
  to {
    opacity: 0;
    transform: scale(2.6);
  }
}

.connection-status .status-label {
  color: var(--text-secondary);
}

.connection-status.connected .status-label {
  color: var(--color-success);
}

/* 重启按钮 */
.restart-btn {
  color: var(--text-secondary);
  font-size: 13px;
}

.restart-btn:hover {
  color: var(--color-danger);
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

.theme-icon-enter-active,
.theme-icon-leave-active {
  transition:
    opacity 0.2s ease,
    transform 0.3s var(--ease-spring);
}

.theme-icon-enter-from {
  opacity: 0;
  transform: rotate(-90deg) scale(0.5);
}

.theme-icon-leave-to {
  opacity: 0;
  transform: rotate(90deg) scale(0.5);
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
