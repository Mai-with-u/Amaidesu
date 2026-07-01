<template>
  <div class="debug-session">
    <header class="page-header">
      <div class="header-left">
        <h1 class="page-title">调试会话</h1>
        <p class="page-subtitle">NormalizedMessage → Intent → Output 全链路事件追踪</p>
      </div>
      <div class="header-actions">
        <el-tag :type="wsConnected ? 'success' : 'danger'" effect="plain" size="small">
          {{ wsConnected ? 'WebSocket 已连接' : 'WebSocket 未连接' }}
        </el-tag>
        <span class="event-count">{{ filteredEvents.length }} / {{ events.length }} 条</span>
        <el-button @click="clearEvents" :disabled="events.length === 0" size="small">
          <el-icon><Delete /></el-icon>
          清空
        </el-button>
      </div>
    </header>

    <!-- 筛选栏 -->
    <section class="filter-bar">
      <div class="filter-left">
        <el-select
          v-model="typeFilter"
          multiple
          collapse-tags
          collapse-tags-tooltip
          placeholder="事件类型"
          clearable
          style="width: 200px"
        >
          <el-option label="弹幕消息 (message.received)" value="message.received" />
          <el-option label="决策意图 (decision.intent)" value="decision.intent" />
          <el-option label="渲染输出 (output.render)" value="output.render" />
        </el-select>
        <el-input
          v-model="searchQuery"
          placeholder="搜索内容..."
          :prefix-icon="Search"
          clearable
          style="width: 220px"
        />
        <el-switch v-model="autoScroll" active-text="自动滚动" inactive-text="手动" />
      </div>
    </section>

    <!-- 事件时间线 -->
    <div ref="timelineRef" class="timeline-container">
      <div v-if="filteredEvents.length === 0" class="empty-state">
        <el-icon class="empty-icon"><Timer /></el-icon>
        <span>{{ events.length === 0 ? '等待事件...' : '没有匹配的事件' }}</span>
        <span class="empty-hint">WebSocket 已连接后将自动接收事件</span>
      </div>

      <div v-else class="timeline">
        <div
          v-for="event in filteredEvents"
          :key="event.id"
          :class="['event-card', `event-card--${eventTypeToClass(event.type)}`]"
          @click="toggleExpand(event.id)"
        >
          <!-- 卡片头部 -->
          <div class="card-header">
            <span :class="['event-badge', `badge--${eventTypeToClass(event.type)}`]">
              {{ eventTypeLabel(event.type) }}
            </span>
            <span class="event-time">{{ formatMs(event.timestamp) }}</span>
            <span class="header-spacer" />
            <span v-if="isExpandable(event)" class="expand-toggle">
              {{ expanded.has(event.id) ? '收起 ▲' : '展开 ▼' }}
            </span>
          </div>

          <!-- message.received 卡片内容 -->
          <template v-if="event.type === 'message.received' && event.message">
            <div class="card-body">
              <div class="field-row">
                <span class="field-label">来源</span>
                <el-tag size="small" type="info">{{ event.message.source }}</el-tag>
                <span class="field-label">类型</span>
                <el-tag size="small" :type="dataTypeTagType(event.message.data_type)">
                  {{ event.message.data_type }}
                </el-tag>
                <span class="field-label">优先级</span>
                <span class="importance-bar">
                  <span
                    class="importance-fill"
                    :style="{ width: `${(event.message.importance ?? 0.5) * 100}%` }"
                  />
                  <span class="importance-text"
                    >{{ ((event.message.importance ?? 0.5) * 100).toFixed(0) }}%</span
                  >
                </span>
              </div>
              <div class="message-text">
                {{ event.message.text }}
              </div>
              <div
                v-if="event.message.user_nickname || event.message.platform"
                class="field-row field-row--meta"
              >
                <span v-if="event.message.user_nickname">
                  <span class="field-label">用户</span>{{ event.message.user_nickname }}
                </span>
                <span v-if="event.message.user_id" class="meta-sep">|</span>
                <span v-if="event.message.user_id">
                  <span class="field-label">UID</span>{{ event.message.user_id }}
                </span>
                <span v-if="event.message.platform" class="meta-sep">|</span>
                <span v-if="event.message.platform">
                  <span class="field-label">平台</span>{{ event.message.platform }}
                </span>
                <span v-if="event.message.room_id" class="meta-sep">|</span>
                <span v-if="event.message.room_id">
                  <span class="field-label">房间</span>{{ event.message.room_id }}
                </span>
              </div>
            </div>
          </template>

          <!-- decision.intent 卡片内容 -->
          <template
            v-if="
              (event.type === 'decision.intent' || event.type === 'output.render') && event.intent
            "
          >
            <div class="card-body">
              <div v-if="event.intent.speech" class="message-text message-text--speech">
                💬 {{ event.intent.speech }}
              </div>
              <div class="field-row">
                <template v-if="event.intent.emotion">
                  <span class="field-label">情绪</span>
                  <el-tag size="small" :type="emotionTagType(event.intent.emotion.name)">
                    {{ event.intent.emotion.name }}
                  </el-tag>
                  <span class="intensity-bar">
                    <span
                      class="intensity-fill"
                      :style="{ width: `${event.intent.emotion.intensity * 100}%` }"
                    />
                  </span>
                  <span class="intensity-num"
                    >{{ (event.intent.emotion.intensity * 100).toFixed(0) }}%</span
                  >
                </template>
                <template v-if="event.intent.action">
                  <span class="field-label">动作</span>
                  <code class="action-name">{{ event.intent.action.name }}</code>
                  <span
                    v-if="Object.keys(event.intent.action.parameters).length"
                    class="action-params"
                  >
                    {{ JSON.stringify(event.intent.action.parameters) }}
                  </span>
                </template>
              </div>
              <div class="field-row field-row--meta">
                <span class="field-label">Decider</span>
                <span>{{ event.deciderName || '-' }}</span>
                <span class="meta-sep">|</span>
                <span class="field-label">决策耗时</span>
                <span>{{ formatDecisionLatency(event) }}</span>
              </div>
            </div>
          </template>

          <!-- 展开的 JSON 详情 -->
          <div v-if="expanded.has(event.id)" class="card-detail">
            <pre class="json-view" v-html="formatJson(event)" />
          </div>
        </div>
      </div>
    </div>

    <!-- 底部输入区：手动注入消息用于测试 -->
    <div class="inject-area">
      <div class="inject-group">
        <div class="inject-label">
          <el-tag size="small" type="info">注入弹幕 (NormalizedMessage)</el-tag>
        </div>
        <el-input
          v-model="danmakuInput"
          type="textarea"
          placeholder="输入弹幕文本..."
          :rows="2"
          :disabled="sending"
          @keydown.enter.ctrl="sendDanmaku"
        />
        <el-button type="primary" size="small" :loading="sending" @click="sendDanmaku">
          <el-icon><Promotion /></el-icon>
          发送
        </el-button>
      </div>
      <div class="inject-group">
        <div class="inject-label">
          <el-tag size="small" type="success">注入回应 (Intent)</el-tag>
        </div>
        <el-input
          v-model="intentInput"
          type="textarea"
          placeholder="输入主播回应文本..."
          :rows="2"
          :disabled="sending"
          @keydown.enter.ctrl="sendMockIntent"
        />
        <el-button type="success" size="small" :loading="sending" @click="sendMockIntent">
          <el-icon><Promotion /></el-icon>
          发送
        </el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue';
import { storeToRefs } from 'pinia';
import { Delete, Timer, Search, Promotion } from '@element-plus/icons-vue';
import { useSessionStore } from '@/stores/session';
import { useWebSocketStore } from '@/stores';
import type { DebugSessionEvent } from '@/types';
import DOMPurify from 'dompurify';
import hljs from 'highlight.js/lib/core';
import json from 'highlight.js/lib/languages/json';
import 'highlight.js/styles/atom-one-dark.min.css';

hljs.registerLanguage('json', json);

const sessionStore = useSessionStore();
const wsStore = useWebSocketStore();
const { events, sending } = storeToRefs(sessionStore);
const { isConnected: wsConnected } = storeToRefs(wsStore);

const timelineRef = ref<HTMLElement | null>(null);
const typeFilter = ref<string[]>([]);
const searchQuery = ref('');
const autoScroll = ref(true);
const expanded = ref<Set<string>>(new Set());
const danmakuInput = ref('');
const intentInput = ref('');

// ===== 筛选 =====
const filteredEvents = computed(() => {
  let result = events.value;

  if (typeFilter.value.length > 0) {
    result = result.filter(e => typeFilter.value.includes(e.type));
  }
  if (searchQuery.value.trim()) {
    const q = searchQuery.value.toLowerCase();
    result = result.filter(e => JSON.stringify(e).toLowerCase().includes(q));
  }
  return result;
});

// ===== 展开/收起 =====
function toggleExpand(id: string) {
  const next = new Set(expanded.value);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  expanded.value = next;
}

function isExpandable(_event: DebugSessionEvent): boolean {
  return true;
}

// ===== 格式化 =====
function formatMs(ts: number): string {
  const d = new Date(ts * 1000);
  return (
    d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) +
    `.${String(d.getMilliseconds()).padStart(3, '0')}`
  );
}

function formatJson(event: DebugSessionEvent): string {
  const obj: Record<string, unknown> = {};
  if (event.message) obj.message = event.message;
  if (event.intent) obj.intent = event.intent;
  if (event.deciderName) obj.deciderName = event.deciderName;
  const str = JSON.stringify(obj, null, 2);
  const highlighted = hljs.highlight(str, { language: 'json' }).value;
  return DOMPurify.sanitize(highlighted);
}

function formatDecisionLatency(event: DebugSessionEvent): string {
  if (!event.intent?.metadata?.decision_time_ms || !event.timestamp) return '-';
  const wsTsMs = event.timestamp * 1000;
  const decisionMs = event.intent.metadata.decision_time_ms;
  const diff = Math.abs(wsTsMs - decisionMs);
  return diff < 1000 ? `${diff}ms` : `${(diff / 1000).toFixed(1)}s`;
}

// ===== 样式辅助 =====
function eventTypeToClass(type: string): string {
  if (type === 'message.received') return 'message';
  if (type === 'decision.intent') return 'intent';
  return 'output';
}

function eventTypeLabel(type: string): string {
  if (type === 'message.received') return '弹幕消息';
  if (type === 'decision.intent') return '决策意图';
  return '渲染输出';
}

function dataTypeTagType(dt: string): 'info' | 'warning' | 'success' | 'danger' | '' {
  if (dt === 'text') return 'info';
  if (dt === 'gift' || dt === 'super_chat') return 'warning';
  if (dt === 'guard') return 'danger';
  if (dt === 'enter') return 'success';
  return '';
}

function emotionTagType(name: string): 'success' | 'warning' | 'danger' | 'info' | '' {
  const positive = ['happy', 'excited', 'surprised', 'grateful', 'relaxed'];
  const negative = ['sad', 'angry', 'fearful', 'disgusted'];
  if (positive.includes(name)) return 'success';
  if (negative.includes(name)) return 'danger';
  if (name === 'neutral') return 'info';
  return 'warning';
}

// ===== 操作 =====
async function sendDanmaku() {
  const text = danmakuInput.value.trim();
  if (!text) return;
  await sessionStore.sendNormalizedMessage(text, 'dashboard');
  danmakuInput.value = '';
}

async function sendMockIntent() {
  const text = intentInput.value.trim();
  if (!text) return;
  await sessionStore.sendIntent(text);
  intentInput.value = '';
}

function clearEvents() {
  sessionStore.clearEvents();
  expanded.value.clear();
}

// ===== 生命周期 =====
let scrollObserver: MutationObserver | null = null;

onMounted(() => {
  sessionStore.connect();

  if (autoScroll.value && timelineRef.value) {
    scrollObserver = new MutationObserver(() => {
      nextTick(() => {
        if (timelineRef.value) {
          timelineRef.value.scrollTop = timelineRef.value.scrollHeight;
        }
      });
    });
    scrollObserver.observe(timelineRef.value, { childList: true, subtree: false });
  }
});

onUnmounted(() => {
  sessionStore.disconnect();
  scrollObserver?.disconnect();
});
</script>

<style scoped>
.debug-session {
  display: flex;
  flex-direction: column;
  height: calc(100vh - var(--header-height, 64px) - var(--spacing-lg, 24px) * 2);
  padding: var(--spacing-lg);
  gap: var(--spacing-md);
  overflow: hidden;
}

/* ===== 头部 ===== */
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  flex-shrink: 0;
}
.page-title {
  font-size: 24px;
  font-weight: 600;
  margin: 0;
  color: var(--text-primary);
}
.page-subtitle {
  font-size: 13px;
  color: var(--text-secondary);
  margin: 2px 0 0 0;
}
.header-actions {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
}
.event-count {
  font-size: 12px;
  color: var(--text-placeholder);
}

/* ===== 筛选栏 ===== */
.filter-bar {
  display: flex;
  align-items: center;
  gap: var(--spacing-md);
  padding: var(--spacing-sm) var(--spacing-md);
  background: var(--bg-card);
  border-radius: var(--radius-md);
  border: 1px solid var(--border-color-light);
  flex-shrink: 0;
}
.filter-left {
  display: flex;
  align-items: center;
  gap: var(--spacing-md);
  flex-wrap: wrap;
}

/* ===== 时间线 ===== */
.timeline-container {
  flex: 1;
  overflow-y: auto;
  background: var(--bg-card);
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-color-light);
  min-height: 200px;
}
.timeline {
  padding: var(--spacing-sm);
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.empty-state {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--spacing-sm);
  color: var(--text-placeholder);
  font-size: 14px;
}
.empty-icon {
  font-size: 48px;
}
.empty-hint {
  font-size: 12px;
  color: var(--text-placeholder);
}

/* ===== 卡片 ===== */
.event-card {
  background: var(--bg-elevated);
  border-radius: var(--radius-md);
  border-left: 4px solid var(--border-color-light);
  cursor: pointer;
  transition: background var(--transition-fast);
  overflow: hidden;
}
.event-card:hover {
  background: var(--bg-hover);
}
.event-card--message {
  border-left-color: #409eff;
}
.event-card--intent {
  border-left-color: #67c23a;
}
.event-card--output {
  border-left-color: #a855f7;
}

.card-header {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  padding: 6px var(--spacing-md);
  border-bottom: 1px solid var(--border-color-light);
  font-size: 12px;
}
.event-badge {
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.3px;
  color: #fff;
}
.badge--message {
  background: #409eff;
}
.badge--intent {
  background: #67c23a;
}
.badge--output {
  background: #a855f7;
}

.event-time {
  color: var(--text-secondary);
  font-family: var(--font-mono, 'Cascadia Code', monospace);
  font-size: 11px;
}
.header-spacer {
  flex: 1;
}
.expand-toggle {
  color: var(--color-primary);
  font-size: 11px;
  white-space: nowrap;
}

/* ===== 卡片内容 ===== */
.card-body {
  padding: var(--spacing-sm) var(--spacing-md);
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.field-row {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  flex-wrap: wrap;
}
.field-row--meta {
  font-size: 11px;
  color: var(--text-placeholder);
}
.field-label {
  font-size: 11px;
  color: var(--text-placeholder);
  font-weight: 500;
  margin-right: 2px;
}
.meta-sep {
  color: var(--border-color-dark);
}

.message-text {
  font-size: 14px;
  line-height: 1.6;
  color: var(--text-primary);
  padding: var(--spacing-sm);
  background: var(--bg-page);
  border-radius: var(--radius-sm);
  white-space: pre-wrap;
  word-break: break-word;
}
.message-text--speech {
  border-left: 3px solid var(--color-success);
}

/* 优先级条 */
.importance-bar {
  display: inline-flex;
  align-items: center;
  width: 80px;
  height: 14px;
  background: var(--bg-page);
  border-radius: 7px;
  overflow: hidden;
  position: relative;
}
.importance-fill {
  height: 100%;
  background: linear-gradient(90deg, #f56c6c, #e6a23c, #67c23a);
  border-radius: 7px;
  transition: width var(--transition-normal);
}
.importance-text {
  position: absolute;
  width: 100%;
  text-align: center;
  font-size: 10px;
  color: var(--text-primary);
  mix-blend-mode: difference;
}

/* 情绪强度条 */
.intensity-bar {
  display: inline-block;
  width: 50px;
  height: 6px;
  background: var(--bg-page);
  border-radius: 3px;
  overflow: hidden;
  vertical-align: middle;
}
.intensity-fill {
  display: block;
  height: 100%;
  background: #e6a23c;
  border-radius: 3px;
  transition: width var(--transition-normal);
}
.intensity-num {
  font-size: 11px;
  color: var(--text-placeholder);
}

/* 动作 */
.action-name {
  font-size: 12px;
  padding: 1px 6px;
  background: var(--bg-page);
  border-radius: 3px;
  color: var(--color-primary);
}
.action-params {
  font-size: 11px;
  color: var(--text-placeholder);
  max-width: 300px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ===== JSON 详情 ===== */
.card-detail {
  border-top: 1px solid var(--border-color-light);
  background: #1e1e2e;
  overflow: hidden;
}
.json-view {
  margin: 0;
  padding: var(--spacing-sm) var(--spacing-md);
  font-family: var(--font-mono, 'Cascadia Code', monospace);
  font-size: 11px;
  line-height: 1.5;
  max-height: 300px;
  overflow-y: auto;
  color: #cdd6f4;
}
.json-view :deep(.hljs-string) {
  color: #a6e3a1;
}
.json-view :deep(.hljs-number) {
  color: #fab387;
}
.json-view :deep(.hljs-literal) {
  color: #cba6f7;
}
.json-view :deep(.hljs-attr) {
  color: #89b4fa;
}

/* ===== 注入区 ===== */
.inject-area {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--spacing-md);
  padding: var(--spacing-md);
  background: var(--bg-card);
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-color-light);
  flex-shrink: 0;
}
.inject-group {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-xs);
}
.inject-label {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
}
.inject-group .el-button {
  align-self: flex-end;
}

/* 滚动条 */
.timeline-container::-webkit-scrollbar {
  width: 6px;
}
.timeline-container::-webkit-scrollbar-thumb {
  background: var(--border-color-dark);
  border-radius: 3px;
}
</style>
