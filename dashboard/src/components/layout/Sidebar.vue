<template>
  <div class="sidebar">
    <nav class="sidebar-nav">
      <el-menu :default-active="currentRoute" class="sidebar-menu" router>
        <el-menu-item index="/">
          <el-icon><Monitor /></el-icon>
          <span>运行总览</span>
        </el-menu-item>
        <el-menu-item index="/live">
          <el-icon><VideoCamera /></el-icon>
          <span>直播间观察</span>
        </el-menu-item>
        <el-menu-item index="/collectors">
          <el-icon><Connection /></el-icon>
          <span>采集器</span>
        </el-menu-item>
        <el-menu-item index="/agents">
          <el-icon><Cpu /></el-icon>
          <span>Agent</span>
        </el-menu-item>
        <el-menu-item index="/tools">
          <el-icon><Tools /></el-icon>
          <span>工具目录</span>
        </el-menu-item>
        <el-menu-item index="/eventlog">
          <el-icon><Document /></el-icon>
          <span>事件流</span>
        </el-menu-item>
        <el-menu-item index="/logs">
          <el-icon><Tickets /></el-icon>
          <span>日志</span>
        </el-menu-item>
        <el-menu-item index="/outline">
          <el-icon><List /></el-icon>
          <span>Agenda 工作台</span>
        </el-menu-item>

        <div class="sidebar-divider" />

        <el-menu-item index="/session">
          <el-icon><ChatLineRound /></el-icon>
          <span>会话调试</span>
        </el-menu-item>
        <el-menu-item index="/devtools">
          <el-icon><Cpu /></el-icon>
          <span>开发者工具</span>
        </el-menu-item>
        <el-menu-item index="/traces">
          <el-icon><Opportunity /></el-icon>
          <span>Trace 链路</span>
        </el-menu-item>
        <el-menu-item index="/llm/usage">
          <el-icon><TrendCharts /></el-icon>
          <span>LLM 用量</span>
        </el-menu-item>
        <el-menu-item index="/llm/history">
          <el-icon><Clock /></el-icon>
          <span>LLM 历史</span>
        </el-menu-item>
        <el-menu-item index="/settings">
          <el-icon><Setting /></el-icon>
          <span>设置</span>
        </el-menu-item>

        <div class="sidebar-divider" />
        <div class="sidebar-pinned-label">小部件</div>
        <el-menu-item index="/danmaku">
          <el-icon><ChatLineSquare /></el-icon>
          <span>弹幕</span>
        </el-menu-item>
        <el-menu-item index="/subtitle">
          <el-icon><ChatDotRound /></el-icon>
          <span>字幕</span>
        </el-menu-item>
      </el-menu>
    </nav>

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
  Cpu,
  TrendCharts,
  Clock,
  ChatLineRound,
  Tickets,
  ChatLineSquare,
  ChatDotRound,
  Opportunity,
  VideoCamera,
  List,
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

.sidebar-menu {
  border-right: none;
  background-color: transparent;
}

.sidebar-menu :deep(.el-menu-item) {
  height: 40px;
  line-height: 40px;
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
  font-size: 17px;
  margin-right: var(--spacing-sm);
}

.sidebar-divider {
  height: 1px;
  margin: var(--spacing-sm) var(--spacing-lg);
  background-color: var(--border-color-light);
}

.sidebar-pinned-label {
  padding: 4px var(--spacing-lg) var(--spacing-xs);
  font-size: 10px;
  font-weight: 600;
  color: var(--text-placeholder);
  letter-spacing: 0.5px;
  text-transform: uppercase;
}

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
