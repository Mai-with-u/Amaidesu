<template>
  <div class="live-observer">
    <!-- ===== 顶部头部 ===== -->
    <header class="page-header">
      <div class="header-left">
        <h1 class="page-title">直播间观察</h1>
        <p class="page-subtitle">实时追踪弹幕 → 决策 → 派发 全链路</p>
      </div>
      <div class="header-actions">
        <span class="connection-pill" :class="wsConnected ? 'is-on' : 'is-off'">
          <span class="dot" />
          WS {{ wsConnected ? '已连接' : '已断开' }}
        </span>
        <span class="chain-count">{{ sortedChains.length }} / {{ MAX_CHAINS }} 条链</span>
        <el-button size="small" :disabled="sortedChains.length === 0" @click="scrollToTop">
          <el-icon><Top /></el-icon>
          回到顶部
        </el-button>
        <el-button size="small" :disabled="sortedChains.length === 0" @click="scrollToBottom">
          <el-icon><Bottom /></el-icon>
          回到底部
        </el-button>
        <el-button
          size="small"
          type="danger"
          plain
          :disabled="sortedChains.length === 0"
          @click="clearAll"
        >
          <el-icon><Delete /></el-icon>
          清空
        </el-button>
      </div>
    </header>

    <!-- ===== 直播状态栏 ===== -->
    <section class="status-bar">
      <div class="status-row">
        <div class="status-group">
          <span class="status-group-label">输入源</span>
          <div class="status-chips">
            <span v-if="collectors.size === 0" class="status-chip-empty">暂无</span>
            <span
              v-for="c in collectorList"
              :key="c.name"
              :class="['collector-chip', c.connected ? 'is-on' : 'is-off']"
              :title="c.name"
            >
              <span class="dot" />
              {{ c.name }}
            </span>
          </div>
        </div>

        <div class="status-group">
          <span class="status-group-label">决策</span>
          <div class="status-chips">
            <el-tag
              :type="status?.decision_phase?.enabled ? 'success' : 'info'"
              size="small"
              effect="plain"
            >
              {{
                status?.decision_phase?.enabled
                  ? `在线 ${status?.decision_phase?.active_components || 0}/${status?.decision_phase?.total_components || 0}`
                  : '未启用'
              }}
            </el-tag>
            <span
              v-for="d in deciderList"
              :key="d.name"
              :class="['collector-chip', d.connected ? 'is-on' : 'is-off']"
              :title="d.name"
            >
              <span class="dot" />
              {{ d.name }}
            </span>
          </div>
        </div>

        <div class="status-group">
          <span class="status-group-label">输出</span>
          <div class="status-chips">
            <el-tag
              :type="status?.output_phase?.enabled ? 'success' : 'info'"
              size="small"
              effect="plain"
            >
              {{
                status?.output_phase?.enabled
                  ? `活跃 ${status?.output_phase?.active_components || 0}/${status?.output_phase?.total_components || 0}`
                  : '未启用'
              }}
            </el-tag>
          </div>
        </div>
      </div>

      <div class="status-row">
        <div :class="['processing-bar', processingChain ? 'is-active' : 'is-idle']">
          <span class="processing-icon">⏳</span>
          <span class="processing-text">
            <template v-if="processingChain">
              正在处理：<strong>{{ processingChain.preview }}</strong>
              <span class="processing-status"> · {{ statusLabel(processingChain.status) }}</span>
            </template>
            <template v-else>暂无未完成链路</template>
          </span>
          <span v-if="processingChain" class="processing-elapsed">
            {{ formatElapsed(processingChain.elapsedSec) }}
          </span>
        </div>

        <div class="page-stats">
          <span class="stat-pill">
            <span class="stat-num">{{ stats.danmaku }}</span>
            <span class="stat-key">弹幕</span>
          </span>
          <span class="stat-pill">
            <span class="stat-num">{{ stats.reply }}</span>
            <span class="stat-key">回复</span>
          </span>
          <span class="stat-pill">
            <span class="stat-num">{{ stats.action }}</span>
            <span class="stat-key">动作</span>
          </span>
        </div>
      </div>
    </section>

    <!-- ===== 链路分组时间线 ===== -->
    <section ref="timelineRef" class="timeline">
      <div v-if="sortedChains.length === 0" class="empty-state">
        <el-icon class="empty-icon"><VideoCamera /></el-icon>
        <span>等待弹幕事件...</span>
      </div>

      <div v-else class="chain-list">
        <article
          v-for="chain in sortedChains"
          :key="chain.messageId"
          :class="['chain', `chain--${chain.status}`]"
        >
          <!-- 链路分隔条 -->
          <div class="chain-meta">
            <span class="chain-id" :title="chain.messageId">
              <span class="mono">#{{ shortId(chain.messageId) }}</span>
            </span>
            <span :class="['chain-status-tag', `tag--${chain.status}`]">
              {{ statusLabel(chain.status) }}
            </span>
            <span class="bubble-spacer" />
            <span class="chain-time mono">{{ formatTime(chain.createdAtMs) }}</span>
          </div>

          <!-- 消息卡 -->
          <div
            v-if="chain.message"
            class="card card--message"
            @click="toggleExpand(chain.messageId, 'message')"
          >
            <div class="card-head">
              <span class="card-icon">💬</span>
              <span class="card-title">消息</span>
              <el-tag v-if="chain.message.source" size="small" effect="plain" type="info">
                {{ chain.message.source }}
              </el-tag>
              <el-tag
                v-if="chain.message.data_type"
                size="small"
                :type="dataTypeTagType(chain.message.data_type)"
              >
                {{ chain.message.data_type }}
              </el-tag>
              <el-tag v-if="chain.message.user_nickname" size="small" effect="plain">
                {{ chain.message.user_nickname }}
              </el-tag>
              <span class="bubble-spacer" />
              <span class="importance-pill">
                <span class="importance-bar-mini">
                  <span
                    class="importance-fill"
                    :style="{
                      width: `${((chain.message.importance ?? 0.5) * 100).toFixed(0)}%`,
                    }"
                  />
                </span>
                <span>优先级 {{ ((chain.message.importance ?? 0.5) * 100).toFixed(0) }}%</span>
              </span>
              <span class="expand-hint">
                {{ isExpanded(chain.messageId, 'message') ? '收起 ▲' : '详情 ▾' }}
              </span>
            </div>
            <div class="card-body">
              <div class="card-text">{{ chain.message.text || '(空)' }}</div>
            </div>
            <div v-if="isExpanded(chain.messageId, 'message')" class="card-detail">
              <pre class="json-view" v-html="formatChainJson(chain, 'message')" />
            </div>
          </div>

          <!-- 决策卡（多条：每条 decision.intent 一张） -->
          <div
            v-for="(dec, idx) in chain.decisions"
            :key="`${chain.messageId}-dec-${idx}`"
            class="card card--decision"
            @click="toggleExpand(chain.messageId, `decision-${idx}`)"
          >
            <div class="card-head">
              <span class="card-icon">🧠</span>
              <span class="card-title">决策</span>
              <el-tag size="small" effect="plain" type="success">{{
                dec.deciderName || 'Decider'
              }}</el-tag>
              <el-tag
                v-if="dec.intent.emotion"
                size="small"
                effect="plain"
                :type="emotionTagType(dec.intent.emotion.name)"
              >
                {{ dec.intent.emotion.name }}
                <template v-if="dec.intent.emotion.intensity != null">
                  {{ (dec.intent.emotion.intensity * 100).toFixed(0) }}%
                </template>
              </el-tag>
              <span class="bubble-spacer" />
              <span class="meta-item mono">· {{ formatLatency(dec) }}</span>
              <span class="expand-hint">
                {{ isExpanded(chain.messageId, `decision-${idx}`) ? '收起 ▲' : '详情 ▾' }}
              </span>
            </div>
            <div class="card-body">
              <div v-if="dec.intent.speech" class="card-text card-text--speech">
                {{ dec.intent.speech }}
              </div>
              <div v-else class="card-text card-text--empty">(无 speech 文本)</div>
            </div>
            <div v-if="isExpanded(chain.messageId, `decision-${idx}`)" class="card-detail">
              <pre class="json-view" v-html="formatDecisionJson(dec)" />
            </div>
          </div>

          <!-- 决策中占位（pending 状态且无 decisions） -->
          <div
            v-if="chain.status !== 'done' && chain.decisions.length === 0"
            class="card card--pending"
          >
            <div class="card-head">
              <span class="card-icon">🧠</span>
              <span class="card-title">决策</span>
              <span class="bubble-spacer" />
              <span class="pending-text">决策中…</span>
            </div>
          </div>

          <!-- 派发卡（仅当有 action 且已完成决策） -->
          <div
            v-if="chain.output && chain.output.intent.action"
            class="card card--output"
            @click="toggleExpand(chain.messageId, 'output')"
          >
            <div class="card-head">
              <span class="card-icon">⚙</span>
              <span class="card-title">派发</span>
              <code class="action-chip">{{ chain.output.intent.action.name }}</code>
              <span class="bubble-spacer" />
              <span class="meta-item mono">· {{ formatOutputLatency(chain) }}</span>
              <span class="expand-hint">
                {{ isExpanded(chain.messageId, 'output') ? '收起 ▲' : '详情 ▾' }}
              </span>
            </div>
            <div class="card-body">
              <div class="card-text card-text--meta">
                <span v-if="chain.output.deciderName">
                  已派发到 <strong>{{ chain.output.deciderName }}</strong>
                </span>
                <span
                  v-if="
                    chain.output.intent.action.parameters &&
                    Object.keys(chain.output.intent.action.parameters).length > 0
                  "
                >
                  · {{ chain.output.intent.action.name }}(
                  <span class="mono">
                    {{
                      Object.entries(chain.output.intent.action.parameters)
                        .map(
                          ([k, v]) =>
                            `${k}=${typeof v === 'string' ? `"${v}"` : JSON.stringify(v)}`,
                        )
                        .join(', ')
                    }}
                  </span>
                  )
                </span>
              </div>
            </div>
            <div v-if="isExpanded(chain.messageId, 'output')" class="card-detail">
              <pre class="json-view" v-html="formatDecisionJson(chain.output)" />
            </div>
          </div>
        </article>
      </div>
    </section>

    <!-- ===== 注入区（MVP 简化） ===== -->
    <section class="inject-area">
      <el-collapse v-model="injectOpen">
        <el-collapse-item name="inject" title="手动注入（调试用）">
          <div class="inject-grid">
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
                    <el-option label="neutral" value="neutral" />
                    <el-option label="happy" value="happy" />
                    <el-option label="excited" value="excited" />
                    <el-option label="sad" value="sad" />
                    <el-option label="angry" value="angry" />
                    <el-option label="surprised" value="surprised" />
                    <el-option label="relaxed" value="relaxed" />
                    <el-option label="grateful" value="grateful" />
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
              <el-input
                v-model="intentInput"
                type="textarea"
                placeholder="输入主播回应文本..."
                :rows="2"
                :disabled="sending"
                @keydown.enter.ctrl="sendIntent"
              />
              <el-button type="success" size="small" :loading="sending" @click="sendIntent">
                <el-icon><Promotion /></el-icon>
                发送
              </el-button>
            </div>
          </div>
        </el-collapse-item>
      </el-collapse>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, nextTick, ref } from 'vue';
import { storeToRefs } from 'pinia';
import { Delete, Top, Bottom, Promotion, VideoCamera } from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';
import { useWebSocketStore } from '@/stores/websocket';
import { useSystemStore } from '@/stores/system';
import { debugApi } from '@/api';
import type {
  WebSocketMessage,
  NormalizedMessageData,
  IntentEventData,
  LiveChainStatus,
} from '@/types';
import DOMPurify from 'dompurify';
import hljs from 'highlight.js/lib/core';
import json from 'highlight.js/lib/languages/json';
import 'highlight.js/styles/atom-one-dark.min.css';

hljs.registerLanguage('json', json);

const MAX_CHAINS = 100;
const AUTO_SCROLL_THRESHOLD = 80; // 距底部 px 阈值

// ====== 系统/WS 状态 ======
const systemStore = useSystemStore();
const wsStore = useWebSocketStore();
const { status } = storeToRefs(systemStore);
const wsConnected = computed(() => wsStore.isConnected);

// ====== 链路 / 输入源 状态 ======
interface Chain {
  messageId: string;
  createdAtMs: number;
  lastTsMs: number;
  status: LiveChainStatus;
  message?: NormalizedMessageData;
  decisions: DebugDecisionEntry[];
  output?: DebugDecisionEntry;
}

interface DebugDecisionEntry {
  id: string;
  timestamp: number;
  deciderName: string;
  intent: IntentEventData;
}

interface ComponentLiveStatus {
  name: string;
  connected: boolean;
}

const chains = ref<Map<string, Chain>>(new Map());
const collectors = ref<Map<string, ComponentLiveStatus>>(new Map());
const deciders = ref<Map<string, ComponentLiveStatus>>(new Map());

// 用于 output.render 兜底：最近一个 deciding 链
let lastDecidingKey: string | null = null;

// ====== 展开状态 ======
const expanded = ref<Set<string>>(new Set());

function expandKey(chainId: string, slot: string): string {
  return `${chainId}::${slot}`;
}

function isExpanded(chainId: string, slot: string): boolean {
  return expanded.value.has(expandKey(chainId, slot));
}

function toggleExpand(chainId: string, slot: string): void {
  const key = expandKey(chainId, slot);
  const next = new Set(expanded.value);
  if (next.has(key)) next.delete(key);
  else next.add(key);
  expanded.value = next;
}

// ====== 注入表单状态 ======
const danmakuInput = ref('');
const danmakuSource = ref('dashboard');
const danmakuDataType = ref('text');
const danmakuImportance = ref(1);
const intentInput = ref('');
const intentEmotion = ref('neutral');
const intentSource = ref('dashboard');
const sending = ref(false);
const injectOpen = ref<string[]>([]);

// ====== 本页统计 ======
const stats = ref({ danmaku: 0, reply: 0, action: 0 });

// ====== 滚动状态 ======
const timelineRef = ref<HTMLElement | null>(null);
let autoScroll = true;
let scrollObserver: MutationObserver | null = null;

// ====== 处理中链 / 定时刷新 ======
const nowTick = ref(Date.now());
let nowTimer: ReturnType<typeof setInterval> | null = null;

const processingChain = computed(() => {
  void nowTick.value; // 触发每秒刷新
  let best: Chain | null = null;
  for (const chain of chains.value.values()) {
    if (chain.status === 'done') continue;
    if (!best || chain.lastTsMs > best.lastTsMs) best = chain;
  }
  if (!best) return null;
  const elapsedSec = Math.max(0, Math.floor((Date.now() - best.lastTsMs) / 1000));
  return {
    preview: best.message?.text?.slice(0, 40) || '(无消息文本)',
    status: best.status,
    elapsedSec,
  };
});

// ====== 计算：排序后的链路列表 ======
// 升序：最新链沉底，新消息从底部出现（聊天式），配合自动滚动可见最新
const sortedChains = computed<Chain[]>(() => {
  return [...chains.value.values()].sort((a, b) => a.lastTsMs - b.lastTsMs);
});

const collectorList = computed<ComponentLiveStatus[]>(() => {
  return [...collectors.value.values()].sort((a, b) => a.name.localeCompare(b.name));
});

const deciderList = computed<ComponentLiveStatus[]>(() => {
  return [...deciders.value.values()].sort((a, b) => a.name.localeCompare(b.name));
});

// ====== 解析工具 ======
function parseMessage(data: Record<string, unknown>): NormalizedMessageData {
  const msg = (data?.message as Record<string, unknown> | undefined) ?? {};
  return {
    text: (msg.text as string) ?? '',
    source: (msg.source as string) ?? (data?.source as string) ?? 'unknown',
    data_type: (msg.data_type as string) ?? 'text',
    importance: (msg.importance as number) ?? 0.5,
    timestamp_ms: (msg.timestamp_ms as number) ?? 0,
    user_id: msg.user_id as string | undefined,
    user_nickname: msg.user_nickname as string | undefined,
    platform: msg.platform as string | undefined,
    room_id: msg.room_id as string | undefined,
    raw: msg.raw as Record<string, unknown> | undefined,
    message_id: msg.message_id as string | undefined,
  };
}

function parseIntent(data: Record<string, unknown>): IntentEventData {
  const intentData = (data?.intent_data as Record<string, unknown> | undefined) ?? {};
  const md = (intentData.metadata as Record<string, unknown> | undefined) ?? {};
  return {
    speech: intentData.speech as string | undefined,
    emotion: intentData.emotion as IntentEventData['emotion'] | undefined,
    action: intentData.action as IntentEventData['action'] | undefined,
    metadata: {
      source_id: (md.source_id as string) ?? (data?.name as string) ?? 'unknown',
      decision_time_ms: (md.decision_time_ms as number) ?? 0,
      source_message_id: md.source_message_id as string | undefined,
    },
  };
}

// ====== 链路维护 ======
function ensureChain(messageId: string, timestampMs: number): Chain {
  let chain = chains.value.get(messageId);
  if (!chain) {
    chain = {
      messageId,
      createdAtMs: timestampMs,
      lastTsMs: timestampMs,
      status: 'pending',
      decisions: [],
    };
    chains.value.set(messageId, chain);
    trimChains();
  }
  return chain;
}

function trimChains(): void {
  if (chains.value.size <= MAX_CHAINS) return;
  // 按 createdAtMs 升序，删最早的直到 <= MAX_CHAINS
  const sorted = [...chains.value.values()].sort((a, b) => a.createdAtMs - b.createdAtMs);
  const toRemove = sorted.slice(0, chains.value.size - MAX_CHAINS);
  for (const c of toRemove) chains.value.delete(c.messageId);
}

function touchChain(chain: Chain, tsMs: number): void {
  chain.lastTsMs = tsMs;
}

function maybeCompleteChain(chain: Chain): void {
  // 决策完成后等 output.render；存在 output 后标记 done
  if (chain.output && chain.decisions.length > 0) {
    chain.status = 'done';
  } else if (chain.decisions.length > 0) {
    chain.status = 'deciding';
  } else {
    chain.status = 'pending';
  }
}

// ====== WebSocket 事件处理 ======
function handleEvent(message: WebSocketMessage): void {
  const t = message.type;

  if (t === 'message.received') {
    const data = (message.data ?? {}) as Record<string, unknown>;
    const msgObj = (data.message as Record<string, unknown> | undefined) ?? {};
    const metaObj = (data.metadata as Record<string, unknown> | undefined) ?? {};
    const idRaw =
      (msgObj.message_id as string | undefined) ??
      (metaObj.message_id as string | undefined) ??
      message.id ??
      `msg-${message.timestamp}`;
    const chain = ensureChain(idRaw, message.timestamp * 1000);
    chain.message = parseMessage(data);
    touchChain(chain, message.timestamp * 1000);
    stats.value.danmaku += 1;
    return;
  }

  if (t === 'decision.intent') {
    const data = (message.data ?? {}) as Record<string, unknown>;
    const intent = parseIntent(data);
    const srcId =
      intent.metadata.source_message_id ??
      intent.metadata.source_id ??
      lastDecidingKey ??
      `orphan-decision-${message.timestamp}`;
    const chain = ensureChain(srcId, message.timestamp * 1000);
    chain.decisions.push({
      id: message.id ?? `decision-${message.timestamp}-${chain.decisions.length}`,
      timestamp: message.timestamp,
      deciderName: (data?.name as string) ?? 'Decider',
      intent,
    });
    chain.status = 'deciding';
    touchChain(chain, message.timestamp * 1000);
    lastDecidingKey = srcId;
    stats.value.reply += 1;
    if (intent.action) stats.value.action += 1;
    return;
  }

  if (t === 'output.render') {
    const data = (message.data ?? {}) as Record<string, unknown>;
    const intent = parseIntent(data);
    const srcId =
      intent.metadata.source_message_id ??
      intent.metadata.source_id ??
      lastDecidingKey ??
      `orphan-output-${message.timestamp}`;
    const chain = ensureChain(srcId, message.timestamp * 1000);
    chain.output = {
      id: message.id ?? `output-${message.timestamp}`,
      timestamp: message.timestamp,
      deciderName: 'Output',
      intent,
    };
    maybeCompleteChain(chain);
    touchChain(chain, message.timestamp * 1000);
    return;
  }

  if (t === 'collector.connected' || t === 'collector.disconnected') {
    const data = (message.data ?? {}) as Record<string, unknown>;
    const name = (data?.name as string) ?? 'unknown';
    const connected = t === 'collector.connected';
    collectors.value.set(name, { name, connected });
    collectors.value = new Map(collectors.value);
    return;
  }

  if (t === 'decider.connected' || t === 'decider.disconnected') {
    const data = (message.data ?? {}) as Record<string, unknown>;
    const name = (data?.name as string) ?? 'unknown';
    const connected = t === 'decider.connected';
    deciders.value.set(name, { name, connected });
    deciders.value = new Map(deciders.value);
    return;
  }

  // 其他类型（system.status / system.error / events.history / ping 等）忽略
}

// ====== 格式化辅助 ======
function formatTime(ms: number): string {
  if (!ms) return '-';
  const d = new Date(ms);
  return (
    d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) +
    `.${String(d.getMilliseconds()).padStart(3, '0')}`
  );
}

function formatElapsed(sec: number): string {
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}m ${s}s`;
}

function shortId(id: string): string {
  if (!id) return '?';
  return id.length > 12 ? `${id.slice(0, 6)}…${id.slice(-4)}` : id;
}

function statusLabel(status: LiveChainStatus): string {
  if (status === 'pending') return '等待决策';
  if (status === 'deciding') return '决策中';
  return '已完成';
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

function formatLatency(entry: DebugDecisionEntry): string {
  const ms = entry.intent?.metadata?.decision_time_ms;
  if (!ms || !entry.timestamp) return '-';
  const wsMs = entry.timestamp * 1000;
  const diff = Math.abs(wsMs - ms);
  return diff < 1000 ? `${diff}ms` : `${(diff / 1000).toFixed(1)}s`;
}

function formatOutputLatency(chain: Chain): string {
  const out = chain.output;
  if (!out) return '-';
  const decMs = chain.decisions[chain.decisions.length - 1]?.intent?.metadata?.decision_time_ms;
  if (!decMs || !out.timestamp) return '-';
  const outMs = out.timestamp * 1000;
  const diff = Math.max(0, outMs - decMs);
  return diff < 1000 ? `${diff}ms` : `${(diff / 1000).toFixed(1)}s`;
}

function formatChainJson(chain: Chain, slot: string): string {
  let obj: Record<string, unknown> = {};
  if (slot === 'message' && chain.message) {
    obj = { message: chain.message };
  }
  const str = JSON.stringify(obj, null, 2);
  return DOMPurify.sanitize(hljs.highlight(str, { language: 'json' }).value);
}

function formatDecisionJson(entry: DebugDecisionEntry): string {
  const obj: Record<string, unknown> = {
    intent: entry.intent,
    deciderName: entry.deciderName,
  };
  const str = JSON.stringify(obj, null, 2);
  return DOMPurify.sanitize(hljs.highlight(str, { language: 'json' }).value);
}

// ====== 操作 ======
function clearAll(): void {
  chains.value.clear();
  expanded.value.clear();
  stats.value = { danmaku: 0, reply: 0, action: 0 };
}

function scrollToTop(): void {
  if (timelineRef.value) timelineRef.value.scrollTop = 0;
}

function scrollToBottom(): void {
  if (timelineRef.value) {
    timelineRef.value.scrollTop = timelineRef.value.scrollHeight;
    autoScroll = true;
  }
}

function onScroll(): void {
  const el = timelineRef.value;
  if (!el) return;
  const distanceToBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
  autoScroll = distanceToBottom < AUTO_SCROLL_THRESHOLD;
}

async function sendDanmaku(): Promise<void> {
  const text = danmakuInput.value.trim();
  if (!text) return;
  sending.value = true;
  try {
    await debugApi.injectMessage({
      text,
      source: danmakuSource.value.trim() || 'dashboard',
      data_type: danmakuDataType.value,
      importance: danmakuImportance.value,
    });
    danmakuInput.value = '';
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '发送失败');
  } finally {
    sending.value = false;
  }
}

async function sendIntent(): Promise<void> {
  const text = intentInput.value.trim();
  if (!text) return;
  sending.value = true;
  try {
    await debugApi.injectIntent({
      text,
      emotion: intentEmotion.value,
      source: intentSource.value.trim() || 'dashboard',
    });
    intentInput.value = '';
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '发送失败');
  } finally {
    sending.value = false;
  }
}

// ====== 生命周期 ======
onMounted(() => {
  wsStore.subscribe(handleEvent);
  wsStore.connect();
  systemStore.startPolling(1000);

  nowTimer = setInterval(() => {
    nowTick.value = Date.now();
  }, 1000);

  nextTick(() => {
    const el = timelineRef.value;
    if (!el) return;
    el.addEventListener('scroll', onScroll, { passive: true });
    scrollObserver = new MutationObserver(() => {
      if (!autoScroll) return;
      nextTick(() => {
        if (timelineRef.value) {
          timelineRef.value.scrollTop = timelineRef.value.scrollHeight;
        }
      });
    });
    scrollObserver.observe(el, { childList: true, subtree: false });
  });
});

onUnmounted(() => {
  wsStore.unsubscribe(handleEvent);
  systemStore.stopPolling();
  if (nowTimer) {
    clearInterval(nowTimer);
    nowTimer = null;
  }
  if (scrollObserver) {
    scrollObserver.disconnect();
    scrollObserver = null;
  }
  if (timelineRef.value) timelineRef.value.removeEventListener('scroll', onScroll);
});
</script>

<style scoped>
.live-observer {
  display: flex;
  flex-direction: column;
  height: calc(100vh - var(--header-height, 64px) - var(--spacing-lg, 24px) * 2);
  padding: var(--spacing-lg);
  gap: var(--spacing-md);
  overflow: hidden;
  max-width: 1400px;
  margin: 0 auto;
}

/* ===== 头部 ===== */
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  flex-shrink: 0;
}
.header-left {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-xs, 4px);
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
.connection-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 2px 10px;
  font-size: 12px;
  font-weight: 500;
  border-radius: 999px;
  background: var(--bg-hover);
  color: var(--text-secondary);
}
.connection-pill .dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--text-placeholder);
}
.connection-pill.is-on .dot {
  background: var(--color-success);
  box-shadow: 0 0 0 2px rgba(103, 194, 58, 0.18);
}
.connection-pill.is-off .dot {
  background: var(--color-danger);
}
.chain-count {
  font-size: 12px;
  color: var(--text-placeholder);
  font-family: var(--font-mono);
}

/* ===== 状态栏 ===== */
.status-bar {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
  padding: var(--spacing-md);
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
  flex-shrink: 0;
}
.status-row {
  display: flex;
  align-items: center;
  gap: var(--spacing-md);
  flex-wrap: wrap;
}
.status-row + .status-row {
  border-top: 1px dashed var(--border-color-light);
  padding-top: var(--spacing-sm);
}
.status-group {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  flex: 1 1 auto;
  min-width: 0;
}
.status-group-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  flex-shrink: 0;
}
.status-chips {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.status-chip-empty {
  font-size: 11px;
  color: var(--text-placeholder);
  font-style: italic;
}
.collector-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 1px 8px;
  font-size: 11px;
  font-weight: 500;
  font-family: var(--font-mono, 'Cascadia Code', monospace);
  border-radius: 999px;
  background: var(--bg-hover);
  color: var(--text-secondary);
  border: 1px solid var(--border-color-light);
}
.collector-chip .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--text-placeholder);
}
.collector-chip.is-on {
  border-color: rgba(103, 194, 58, 0.4);
  color: var(--color-success);
  background: rgba(103, 194, 58, 0.06);
}
.collector-chip.is-on .dot {
  background: var(--color-success);
}
.collector-chip.is-off {
  border-color: rgba(245, 108, 108, 0.35);
  color: var(--color-danger);
  background: rgba(245, 108, 108, 0.06);
}
.collector-chip.is-off .dot {
  background: var(--color-danger);
}

/* 处理中条 */
.processing-bar {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  flex: 1 1 auto;
  padding: 6px 12px;
  border-radius: var(--radius-md);
  border: 1px solid var(--border-color-light);
  background: var(--bg-page);
  font-size: 12px;
  min-width: 0;
}
.processing-bar.is-active {
  border-color: rgba(230, 162, 60, 0.5);
  background: rgba(230, 162, 60, 0.08);
}
.processing-bar.is-idle {
  color: var(--text-placeholder);
}
.processing-icon {
  font-size: 14px;
}
.processing-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-primary);
}
.processing-text strong {
  font-weight: 600;
}
.processing-status {
  color: var(--text-secondary);
  font-size: 11px;
}
.processing-elapsed {
  font-family: var(--font-mono, 'Cascadia Code', monospace);
  font-size: 11px;
  color: var(--color-warning);
  font-weight: 600;
}

.page-stats {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  flex-shrink: 0;
}
.stat-pill {
  display: inline-flex;
  align-items: baseline;
  gap: 4px;
  padding: 2px 10px;
  border-radius: var(--radius-sm, 4px);
  background: var(--bg-page);
  border: 1px solid var(--border-color-light);
  font-size: 11px;
}
.stat-num {
  font-family: var(--font-mono);
  font-size: 13px;
  font-weight: 700;
  color: var(--text-primary);
}
.stat-key {
  color: var(--text-secondary);
}

/* ===== 时间线 ===== */
.timeline {
  flex: 1;
  overflow-y: auto;
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
  padding: var(--spacing-md);
  min-height: 200px;
}
.timeline::-webkit-scrollbar {
  width: 6px;
}
.timeline::-webkit-scrollbar-thumb {
  background: var(--border-color-dark);
  border-radius: 3px;
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

.chain-list {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
}
.chain {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px 12px;
  background: var(--bg-elevated);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-md);
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.03);
  animation: chainIn 0.22s cubic-bezier(0.4, 0, 0.2, 1);
}
.chain--done {
  border-left: 3px solid var(--color-success);
}
.chain--deciding {
  border-left: 3px solid var(--color-warning);
}
.chain--pending {
  border-left: 3px solid var(--color-primary);
}

@keyframes chainIn {
  from {
    opacity: 0;
    transform: translateY(6px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.chain-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
  color: var(--text-secondary);
}
.chain-id {
  font-family: var(--font-mono, 'Cascadia Code', monospace);
  color: var(--text-placeholder);
}
.chain-status-tag {
  padding: 1px 8px;
  border-radius: 999px;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.4px;
}
.tag--pending {
  background: rgba(64, 158, 255, 0.12);
  color: var(--color-primary);
}
.tag--deciding {
  background: rgba(230, 162, 60, 0.15);
  color: var(--color-warning);
}
.tag--done {
  background: rgba(103, 194, 58, 0.15);
  color: var(--color-success);
}
.chain-time {
  font-size: 11px;
  color: var(--text-placeholder);
}
.bubble-spacer {
  flex: 1;
}

/* ===== 卡片（链路子项） ===== */
.card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 12px;
  border-radius: var(--radius-sm, 6px);
  border: 1px solid var(--border-color-light);
  background: var(--bg-card);
  cursor: pointer;
  transition:
    border-color var(--transition-fast),
    box-shadow var(--transition-fast);
}
.card:hover {
  border-color: rgba(64, 158, 255, 0.4);
  box-shadow: 0 2px 8px rgba(64, 158, 255, 0.08);
}
.card--message {
  background: var(--bg-page);
}
.card--decision {
  background: rgba(103, 194, 58, 0.04);
}
.card--output {
  background: rgba(64, 158, 255, 0.04);
}
.card--pending {
  background: var(--bg-page);
  cursor: default;
  border-style: dashed;
  color: var(--text-secondary);
}
.card--pending:hover {
  border-color: var(--border-color-light);
  box-shadow: none;
}

.card-head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: var(--text-secondary);
}
.card-icon {
  font-size: 13px;
}
.card-title {
  font-weight: 600;
  font-size: 12px;
  color: var(--text-primary);
}
.expand-hint {
  margin-left: auto;
  font-size: 11px;
  font-weight: 500;
  color: var(--color-primary);
  user-select: none;
}
.card-body {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.card-text {
  font-size: 13px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--text-primary);
}
.card-text--speech {
  font-size: 14px;
  font-weight: 500;
}
.card-text--empty {
  color: var(--text-placeholder);
  font-style: italic;
}
.card-text--meta {
  font-size: 12px;
  color: var(--text-secondary);
}
.card-text--meta strong {
  color: var(--text-primary);
}

.meta-item {
  opacity: 0.85;
  font-size: 11px;
}

.action-chip {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 4px;
  background: rgba(64, 158, 255, 0.12);
  color: var(--color-primary);
  font-family: var(--font-mono, 'Cascadia Code', monospace);
  font-size: 11px;
  font-weight: 600;
}

.pending-text {
  font-size: 12px;
  color: var(--text-secondary);
  font-style: italic;
}

/* 优先级 pill（沿用 SessionHistory.vue 样式） */
.importance-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 1px 8px;
  border-radius: 10px;
  background: var(--bg-card);
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
}

/* JSON 详情 */
.card-detail {
  margin-top: 6px;
  border-radius: 6px;
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
  flex-shrink: 0;
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
  padding: 0 var(--spacing-md);
}
.inject-area :deep(.el-collapse-item__header) {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary);
}
.inject-grid {
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

@media (max-width: 960px) {
  .inject-grid {
    grid-template-columns: 1fr;
  }
  .status-row {
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>
