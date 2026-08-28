<template>
  <div class="show-observer">
    <!-- ============================================================ -->
    <!-- 顶栏：页面身份 + 实时脉冲 + 台上时钟                            -->
    <!-- ============================================================ -->
    <header class="show-head">
      <span class="pulse" :class="wsConnected ? 'is-live' : 'is-dead'" aria-hidden="true" />
      <h1 class="show-title">直播间观察</h1>
      <span class="show-tagline">综合实时演出视角 · 观众与主播同轴</span>
      <span class="grow" />
      <span class="show-feed" :class="{ 'is-dead': !wsConnected }">
        {{ wsConnected ? '实时' : '连接中断' }}
      </span>
      <time class="show-clock mono">{{ wallClock }}</time>
    </header>

    <!-- ============================================================ -->
    <!-- 常驻横幅：当前 Agenda 环节（不参与滚动）                        -->
    <!-- ============================================================ -->
    <section class="slate" :class="{ 'is-idle': !agenda }" aria-label="当前环节">
      <span class="slate-eyebrow">当前环节</span>
      <template v-if="agenda">
        <span class="slate-order mono">#{{ agenda.order }}</span>
        <h2 class="slate-label" :title="agenda.label">{{ agenda.label }}</h2>
        <span class="slate-action">{{ agenda.actionLabel }}</span>
        <span v-if="agenda.note" class="slate-note" :title="agenda.note">{{ agenda.note }}</span>
        <span class="grow" />
        <span v-if="agenda.startLabel" class="slate-meta mono">计划 {{ agenda.startLabel }}</span>
        <span v-if="agenda.expectedLabel" class="slate-meta mono">
          预计 {{ agenda.expectedLabel }}
        </span>
        <span class="slate-meta mono">{{ relativeTime(agenda.changedAtSec) }}</span>
      </template>
      <span v-else class="slate-idle">节目单未运行或未接入</span>
    </section>

    <!-- ============================================================ -->
    <!-- 演出时间线：单条合并流，最新在下方                              -->
    <!-- ============================================================ -->
    <section class="stage" aria-label="演出时间线">
      <header class="stage-bar" :class="{ 'is-paused': paused }">
        <h2 class="stage-title">演出时间线</h2>
        <span class="stage-hint">{{ paused ? '已暂停 · 事件仍在后台累积' : '最新在下方' }}</span>
        <span class="grow" />
        <span class="stage-count mono">{{ entries.length }} / {{ MAX_ENTRIES }}</span>
        <el-button size="small" :type="paused ? 'primary' : 'default'" @click="togglePause">
          {{ paused ? '继续' : '暂停' }}
        </el-button>
        <el-button size="small" :disabled="entries.length === 0" @click="clearTimeline">
          清空
        </el-button>
      </header>

      <div class="stage-body">
        <div ref="scrollRef" class="stage-scroll" @scroll.passive="onScroll">
          <div v-if="entries.length === 0" class="stage-empty">
            <el-icon class="stage-empty-icon"><VideoCamera /></el-icon>
            <p class="stage-empty-text">静候直播开始——弹幕与主播动作将在此实时呈现</p>
          </div>

          <ol v-else class="feed">
            <li v-for="entry in entries" :key="entry.id" class="feed-row">
              <!-- 环节推进：横贯分隔行 -->
              <div v-if="entry.kind === 'agenda'" class="beat">
                <span class="beat-rule" aria-hidden="true" />
                <span class="beat-body">
                  <span class="beat-eyebrow">环节</span>
                  <span class="beat-label">{{ entry.text }}</span>
                  <span class="beat-action">{{ entry.badge }}</span>
                  <span v-if="entry.note" class="beat-note">{{ entry.note }}</span>
                </span>
                <span class="beat-rule" aria-hidden="true" />
                <time class="stamp mono">{{ relativeTime(entry.tsSec) }}</time>
              </div>

              <!-- 里程碑：庆祝行 -->
              <div v-else-if="entry.kind === 'milestone'" class="milestone">
                <span class="milestone-mark" aria-hidden="true">★</span>
                <div class="milestone-body">
                  <p class="milestone-text">{{ entry.text }}</p>
                  <p v-if="entry.note" class="milestone-meta mono">{{ entry.note }}</p>
                </div>
                <time class="stamp mono">{{ relativeTime(entry.tsSec) }}</time>
              </div>

              <!-- 进场：安静单行 -->
              <div v-else-if="entry.kind === 'enter'" class="whisper">
                <span class="whisper-dot" aria-hidden="true" />
                <span class="whisper-text">{{ entry.text }}</span>
                <span class="grow" />
                <time class="stamp mono">{{ relativeTime(entry.tsSec) }}</time>
              </div>

              <!-- 主播动作（工具结果）：脱轴右靠 -->
              <div
                v-else-if="entry.kind === 'tool'"
                class="act"
                :class="{ 'is-failed': entry.failed, 'is-speak': entry.speak }"
              >
                <div class="act-head">
                  <span class="act-kind">主播</span>
                  <code class="act-tool mono">{{ entry.actor }}</code>
                  <span class="act-arrow" aria-hidden="true">→</span>
                  <span v-if="entry.badge" class="act-badge">{{ entry.badge }}</span>
                  <span class="grow" />
                  <time class="stamp mono">{{ relativeTime(entry.tsSec) }}</time>
                </div>
                <p class="act-text">{{ entry.text }}</p>
                <p v-if="entry.note" class="act-note">{{ entry.note }}</p>
              </div>

              <!-- 观众发声：弹幕 / 礼物 / SC -->
              <div v-else class="chat" :class="`chat--${entry.kind}`">
                <span class="avatar" aria-hidden="true">{{ entry.initial }}</span>
                <div class="bubble">
                  <div class="bubble-head">
                    <span class="who" :title="entry.actor">{{ entry.actor }}</span>
                    <span v-if="entry.badge" class="chip">{{ entry.badge }}</span>
                    <span v-if="entry.money" class="money mono">{{ entry.money }}</span>
                    <span class="grow" />
                    <time class="stamp mono">{{ relativeTime(entry.tsSec) }}</time>
                  </div>
                  <p class="say">{{ entry.text }}</p>
                </div>
              </div>
            </li>
          </ol>
        </div>

        <button v-if="unseen > 0" type="button" class="jump" @click="jumpToLatest">
          {{ unseen >= 99 ? '99+' : unseen }} 条新演出 · 回到最新 ↓
        </button>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
/**
 * 直播间观察 —— 演出时间线（综合实时演出视角）
 *
 * 本页是 Dashboard 里**唯一**把多个语义域合并成一条时间线的视图（其余页面均为单域视角）：
 * 观众的弹幕/礼物/SC/进场、主播的工具动作、节目单环节推进、游戏里程碑，
 * 按时间先后汇成一条流（最新沉底、自动滚动），供运营在第二屏常开盯场。
 *
 * 数据来源：events store（全局 WS 事件缓冲，main.ts 已启动订阅），只读消费。
 * 渲染字段一律取自后端真实 Payload（src/modules/events/payloads/）：
 * - room.message.*  → message_type / user{id,name} / content / gift{name,count} / sc{amount}
 * - tool.result.*   → tool_name / status / result / error_message
 * - agenda.update   → action / item{order,label,note,starts_at_ms,expected_ms} / changed_at_ms
 * - game.milestone  → message / game / scene
 * 字段缺失时回落到共享的 summarizeEvent()，不臆造字段。
 *
 * 跨段关联（把一条弹幕与后续动作串成链）暂不做：事件负载尚无关联键，属后端待办票。
 */
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue';
import { storeToRefs } from 'pinia';
import { VideoCamera } from '@element-plus/icons-vue';
import { useEventsStore, useWebSocketStore } from '@/stores';
import { summarizeEvent } from '@/utils/eventSummary';
import type { WebSocketMessage } from '@/types';

// ============================================================
// 常量
// ============================================================

const MAX_ENTRIES = 200;
/** 距底 ≤ 此距离视为"贴底"，可自动跟随 */
const BOTTOM_THRESHOLD_PX = 40;
/** 结果载荷里可作"主播说了什么"的字段候选（按优先级） */
const TOOL_TEXT_KEYS = ['speech_text', 'text', 'speech', 'content', 'message', 'summary'] as const;

const AGENDA_ACTION_LABEL: Record<string, string> = {
  done: '已完成',
  schedule: '已改期',
  insert: '新增环节',
};

// ============================================================
// 类型
// ============================================================

type EntryKind = 'danmaku' | 'gift' | 'super_chat' | 'enter' | 'tool' | 'agenda' | 'milestone';

/** 事件缓冲条目：events store 在 WebSocketMessage 上补了去重 id */
type FeedEvent = WebSocketMessage & { id: string };

/** 时间线条目（view-ready，模板不再碰原始 payload） */
interface ShowEntry {
  id: string;
  kind: EntryKind;
  /** Unix 秒（后端事件 timestamp 为秒，毫秒亦兼容） */
  tsSec: number;
  /** 观众昵称 / 工具名 / 环节名 */
  actor: string;
  /** 主体文案 */
  text: string;
  /** 次要文案（错误信息 / 环节备注 / 游戏场景） */
  note: string;
  /** 类型徽标（礼物 / SC / 失败 / 环节动作） */
  badge: string;
  /** 金额强调（¥50） */
  money: string;
  failed: boolean;
  speak: boolean;
  /** 头像首字 */
  initial: string;
}

interface AgendaBanner {
  order: number;
  label: string;
  actionLabel: string;
  note: string;
  startLabel: string;
  expectedLabel: string;
  changedAtSec: number;
}

// ============================================================
// Store
// ============================================================

const eventsStore = useEventsStore();
const wsStore = useWebSocketStore();
const { events } = storeToRefs(eventsStore);
const { isConnected: wsConnected } = storeToRefs(wsStore);

// ============================================================
// 通用取值助手
// ============================================================

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function str(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function num(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

/** 时间戳归一到 Unix 秒（后端为秒；毫秒值兜底换算） */
function toSeconds(value: number): number {
  return value > 1e12 ? value / 1000 : value;
}

function formatAmount(amount: number): string {
  return Number.isInteger(amount) ? String(amount) : amount.toFixed(2);
}

function clockLabel(ms: number): string {
  return new Date(ms).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

function durationLabel(ms: number): string {
  const minutes = Math.round(ms / 60000);
  if (minutes < 1) return '<1m';
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest > 0 ? `${hours}h${rest}m` : `${hours}h`;
}

function pickToolText(result: unknown): string {
  if (!isRecord(result)) return '';
  for (const key of TOOL_TEXT_KEYS) {
    const text = str(result[key]);
    if (text) return text;
  }
  return '';
}

/** RoomMessageUser → 展示名（与 summarizeEvent 的取名口径一致） */
function userLabel(value: unknown): string {
  if (!isRecord(value)) return '匿名观众';
  const name = str(value.name);
  if (name) return name;
  const id = str(value.id);
  return id ? `#${id}` : '匿名观众';
}

function initialOf(actor: string): string {
  const chars = Array.from(actor.replace(/^#/, ''));
  return chars.length > 0 ? chars[0].toUpperCase() : '?';
}

// ============================================================
// 事件 → 时间线条目
// ============================================================

function makeEntry(base: {
  id: string;
  kind: EntryKind;
  tsSec: number;
  actor?: string;
  text: string;
  note?: string;
  badge?: string;
  money?: string;
  failed?: boolean;
  speak?: boolean;
}): ShowEntry {
  const actor = base.actor ?? '';
  return {
    id: base.id,
    kind: base.kind,
    tsSec: base.tsSec,
    actor,
    text: base.text,
    note: base.note ?? '',
    badge: base.badge ?? '',
    money: base.money ?? '',
    failed: base.failed ?? false,
    speak: base.speak ?? false,
    initial: initialOf(actor),
  };
}

/** 观众行为流：room.message.*（RoomMessagePayload，message_type 判别） */
function fromRoomMessage(event: FeedEvent, data: Record<string, unknown>): ShowEntry {
  const tsSec = toSeconds(event.timestamp);
  const actor = userLabel(data.user);
  const content = str(data.content);
  const fallback = () => content || summarizeEvent(event.type, data);
  const messageType = str(data.message_type) || event.type.slice('room.message.'.length);

  if (messageType === 'gift') {
    const gift = isRecord(data.gift) ? data.gift : null;
    const giftName = gift ? str(gift.name) : '';
    const count = (gift ? num(gift.count) : null) ?? 1;
    return makeEntry({
      id: event.id,
      kind: 'gift',
      tsSec,
      actor,
      text: giftName ? `送出 ${giftName} ×${count}` : fallback(),
      badge: '礼物',
    });
  }

  if (messageType === 'super_chat') {
    const sc = isRecord(data.sc) ? data.sc : null;
    const amount = sc ? num(sc.amount) : null;
    return makeEntry({
      id: event.id,
      kind: 'super_chat',
      tsSec,
      actor,
      text: fallback(),
      badge: 'SC',
      money: amount != null ? `¥${formatAmount(amount)}` : '',
    });
  }

  if (messageType === 'enter') {
    return makeEntry({
      id: event.id,
      kind: 'enter',
      tsSec,
      actor,
      text: `${actor} 进入直播间`,
    });
  }

  return makeEntry({
    id: event.id,
    kind: 'danmaku',
    tsSec,
    actor,
    text: fallback(),
  });
}

/** 主播动作：tool.result.*（ToolResultPayload） */
function fromToolResult(event: FeedEvent, data: Record<string, unknown>): ShowEntry {
  const toolName = str(data.tool_name) || event.type.slice('tool.result.'.length) || 'tool';
  const status = str(data.status);
  const failed = status === 'error';
  const spoken = pickToolText(data.result);
  const statusText = status ? (failed ? '执行失败' : '执行完成') : '';
  return makeEntry({
    id: event.id,
    kind: 'tool',
    tsSec: toSeconds(event.timestamp),
    actor: toolName,
    text: spoken || statusText || summarizeEvent(event.type, data),
    note: failed ? str(data.error_message) : '',
    badge: failed ? '失败' : '',
    failed,
    speak: toolName === 'speak',
  });
}

/** 环节推进：agenda.update（AgendaPayload） */
function fromAgenda(event: FeedEvent, data: Record<string, unknown>): ShowEntry {
  const item = isRecord(data.item) ? data.item : {};
  const action = str(data.action);
  return makeEntry({
    id: event.id,
    kind: 'agenda',
    tsSec: toSeconds(event.timestamp),
    text: str(item.label) || summarizeEvent(event.type, data) || '未命名环节',
    note: str(item.note),
    badge: AGENDA_ACTION_LABEL[action] ?? action,
  });
}

/** 里程碑：game.milestone（GamePayload） */
function fromMilestone(event: FeedEvent, data: Record<string, unknown>): ShowEntry {
  const meta = [str(data.game), str(data.scene)].filter(Boolean).join(' · ');
  return makeEntry({
    id: event.id,
    kind: 'milestone',
    tsSec: toSeconds(event.timestamp),
    text: str(data.message) || summarizeEvent(event.type, data),
    note: meta,
  });
}

/** 非演出事件（planner.* / live.* / core.* / system.* 等）返回 null，不进时间线 */
function toEntry(event: FeedEvent): ShowEntry | null {
  const data = isRecord(event.data) ? event.data : {};
  if (event.type.startsWith('room.message.')) return fromRoomMessage(event, data);
  if (event.type.startsWith('tool.result.')) return fromToolResult(event, data);
  if (event.type === 'agenda.update') return fromAgenda(event, data);
  if (event.type === 'game.milestone') return fromMilestone(event, data);
  return null;
}

// ============================================================
// 时间线状态：暂停 / 清空水位 / 条目缓冲
// ============================================================

const paused = ref(false);
/** 清空水位：记下当时缓冲区里的事件 id，之后重建时永久跳过（store 仍不丢数据） */
const hiddenIds = ref<Set<string>>(new Set());
const entries = ref<ShowEntry[]>([]);

watch(
  [events, paused, hiddenIds],
  ([list, isPaused, hidden]) => {
    if (isPaused) return;
    const next: ShowEntry[] = [];
    for (const event of list) {
      if (hidden.has(event.id)) continue;
      const entry = toEntry(event as FeedEvent);
      if (entry) next.push(entry);
    }
    entries.value = next.slice(-MAX_ENTRIES);
  },
  { immediate: true },
);

function togglePause(): void {
  paused.value = !paused.value;
}

function clearTimeline(): void {
  hiddenIds.value = new Set(events.value.map(event => event.id));
  entries.value = [];
  unseen.value = 0;
}

// ============================================================
// 当前环节横幅：取最近一条 agenda.update
// ============================================================

const agenda = computed<AgendaBanner | null>(() => {
  const list = events.value;
  for (let i = list.length - 1; i >= 0; i -= 1) {
    const event = list[i];
    if (event.type !== 'agenda.update') continue;
    const data = isRecord(event.data) ? event.data : {};
    const item = isRecord(data.item) ? data.item : {};
    const action = str(data.action);
    const startsAtMs = num(item.starts_at_ms);
    const expectedMs = num(item.expected_ms);
    const changedAtMs = num(data.changed_at_ms);
    return {
      order: num(item.order) ?? 0,
      label: str(item.label) || '未命名环节',
      actionLabel: AGENDA_ACTION_LABEL[action] ?? (action || '进行中'),
      note: str(item.note),
      startLabel: startsAtMs != null ? clockLabel(startsAtMs) : '',
      expectedLabel: expectedMs != null && expectedMs > 0 ? durationLabel(expectedMs) : '',
      changedAtSec: changedAtMs != null ? changedAtMs / 1000 : toSeconds(event.timestamp),
    };
  }
  return null;
});

// ============================================================
// 滚动跟随：贴底自动跟随；上滚时冒出"回到最新"
// ============================================================

const scrollRef = ref<HTMLElement | null>(null);
const atBottom = ref(true);
const unseen = ref(0);

function isAtBottom(el: HTMLElement): boolean {
  return el.scrollHeight - el.scrollTop - el.clientHeight <= BOTTOM_THRESHOLD_PX;
}

function onScroll(): void {
  const el = scrollRef.value;
  if (!el) return;
  atBottom.value = isAtBottom(el);
  if (atBottom.value) unseen.value = 0;
}

function scrollToBottom(): void {
  const el = scrollRef.value;
  if (!el) return;
  el.scrollTop = el.scrollHeight;
}

function jumpToLatest(): void {
  atBottom.value = true;
  unseen.value = 0;
  scrollToBottom();
}

/** 新增条目数：以上一帧末条 id 为锚，找不到锚点则视为全新 */
function countAdded(next: ShowEntry[], prev: ShowEntry[]): number {
  const anchor = prev.length > 0 ? prev[prev.length - 1].id : null;
  if (!anchor) return next.length;
  const index = next.findIndex(entry => entry.id === anchor);
  return index === -1 ? next.length : next.length - 1 - index;
}

watch(entries, async (next, prev) => {
  const added = countAdded(next, prev ?? []);
  await nextTick();
  if (atBottom.value) {
    scrollToBottom();
    unseen.value = 0;
    return;
  }
  if (added > 0) unseen.value += added;
});

// ============================================================
// 秒级时钟：驱动相对时间与台上时钟刷新
// ============================================================

const nowTick = ref(Date.now());
let tickTimer: ReturnType<typeof setInterval> | null = null;

const wallClock = computed(() =>
  new Date(nowTick.value).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }),
);

function relativeTime(tsSec: number): string {
  const diff = Math.max(0, Math.floor(nowTick.value / 1000 - tsSec));
  if (diff < 5) return '刚刚';
  if (diff < 60) return `${diff}s 前`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m 前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h 前`;
  return `${Math.floor(diff / 86400)}d 前`;
}

// ============================================================
// 生命周期
// ============================================================

onMounted(async () => {
  tickTimer = setInterval(() => {
    nowTick.value = Date.now();
  }, 1000);
  await nextTick();
  scrollToBottom();
});

onUnmounted(() => {
  if (tickTimer) {
    clearInterval(tickTimer);
    tickTimer = null;
  }
});
</script>

<style scoped>
/* ============================================================ */
/* 版面：顶栏 / 环节横幅 常驻，时间线独占剩余高度                  */
/* ============================================================ */
.show-observer {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
  height: calc(100vh - var(--header-height) - 2 * var(--spacing-lg));
  min-height: 560px;
}

.grow {
  flex: 1;
  min-width: 0;
}

.mono {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}

/* ============================================================ */
/* 顶栏                                                          */
/* ============================================================ */
.show-head {
  display: flex;
  align-items: baseline;
  gap: var(--spacing-sm);
  flex-shrink: 0;
}

.show-title {
  margin: 0;
  font-size: 22px;
  font-weight: 650;
  letter-spacing: -0.2px;
  color: var(--text-primary);
}

.show-tagline {
  font-size: 12px;
  color: var(--text-secondary);
  letter-spacing: 0.2px;
}

.pulse {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  flex-shrink: 0;
  align-self: center;
  background: var(--text-placeholder);
}
.pulse.is-live {
  background: var(--color-danger);
  animation: onAir 2s ease-in-out infinite;
}
.pulse.is-dead {
  background: var(--text-placeholder);
}

@keyframes onAir {
  0%,
  100% {
    box-shadow: 0 0 0 0 var(--color-danger-bg);
    opacity: 1;
  }
  50% {
    box-shadow: 0 0 0 5px var(--color-danger-bg);
    opacity: 0.65;
  }
}

.show-feed {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 1.4px;
  text-transform: uppercase;
  color: var(--color-danger);
}
.show-feed.is-dead {
  color: var(--text-placeholder);
}

.show-clock {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-secondary);
}

/* ============================================================ */
/* 环节横幅（常驻，不滚动）                                       */
/* ============================================================ */
.slate {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  flex-shrink: 0;
  min-height: 52px;
  padding: var(--spacing-sm) var(--spacing-md);
  border: 1px solid var(--border-color-light);
  border-left: 3px solid var(--color-agenda);
  border-radius: var(--radius-md);
  background: var(--color-agenda-bg);
}
.slate.is-idle {
  border-left-color: var(--border-color-dark);
  background: var(--bg-card);
}

.slate-eyebrow {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 1.6px;
  color: var(--color-agenda);
  flex-shrink: 0;
}
.slate.is-idle .slate-eyebrow {
  color: var(--text-placeholder);
}

.slate-order {
  font-size: 11px;
  color: var(--text-secondary);
  flex-shrink: 0;
}

.slate-label {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 40%;
}

.slate-action {
  padding: 1px 8px;
  border-radius: 999px;
  border: 1px solid var(--color-agenda);
  font-size: 11px;
  font-weight: 600;
  color: var(--color-agenda);
  flex-shrink: 0;
}

.slate-note {
  font-size: 12px;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 28%;
}

.slate-meta {
  font-size: 11px;
  color: var(--text-secondary);
  flex-shrink: 0;
}

.slate-idle {
  font-size: 13px;
  color: var(--text-placeholder);
}

/* ============================================================ */
/* 舞台容器                                                      */
/* ============================================================ */
.stage {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
  overflow: hidden;
}

.stage-bar {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  flex-shrink: 0;
  padding: 10px var(--spacing-md);
  border-bottom: 1px solid var(--border-color-light);
  transition: background var(--transition-normal);
}
.stage-bar.is-paused {
  background: var(--color-warning-bg);
}

.stage-title {
  margin: 0;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 1.2px;
  color: var(--text-primary);
}

.stage-hint {
  font-size: 11px;
  color: var(--text-secondary);
}

.stage-count {
  font-size: 11px;
  color: var(--text-secondary);
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  background: var(--bg-hover);
}

/* ============================================================ */
/* 滚动体 + 顶部渐隐 + 回到最新                                   */
/* ============================================================ */
.stage-body {
  position: relative;
  flex: 1;
  min-height: 0;
}
.stage-body::before {
  content: '';
  position: absolute;
  inset: 0 0 auto 0;
  height: 18px;
  z-index: 2;
  pointer-events: none;
  background: linear-gradient(to bottom, var(--bg-card), transparent);
}

.stage-scroll {
  height: 100%;
  overflow-y: auto;
  padding: var(--spacing-md) var(--spacing-md) var(--spacing-lg);
}
.stage-scroll::-webkit-scrollbar {
  width: 6px;
}
.stage-scroll::-webkit-scrollbar-thumb {
  background: var(--border-color-dark);
  border-radius: 3px;
}

.jump {
  position: absolute;
  bottom: var(--spacing-md);
  left: 50%;
  transform: translateX(-50%);
  z-index: 3;
  padding: 5px 14px;
  border: 1px solid var(--color-primary);
  border-radius: 999px;
  background: var(--bg-elevated);
  color: var(--color-primary);
  font-family: inherit;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  box-shadow: var(--shadow-md);
  transition:
    background var(--transition-fast),
    transform var(--transition-fast);
}
.jump:hover {
  background: var(--bg-active);
  transform: translateX(-50%) translateY(-1px);
}

/* ============================================================ */
/* 空态                                                          */
/* ============================================================ */
.stage-empty {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--spacing-sm);
}
.stage-empty-icon {
  font-size: 44px;
  color: var(--border-color-dark);
}
.stage-empty-text {
  margin: 0;
  font-size: 13px;
  letter-spacing: 0.3px;
  color: var(--text-placeholder);
}

/* ============================================================ */
/* 流：左侧时间轴脊线，观众沿轴、主播脱轴右靠                      */
/* ============================================================ */
.feed {
  position: relative;
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.feed::before {
  content: '';
  position: absolute;
  top: 4px;
  bottom: 4px;
  left: 14px;
  width: 1px;
  background: var(--border-color);
}

.feed-row {
  display: flex;
  flex-direction: column;
  animation: rowIn 0.22s cubic-bezier(0.33, 1, 0.68, 1);
}

@keyframes rowIn {
  from {
    opacity: 0;
    transform: translateY(5px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.stamp {
  font-size: 10px;
  color: var(--text-placeholder);
  flex-shrink: 0;
  white-space: nowrap;
}

/* ============================================================ */
/* 观众发声：气泡                                                */
/* ============================================================ */
.chat {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  max-width: 82%;
}

.avatar {
  width: 28px;
  height: 28px;
  flex-shrink: 0;
  border-radius: 50%;
  display: grid;
  place-items: center;
  font-size: 11px;
  font-weight: 700;
  background: var(--bg-card);
  border: 1px solid var(--color-collector);
  color: var(--color-collector);
  box-shadow: 0 0 0 3px var(--bg-card);
  z-index: 1;
}

.bubble {
  flex: 1;
  min-width: 0;
  padding: 7px 12px;
  border-radius: 4px 12px 12px 12px;
  background: var(--bg-hover);
}

.bubble-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 2px;
}

.who {
  font-size: 11px;
  font-weight: 600;
  color: var(--color-collector);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 220px;
}

.chip {
  padding: 0 6px;
  border-radius: var(--radius-sm);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.6px;
  background: var(--bg-card);
  color: var(--text-secondary);
  flex-shrink: 0;
}

.money {
  font-size: 12px;
  font-weight: 700;
  flex-shrink: 0;
}

.say {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-regular);
  white-space: pre-wrap;
  word-break: break-word;
}

/* --- 礼物：暖色高亮 --- */
.chat--gift {
  max-width: 88%;
}
.chat--gift .avatar {
  border-color: var(--color-warning);
  color: var(--color-warning);
}
.chat--gift .bubble {
  background: var(--color-warning-bg);
  border-left: 2px solid var(--color-warning);
  box-shadow: var(--shadow-sm);
}
.chat--gift .who {
  color: var(--color-warning);
}
.chat--gift .say {
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
}

/* --- SC：最强高亮（暖色渐变 + 金额） --- */
.chat--super_chat {
  max-width: 92%;
}
.chat--super_chat .avatar {
  border-color: var(--color-danger);
  color: var(--color-danger);
}
.chat--super_chat .bubble {
  padding: 10px 14px;
  border-left: 3px solid var(--color-danger);
  background: linear-gradient(100deg, var(--color-danger-bg), var(--color-warning-bg));
  box-shadow: var(--shadow-md);
}
.chat--super_chat .who {
  font-size: 12px;
  color: var(--color-danger);
}
.chat--super_chat .chip {
  background: var(--color-danger);
  color: var(--text-inverse);
}
.chat--super_chat .money {
  font-size: 14px;
  color: var(--color-danger);
}
.chat--super_chat .say {
  font-size: 15px;
  font-weight: 500;
  line-height: 1.55;
  color: var(--text-primary);
}

/* ============================================================ */
/* 进场：安静单行                                                */
/* ============================================================ */
.whisper {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 18px;
}
/* 占位宽度与头像一致（28px），使圆点正落在时间轴脊线上 */
.whisper-dot {
  width: 28px;
  height: 12px;
  flex-shrink: 0;
  display: grid;
  place-items: center;
  z-index: 1;
}
.whisper-dot::before {
  content: '';
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--text-placeholder);
  box-shadow: 0 0 0 3px var(--bg-card);
}
.whisper-text {
  font-size: 11px;
  color: var(--text-placeholder);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ============================================================ */
/* 主播动作：脱轴右靠                                            */
/* ============================================================ */
.act {
  align-self: flex-end;
  max-width: 78%;
  min-width: 240px;
  padding: 8px 12px;
  border-radius: 12px 4px 12px 12px;
  background: var(--color-tool-bg);
  border-right: 2px solid var(--color-tool);
}
.act.is-speak {
  padding: 10px 14px;
  box-shadow: var(--shadow-sm);
}
.act.is-failed {
  background: var(--color-danger-bg);
  border-right-color: var(--color-danger);
}

.act-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 3px;
}

.act-kind {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 1.4px;
  color: var(--color-tool);
  flex-shrink: 0;
}
.act.is-failed .act-kind {
  color: var(--color-danger);
}

.act-tool {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.act-arrow {
  font-size: 11px;
  color: var(--text-placeholder);
  flex-shrink: 0;
}

.act-badge {
  padding: 0 6px;
  border-radius: var(--radius-sm);
  font-size: 10px;
  font-weight: 700;
  background: var(--color-danger);
  color: var(--text-inverse);
  flex-shrink: 0;
}

.act-text {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-regular);
  white-space: pre-wrap;
  word-break: break-word;
}
.act.is-speak .act-text {
  font-size: 15px;
  font-weight: 500;
  color: var(--text-primary);
}
.act.is-speak .act-text::before {
  content: '「';
  color: var(--color-tool);
}
.act.is-speak .act-text::after {
  content: '」';
  color: var(--color-tool);
}

.act-note {
  margin: 4px 0 0;
  font-size: 11px;
  line-height: 1.5;
  color: var(--color-danger);
  word-break: break-word;
}

/* ============================================================ */
/* 环节推进：横贯分隔行                                          */
/* ============================================================ */
.beat {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 6px 0;
}
.beat-rule {
  height: 1px;
  background: var(--color-agenda);
  opacity: 0.45;
}
.beat-rule:first-child {
  width: 24px;
  flex-shrink: 0;
}
.beat-rule:last-of-type {
  flex: 1;
}
.beat-body {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.beat-eyebrow {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 1.6px;
  color: var(--color-agenda);
  flex-shrink: 0;
}
.beat-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.beat-action {
  padding: 0 7px;
  border-radius: 999px;
  font-size: 10px;
  font-weight: 700;
  background: var(--color-agenda-bg);
  color: var(--color-agenda);
  flex-shrink: 0;
}
.beat-note {
  font-size: 11px;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ============================================================ */
/* 里程碑：庆祝行                                                */
/* ============================================================ */
.milestone {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 4px 0;
  padding: 8px 14px;
  border-radius: var(--radius-md);
  background: var(--color-game-bg);
  border: 1px dashed var(--color-game);
}
.milestone-mark {
  font-size: 15px;
  color: var(--color-game);
  flex-shrink: 0;
}
.milestone-body {
  flex: 1;
  min-width: 0;
}
.milestone-text {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  word-break: break-word;
}
.milestone-meta {
  margin: 2px 0 0;
  font-size: 11px;
  color: var(--text-secondary);
}

/* ============================================================ */
/* 窄屏                                                          */
/* ============================================================ */
@media (max-width: 960px) {
  .show-tagline,
  .slate-note {
    display: none;
  }
  .chat,
  .chat--gift,
  .chat--super_chat,
  .act {
    max-width: 100%;
  }
  .slate-label {
    max-width: 55%;
  }
}
</style>
