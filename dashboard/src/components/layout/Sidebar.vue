<template>
  <div class="sidebar">
    <!-- 扁平导航 -->
    <nav class="sidebar-nav">
      <el-menu :default-active="currentRoute" class="sidebar-menu" router>
        <el-menu-item index="/">
          <el-icon><Monitor /></el-icon>
          <span>仪表盘</span>
        </el-menu-item>
        <el-menu-item index="/providers">
          <el-icon><Connection /></el-icon>
          <span>Providers</span>
        </el-menu-item>
        <el-menu-item index="/eventlog">
          <el-icon><Document /></el-icon>
          <span>事件日志</span>
        </el-menu-item>
        <el-menu-item index="/logs">
          <el-icon><Tickets /></el-icon>
          <span>日志查看器</span>
        </el-menu-item>
        <el-menu-item index="/session">
          <el-icon><ChatLineRound /></el-icon>
          <span>会话历史</span>
        </el-menu-item>

        <!-- LLM 分组 -->
        <el-sub-menu index="llm-group">
          <template #title>
            <el-icon><Cpu /></el-icon>
            <span>LLM 监控</span>
          </template>
          <el-menu-item index="/llm/usage">
            <el-icon><TrendCharts /></el-icon>
            <span>用量统计</span>
          </el-menu-item>
          <el-menu-item index="/llm/history">
            <el-icon><Clock /></el-icon>
            <span>请求历史</span>
          </el-menu-item>
        </el-sub-menu>

        <el-menu-item index="/devtools">
          <el-icon><Tools /></el-icon>
          <span>开发工具</span>
        </el-menu-item>
        <el-menu-item index="/settings">
          <el-icon><Setting /></el-icon>
          <span>系统设置</span>
        </el-menu-item>
      </el-menu>
    </nav>

    <!-- 底部版本信息 -->
    <div class="sidebar-footer">
      <span class="version">v0.1.0</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { useRoute } from 'vue-router';
import {
  Monitor,
  Connection,
  Document,
  Tools,
  Setting,
  Tickets,
  Cpu,
  TrendCharts,
  Clock,
  ChatLineRound,
} from '@element-plus/icons-vue';

const route = useRoute();
const currentRoute = computed(() => route.path);
</script>

<style scoped>
.sidebar {
  height: 100%;
  display: flex;
  flex-direction: column;
  background-color: var(--bg-sidebar);
}

.sidebar-nav {
  flex: 1;
  overflow-y: auto;
  padding: var(--spacing-md) 0;
}

/* 菜单样式 */
.sidebar-menu {
  border-right: none;
  background-color: transparent;
}

.sidebar-menu :deep(.el-menu-item) {
  height: 44px;
  line-height: 44px;
  margin: 2px var(--spacing-sm);
  border-radius: var(--radius-md);
  color: var(--text-regular);
  transition: all var(--transition-fast);
}

.sidebar-menu :deep(.el-menu-item:hover) {
  background-color: var(--bg-hover);
  color: var(--text-primary);
}

.sidebar-menu :deep(.el-menu-item.is-active) {
  background-color: var(--bg-active);
  color: var(--color-primary);
  font-weight: 500;
}

.sidebar-menu :deep(.el-menu-item.is-active)::before {
  content: '';
  position: absolute;
  left: 0;
  top: 50%;
  transform: translateY(-50%);
  width: 3px;
  height: 20px;
  background-color: var(--color-primary);
  border-radius: 0 2px 2px 0;
}

.sidebar-menu :deep(.el-menu-item .el-icon) {
  font-size: 18px;
  margin-right: var(--spacing-sm);
}

/* 子菜单样式 */
.sidebar-menu :deep(.el-sub-menu) {
  margin: 2px var(--spacing-sm);
}

.sidebar-menu :deep(.el-sub-menu__title) {
  height: 44px;
  line-height: 44px;
  border-radius: var(--radius-md);
  color: var(--text-regular);
  transition: all var(--transition-fast);
}

.sidebar-menu :deep(.el-sub-menu__title:hover) {
  background-color: var(--bg-hover);
  color: var(--text-primary);
}

.sidebar-menu :deep(.el-sub-menu .el-icon) {
  font-size: 18px;
  margin-right: var(--spacing-sm);
}

.sidebar-menu :deep(.el-sub-menu .el-menu) {
  background-color: transparent;
  padding-left: 0;
}

.sidebar-menu :deep(.el-sub-menu .el-menu-item) {
  height: 40px;
  line-height: 40px;
  padding-left: 48px !important;
  margin: 1px var(--spacing-sm);
}

.sidebar-menu :deep(.el-sub-menu.is-active > .el-sub-menu__title) {
  color: var(--color-primary);
}

/* 底部版本信息 */
.sidebar-footer {
  padding: var(--spacing-md) var(--spacing-lg);
  border-top: 1px solid var(--border-color-light);
  text-align: center;
}

.version {
  font-size: 11px;
  color: var(--text-placeholder);
  font-family: var(--font-mono);
}
</style>
