<template>
  <div class="debug-page">
    <!-- 页面头：身份 + 定位说明 -->
    <header class="page-header">
      <div class="header-left">
        <h1 class="page-title">调试</h1>
        <p class="page-subtitle">基础设施直测 · 发一条看效果，不经过 LLM 与弹幕链路</p>
      </div>
    </header>

    <!-- 主从布局：左列调试项目录，右列选中项详情 -->
    <div class="debug-master-detail">
      <!-- 左：调试项目录 -->
      <aside class="md-list-panel" aria-label="调试项目录">
        <header class="md-list-header">
          <div class="md-list-header-main">
            <h2 class="md-list-title">调试项</h2>
          </div>
        </header>
        <ul class="md-list" role="listbox">
          <li
            v-for="item in DEBUG_ITEMS"
            :key="item.key"
            class="md-row"
            :class="{ 'is-selected': item.key === selectedKey }"
            role="option"
            :aria-selected="item.key === selectedKey"
            tabindex="0"
            @click="selectedKey = item.key"
            @keydown.enter.prevent="selectedKey = item.key"
            @keydown.space.prevent="selectedKey = item.key"
          >
            <span class="md-row-name" :title="item.label">{{ item.label }}</span>
          </li>
        </ul>
      </aside>

      <!-- 右：选中项详情 -->
      <main class="md-detail-panel" aria-label="调试项详情">
        <SubtitleDebug v-if="selectedKey === 'subtitle'" />
        <TtsDebug v-else-if="selectedKey === 'tts'" />
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
// 调试页主从布局壳：目录固定两列渲染，以后加调试项 = 目录加一行 + 详情区一个组件
import { ref } from 'vue';
import SubtitleDebug from '@/components/debug/SubtitleDebug.vue';
import TtsDebug from '@/components/debug/TtsDebug.vue';
import '@/styles/component-master-detail.css';

interface DebugItem {
  key: 'subtitle' | 'tts';
  label: string;
}

const DEBUG_ITEMS: DebugItem[] = [
  { key: 'subtitle', label: '字幕' },
  { key: 'tts', label: 'TTS 语音' },
];

// 默认选中第一项
const selectedKey = ref<DebugItem['key']>('subtitle');
</script>

<style scoped>
/* 页面级：主从网格布局；主从通用样式见 styles/component-master-detail.css */
.debug-page {
  --md-accent: var(--color-primary);
  --md-accent-bg: var(--color-primary-bg);
  --md-pulse-ring: rgba(64, 158, 255, 0.5);

  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
  height: calc(100vh - var(--header-height) - 2 * var(--spacing-lg));
}

.page-header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--spacing-md);
  flex-shrink: 0;
}

.page-title {
  margin: 0;
  font-size: 22px;
  font-weight: 650;
  color: var(--text-primary);
}

.page-subtitle {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--text-secondary);
}

.debug-master-detail {
  display: grid;
  grid-template-columns: 240px minmax(0, 1fr);
  gap: var(--spacing-md);
  flex: 1;
  min-height: 0;
}

/* 详情区自身滚动：壳高度固定，长详情页不撑破布局 */
.md-detail-panel {
  overflow-y: auto;
}

/* 目录行无状态点需求（调试项无启停状态），去掉共享样式的圆点占位 */
.md-row .md-row-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 768px) {
  .debug-master-detail {
    grid-template-columns: 1fr;
  }
}
</style>
