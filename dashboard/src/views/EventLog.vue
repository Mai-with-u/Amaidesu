<template>
  <div class="event-log">
    <!-- 页面标题 -->
    <header class="page-header">
      <div class="header-left">
        <h1 class="page-title">事件日志</h1>
        <p class="page-subtitle">实时监控系统事件流</p>
      </div>
      <div class="header-actions">
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
          v-for="event in visibleEvents"
          :key="event.timestamp + event.type"
          class="event-row"
          @click="toggleEventExpand(event.timestamp + event.type)"
        >
          <span class="event-time mono">{{ formatTime(event.timestamp) }}</span>
          <span class="event-type" :class="getEventClass(event.type)">{{ event.type }}</span>
          <div class="event-data-wrapper">
            <pre
              class="event-data"
              :class="{ expanded: expandedEvents.has(event.timestamp + event.type) }"
              v-html="
                formatEventDataHtml(event.data, expandedEvents.has(event.timestamp + event.type))
              "
            ></pre>
            <span v-if="shouldShowExpand(event.data)" class="expand-hint">
              {{ expandedEvents.has(event.timestamp + event.type) ? '点击收起' : '点击展开' }}
            </span>
          </div>
          <el-button
            v-if="getMessageId(event)"
            class="trace-link-btn"
            size="small"
            type="primary"
            link
            @click.stop="openTrace(event)"
          >
            🔍 链路
          </el-button>
        </div>
        </div>
      </div>
    </section>

    <!-- Trace 详情抽屉 -->
    <el-drawer
      v-model="traceDrawerVisible"
      title="消息链路追踪"
      size="480px"
      direction="rtl"
      :before-close="closeTraceDrawer"
    >
      <template #default>
        <!-- 加载中 -->
        <div v-if="traceLoading" class="drawer-loading">
          <el-skeleton :rows="3" animated />
          <el-skeleton :rows="4" animated style="margin-top: 16px" />
          <el-skeleton :rows="2" animated style="margin-top: 16px" />
        </div>

        <!-- 错误/未找到 -->
        <el-result
          v-else-if="traceError"
          icon="warning"
          title="加载失败"
          :sub-title="traceError"
        >
          <template #extra>
            <el-button type="primary" @click="loadTrace(currentTraceId)">重试</el-button>
          </template>
        </el-result>

        <!-- 链路详情 -->
        <div v-else-if="currentTrace" class="drawer-trace">
          <!-- 消息 -->
          <div class="dt-node input">
            <div class="dt-dot" />
            <div class="dt-card">
              <div class="dt-card-header">
                <span class="dt-card-title">📩 消息</span>
                <el-tag size="small" type="primary" effect="plain">{{ currentTrace.message.source }}</el-tag>
              </div>
              <div class="dt-message-text">{{ currentTrace.message.text }}</div>
              <div class="dt-message-meta">
                <span>{{ formatDrawerTime(currentTrace.message.timestamp_ms) }}</span>
                <span v-if="currentTrace.message.user_nickname || currentTrace.message.user_id">
                  👤 {{ currentTrace.message.user_nickname || currentTrace.message.user_id }}
                </span>
              </div>
            </div>
          </div>

          <!-- 箭头 -->
          <div class="dt-arrow">
            <span class="dt-arrow-label">{{ formatDrawerLatency(currentTrace.decision?.elapsed_ms) }}</span>
          </div>

          <!-- 决策 -->
          <div class="dt-node decision">
            <div class="dt-dot" />
            <div class="dt-card">
              <div class="dt-card-header">
                <span class="dt-card-title">🧠 决策</span>
                <el-tag size="small" type="warning" effect="plain">
                  {{ currentTrace.decision?.decider || '无' }}
                </el-tag>
              </div>
              <template v-if="currentTrace.decision">
                <div class="dt-speech">💬 {{ currentTrace.decision.speech }}</div>
                <div v-if="currentTrace.decision.emotion" class="dt-meta-row">
                  😊 {{ currentTrace.decision.emotion.name }} ({{ currentTrace.decision.emotion.intensity.toFixed(2) }})
                </div>
                <div v-if="currentTrace.decision.action" class="dt-meta-row">
                  🎬 <code>{{ currentTrace.decision.action.name }}</code>
                  <span v-if="Object.keys(currentTrace.decision.action.parameters).length">({{ JSON.stringify(currentTrace.decision.action.parameters) }})</span>
                </div>
              </template>
              <div v-else class="dt-empty-hint">未生成决策（消息被过滤）</div>
            </div>
          </div>

          <!-- 箭头 -->
          <div class="dt-arrow">
            <span class="dt-arrow-label">→ 输出</span>
          </div>

          <!-- 输出 -->
          <div class="dt-node outputs">
            <div class="dt-dot" />
            <div class="dt-card">
              <div class="dt-card-header">
                <span class="dt-card-title">📤 输出</span>
                <el-tag size="small" type="success" effect="plain">
                  {{ currentTrace.outputs.length }} 个 Handler
                </el-tag>
              </div>
              <div v-if="currentTrace.outputs.length === 0" class="dt-empty-hint">
                无 Handler 处理此 Intent
              </div>
              <div v-else class="dt-output-list">
                <div v-for="(out, i) in currentTrace.outputs" :key="i" class="dt-output-row">
                  <el-icon class="dt-ok"><Check /></el-icon>
                  <el-tag size="small" type="success" effect="plain">{{ out.handler }}</el-tag>
                  <span class="dt-elapsed">{{ formatDrawerLatency(out.elapsed_ms) }}</span>
                  <span v-if="out.speech" class="dt-speech">{{ out.speech }}</span>
                </div>
              </div>
            </div>
          </div>

          <!-- 汇总 -->
          <div class="dt-summary">
            总耗时 <strong>{{ formatDrawerLatency(currentTrace.total_elapsed_ms) }}</strong>
            <span class="dt-divider">|</span>
            ID <code>{{ currentTrace.message_id.slice(0, 16) }}…</code>
          </div>
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, nextTick, markRaw } from 'vue';
import { Document, VideoPause, VideoPlay, Search, Delete, Check } from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';
import { useEventsStore } from '@/stores';
import { storeToRefs } from 'pinia';
import DOMPurify from 'dompurify';
import hljs from 'highlight.js/lib/core';
import json from 'highlight.js/lib/languages/json';
import 'highlight.js/styles/atom-one-dark.min.css';
import { tracesApi } from '@/api';
import type { Trace } from '@/types';

// 注册 JSON 语言
hljs.registerLanguage('json', json);

const eventsStore = useEventsStore();
const { events } = storeToRefs(eventsStore);

// 状态
const eventListRef = ref<HTMLElement | null>(null);
const isPaused = ref(false);
const searchQuery = ref('');
const selectedTypes = ref<string[]>([]);
const showHiddenEvents = ref(false);
const expandedEvents = ref<Set<string>>(new Set());

// 默认隐藏的事件类型（系统噪音）
const HIDDEN_EVENT_TYPES = ['system.heartbeat', 'heartbeat', 'ping', 'pong', 'log.entry'];

// 动态图标
const pausePlayIcon = computed(() => (isPaused.value ? markRaw(VideoPlay) : markRaw(VideoPause)));

// 获取所有出现过的唯一事件类型（动态生成）
const availableEventTypes = computed(() => {
  const types = new Set<string>();
  events.value.forEach(event => {
    types.add(event.type);
  });
  return Array.from(types).sort();
});

// 获取某个事件类型的数量
function getEventTypeCount(type: string): number {
  return events.value.filter(e => e.type === type).length;
}

// 筛选后的事件列表
const filteredEvents = computed(() => {
  let result = events.value;

  // 1. 过滤隐藏的系统事件
  if (!showHiddenEvents.value) {
    result = result.filter(event => !isHiddenEvent(event.type));
  }

  // 2. 按事件类型筛选
  if (selectedTypes.value.length > 0) {
    result = result.filter(event => selectedTypes.value.includes(event.type));
  }

  // 3. 按搜索关键词筛选
  if (searchQuery.value.trim()) {
    const query = searchQuery.value.toLowerCase();
    result = result.filter(event => {
      // 搜索事件类型
      if (event.type.toLowerCase().includes(query)) return true;
      // 搜索事件数据
      const dataStr = JSON.stringify(event.data).toLowerCase();
      return dataStr.includes(query);
    });
  }

  return result;
});

const MAX_VISIBLE_EVENTS = 100;
const visibleEvents = computed(() => {
  const list = filteredEvents.value;
  if (list.length <= MAX_VISIBLE_EVENTS) return list;
  return list.slice(list.length - MAX_VISIBLE_EVENTS);
});

// 判断是否为隐藏的系统事件
function isHiddenEvent(type: string): boolean {
  return HIDDEN_EVENT_TYPES.some(hidden => type.toLowerCase().includes(hidden.toLowerCase()));
}

// 暂停/继续自动滚动（事件始终在后台接收,仅停止自动滚动）
function togglePause() {
  isPaused.value = !isPaused.value;
  if (isPaused.value) {
    ElMessage.info('显示已暂停(后台仍在接收)');
  } else {
    ElMessage.success('显示已恢复');
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
function toggleEventExpand(key: string) {
  if (expandedEvents.value.has(key)) {
    expandedEvents.value.delete(key);
  } else {
    expandedEvents.value.add(key);
  }
  expandedEvents.value = new Set(expandedEvents.value);
}

// Trace 抽屉状态
const traceDrawerVisible = ref(false);
const traceLoading = ref(false);
const traceError = ref('');
const currentTraceId = ref('');
const currentTrace = ref<Trace | null>(null);

async function loadTrace(messageId: string) {
  traceLoading.value = true;
  traceError.value = '';
  currentTrace.value = null;
  try {
    const res = await tracesApi.get(messageId);
    if (res.data.trace) {
      currentTrace.value = res.data.trace;
    } else {
      traceError.value = res.data.error || '未找到该链路';
    }
  } catch (e) {
    traceError.value = e instanceof Error ? e.message : '请求失败';
  } finally {
    traceLoading.value = false;
  }
}

function closeTraceDrawer() {
  traceDrawerVisible.value = false;
  currentTrace.value = null;
  traceError.value = '';
}

// 从 message.received 事件中提取 message_id
function getMessageId(event: { type: string; data: Record<string, unknown> }): string | null {
  if (event.type !== 'message.received') return null;
  const msg = event.data?.message as Record<string, unknown> | undefined;
  if (!msg) return null;
  const id = msg.message_id;
  return typeof id === 'string' && id.length > 0 ? id : null;
}

function openTrace(event: { type: string; data: Record<string, unknown> }) {
  const id = getMessageId(event);
  if (id) {
    currentTraceId.value = id;
    traceDrawerVisible.value = true;
    loadTrace(id);
  }
}

// Trace 抽屉内的时间/延迟格式化
function formatDrawerTime(tsMs: number): string {
  if (!tsMs) return '';
  return new Date(tsMs).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function formatDrawerLatency(ms: number | undefined | null): string {
  if (ms == null) return '';
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

// 获取事件类型的 CSS 类
function getEventClass(type: string): string {
  if (type.includes('input') || type.includes('message')) return 'type-input';
  if (type.includes('decision') || type.includes('intent')) return 'type-decision';
  if (type.includes('output') || type.includes('render')) return 'type-output';
  if (type.includes('error')) return 'type-error';
  if (type.includes('collector')) return 'type-collector';
  if (type.includes('system') || type.includes('heartbeat')) return 'type-system';
  return 'type-default';
}

// 自动滚动到底部 - 使用 requestAnimationFrame 节流
let scrollRAFId: number | null = null;
let pendingScroll = false;

function scrollToBottom() {
  if (pendingScroll) return;
  pendingScroll = true;

  scrollRAFId = requestAnimationFrame(() => {
    pendingScroll = false;
    nextTick(() => {
      if (eventListRef.value) {
        eventListRef.value.scrollTop = eventListRef.value.scrollHeight;
      }
    });
  });
}

// 生命周期
let unsubscribe: (() => void) | null = null;

onMounted(() => {
  // 订阅事件变化,自动滚动 (事件接收由 store setup 阶段统一处理)
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
  // 取消 pending 的 scrollToBottom
  if (scrollRAFId !== null) {
    cancelAnimationFrame(scrollRAFId);
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

.event-type.type-collector {
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

.trace-link-btn {
  flex-shrink: 0;
  align-self: center;
}

/* Trace 抽屉 */
.drawer-loading {
  padding: 24px 16px;
}

.drawer-trace {
  padding: 8px 0;
}

.dt-node {
  display: flex;
  gap: 12px;
  margin-bottom: 4px;
}

.dt-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
  margin-top: 14px;
}

.dt-node.input .dt-dot { background: var(--color-primary); }
.dt-node.decision .dt-dot { background: var(--color-warning); }
.dt-node.outputs .dt-dot { background: var(--color-success); }

.dt-card {
  flex: 1;
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-md);
  padding: 12px;
}

.dt-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.dt-card-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

.dt-message-text {
  font-size: 14px;
  color: var(--text-primary);
  margin-bottom: 6px;
  word-break: break-all;
}

.dt-message-meta {
  display: flex;
  gap: 12px;
  font-size: 11px;
  color: var(--text-secondary);
}

.dt-speech {
  font-size: 13px;
  color: var(--text-primary);
  margin-bottom: 4px;
}

.dt-meta-row {
  font-size: 12px;
  color: var(--text-secondary);
  margin-top: 4px;
}
.dt-meta-row code {
  background: var(--bg-hover);
  padding: 1px 4px;
  border-radius: 2px;
  font-size: 11px;
}

.dt-empty-hint {
  font-size: 12px;
  color: var(--text-placeholder);
  padding: 8px 0;
}

.dt-output-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.dt-output-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  flex-wrap: wrap;
}

.dt-ok {
  color: var(--color-success);
  font-size: 14px;
}

.dt-elapsed {
  color: var(--text-secondary);
  font-family: var(--font-mono);
  font-size: 11px;
}

.dt-arrow {
  display: flex;
  align-items: center;
  padding: 4px 0 4px 22px;
}

.dt-arrow-label {
  font-size: 11px;
  color: var(--text-placeholder);
  font-family: var(--font-mono);
}

.dt-summary {
  margin-top: 16px;
  padding: 12px;
  background: var(--bg-hover);
  border-radius: var(--radius-sm);
  font-size: 12px;
  color: var(--text-secondary);
  text-align: center;
}

.dt-summary strong {
  color: var(--color-primary);
}

.dt-divider {
  margin: 0 8px;
  color: var(--border-color);
}

.dt-summary code {
  font-size: 11px;
  color: var(--text-secondary);
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
