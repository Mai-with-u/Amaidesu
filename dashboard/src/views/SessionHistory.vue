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

    <!-- 聊天事件流 -->
    <div ref="timelineRef" class="timeline-container">
      <div v-if="filteredEvents.length === 0" class="empty-state">
        <el-icon class="empty-icon"><Timer /></el-icon>
        <span>{{ events.length === 0 ? '等待事件...' : '没有匹配的事件' }}</span>
        <span class="empty-hint">WebSocket 已连接后将自动接收事件</span>
      </div>

      <div v-else class="chat-stream">
        <div
          v-for="event in filteredEvents"
          :key="event.id"
          :class="['chat-row', `chat-row--${eventTypeToClass(event.type)}`]"
        >
          <!-- message.received: 左侧消息气泡（带头像） -->
          <template v-if="event.type === 'message.received' && event.message">
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
            <div
              class="chat-bubble chat-bubble--message"
              @click="toggleExpand(event.id)"
            >
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
                <span class="bubble-time">{{
                  formatMs(event.timestamp).split('.')[0]
                }}</span>
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

          <!-- decision.intent: 右侧 AI 意图气泡（带头像） -->
          <template v-if="event.type === 'decision.intent' && event.intent">
            <div
              class="chat-bubble chat-bubble--intent"
              @click="toggleExpand(event.id)"
            >
              <div class="bubble-header">
                <el-tag size="small" effect="plain" type="success">AI</el-tag>
                <span class="bubble-sender">{{
                  event.deciderName || 'Decider'
                }}</span>
                <span class="bubble-spacer" />
                <span class="bubble-time">{{
                  formatMs(event.timestamp).split('.')[0]
                }}</span>
              </div>
              <div v-if="event.intent.speech" class="bubble-text bubble-text--speech">
                <span class="speech-marker">💬</span>{{ event.intent.speech }}
              </div>
              <div
                v-if="event.intent.emotion || event.intent.action"
                class="bubble-meta"
              >
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
                <code v-if="event.intent.action" class="action-chip">
                  {{ event.intent.action.name }}
                </code>
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
            <div class="chat-avatar chat-avatar--intent" title="AI · Bot">M</div>
          </template>

          <!-- output.render: 居中系统消息 -->
          <template v-if="event.type === 'output.render' && event.intent">
            <div class="chat-system" @click="toggleExpand(event.id)">
              <span class="system-icon">⚙</span>
              <span class="system-text">
                已派发到 <strong>{{ event.deciderName || 'Output' }}</strong>
              </span>
              <code
                v-if="event.intent.action"
                class="action-chip action-chip--mini action-chip--system"
              >
                {{ event.intent.action.name }}
              </code>
              <span
                v-if="event.intent.speech"
                class="system-speech"
                :title="event.intent.speech"
              >
                ·
                {{
                  event.intent.speech.length > 40
                    ? event.intent.speech.slice(0, 40) + '…'
                    : event.intent.speech
                }}
              </span>
              <span class="bubble-spacer" />
              <span class="system-time">{{
                formatMs(event.timestamp).split('.')[0]
              }}</span>
              <span class="expand-hint expand-hint--system">
                {{ expanded.has(event.id) ? '▲' : '▾' }}
              </span>
            </div>
            <div v-if="expanded.has(event.id)" class="chat-system-detail">
              <pre class="json-view" v-html="formatJson(event)" />
            </div>
          </template>
        </div>
      </div>
    </div>

    <!-- 底部输入区：手动注入消息用于测试 -->
    <div class="inject-area">
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
      <div class="inject-group">
        <div class="inject-label">
          <el-tag size="small" type="success">注入意图 (Intent)</el-tag>
        </div>
        <div class="inject-fields inject-fields--two">
          <div class="inject-field">
            <label class="inject-field-label">emotion</label>
            <el-select
              v-model="intentEmotion"
              size="small"
              :disabled="sending"
              style="width: 100%"
            >
              <el-option label="happy" value="happy" />
              <el-option label="sad" value="sad" />
              <el-option label="angry" value="angry" />
              <el-option label="excited" value="excited" />
              <el-option label="neutral" value="neutral" />
              <el-option label="surprised" value="surprised" />
              <el-option label="fearful" value="fearful" />
              <el-option label="disgusted" value="disgusted" />
              <el-option label="grateful" value="grateful" />
              <el-option label="relaxed" value="relaxed" />
              <el-option label="puzzled" value="puzzled" />
              <el-option label="bored" value="bored" />
            </el-select>
          </div>
          <div class="inject-field">
            <label class="inject-field-label">source</label>
            <el-input
              v-model="intentSource"
              size="small"
              placeholder="dashboard"
              :disabled="sending"
            />
          </div>
        </div>
        <div class="inject-actions">
          <div class="inject-actions-header">
            <span class="inject-actions-title">动作 (IntentAction[])</span>
            <el-button
              size="small"
              text
              type="primary"
              :disabled="sending"
              @click="addAction"
            >
              <el-icon><Plus /></el-icon>
              <span>添加动作</span>
            </el-button>
          </div>
          <div v-if="intentActions.length === 0" class="inject-actions-empty">
            暂无动作
          </div>
          <div v-else class="inject-actions-list">
            <div
              v-for="(action, idx) in intentActions"
              :key="idx"
              class="inject-action-row"
            >
              <div class="inject-action-header">
                <el-select
                  v-model="action.actionName"
                  filterable
                  :loading="loadingCapabilities"
                  :disabled="sending || loadingCapabilities"
                  placeholder="选择动作..."
                  no-data-text="暂无可用动作"
                  size="small"
                  class="inject-action-select"
                  @change="onActionSelect(idx)"
                >
                  <el-option
                    v-if="capabilitiesError"
                    :label="`加载失败: ${capabilitiesError}`"
                    value="__error__"
                    disabled
                  />
                  <el-option-group
                    v-for="group in groupedAvailableActions"
                    :key="group.handlerPrefix"
                    :label="group.handlerPrefix"
                  >
                    <el-option
                      v-for="entry in group.actions"
                      :key="entry.name"
                      :label="getLocalName(entry.name)"
                      :value="entry.name"
                    >
                      <div class="action-opt-row">
                        <span class="action-opt-label">{{ getLocalName(entry.name) }}</span>
                        <span v-if="entry.description" class="action-opt-desc">
                          {{ entry.description }}
                        </span>
                      </div>
                    </el-option>
                  </el-option-group>
                </el-select>
                <el-input-number
                  v-model="action.priority"
                  size="small"
                  :min="0"
                  :max="10"
                  :step="1"
                  :disabled="sending"
                  controls-position="right"
                  class="inject-action-priority"
                />
                <el-button
                  size="small"
                  type="danger"
                  text
                  :disabled="sending"
                  class="inject-action-remove"
                  title="移除动作"
                  @click="removeAction(idx)"
                >
                  ✕
                </el-button>
              </div>
              <div
                v-if="
                  action.actionName &&
                  getActionSpec(action.actionName) &&
                  Object.keys(getActionSpec(action.actionName)!.parameters).length > 0
                "
                class="inject-action-params"
              >
                <div
                  v-for="(spec, key) in getActionSpec(action.actionName)!.parameters"
                  :key="key"
                  class="inject-param-row"
                >
                  <label class="inject-param-label">
                    <span class="inject-param-key">{{ key }}</span>
                    <span
                      v-if="spec.required"
                      class="inject-param-required"
                      title="必填"
                    >*</span>
                    <el-tooltip
                      v-if="spec.description"
                      :content="spec.description"
                      placement="top"
                    >
                      <span class="inject-param-help">?</span>
                    </el-tooltip>
                  </label>
                  <div class="inject-param-control">
                    <el-input
                      v-if="spec.type === 'string'"
                      v-model="action.params[key]"
                      size="small"
                      :disabled="sending"
                      placeholder="string"
                    />
                    <el-input-number
                      v-else-if="spec.type === 'integer'"
                      v-model="action.params[key]"
                      :step="1"
                      :precision="0"
                      :min="spec.minimum"
                      :max="spec.maximum"
                      size="small"
                      controls-position="right"
                      class="inject-param-number"
                    />
                    <el-input-number
                      v-else-if="spec.type === 'number'"
                      v-model="action.params[key]"
                      :step="1"
                      :min="spec.minimum"
                      :max="spec.maximum"
                      size="small"
                      controls-position="right"
                      class="inject-param-number"
                    />
                    <el-switch
                      v-else-if="spec.type === 'boolean'"
                      v-model="action.params[key]"
                      size="small"
                      :disabled="sending"
                    />
                  </div>
                </div>
              </div>
            </div>
          </div>
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
import { Delete, Timer, Search, Promotion, Plus } from '@element-plus/icons-vue';
import { useSessionStore } from '@/stores/session';
import { useWebSocketStore } from '@/stores';
import { capabilitiesApi } from '@/api';
import type { DebugSessionEvent, UnifiedActionEntry, ParameterType } from '@/types';
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
const danmakuSource = ref('dashboard');
const danmakuDataType = ref('text');
const danmakuImportance = ref(1);
const intentInput = ref('');
const intentEmotion = ref('neutral');
const intentSource = ref('dashboard');

// 意图附带的 Action 草稿（actionName / params / priority）
interface IntentActionDraft {
  actionName: string;
  priority: number;
  params: Record<string, unknown>;
}
const intentActions = ref<IntentActionDraft[]>([]);

// ===== 可用 Action（来自 /api/v1/capabilities）=====
const availableActions = ref<UnifiedActionEntry[]>([]);
const loadingCapabilities = ref(false);
const capabilitiesError = ref<string | null>(null);

interface ActionGroup {
  handlerPrefix: string;
  actions: UnifiedActionEntry[];
}

async function fetchCapabilities(): Promise<void> {
  loadingCapabilities.value = true;
  capabilitiesError.value = null;
  try {
    const response = await capabilitiesApi.list();
    availableActions.value = response.data.actions ?? [];
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : '获取 capabilities 失败';
    capabilitiesError.value = msg;
    availableActions.value = [];
    console.warn('[inject-intent] 获取 capabilities 失败', err);
  } finally {
    loadingCapabilities.value = false;
  }
}

const groupedAvailableActions = computed<ActionGroup[]>(() => {
  const groups = new Map<string, UnifiedActionEntry[]>();
  for (const action of availableActions.value) {
    const prefix = action.name.split('.')[0] ?? '';
    const list = groups.get(prefix);
    if (list) {
      list.push(action);
    } else {
      groups.set(prefix, [action]);
    }
  }
  const result: ActionGroup[] = [];
  for (const [handlerPrefix, actions] of groups) {
    result.push({ handlerPrefix, actions });
  }
  return result;
});

function getActionSpec(name: string): UnifiedActionEntry | undefined {
  if (!name) return undefined;
  return availableActions.value.find(a => a.name === name);
}

function getLocalName(fullName: string): string {
  const dot = fullName.indexOf('.');
  return dot >= 0 ? fullName.substring(dot + 1) : fullName;
}

function defaultValueForType(type: ParameterType): unknown {
  switch (type) {
    case 'string':
      return '';
    case 'integer':
    case 'number':
      return null;
    case 'boolean':
      return false;
  }
}

function initDraftParams(spec: UnifiedActionEntry): Record<string, unknown> {
  const params: Record<string, unknown> = {};
  for (const [key, paramSpec] of Object.entries(spec.parameters)) {
    if ('default' in paramSpec && paramSpec.default !== undefined) {
      params[key] = paramSpec.default;
    } else {
      params[key] = defaultValueForType(paramSpec.type);
    }
  }
  return params;
}

function addAction() {
  intentActions.value.push({ actionName: '', priority: 5, params: {} });
}

function onActionSelect(idx: number): void {
  const draft = intentActions.value[idx];
  if (!draft) return;
  const spec = getActionSpec(draft.actionName);
  if (!spec) {
    draft.params = {};
    return;
  }
  draft.params = initDraftParams(spec);
}

function removeAction(index: number) {
  intentActions.value.splice(index, 1);
}

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

async function sendMockIntent() {
  const text = intentInput.value.trim();
  const hasActions = intentActions.value.some(a => a.actionName.trim());

  // 既没有文本也没有动作时阻止发送
  if (!text && !hasActions) return;

  // 将草稿 actions 序列化为后端要求的结构（过滤未选择 action 的项）
  const builtActions: Record<string, any>[] = intentActions.value
    .map((a): Record<string, any> | null => {
      const type = a.actionName.trim();
      if (!type) return null;

      const priorityNum = Number(a.priority);
      return {
        type,
        params: { ...a.params },
        priority: Number.isFinite(priorityNum) ? priorityNum : 5,
      };
    })
    .filter((a): a is Record<string, any> => a !== null);

  await sessionStore.sendIntent(
    text || undefined,
    intentEmotion.value,
    intentSource.value.trim() || 'dashboard',
    undefined,
    builtActions,
  );
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

  // 异步加载可用 capabilities（用于 action 选择器）
  void fetchCapabilities();
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
.chat-row--intent {
  justify-content: flex-end;
}
.chat-row--output {
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
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
.chat-avatar--intent {
  background: linear-gradient(135deg, #67c23a 0%, #4a9b2a 100%);
}

/* ===== 气泡 ===== */
.chat-bubble {
  max-width: min(72%, 540px);
  padding: 10px 14px 8px;
  cursor: pointer;
  transition: transform var(--transition-fast), box-shadow var(--transition-fast),
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
.chat-bubble--intent {
  background: var(--bg-elevated);
  color: var(--text-primary);
  border-radius: 16px 16px 4px 16px;
  border: 1px solid var(--border-color-light);
  border-left: 3px solid #67c23a;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
}
.chat-bubble--intent:hover {
  box-shadow: 0 4px 14px rgba(64, 158, 255, 0.18);
  border-color: rgba(64, 158, 255, 0.4);
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
.chat-bubble--intent .bubble-header {
  color: var(--text-secondary);
}
.bubble-sender {
  font-weight: 600;
  font-size: 13px;
}
.chat-bubble--message .bubble-sender {
  color: var(--text-primary);
}
.chat-bubble--intent .bubble-sender {
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
.chat-bubble--intent .bubble-meta {
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
.chat-bubble--intent .expand-hint {
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
.chat-bubble--message .action-chip {
  background: rgba(64, 158, 255, 0.12);
  color: var(--color-primary);
}
.chat-bubble--intent .action-chip {
  background: rgba(103, 194, 58, 0.12);
  color: #4a9b2a;
}
.action-chip--mini {
  font-size: 10px;
  padding: 0 6px;
}

/* ===== 系统消息（居中、紧凑） ===== */
.chat-system {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 12px;
  border-radius: 999px;
  background: var(--bg-page);
  color: var(--text-primary);
  font-size: 12px;
  cursor: pointer;
  transition: background var(--transition-fast);
  border: 1px solid var(--border-color-light);
  max-width: min(80%, 600px);
}
.chat-system:hover {
  background: var(--bg-hover);
}
.system-icon {
  font-size: 11px;
  opacity: 0.65;
}
.system-text {
  font-weight: 500;
  color: var(--text-primary);
}
.system-text strong {
  color: var(--text-primary);
  font-weight: 600;
}
.system-speech {
  font-style: italic;
  color: var(--text-secondary);
  font-size: 12px;
  max-width: 360px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.system-time {
  font-family: var(--font-mono, 'Cascadia Code', monospace);
  font-size: 11px;
  opacity: 0.65;
  color: var(--text-secondary);
}
.action-chip--system {
  background: rgba(64, 158, 255, 0.12);
  color: var(--color-primary);
}
.expand-hint--system {
  font-size: 10px;
  color: var(--text-placeholder);
  margin-left: 2px;
}

.chat-system-detail {
  width: min(80%, 600px);
  border-radius: 8px;
  overflow: hidden;
  margin-top: 4px;
  background: #1e1e2e;
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

/* ===== Action 注入（intent）===== */
.inject-actions {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 6px 8px;
  background: var(--bg-page);
  border-radius: var(--radius-sm, 6px);
  border: 1px solid var(--border-color-light);
}
.inject-actions-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--spacing-sm);
}
.inject-actions-title {
  font-size: 11px;
  font-weight: 500;
  color: var(--text-secondary);
}
.inject-actions-empty {
  font-size: 11px;
  color: var(--text-placeholder);
  font-style: italic;
  text-align: center;
  padding: 2px 0;
}
.inject-actions-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.inject-action-row {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 6px 0;
  border-bottom: 1px dashed var(--border-color-light);
}
.inject-action-row:last-child {
  border-bottom: none;
}
.inject-action-header {
  display: flex;
  align-items: center;
  gap: 4px;
}
.inject-action-select {
  flex: 1;
  min-width: 0;
}
.inject-action-priority {
  flex: 0 0 92px;
}
.inject-action-priority :deep(.el-input-number) {
  width: 100%;
}
.inject-action-remove {
  flex: 0 0 auto;
  padding: 0 8px;
  min-height: 24px;
  font-size: 12px;
  line-height: 1;
  font-weight: 700;
}

/* 下拉项：本地名 + 描述 */
.action-opt-row {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.action-opt-label {
  font-family: var(--font-mono, 'Cascadia Code', monospace);
  font-size: 12px;
  font-weight: 600;
  color: var(--color-primary);
  flex-shrink: 0;
}
.action-opt-desc {
  font-size: 11px;
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

/* 参数区 */
.inject-action-params {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 6px 8px;
  margin-left: 2px;
  background: var(--bg-card);
  border-radius: var(--radius-sm, 4px);
  border: 1px solid var(--border-color-light);
}
.inject-param-row {
  display: grid;
  grid-template-columns: 110px 1fr;
  align-items: center;
  gap: var(--spacing-sm);
  min-width: 0;
}
.inject-param-label {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  min-width: 0;
}
.inject-param-key {
  font-family: var(--font-mono, 'Cascadia Code', monospace);
  color: var(--text-primary);
  font-size: 11px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.inject-param-required {
  color: var(--color-danger);
  font-weight: 700;
  font-size: 12px;
  flex-shrink: 0;
}
.inject-param-help {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  font-size: 10px;
  font-weight: 600;
  color: var(--text-secondary);
  background: var(--bg-hover);
  border-radius: 50%;
  cursor: help;
  flex-shrink: 0;
}
.inject-param-control {
  min-width: 0;
}
.inject-param-number {
  width: 100%;
}
.inject-param-number :deep(.el-input-number) {
  width: 100%;
}
</style>
