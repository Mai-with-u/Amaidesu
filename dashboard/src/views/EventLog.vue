<template>
  <div class="event-log">
    <!-- 页面标题 -->
    <header class="page-header">
      <div class="header-left">
        <h1 class="page-title">事件日志</h1>
        <p class="page-subtitle">实时监控系统事件流</p>
      </div>
      <div class="header-actions">
        <el-tag :type="isConnected ? 'success' : 'danger'" effect="plain" size="small">
          {{ isConnected ? '已连接' : '未连接' }}
        </el-tag>
        <el-tag type="info" effect="plain" size="small">
          {{ filteredEvents.length }} / {{ events.length }} 条事件
        </el-tag>
      </div>
    </header>

    <!-- 筛选工具栏 -->
    <section class="filter-toolbar">
      <div class="filter-left">
        <!-- 搜索框 -->
        <el-input
          v-model="searchQuery"
          placeholder="搜索事件内容..."
          :prefix-icon="Search"
          clearable
          style="width: 240px"
        />

        <!-- 事件类型筛选（动态生成） -->
        <el-select
          v-model="selectedTypes"
          multiple
          collapse-tags
          collapse-tags-tooltip
          placeholder="筛选事件类型"
          clearable
          style="width: 280px"
        >
          <el-option v-for="type in availableEventTypes" :key="type" :label="type" :value="type">
            <span class="type-option">
              <span class="type-name">{{ type }}</span>
              <span class="type-count">{{ getEventTypeCount(type) }}</span>
            </span>
          </el-option>
        </el-select>

        <!-- 显示隐藏事件开关 -->
        <el-tooltip content="显示默认隐藏的系统事件（如心跳）" placement="top">
          <el-switch v-model="showHiddenEvents" active-text="显示全部" inactive-text="过滤噪音" />
        </el-tooltip>
      </div>

      <div class="filter-right">
        <!-- 暂停/继续 -->
        <el-button :type="isPaused ? 'primary' : 'default'" @click="togglePause">
          <el-icon><component :is="pausePlayIcon" /></el-icon>
          {{ isPaused ? '继续' : '暂停' }}
        </el-button>

        <!-- 清空 -->
        <el-button :disabled="events.length === 0" @click="clearEvents">
          <el-icon><Delete /></el-icon>
          清空
        </el-button>
      </div>
    </section>

    <!-- 事件列表 -->
    <section class="event-list-section">
      <div ref="eventListRef" class="event-list">
        <div v-if="filteredEvents.length === 0" class="empty-state">
          <el-icon :size="48" color="var(--text-placeholder)">
            <Document />
          </el-icon>
          <span>{{
            isPaused ? '事件流已暂停' : events.length === 0 ? '等待事件...' : '没有匹配的事件'
          }}</span>
        </div>

        <div v-else class="events-container">
          <div
            v-for="(event, index) in filteredEvents"
            :key="index"
            class="event-row"
            @click="toggleEventExpand(index)"
          >
            <span class="event-time mono">{{ formatTime(event.timestamp) }}</span>
            <span class="event-type" :class="getEventClass(event.type)">{{ event.type }}</span>
            <div class="event-data-wrapper">
              <pre
                class="event-data"
                :class="{ expanded: expandedEvents.has(index) }"
                v-html="formatEventDataHtml(event.data, expandedEvents.has(index))"
              ></pre>
              <span v-if="shouldShowExpand(event.data)" class="expand-hint">
                {{ expandedEvents.has(index) ? '点击收起' : '点击展开' }}
              </span>
            </div>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, nextTick, markRaw } from 'vue';
import { Document, VideoPause, VideoPlay, Search, Delete } from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';
import { useEventsStore, useWebSocketStore } from '@/stores';
import { storeToRefs } from 'pinia';
import DOMPurify from 'dompurify';
import hljs from 'highlight.js/lib/core';
import json from 'highlight.js/lib/languages/json';
import 'highlight.js/styles/atom-one-dark.min.css';

// 注册 JSON 语言
hljs.registerLanguage('json', json);

const eventsStore = useEventsStore();
const wsStore = useWebSocketStore();
const { events } = storeToRefs(eventsStore);
const { isConnected } = storeToRefs(wsStore);

// 状态
const eventListRef = ref<HTMLElement | null>(null);
const isPaused = ref(false);
const searchQuery = ref('');
const selectedTypes = ref<string[]>([]);
const showHiddenEvents = ref(false);
const expandedEvents = ref<Set<number>>(new Set());

// 默认隐藏的事件类型（系统噪音）
const HIDDEN_EVENT_TYPES = ['system.heartbeat', 'heartbeat', 'ping', 'pong'];

// 动态图标
const pausePlayIcon = computed(() => (isPaused.value ? markRaw(VideoPlay) : markRaw(VideoPause)));

// 获取所有出现过的唯一事件类型（动态生成）
const availableEventTypes = computed(() => {
  const types = new Set<string>();
  events.value.forEach((event) => {
    types.add(event.type);
  });
  return Array.from(types).sort();
});

// 获取某个事件类型的数量
function getEventTypeCount(type: string): number {
  return events.value.filter((e) => e.type === type).length;
}

// 筛选后的事件列表
const filteredEvents = computed(() => {
  let result = events.value;

  // 1. 过滤隐藏的系统事件
  if (!showHiddenEvents.value) {
    result = result.filter((event) => !isHiddenEvent(event.type));
  }

  // 2. 按事件类型筛选
  if (selectedTypes.value.length > 0) {
    result = result.filter((event) => selectedTypes.value.includes(event.type));
  }

  // 3. 按搜索关键词筛选
  if (searchQuery.value.trim()) {
    const query = searchQuery.value.toLowerCase();
    result = result.filter((event) => {
      // 搜索事件类型
      if (event.type.toLowerCase().includes(query)) return true;
      // 搜索事件数据
      const dataStr = JSON.stringify(event.data).toLowerCase();
      return dataStr.includes(query);
    });
  }

  return result;
});

// 判断是否为隐藏的系统事件
function isHiddenEvent(type: string): boolean {
  return HIDDEN_EVENT_TYPES.some((hidden) => type.toLowerCase().includes(hidden.toLowerCase()));
}

// 暂停/继续事件流
function togglePause() {
  isPaused.value = !isPaused.value;
  if (isPaused.value) {
    eventsStore.disconnect();
    ElMessage.info('事件流已暂停');
  } else {
    eventsStore.connect();
    ElMessage.success('事件流已恢复');
  }
}

// 清空事件
function clearEvents() {
  eventsStore.clearEvents();
  expandedEvents.value.clear();
  ElMessage.success('已清空事件列表');
}

// 格式化时间
function formatTime(timestamp: number): string {
  const date = new Date(timestamp * 1000);
  return date.toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

// 格式化事件数据（带语法高亮和 XSS 防护）
function formatEventDataHtml(data: Record<string, unknown>, expanded: boolean = false): string {
  if (expanded) {
    const jsonStr = JSON.stringify(data, null, 2);
    const highlighted = hljs.highlight(jsonStr, { language: 'json' }).value;
    return DOMPurify.sanitize(highlighted);
  }
  const str = JSON.stringify(data);
  const truncated = str.length > 150 ? str.substring(0, 150) + '...' : str;
  const highlighted = hljs.highlight(truncated, { language: 'json' }).value;
  return DOMPurify.sanitize(highlighted);
}

// 判断是否需要展开按钮
function shouldShowExpand(data: Record<string, unknown>): boolean {
  const str = JSON.stringify(data);
  return str.length > 150;
}

// 切换事件展开状态
function toggleEventExpand(index: number) {
  if (expandedEvents.value.has(index)) {
    expandedEvents.value.delete(index);
  } else {
    expandedEvents.value.add(index);
  }
  expandedEvents.value = new Set(expandedEvents.value);
}

// 获取事件类型的 CSS 类
function getEventClass(type: string): string {
  if (type.includes('input') || type.includes('message')) return 'type-input';
  if (type.includes('decision') || type.includes('intent')) return 'type-decision';
  if (type.includes('output') || type.includes('render')) return 'type-output';
  if (type.includes('error')) return 'type-error';
  if (type.includes('provider')) return 'type-provider';
  if (type.includes('system') || type.includes('heartbeat')) return 'type-system';
  return 'type-default';
}

// 自动滚动到底部
function scrollToBottom() {
  nextTick(() => {
    if (eventListRef.value) {
      eventListRef.value.scrollTop = eventListRef.value.scrollHeight;
    }
  });
}

// 生命周期
let unsubscribe: (() => void) | null = null;

onMounted(() => {
  // 连接 WebSocket
  eventsStore.connect();

  // 订阅事件变化，自动滚动
  unsubscribe = eventsStore.$subscribe(() => {
    if (!isPaused.value) {
      scrollToBottom();
    }
  });
});

onUnmounted(() => {
  if (unsubscribe) {
    unsubscribe();
  }
});
</script>

<style scoped>
.event-log {
  display: flex;
  flex-direction: column;
  height: calc(100vh - var(--header-height) - var(--spacing-lg) * 2);
  overflow: hidden;
}

/* 页面标题 */
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: var(--spacing-md);
  flex-shrink: 0;
}

.header-left {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-xs);
}

.header-actions {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
}

/* 筛选工具栏 */
.filter-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--spacing-md);
  padding: var(--spacing-md);
  background: var(--bg-card);
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-color-light);
  margin-bottom: var(--spacing-md);
  flex-shrink: 0;
}

.filter-left {
  display: flex;
  align-items: center;
  gap: var(--spacing-md);
}

.filter-right {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
}

.type-option {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
}

.type-count {
  font-size: 11px;
  color: var(--text-secondary);
  background: var(--bg-hover);
  padding: 2px 6px;
  border-radius: var(--radius-sm);
}

/* 事件列表 */
.event-list-section {
  flex: 1;
  background: var(--bg-card);
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-color-light);
  overflow: hidden;
}

.event-list {
  height: 100%;
  overflow-y: auto;
  background-color: var(--bg-elevated);
}

.empty-state {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--spacing-md);
  color: var(--text-placeholder);
}

.events-container {
  padding: var(--spacing-sm) 0;
}

.event-row {
  display: flex;
  align-items: flex-start;
  gap: var(--spacing-md);
  padding: var(--spacing-sm) var(--spacing-md);
  border-bottom: 1px solid var(--border-color-light);
  font-size: 13px;
  cursor: pointer;
  transition: background-color var(--transition-fast);
}

.event-row:hover {
  background-color: var(--bg-hover);
}

.event-row:last-child {
  border-bottom: none;
}

.event-time {
  color: var(--text-secondary);
  white-space: nowrap;
  min-width: 80px;
  font-size: 12px;
}

.event-type {
  padding: 2px 8px;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.5px;
  border-radius: var(--radius-sm);
  white-space: nowrap;
  min-width: 120px;
  text-align: center;
  background-color: var(--bg-hover);
  color: var(--text-secondary);
  flex-shrink: 0;
}

.event-type.type-input {
  background-color: var(--color-input-bg);
  color: var(--color-input);
}

.event-type.type-decision {
  background-color: var(--color-decision-bg);
  color: var(--color-decision);
}

.event-type.type-output {
  background-color: var(--color-output-bg);
  color: var(--color-output);
}

.event-type.type-error {
  background-color: var(--color-danger-bg);
  color: var(--color-danger);
}

.event-type.type-provider {
  background-color: var(--color-warning-light);
  color: var(--color-warning);
}

.event-type.type-system {
  background-color: var(--bg-hover);
  color: var(--text-placeholder);
}

.event-data-wrapper {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.event-data {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-secondary);
  margin: 0;
  word-break: break-all;
  white-space: pre-wrap;
  max-height: 60px;
  overflow: hidden;
  transition: max-height var(--transition-normal);
}

.event-data.expanded {
  max-height: 400px;
  overflow-y: auto;
  background-color: var(--bg-elevated);
  padding: var(--spacing-sm);
  border-radius: var(--radius-sm);
  white-space: pre;
}

.expand-hint {
  font-size: 10px;
  color: var(--color-primary);
  opacity: 0;
  transition: opacity var(--transition-fast);
}

.event-row:hover .expand-hint {
  opacity: 1;
}

/* 滚动条样式 */
.event-list::-webkit-scrollbar {
  width: 8px;
}

.event-list::-webkit-scrollbar-track {
  background: transparent;
}

.event-list::-webkit-scrollbar-thumb {
  background: var(--border-color-dark);
  border-radius: 4px;
}

.event-list::-webkit-scrollbar-thumb:hover {
  background: var(--text-secondary);
}

/* 响应式 */
@media (max-width: 1200px) {
  .filter-toolbar {
    flex-direction: column;
    align-items: stretch;
  }

  .filter-left,
  .filter-right {
    flex-wrap: wrap;
  }
}

@media (max-width: 768px) {
  .page-header {
    flex-direction: column;
    gap: var(--spacing-sm);
  }

  .header-actions {
    width: 100%;
    justify-content: flex-end;
  }

  .filter-left {
    flex-direction: column;
    align-items: stretch;
  }

  .filter-left .el-input,
  .filter-left .el-select {
    width: 100% !important;
  }
}
</style>
