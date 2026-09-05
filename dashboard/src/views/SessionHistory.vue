<template>
  <div class="debug-session">
    <header class="page-header">
      <div class="header-left">
        <h1 class="page-title">会话调试</h1>
        <p class="page-subtitle">v2 对话闭环：观众消息 → 主播发言（Planner / Agenda / 工具作为过程行）</p>
      </div>
      <div class="header-actions">
        <span class="event-count">{{ filteredEvents.length }} / {{ events.length }} 条</span>
        <el-button :disabled="events.length === 0" size="small" @click="clearEvents">
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
          placeholder="事件域"
          clearable
          style="width: 240px"
        >
          <el-option label="观众消息 (room.message)" value="room.message" />
          <el-option label="主播发言 (streamer.speech)" value="streamer.speech" />
          <el-option label="Planner 决策 (planner.*)" value="planner" />
          <el-option label="Agenda 节目单 (agenda.*)" value="agenda" />
          <el-option label="工具结果 (tool.result.*)" value="tool.result" />
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

    <!-- 会话流 -->
    <div ref="timelineRef" class="timeline-container">
      <div v-if="filteredEvents.length === 0" class="empty-state">
        <el-icon class="empty-icon"><Timer /></el-icon>
        <span>{{ events.length === 0 ? '等待事件...' : '没有匹配的事件' }}</span>
        <span class="empty-hint">注入一条弹幕，或在开发者工具里驱动一次主播决策</span>
      </div>

      <div v-else class="chat-stream">
        <div
          v-for="event in filteredEvents"
          :key="event.id"
          :class="['chat-row', `chat-row--${event.kind}`]"
        >
          <!-- 观众消息（左侧气泡 · room.message） -->
          <template v-if="event.kind === 'message' && event.message">
            <div class="chat-avatar chat-avatar--message" :title="userNameOf(event)">
              {{ initialOf(userNameOf(event)) }}
            </div>
            <div class="chat-bubble chat-bubble--message" @click="toggleExpand(event.id)">
              <div class="bubble-header">
                <el-tag size="small" effect="plain" :type="messageTypeTagType(event.message.message_type)">
                  {{ messageTypeLabel(event.message.message_type) }}
                </el-tag>
                <el-tag v-if="event.message.simulated" size="small" effect="plain" type="warning">
                  模拟
                </el-tag>
                <span class="bubble-sender">{{ userNameOf(event) }}</span>
                <span class="bubble-spacer" />
                <span class="bubble-time">{{ formatMs(event.timestamp).split('.')[0] }}</span>
              </div>
              <div class="bubble-text">{{ messageTextOf(event) }}</div>
              <div class="bubble-meta">
                <span v-if="event.message.user?.id" class="meta-item">UID {{ event.message.user.id }}</span>
                <span v-if="event.message.live_session_id" class="meta-item">
                    · {{ event.message.live_session_id }}
                </span>
                <span class="bubble-spacer" />
                <span class="expand-hint">
                  {{ expanded.has(event.id) ? '收起 ▲' : '查看详情 ▾' }}
                </span>
              </div>
              <div v-if="expanded.has(event.id)" class="bubble-detail">
                <pre class="json-view" v-html="formatJson(event)" />
              </div>
            </div>
          </template>

          <!-- 主播发言（右侧气泡 · streamer.speech） -->
          <template v-else-if="event.kind === 'speech' && event.speech">
            <div class="chat-bubble chat-bubble--speech" @click="toggleExpand(event.id)">
              <div class="bubble-header">
                <el-tag size="small" effect="plain" type="success">主播</el-tag>
                <span v-if="event.speech.emotion" class="emotion-chip">
                  {{ event.speech.emotion }}
                </span>
                <span class="bubble-spacer" />
                <span class="bubble-time">{{ formatMs(event.timestamp).split('.')[0] }}</span>
              </div>
              <div class="bubble-text bubble-text--speech">🎤 {{ event.speech.text }}</div>
              <div class="bubble-meta">
                <span v-if="event.speech.utterance_id" class="meta-item mono-meta">
                  {{ event.speech.utterance_id }}
                </span>
                <span class="bubble-spacer" />
                <span class="expand-hint">
                  {{ expanded.has(event.id) ? '收起 ▲' : '查看详情 ▾' }}
                </span>
              </div>
              <div v-if="expanded.has(event.id)" class="bubble-detail">
                <pre class="json-view" v-html="formatJson(event)" />
              </div>
            </div>
            <div class="chat-avatar chat-avatar--speech" title="主播">主</div>
          </template>

          <!-- 决策/编排/工具过程行（居中 · planner / agenda / tool.result） -->
          <template v-else-if="event.kind === 'system'">
            <div
              :class="['system-strip', { 'is-expanded': expanded.has(event.id) }]"
              @click="toggleExpand(event.id)"
            >
              <div class="system-strip-head">
                <el-tag size="small" effect="plain" :type="systemBadgeType(event.type)">
                  {{ systemBadgeLabel(event.type) }}
                </el-tag>
                <span class="system-summary">{{ systemSummary(event) }}</span>
                <span class="bubble-time">{{ formatMs(event.timestamp).split('.')[0] }}</span>
              </div>
              <div v-if="expanded.has(event.id)" class="bubble-detail">
                <pre class="json-view" v-html="formatJson(event)" />
              </div>
            </div>
          </template>
        </div>
      </div>
    </div>

    <!-- 底部注入区：折叠到"高级" -->
    <div class="inject-area">
      <el-collapse v-model="advancedOpen">
        <el-collapse-item name="advanced" title="高级 · 手动注入弹幕（走真实弹幕链路）">
          <div class="advanced-grid">
            <div class="inject-group">
              <div class="inject-label">
                <el-tag size="small" type="info">注入消息（→ room.message.danmaku）</el-tag>
              </div>
              <div class="inject-fields">
                <div class="inject-field">
                  <label class="inject-field-label">source</label>
                  <el-input
                    v-model="danmakuSource"
                    size="small"
                    placeholder="dashboard"
                    :disabled="sending"
                  />
                </div>
                <div class="inject-field">
                  <label class="inject-field-label">data_type</label>
                  <el-select
                    v-model="danmakuDataType"
                    size="small"
                    :disabled="sending"
                    style="width: 100%"
                  >
                    <el-option label="text" value="text" />
                    <el-option label="gift" value="gift" />
                    <el-option label="super_chat" value="super_chat" />
                    <el-option label="guard" value="guard" />
                    <el-option label="enter" value="enter" />
                  </el-select>
                </div>
                <div class="inject-field">
                  <label class="inject-field-label">
                    importance
                    <span class="inject-field-value">{{ danmakuImportance.toFixed(2) }}</span>
                  </label>
                  <el-slider
                    v-model="danmakuImportance"
                    :min="0"
                    :max="1"
                    :step="0.05"
                    :disabled="sending"
                    size="small"
                  />
                </div>
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
          </div>
        </el-collapse-item>
      </el-collapse>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue';
import { storeToRefs } from 'pinia';
import { Delete, Timer, Search, Promotion } from '@element-plus/icons-vue';
import { useSessionStore } from '@/stores/session';
import { summarizeEvent } from '@/utils/eventSummary';
import type { DebugSessionEvent, RoomMessageEventData } from '@/types';
import DOMPurify from 'dompurify';
import hljs from 'highlight.js/lib/core';
import json from 'highlight.js/lib/languages/json';
import 'highlight.js/styles/atom-one-dark.min.css';

hljs.registerLanguage('json', json);

const sessionStore = useSessionStore();
const { events, sending } = storeToRefs(sessionStore);

const timelineRef = ref<HTMLElement | null>(null);
const typeFilter = ref<string[]>([]);
const searchQuery = ref('');
const autoScroll = ref(true);
const expanded = ref<Set<string>>(new Set());
const danmakuInput = ref('');
const danmakuSource = ref('dashboard');
const danmakuDataType = ref('text');
const danmakuImportance = ref(1);
const advancedOpen = ref<string[]>([]);

// ===== 筛选 =====
//
// typeFilter 元素是事件名前缀（如 `planner`），完整事件名按「全等或带点后缀」匹配；
// 统一广播类型（room.message / streamer.speech）按全等命中。
const filteredEvents = computed(() => {
  let result = events.value;

  if (typeFilter.value.length > 0) {
    result = result.filter(e => {
      const t = e.type;
      return typeFilter.value.some(prefix => t === prefix || t.startsWith(`${prefix}.`));
    });
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

// ===== 消息气泡渲染 =====

const MESSAGE_TYPE_LABELS: Record<string, string> = {
  danmaku: '弹幕',
  gift: '礼物',
  super_chat: 'SC',
  enter: '进场',
};

function messageTypeLabel(type: string): string {
  return MESSAGE_TYPE_LABELS[type] ?? type;
}

function messageTypeTagType(type: string): 'info' | 'warning' | 'danger' | 'success' {
  if (type === 'gift') return 'warning';
  if (type === 'super_chat') return 'danger';
  return 'info';
}

function userNameOf(event: DebugSessionEvent): string {
  const user = event.message?.user;
  return user?.name || user?.id || '匿名观众';
}

function initialOf(name: string): string {
  const chars = Array.from(name.replace(/^#/, ''));
  return chars.length > 0 ? chars[0].toUpperCase() : '?';
}

/** 消息主体文案：gift/enter 无正文，按 message_type 派生（与弹幕小部件口径一致） */
function messageTextOf(event: DebugSessionEvent): string {
  const m = event.message as RoomMessageEventData | undefined;
  if (!m) return '';
  if (m.message_type === 'gift') {
    return `送出 ${m.gift?.name || '礼物'} ×${m.gift?.count ?? 1}`;
  }
  if (m.message_type === 'enter') {
    return '进入了直播间';
  }
  return m.content || '';
}

// ===== 过程行渲染 =====

function systemBadgeLabel(type: string): string {
  if (type.startsWith('planner')) return 'Planner';
  if (type.startsWith('agenda')) return 'Agenda';
  if (type.startsWith('tool.result')) return '工具';
  return '系统';
}

function systemBadgeType(type: string): 'info' | 'warning' | 'success' {
  if (type.startsWith('planner')) return 'warning';
  if (type.startsWith('tool.result')) return 'success';
  return 'info';
}

function systemSummary(event: DebugSessionEvent): string {
  return summarizeEvent(event.type, event.data);
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
  const str = JSON.stringify(event.data ?? {}, null, 2);
  const highlighted = hljs.highlight(str, { language: 'json' }).value;
  return DOMPurify.sanitize(highlighted);
}

// ===== 操作 =====
async function sendDanmaku() {
  const text = danmakuInput.value.trim();
  if (!text) return;
  await sessionStore.sendNormalizedMessage(
    text,
    danmakuSource.value.trim() || 'dashboard',
    danmakuDataType.value,
    danmakuImportance.value,
  );
  danmakuInput.value = '';
}

function clearEvents() {
  sessionStore.clearEvents();
  expanded.value.clear();
}

// ===== 生命周期 =====
let scrollObserver: MutationObserver | null = null;

onMounted(() => {
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

/* ===== 滚动容器 ===== */
.timeline-container {
  flex: 1;
  overflow-y: auto;
  background: var(--bg-card);
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-color-light);
  min-height: 200px;
}
.timeline-container::-webkit-scrollbar {
  width: 6px;
}
.timeline-container::-webkit-scrollbar-thumb {
  background: var(--border-color-dark);
  border-radius: 3px;
}

/* 空状态 */
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

/* ===== 会话流 ===== */
.chat-stream {
  padding: var(--spacing-md) var(--spacing-md) var(--spacing-lg);
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-height: 100%;
}

.chat-row {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  animation: chatBubbleIn 0.22s cubic-bezier(0.4, 0, 0.2, 1);
}
.chat-row--message {
  justify-content: flex-start;
}
.chat-row--speech {
  justify-content: flex-end;
}
.chat-row--system {
  justify-content: center;
}

@keyframes chatBubbleIn {
  from {
    opacity: 0;
    transform: translateY(6px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* ===== 头像 ===== */
.chat-avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  font-size: 14px;
  color: #fff;
  flex-shrink: 0;
  user-select: none;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
  letter-spacing: 0.5px;
}
.chat-avatar--message {
  background: linear-gradient(135deg, #5fa8ff 0%, #3a7bd5 100%);
}
.chat-avatar--speech {
  background: linear-gradient(135deg, #a78bfa 0%, #6d28d9 100%);
}

/* ===== 气泡 ===== */
.chat-bubble {
  max-width: min(72%, 540px);
  padding: 10px 14px 8px;
  cursor: pointer;
  transition:
    transform var(--transition-fast),
    box-shadow var(--transition-fast),
    border-color var(--transition-fast);
  word-break: break-word;
  position: relative;
}
.chat-bubble:hover {
  transform: translateY(-1px);
}
.chat-bubble--message {
  background: var(--bg-elevated);
  color: var(--text-primary);
  border-radius: 4px 16px 16px 16px;
  border: 1px solid var(--border-color-light);
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
}
.chat-bubble--message:hover {
  box-shadow: 0 4px 14px rgba(64, 158, 255, 0.18);
  border-color: rgba(64, 158, 255, 0.4);
}
.chat-bubble--speech {
  background: var(--bg-elevated);
  color: var(--text-primary);
  border-radius: 16px 4px 16px 16px;
  border: 1px solid var(--border-color-light);
  border-right: 3px solid var(--color-agent);
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
}
.chat-bubble--speech:hover {
  box-shadow: 0 4px 14px rgba(139, 92, 246, 0.18);
  border-color: rgba(139, 92, 246, 0.4);
}

/* 气泡头部 */
.bubble-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
  font-size: 12px;
  color: var(--text-secondary);
}
.bubble-sender {
  font-weight: 600;
  font-size: 13px;
  color: var(--text-primary);
}
.bubble-spacer {
  flex: 1;
}
.bubble-time {
  font-family: var(--font-mono, 'Cascadia Code', monospace);
  font-size: 11px;
  opacity: 0.7;
}

/* 主文本 */
.bubble-text {
  font-size: 14px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  margin: 4px 0;
  color: inherit;
}
.bubble-text--speech {
  font-size: 15px;
  font-weight: 500;
}

/* 元信息行 */
.bubble-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 4px;
  font-size: 11px;
  flex-wrap: wrap;
  color: var(--text-placeholder);
}
.meta-item {
  opacity: 0.85;
}
.mono-meta {
  font-family: var(--font-mono, 'Cascadia Code', monospace);
  font-size: 10px;
}
.expand-hint {
  margin-left: auto;
  font-size: 11px;
  font-weight: 500;
  user-select: none;
  color: var(--color-primary);
}

/* 情绪芯片（v2 情绪为纯文本标签） */
.emotion-chip {
  padding: 0 8px;
  border-radius: 10px;
  background: var(--bg-page, rgba(0, 0, 0, 0.04));
  font-size: 11px;
  color: var(--text-secondary);
}

/* ===== 过程行（planner / agenda / tool.result） ===== */
.system-strip {
  display: flex;
  flex-direction: column;
  max-width: min(80%, 640px);
  padding: 4px 12px;
  border-radius: 12px;
  background: var(--bg-page, rgba(0, 0, 0, 0.03));
  border: 1px dashed var(--border-color-light);
  cursor: pointer;
  transition: border-color var(--transition-fast);
}
.system-strip:hover {
  border-color: var(--color-primary);
}
.system-strip-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--text-secondary);
}
.system-summary {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ===== JSON 详情 ===== */
.bubble-detail {
  margin-top: 10px;
  border-radius: 8px;
  overflow: hidden;
  background: #1e1e2e;
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
  background: var(--bg-card);
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-color-light);
  flex-shrink: 0;
  padding: 0 var(--spacing-md);
}
.advanced-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--spacing-md);
  padding: var(--spacing-sm) 0 var(--spacing-md);
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
.inject-fields {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: var(--spacing-sm);
  align-items: end;
}
.inject-field {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.inject-field-label {
  font-size: 11px;
  color: var(--text-secondary);
  font-weight: 500;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
}
.inject-field-value {
  font-family: var(--font-mono, 'Cascadia Code', monospace);
  font-size: 10px;
  color: var(--text-placeholder);
}
.inject-field :deep(.el-slider) {
  margin: 4px 0 0;
}
.inject-field :deep(.el-input__wrapper),
.inject-field :deep(.el-select__wrapper) {
  padding: 1px 8px;
}
.inject-group .el-button {
  align-self: flex-end;
}
</style>
