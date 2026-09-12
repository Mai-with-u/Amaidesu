<!--
  运行总览（直播间状态板）

  首页只回答运营视角的四个问题：
  - 她在正常陪观众聊吗？（直播对话流 + 决策心跳）
  - 她卡住了吗 / 出错了吗？（结论条 + 主播体征）
  - 花多少？（今日成本 / 调用）
  - 节目到哪了？（基座一行末尾）

  组件启停、批量控制、运行时元信息（版本 / Python / 入口 URL）
  归 /collectors、/agents、/tools 各页管辖，首页不重复承载。
-->
<template>
  <div class="dashboard">
    <!-- 1. 结论条 -->
    <header class="verdict-bar" :class="`is-${verdict.tone}`">
      <div class="verdict-lamp">
        <span
          class="verdict-dot"
          :class="{ 'is-live': verdict.tone === 'live' }"
          aria-hidden="true"
        />
        <span class="verdict-label">{{ verdict.phrase }}</span>
        <span v-if="verdict.detail" class="verdict-detail">{{ verdict.detail }}</span>
      </div>
      <div class="verdict-meta">
        <span class="verdict-meta-item mono">运行时长 {{ formatUptime(uptimeSec) }}</span>
        <span class="verdict-meta-sep" aria-hidden="true">·</span>
        <span class="verdict-meta-item mono">今日成本 {{ todayCostText }}</span>
        <span v-if="todayCostSubText" class="verdict-meta-sub mono">{{ todayCostSubText }}</span>
        <span class="verdict-meta-sep" aria-hidden="true">·</span>
        <span class="verdict-meta-item mono">今日调用 {{ todayCallsText }}</span>
        <span class="verdict-meta-sep" aria-hidden="true">·</span>
        <div class="verdict-proactive">
          <span class="verdict-meta-item">主动发言</span>
          <el-switch
            v-model="proactiveEnabled"
            size="small"
            :loading="proactiveToggling"
            @change="onProactiveToggle"
          />
        </div>
        <el-button class="verdict-eventlog" type="primary" plain size="small" @click="goEventLog">
          事件流
        </el-button>
      </div>
    </header>

    <!-- 2. 主网格：直播对话流（hero） + 主播体征 / 活动脉搏（右列） -->
    <section class="main-grid">
      <article class="card feed-card">
        <div class="card-head">
          <h2 class="card-title">直播对话流</h2>
          <span class="card-hint">最近 {{ liveEntries.length }} 条 · 实时折叠</span>
          <span class="grow" />
          <a class="card-link" @click.prevent="router.push('/live')">深度观察 → 直播控制台</a>
        </div>
        <div class="feed-scroll">
          <FeedTimeline :entries="liveEntries" compact empty-text="暂无直播活动" />
        </div>
      </article>

      <div class="side-column">
        <article class="card vitals-card">
          <div class="card-head">
            <h2 class="card-title">主播体征</h2>
            <span class="card-hint">启动以来累计</span>
          </div>
          <div v-if="!streamerAvailable" class="vitals-empty">主播 Agent 未运行</div>
          <div v-else class="vitals-body">
            <div class="heartbeat-row">
              <span class="heartbeat-label">决策心跳</span>
              <span class="heartbeat-time mono" :class="`tone-${heartbeat.tone}`">
                {{ heartbeat.text }}
              </span>
            </div>
            <div class="funnel-row">
              <template v-for="(step, idx) in funnelSteps" :key="step.label">
                <span class="funnel-step">
                  <span class="funnel-value mono">{{ step.value }}</span>
                  <span class="funnel-label">{{ step.label }}</span>
                </span>
                <span v-if="idx < funnelSteps.length - 1" class="funnel-arrow" aria-hidden="true"
                  >→</span
                >
              </template>
              <span class="funnel-rate mono">{{ replyRateText }}</span>
            </div>
            <div class="fail-row">
              <span v-for="fail in failItems" :key="fail.key" class="fail-item">
                <span class="fail-label">{{ fail.label }}</span>
                <span class="fail-value mono" :class="{ 'is-warn': fail.value > 0 }">
                  {{ fail.value }}
                </span>
              </span>
            </div>
          </div>
        </article>

        <article class="card pulse-card">
          <div class="card-head">
            <h2 class="card-title">活动脉搏</h2>
            <span class="card-hint">近 60 分钟（本地缓冲）</span>
          </div>
          <PulseChart :series="pulseSeries" :labels="pulseLabels" :height="120" />
        </article>
      </div>
    </section>

    <!-- 3. 今日统计条 -->
    <section class="stats-strip" aria-label="今日统计">
      <template v-for="(item, idx) in statsStrip" :key="item.key">
        <div v-if="idx > 0" class="stat-divider" aria-hidden="true" />
        <div class="stat-block">
          <span class="stat-num mono">{{ item.value }}</span>
          <span class="stat-key">{{ item.label }}</span>
          <span class="stat-cap">{{ item.window }}</span>
        </div>
      </template>
      <span class="grow" />
      <span class="stat-strip-tail">本地缓冲窗口</span>
    </section>

    <!-- 4. 基座一行 -->
    <footer class="infra-row" aria-label="基座状态">
      <a class="infra-link" @click.prevent="router.push('/collectors')">
        <span>采集</span>
        <span class="mono">{{ infra.collectors.started }} / {{ infra.collectors.total }}</span>
        <span v-if="infra.collectors.idleNames.length > 0" class="infra-idle mono">
          （{{ infra.collectors.idleNames.join('、') }}）
        </span>
      </a>
      <span class="infra-sep" aria-hidden="true">·</span>
      <a class="infra-link" @click.prevent="router.push('/tools')">
        <span>工具熔断</span>
        <span class="mono" :class="{ 'is-warn': infra.tools.tripped > 0 }">
          {{ infra.tools.tripped }}
        </span>
      </a>
      <span class="infra-sep" aria-hidden="true">·</span>
      <a
        class="infra-link"
        :class="{ 'is-disabled': infra.rundown.unavailable }"
        @click.prevent="router.push('/outline')"
      >
        <span>节目</span>
        <span class="mono">{{ infra.rundown.text }}</span>
      </a>
      <span class="grow" />
      <span class="infra-hint mono">→</span>
    </footer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { ElMessage } from 'element-plus';
import { storeToRefs } from 'pinia';
import { useSystemStore, useEventsStore } from '@/stores';
import {
  rundownApi,
  componentApi,
  liveSessionsApi,
  llmApi,
  streamerApi,
  toolsApi,
  viewersApi,
} from '@/api';
import type {
  RundownStateResponse,
  LiveSessionListResponse,
  LLMHistoryStatistics,
  LLMUsageSummary,
  StreamerStatusResponse,
  ToolEntry,
} from '@/types';
import FeedTimeline from '@/components/live/FeedTimeline.vue';
import PulseChart from '@/components/dashboard/PulseChart.vue';
import { buildLiveEntries, type FeedEvent, type ShowEntry } from '@/utils/liveFeed';

const router = useRouter();
const systemStore = useSystemStore();
const eventsStore = useEventsStore();
const { status } = storeToRefs(systemStore);

// ====== 运行状态（持续取自 system store） ======

const uptimeSec = computed(() => status.value?.uptime_seconds ?? 0);

// ====== 组件 / 工具 / 主播 / 流程单 / 场次（REST 周期刷） ======

interface CollectorSummary {
  name: string;
  is_started: boolean;
  is_enabled: boolean;
}

const collectors = ref<CollectorSummary[]>([]);
const tools = ref<ToolEntry[]>([]);
const streamerStatus = ref<StreamerStatusResponse | null>(null);
const proactiveEnabled = ref(false);
const proactiveToggling = ref(false);

async function onProactiveToggle(value: string | number | boolean) {
  const enabled = Boolean(value);
  proactiveToggling.value = true;
  try {
    const resp = await streamerApi.toggleProactive({ enabled });
    ElMessage.success(resp.data.message || (enabled ? '主动发言已开启' : '主动发言已关闭'));
  } catch (error) {
    proactiveEnabled.value = !enabled;
    ElMessage.error(`切换失败: ${(error as Error).message}`);
  } finally {
    proactiveToggling.value = false;
  }
}
const viewerCount = ref<number>(0);
const agendaState = ref<RundownStateResponse | null>(null);
const sessions = ref<LiveSessionListResponse | null>(null);

const streamerAvailable = computed(() => streamerStatus.value?.available === true);

// ====== LLM 今日统计 + 累计兜底 ======

const llmStats = ref<LLMHistoryStatistics | null>(null);
const llmSummary = ref<LLMUsageSummary | null>(null);

/** Unix 毫秒：今日本地零点。用于截取今日聚合统计 */
function todayStartMs(): number {
  const now = new Date();
  now.setHours(0, 0, 0, 0);
  return now.getTime();
}

async function fetchLLM() {
  const [statsRes, summaryRes] = await Promise.allSettled([
    llmApi.getStatistics({ start_time: todayStartMs() }),
    llmApi.getUsageSummary(),
  ]);
  llmStats.value = statsRes.status === 'fulfilled' ? statsRes.value.data : null;
  llmSummary.value = summaryRes.status === 'fulfilled' ? summaryRes.value.data : null;
}

const todayCostText = computed(() => {
  const stats = llmStats.value;
  if (stats && stats.total_cost > 0) return `¥${stats.total_cost.toFixed(2)}`;
  if (stats && stats.total_requests > 0) return '¥0.00';
  return '¥—';
});

/** 累计兜底小字：仅在今日统计不可用但累计数据存在时显示 */
const todayCostSubText = computed(() => {
  if (llmStats.value && llmStats.value.total_requests > 0) return '';
  const summary = llmSummary.value;
  if (!summary || summary.total_cost <= 0) return '';
  return `累计 ¥${summary.total_cost.toFixed(2)}`;
});

const todayCallsText = computed(() => {
  const stats = llmStats.value;
  if (!stats) return '—';
  if (stats.total_requests === 0) return '0 次';
  const rate = Math.round(stats.success_rate * 100);
  return `${stats.total_requests} 次 · 成功率 ${rate}%`;
});

// ====== 直播对话流：把 events 折叠成 ShowEntry 后取尾 15 ======

const liveEntries = computed<ShowEntry[]>(() => {
  const events = eventsStore.events as unknown as FeedEvent[];
  const list = buildLiveEntries(events, new Set<string>());
  return list.slice(-15);
});

// ====== 主播体征：决策心跳 + 漏斗 + 失败计数 ======

const stats = computed(() => {
  const s = streamerStatus.value?.statistics ?? {};
  return {
    messages: s.total_messages ?? 0,
    batches: s.total_batches ?? 0,
    replies: s.total_replies ?? 0,
    proactive: s.total_proactive ?? 0,
    plannerFailures: s.planner_failures ?? 0,
    replyerFailures: s.replyer_failures ?? 0,
  };
});

const replyRateText = computed(() => {
  const m = stats.value.messages;
  if (m <= 0) return '回复率 —';
  return `回复率 ${((stats.value.replies / m) * 100).toFixed(1)}%`;
});

const funnelSteps = computed(() => [
  { label: '消息', value: stats.value.messages },
  { label: '批次', value: stats.value.batches },
  { label: '回复', value: stats.value.replies },
]);

const failItems = computed(() => [
  { key: 'planner', label: '决策失败', value: stats.value.plannerFailures },
  { key: 'replyer', label: '生成失败', value: stats.value.replyerFailures },
]);

const heartbeat = computed<{ text: string; tone: 'live' | 'fresh' | 'stale' | 'silent' }>(() => {
  const events = eventsStore.events as unknown as FeedEvent[];
  let latest = 0;
  for (const event of events) {
    if (event.type === 'streamer.stage') continue;
    if (!event.type.startsWith('planner.') && event.type !== 'streamer.speech') continue;
    if (event.timestamp > latest) latest = event.timestamp;
  }
  // 后端 timestamp 已是秒；毫秒值兜底换算
  const latestSec = latest > 1e12 ? latest / 1000 : latest;
  if (latestSec === 0) return { text: '尚未触发', tone: 'silent' };
  const diff = Math.max(0, Math.floor(Date.now() / 1000 - latestSec));
  if (diff < 60) return { text: `${diff}s 前`, tone: 'live' };
  if (diff < 300) return { text: `${Math.floor(diff / 60)}m ${diff % 60}s 前`, tone: 'fresh' };
  const text = diff < 3600 ? `${Math.floor(diff / 60)}m 前` : `${Math.floor(diff / 3600)}h 前`;
  return { text, tone: 'stale' };
});

// ====== 活动脉搏：60 分钟按分钟分桶的弹幕 / 发言双系列 ======

const PULSE_BUCKETS = 60;
const PULSE_BUCKET_SEC = 60;

interface BucketWindow {
  labels: string[];
  series: { label: string; color: string; values: number[] }[];
}

function buildPulse(events: FeedEvent[]): BucketWindow {
  // 当前分钟对齐到 60s 边界；窗口 = [now-59min, now]，labels[i] = 窗口内第 i 分钟 HH:MM
  const nowSec = Math.floor(Date.now() / 1000);
  const windowEnd = Math.floor(nowSec / PULSE_BUCKET_SEC) * PULSE_BUCKET_SEC;
  const windowStart = windowEnd - (PULSE_BUCKETS - 1) * PULSE_BUCKET_SEC;

  const audience = new Array<number>(PULSE_BUCKETS).fill(0);
  const streamer = new Array<number>(PULSE_BUCKETS).fill(0);
  const labels = new Array<string>(PULSE_BUCKETS);
  for (let i = 0; i < PULSE_BUCKETS; i++) {
    const d = new Date((windowStart + i * PULSE_BUCKET_SEC) * 1000);
    labels[i] =
      `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  }
  for (const event of events) {
    const ts = event.timestamp > 1e12 ? event.timestamp / 1000 : event.timestamp;
    if (ts < windowStart || ts > windowEnd + PULSE_BUCKET_SEC) continue;
    const idx = Math.floor((ts - windowStart) / PULSE_BUCKET_SEC);
    if (idx < 0 || idx >= PULSE_BUCKETS) continue;
    if (event.type === 'room.message') audience[idx] += 1;
    else if (event.type === 'streamer.speech') streamer[idx] += 1;
  }
  return {
    labels,
    series: [
      { label: '观众消息', color: 'var(--color-collector)', values: audience },
      { label: '主播发言', color: 'var(--color-agent)', values: streamer },
    ],
  };
}

const pulseWindow = computed(() => buildPulse(eventsStore.events as unknown as FeedEvent[]));
const pulseSeries = computed(() => pulseWindow.value.series);
const pulseLabels = computed(() => pulseWindow.value.labels);

// ====== 今日缓冲窗口按类型分桶（仅取 room.message） ======

const bufferCounts = computed(() => {
  const result = { danmaku: 0, gift: 0, superChat: 0, enter: 0 };
  for (const event of eventsStore.events as unknown as FeedEvent[]) {
    if (event.type !== 'room.message') continue;
    const data = event.data as Record<string, unknown> | undefined;
    const mt = (data?.message_type ?? '') as string;
    if (mt === 'danmaku') result.danmaku += 1;
    else if (mt === 'gift') result.gift += 1;
    else if (mt === 'super_chat') result.superChat += 1;
    else if (mt === 'enter') result.enter += 1;
  }
  return result;
});

interface StatItem {
  key: string;
  label: string;
  value: number;
  window: string;
}

const statsStrip = computed<StatItem[]>(() => [
  { key: 'danmaku', label: '弹幕', value: bufferCounts.value.danmaku, window: '缓冲' },
  { key: 'gift', label: '礼物', value: bufferCounts.value.gift, window: '缓冲' },
  { key: 'sc', label: 'SC', value: bufferCounts.value.superChat, window: '缓冲' },
  { key: 'enter', label: '进场', value: bufferCounts.value.enter, window: '缓冲' },
  { key: 'viewers', label: '观众', value: viewerCount.value, window: '入库以来' },
  { key: 'replies', label: '回复', value: stats.value.replies, window: '启动以来' },
  { key: 'proactive', label: '主动', value: stats.value.proactive, window: '启动以来' },
]);

// ====== 结论条：异常 > 降级 > 直播中 > 空闲 ======

const ERROR_WINDOW_SEC = 300;

interface Verdict {
  tone: 'error' | 'warn' | 'live' | 'idle';
  phrase: string;
  detail: string;
}

const verdict = computed<Verdict>(() => {
  // a) 异常：工具熔断 或 最近 5 分钟内的 system.error
  const tripped = tools.value.filter(t => t.health?.state === 'tripped');
  if (tripped.length > 0) {
    const head = tripped[0];
    return { tone: 'error', phrase: '异常', detail: `工具 ${head.name} 熔断` };
  }
  const nowSec = Date.now() / 1000;
  const recentError = (eventsStore.events as unknown as FeedEvent[]).find(event => {
    if (event.type !== 'system.error') return false;
    const ts = event.timestamp > 1e12 ? event.timestamp / 1000 : event.timestamp;
    return nowSec - ts <= ERROR_WINDOW_SEC;
  });
  if (recentError) {
    return { tone: 'error', phrase: '异常', detail: '最近 5 分钟内出现系统错误' };
  }

  // b) 降级：启用了采集器但没跑 / 主播 Agent 未注册
  const idleCollectors = collectors.value.filter(c => c.is_enabled && !c.is_started);
  if (idleCollectors.length > 0) {
    const names = idleCollectors
      .slice(0, 3)
      .map(c => c.name)
      .join('、');
    return { tone: 'warn', phrase: '降级', detail: `采集器 ${names} 未运行` };
  }
  if (!streamerAvailable.value) {
    return { tone: 'warn', phrase: '降级', detail: '主播 Agent 未注册' };
  }
  // c) 直播中：存在活跃场次
  if (sessions.value?.active_session_id != null) {
    return { tone: 'live', phrase: '直播中', detail: '' };
  }
  // d) 空闲
  return { tone: 'idle', phrase: '空闲', detail: '系统就绪 · 等待场次' };
});

// ====== 基座一行：采集、工具熔断、节目 ======

const infra = computed(() => {
  const total = collectors.value.length;
  const started = collectors.value.filter(c => c.is_started).length;
  const idle = collectors.value.filter(c => !c.is_started).map(c => c.name);
  const tripped = tools.value.filter(t => t.health?.state === 'tripped').length;

  let agendaText = '未加载';
  let unavailable = false;
  const snap = agendaState.value?.snapshot ?? null;
  if (snap?.status === 'running' || snap?.status === 'paused') {
    const cur = snap.current;
    agendaText = `环节 ${snap.index + 1}/${snap.total} · ${cur?.title ?? '环节'}`;
    if (snap.status === 'paused') agendaText += ' · 已暂停';
  } else if (snap?.status === 'done') {
    agendaText = '已完结';
  } else {
    unavailable = true;
    agendaText = '未加载';
  }

  return {
    collectors: { total, started, idleNames: idle.slice(0, 3) },
    tools: { tripped },
    rundown: { text: agendaText, unavailable },
  };
});

// ====== 周期刷新（采集/工具/主播/节目/场次） ======

let refreshTimer: ReturnType<typeof setInterval> | null = null;

async function refreshSnapshot(): Promise<void> {
  // 组件清单由 systemStore 轮询驱动（1s tick），这里只补独立 REST
  try {
    const [compResp, toolsResp, streamerResp, rundownResp, sessionsResp] = await Promise.all([
      componentApi.getAll(),
      toolsApi.list(),
      streamerApi.getStatus(),
      rundownApi.getState(),
      liveSessionsApi.list(),
    ]);
    collectors.value = compResp.data.collectors ?? [];
    tools.value = toolsResp.data.tools ?? [];
    streamerStatus.value = streamerResp.data;
    proactiveEnabled.value = streamerResp.data.config?.proactive_enabled ?? false;
    agendaState.value = rundownResp.data;
    sessions.value = sessionsResp.data;
  } catch {
    // 任一接口失败都保留旧值；结论条自然按缺失数据降级（直播中/降级/空闲）
  }

  // 观众数字独立取数：失败只保留旧值，不拖累上方整体快照
  try {
    const viewersResp = await viewersApi.get({ limit: 5 });
    viewerCount.value = viewersResp.data.count;
  } catch {
    // 观众接口失败时保留旧值（显示 0）
  }
}

function startRefresh(): void {
  refreshTimer = setInterval(() => {
    void refreshSnapshot();
  }, 12000);
}

function stopRefresh(): void {
  if (refreshTimer !== null) {
    clearInterval(refreshTimer);
    refreshTimer = null;
  }
}

// 收到 rundown.changed 时立即刷新流程单快照（避免等下个 12s tick）
watch(
  () => eventsStore.events[eventsStore.events.length - 1]?.type,
  type => {
    if (type === 'rundown.changed')
      void rundownApi.getState().then(r => (agendaState.value = r.data));
  },
);

// ====== 工具函数 ======

function formatUptime(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

function goEventLog(): void {
  router.push('/eventlog');
}

// ====== 生命周期 ======

onMounted(async () => {
  await systemStore.fetchStatus();
  systemStore.startPolling(1000);
  void eventsStore.backfill();
  await refreshSnapshot();
  void fetchLLM();
  startRefresh();
});

onUnmounted(() => {
  systemStore.stopPolling();
  stopRefresh();
});
</script>

<style scoped>
.dashboard {
  max-width: 1600px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: var(--spacing-lg);
}

/* 共享卡片骨架 */
.card {
  background: var(--bg-card);
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-color-light);
  padding: var(--spacing-md) var(--spacing-lg);
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.card-head {
  display: flex;
  align-items: baseline;
  gap: var(--spacing-sm);
  margin-bottom: var(--spacing-md);
}
.card-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
}
.card-hint {
  font-size: 11px;
  color: var(--text-placeholder);
}
.card-link {
  font-size: 12px;
  font-weight: 500;
  color: var(--color-primary);
  cursor: pointer;
}
.card-link:hover {
  color: var(--color-primary-light);
}
.grow {
  flex: 1;
  min-width: 0;
}
.mono {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}

/* 行 1：结论条 */
.verdict-bar {
  display: flex;
  align-items: center;
  gap: var(--spacing-md);
  padding: var(--spacing-md) var(--spacing-lg);
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
  flex-wrap: wrap;
}
.verdict-lamp {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  flex: 1;
  min-width: 0;
}
.verdict-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  display: inline-block;
  background: var(--text-placeholder);
  flex-shrink: 0;
}
.verdict-bar.is-live .verdict-dot {
  background: var(--color-success);
  box-shadow: 0 0 0 4px var(--color-success-bg);
  animation: pulse 2s ease-in-out infinite;
}
.verdict-bar.is-error .verdict-dot {
  background: var(--color-danger);
  box-shadow: 0 0 0 4px var(--color-danger-bg);
}
.verdict-bar.is-warn .verdict-dot {
  background: var(--color-warning);
  box-shadow: 0 0 0 4px var(--color-warning-bg);
}
.verdict-bar.is-idle .verdict-dot {
  background: var(--color-info);
}
.verdict-label {
  font-size: 16px;
  font-weight: 700;
  color: var(--text-primary);
}
.verdict-bar.is-live .verdict-label {
  color: var(--color-success);
}
.verdict-bar.is-error .verdict-label {
  color: var(--color-danger);
}
.verdict-bar.is-warn .verdict-label {
  color: var(--color-warning);
}
.verdict-detail {
  font-size: 13px;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.verdict-meta {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  flex-shrink: 0;
  flex-wrap: wrap;
}
.verdict-proactive {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.verdict-meta-item {
  font-size: 12px;
  color: var(--text-secondary);
}
.verdict-meta-sub {
  font-size: 11px;
  color: var(--text-placeholder);
  margin-left: 2px;
}
.verdict-meta-sep {
  color: var(--text-placeholder);
  font-size: 12px;
}
.verdict-eventlog {
  margin-left: var(--spacing-sm);
}

@keyframes pulse {
  0%,
  100% {
    opacity: 1;
    transform: scale(1);
  }
  50% {
    opacity: 0.55;
    transform: scale(0.85);
  }
}

/* 行 2：主网格 */
.main-grid {
  display: grid;
  grid-template-columns: 2fr 1fr;
  gap: var(--spacing-lg);
  min-height: 0;
}

.feed-card {
  min-height: 460px;
}
.feed-scroll {
  flex: 1;
  min-height: 0;
  max-height: 460px;
  overflow-y: auto;
  margin: 0 calc(var(--spacing-sm) * -1);
  padding: 0 var(--spacing-sm);
}

.side-column {
  display: grid;
  grid-template-rows: auto auto;
  gap: var(--spacing-lg);
  min-height: 0;
}

/* 主播体征 */
.vitals-card {
  min-height: 200px;
}
.vitals-empty {
  flex: 1;
  display: grid;
  place-items: center;
  color: var(--text-placeholder);
  font-size: 13px;
  padding: var(--spacing-xl) 0;
}
.vitals-body {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
}
.heartbeat-row {
  display: flex;
  align-items: center;
  gap: var(--spacing-md);
}
.heartbeat-label {
  font-size: 12px;
  color: var(--text-secondary);
}
.heartbeat-time {
  font-size: 14px;
  font-weight: 600;
  padding: 2px 10px;
  border-radius: var(--radius-sm);
}
.heartbeat-time.tone-live {
  background: var(--color-success-bg);
  color: var(--color-success);
}
.heartbeat-time.tone-fresh {
  background: var(--color-warning-bg);
  color: var(--color-warning);
}
.heartbeat-time.tone-stale {
  background: var(--color-danger-bg);
  color: var(--color-danger);
}
.heartbeat-time.tone-silent {
  background: var(--bg-hover);
  color: var(--text-placeholder);
}
.funnel-row,
.fail-row {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
}
.fail-row {
  gap: var(--spacing-lg);
  padding-top: var(--spacing-xs);
  border-top: 1px dashed var(--border-color-light);
}
.funnel-row {
  flex-wrap: wrap;
}
.funnel-step {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 56px;
}
.funnel-value {
  font-size: 20px;
  font-weight: 700;
  color: var(--text-primary);
}
.funnel-label,
.fail-label {
  font-size: 11px;
  color: var(--text-secondary);
}
.funnel-arrow {
  color: var(--text-placeholder);
  font-size: 16px;
  align-self: center;
  padding-bottom: 12px;
}
.funnel-rate {
  margin-left: auto;
  font-size: 12px;
  color: var(--text-secondary);
}
.fail-item {
  display: flex;
  align-items: baseline;
  gap: 6px;
}
.fail-value {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-secondary);
}
.fail-value.is-warn {
  color: var(--color-danger);
}

/* 行 3：今日统计条 */
.stats-strip {
  display: flex;
  align-items: stretch;
  gap: var(--spacing-md);
  padding: var(--spacing-md) var(--spacing-lg);
  background: var(--bg-card);
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-color-light);
  flex-wrap: wrap;
}
.stat-block {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  min-width: 64px;
}
.stat-num {
  font-size: 18px;
  font-weight: 700;
  color: var(--text-primary);
  line-height: 1.2;
}
.stat-key {
  font-size: 11px;
  color: var(--text-secondary);
}
.stat-cap {
  font-size: 10px;
  color: var(--text-placeholder);
}
.stat-divider {
  width: 1px;
  align-self: stretch;
  background: var(--border-color-light);
}
.stat-strip-tail {
  margin-left: auto;
  align-self: center;
  font-size: 11px;
  color: var(--text-placeholder);
}

/* 行 4：基座一行 */
.infra-row {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  padding: var(--spacing-sm) var(--spacing-md);
  font-size: 12px;
  color: var(--text-secondary);
}
.infra-link {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  color: var(--text-regular);
  cursor: pointer;
  padding: 2px 6px;
  border-radius: var(--radius-sm);
  transition: background var(--transition-fast);
}
.infra-link:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}
.infra-link.is-disabled,
.infra-idle,
.infra-sep,
.infra-hint {
  color: var(--text-placeholder);
}
.infra-idle {
  font-size: 11px;
}
.infra-link .mono.is-warn {
  color: var(--color-danger);
  font-weight: 700;
}
.infra-hint {
  font-size: 14px;
}

/* 响应式 */
@media (max-width: 1200px) {
  .main-grid {
    grid-template-columns: 1fr;
  }
  .feed-card,
  .feed-scroll {
    max-height: none;
    min-height: 0;
  }
  .feed-scroll {
    max-height: 420px;
  }
}
@media (max-width: 768px) {
  .verdict-bar {
    flex-direction: column;
    align-items: flex-start;
  }
  .verdict-meta {
    width: 100%;
  }
  .stats-strip {
    gap: var(--spacing-sm);
  }
  .stat-divider {
    display: none;
  }
}
</style>
