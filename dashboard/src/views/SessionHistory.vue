<template>
  <div class="debug-session">
    <header class="page-header">
      <div class="header-left">
        <h1 class="page-title">会话调试</h1>
        <p class="page-subtitle">Agent 全链路事件流：用户消息 → Agent 决策 → 工具结果</p>
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
          <el-option label="房间消息 (room.message.*)" value="room.message" />
          <el-option label="Planner 决策 (planner.*)" value="planner" />
          <el-option label="Agenda 节目单 (agenda.*)" value="agenda" />
          <el-option label="工具结果 (tool.result.*)" value="tool.result" />
          <el-option label="游戏事件 (game.*)" value="game" />
          <el-option label="核心事件 (core.*)" value="core" />
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

    <!-- 聊天事件流 -->
    <div ref="timelineRef" class="timeline-container">
      <div v-if="filteredEvents.length === 0" class="empty-state">
        <el-icon class="empty-icon"><Timer /></el-icon>
        <span>{{ events.length === 0 ? '等待事件...' : '没有匹配的事件' }}</span>
      </div>

      <div v-else class="chat-stream">
        <div
          v-for="event in filteredEvents"
          :key="event.id"
          :class="['chat-row', `chat-row--${eventTypeToClass(event.type)}`]"
        >
          <!-- 用户消息气泡（左侧 · room.message.*） -->
          <template v-if="event.type.startsWith('room.message') && event.message">
            <div
              class="chat-avatar chat-avatar--message"
              :title="event.message.user_nickname || event.message.source || '用户'"
            >
              {{
                (
                  (event.message.user_nickname && event.message.user_nickname[0]) ||
                  (event.message.source && event.message.source[0]) ||
                  '?'
                ).toUpperCase()
              }}
            </div>
            <div class="chat-bubble chat-bubble--message" @click="toggleExpand(event.id)">
              <div class="bubble-header">
                <el-tag size="small" effect="plain" type="info">
                  {{ event.message.source }}
                </el-tag>
                <el-tag
                  v-if="event.message.data_type"
                  size="small"
                  :type="dataTypeTagType(event.message.data_type)"
                >
                  {{ event.message.data_type }}
                </el-tag>
                <span class="bubble-sender">{{
                  event.message.user_nickname || event.message.source || '匿名用户'
                }}</span>
                <span class="bubble-spacer" />
                <span class="bubble-time">{{ formatMs(event.timestamp).split('.')[0] }}</span>
              </div>
              <div class="bubble-text">{{ event.message.text }}</div>
              <div class="bubble-meta">
                <span class="importance-pill">
                  <span class="importance-bar-mini">
                    <span
                      class="importance-fill"
                      :style="{
                        width: `${((event.message.importance ?? 0.5) * 100).toFixed(0)}%`,
                      }"
                    />
                  </span>
                  <span>优先级 {{ ((event.message.importance ?? 0.5) * 100).toFixed(0) }}%</span>
                </span>
                <span v-if="event.message.user_nickname" class="meta-item">
                  · {{ event.message.user_nickname }}
                </span>
                <span v-if="event.message.user_id" class="meta-item">
                  · UID {{ event.message.user_id }}
                </span>
                <span v-if="event.message.platform" class="meta-item">
                  · {{ event.message.platform }}
                </span>
                <span v-if="event.message.room_id" class="meta-item">
                  · 房间 {{ event.message.room_id }}
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

          <!-- Agent 行为气泡（中心 · planner.checkpoint / agenda.*） -->
          <template
            v-if="
              (event.type.startsWith('planner') || event.type.startsWith('agenda')) && event.intent
            "
          >
            <div class="chat-bubble chat-bubble--agent" @click="toggleExpand(event.id)">
              <div class="bubble-header">
                <el-tag size="small" effect="plain" :type="agentTagType(event.type)">
                  {{ agentBadgeLabel(event.type) }}
                </el-tag>
                <span class="bubble-sender">{{ event.deciderName || 'Agent' }}</span>
                <span class="bubble-spacer" />
                <span class="bubble-time">{{ formatMs(event.timestamp).split('.')[0] }}</span>
              </div>
              <div v-if="event.intent.speech" class="bubble-text bubble-text--speech">
                <span class="speech-marker">💬</span>{{ event.intent.speech }}
              </div>
              <div v-if="event.intent.emotion || event.intent.action" class="bubble-meta">
                <el-tag
                  v-if="event.intent.emotion"
                  size="small"
                  effect="plain"
                  :type="emotionTagType(event.intent.emotion.name)"
                >
                  {{ event.intent.emotion.name }}
                  <template v-if="event.intent.emotion.intensity != null">
                    {{ (event.intent.emotion.intensity * 100).toFixed(0) }}%
                  </template>
                </el-tag>
                <span class="meta-item">· {{ formatDecisionLatency(event) }}</span>
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

          <!-- 工具结果卡片（右侧 · tool.result.*，含 action 时渲染） -->
          <template
            v-if="event.type.startsWith('tool.result') && event.intent && event.intent.action"
          >
            <div class="chat-bubble chat-bubble--tool" @click="toggleExpand(event.id)">
              <div class="bubble-header">
                <el-tag size="small" effect="plain" type="success">工具</el-tag>
                <span class="bubble-sender">{{ event.deciderName || 'Tool' }}</span>
                <span class="bubble-spacer" />
                <span class="bubble-time">{{ formatMs(event.timestamp).split('.')[0] }}</span>
              </div>
              <div class="bubble-text bubble-text--speech">
                <span class="speech-marker">⚙</span>
                <span>执行动作 </span>
                <code class="action-chip action-chip--mini">{{ event.intent.action.name }}</code>
              </div>
              <div
                v-if="
                  event.intent.action.parameters &&
                  Object.keys(event.intent.action.parameters).length > 0
                "
                class="bubble-meta"
              >
                <span class="meta-item">
                  {{ JSON.stringify(event.intent.action.parameters) }}
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
                <el-tag size="small" type="info">注入消息 (NormalizedMessage)</el-tag>
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
import type { DebugSessionEvent } from '@/types';
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

// ===== 筛选 =====
//
// typeFilter 元素是事件名前缀（如 `room.message`），用于匹配完整事件名
// （如 `room.message.danmaku`）。旧后端事件名（如 `message.received`）按全名匹配。
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
  if (type.startsWith('room.message')) return 'message';
  if (type.startsWith('planner') || type.startsWith('agenda')) return 'agent';
  if (type.startsWith('tool.result')) return 'tool';
  return 'agent';
}

function agentBadgeLabel(type: string): string {
  if (type.startsWith('planner')) return '规划';
  if (type.startsWith('agenda.update')) return 'Agenda';
  if (type.startsWith('agenda.speech')) return '发言';
  return 'Agent';
}

function agentTagType(type: string): 'success' | 'warning' | 'info' {
  if (type.startsWith('planner')) return 'warning';
  if (type.startsWith('agenda.update')) return 'info';
  if (type.startsWith('agenda.speech')) return 'success';
  return 'info';
}

const advancedOpen = ref<string[]>([]);

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

/* ===== 滚动容器（保留原状以驱动 autoScroll 行为） ===== */
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

/* ===== 聊天事件流 ===== */
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
.chat-row--agent {
  justify-content: center;
}
.chat-row--tool {
  justify-content: flex-end;
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
.chat-avatar--agent {
  background: linear-gradient(135deg, #a78bfa 0%, #6d28d9 100%);
}
.chat-avatar--tool {
  background: linear-gradient(135deg, #34d399 0%, #059669 100%);
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
.chat-bubble--agent {
  background: var(--bg-elevated);
  color: var(--text-primary);
  border-radius: 12px;
  border: 1px solid var(--border-color-light);
  border-left: 3px solid var(--color-agent);
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
  max-width: min(80%, 640px);
}
.chat-bubble--agent:hover {
  box-shadow: 0 4px 14px rgba(139, 92, 246, 0.18);
  border-color: rgba(139, 92, 246, 0.4);
}
.chat-bubble--tool {
  background: var(--bg-elevated);
  color: var(--text-primary);
  border-radius: 16px 16px 4px 16px;
  border: 1px solid var(--border-color-light);
  border-right: 3px solid var(--color-tool);
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
}
.chat-bubble--tool:hover {
  box-shadow: 0 4px 14px rgba(16, 185, 129, 0.18);
  border-color: rgba(16, 185, 129, 0.4);
}

/* 气泡头部 */
.bubble-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
  font-size: 12px;
}
.chat-bubble--message .bubble-header {
  color: var(--text-secondary);
}
.chat-bubble--agent .bubble-header {
  color: var(--text-secondary);
}
.chat-bubble--tool .bubble-header {
  color: var(--text-secondary);
}
.bubble-sender {
  font-weight: 600;
  font-size: 13px;
}
.chat-bubble--message .bubble-sender {
  color: var(--text-primary);
}
.chat-bubble--agent .bubble-sender {
  color: var(--text-primary);
}
.chat-bubble--tool .bubble-sender {
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
.speech-marker {
  margin-right: 4px;
  display: inline-block;
}

/* 元信息行 */
.bubble-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 4px;
  font-size: 11px;
  flex-wrap: wrap;
}
.chat-bubble--message .bubble-meta {
  color: var(--text-placeholder);
}
.chat-bubble--agent .bubble-meta {
  color: var(--text-placeholder);
}
.chat-bubble--tool .bubble-meta {
  color: var(--text-placeholder);
}
.meta-item {
  opacity: 0.85;
}
.expand-hint {
  margin-left: auto;
  font-size: 11px;
  font-weight: 500;
  user-select: none;
}
.chat-bubble--message .expand-hint {
  color: var(--color-primary);
}
.chat-bubble--agent .expand-hint {
  color: var(--color-primary);
}
.chat-bubble--tool .expand-hint {
  color: var(--color-primary);
}

/* 优先级 pill */
.importance-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 1px 8px;
  border-radius: 10px;
  background: var(--bg-page);
  font-weight: 500;
  font-size: 11px;
  color: var(--text-secondary);
}
.importance-bar-mini {
  width: 32px;
  height: 4px;
  background: rgba(0, 0, 0, 0.08);
  border-radius: 2px;
  overflow: hidden;
  display: inline-block;
}
.importance-fill {
  display: block;
  height: 100%;
  background: linear-gradient(90deg, #f56c6c, #e6a23c, #67c23a);
  border-radius: 2px;
  transition: width var(--transition-normal);
}

/* 动作芯片 */
.action-chip {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 4px;
  background: rgba(255, 255, 255, 0.2);
  font-family: var(--font-mono, 'Cascadia Code', monospace);
  font-size: 11px;
  font-weight: 500;
  color: #fff;
}
.action-chip--mini {
  font-size: 10px;
  padding: 0 6px;
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
.inject-fields--two {
  grid-template-columns: 1fr 1fr;
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
