// 运行总览（Dashboard 首页）的运营判定与数据合成纯逻辑
//
// 首页视图只负责取数与接线，判定规则集中在此处共享可测：
//   - 结论条状态机（verdictOf）：异常 > 降级 > 直播中 > 空闲
//   - 决策心跳（heartbeatOf）：最近一次 Planner 事件/主播发言距今的分级
//   - 活动脉搏（buildPulse）：60 分钟按分钟分桶的双系列计数
//   - 缓冲统计（bufferCounts）：room.message 按消息类型四桶计数
//
// 注意：所有时间戳以 Unix 毫秒为内部单位；"当前时刻"一律由调用方注入
// nowMs 参数，函数自身不取时钟；柱色等呈现值同样由调用方传入，保持纯函数。

import { EVENT, isPlannerEvent } from '@/types/events';
import type { LiveSessionListResponse, ToolEntry } from '@/types';
import type { FeedEvent } from './liveFeed';
import { formatTimeHM } from './format';

// 活动脉搏窗口

/** 活动脉搏桶数：近 60 分钟，每分钟一桶 */
export const PULSE_BUCKETS = 60;
/** 活动脉搏分桶宽度（毫秒）：按分钟分桶 */
export const PULSE_BUCKET_MS = 60_000;

// 结论条

/** core.error 判新的回看窗口（毫秒） */
export const ERROR_WINDOW_MS = 300_000;

export type VerdictTone = 'error' | 'warn' | 'live' | 'idle';

export interface Verdict {
  tone: VerdictTone;
  phrase: string;
  detail: string;
}

/** 采集器概要（componentApi.getAll 的 collectors 成员） */
export interface CollectorSummary {
  name: string;
  is_started: boolean;
  is_enabled: boolean;
}

/** verdictOf 的输入聚合：判定所需的四路快照 + 事件缓冲 */
export interface VerdictInput {
  tools: ToolEntry[];
  events: FeedEvent[];
  collectors: CollectorSummary[];
  streamerAvailable: boolean;
  sessions: LiveSessionListResponse | null;
}

/**
 * 结论条状态机：工具熔断 → 近 5 分钟 core.error → 启用未运行的采集器 →
 * 主播 Agent 未注册 → 活跃场次 → 空闲。判定顺序即优先级，文案逐条对应。
 */
export function verdictOf(input: VerdictInput, nowMs: number): Verdict {
  // a) 异常：工具熔断 或 最近 5 分钟内的 core.error
  const tripped = input.tools.filter(t => t.health?.state === 'tripped');
  if (tripped.length > 0) {
    const head = tripped[0];
    return { tone: 'error', phrase: '异常', detail: `工具 ${head.name} 熔断` };
  }
  const recentError = input.events.find(event => {
    if (event.type !== EVENT.coreError) return false;
    return nowMs - event.timestamp_ms <= ERROR_WINDOW_MS;
  });
  if (recentError) {
    return { tone: 'error', phrase: '异常', detail: '最近 5 分钟内出现系统错误' };
  }

  // b) 降级：启用了采集器但没跑 / 主播 Agent 未注册
  const idleCollectors = input.collectors.filter(c => c.is_enabled && !c.is_started);
  if (idleCollectors.length > 0) {
    const names = idleCollectors
      .slice(0, 3)
      .map(c => c.name)
      .join('、');
    return { tone: 'warn', phrase: '降级', detail: `采集器 ${names} 未运行` };
  }
  if (!input.streamerAvailable) {
    return { tone: 'warn', phrase: '降级', detail: '主播 Agent 未注册' };
  }
  // c) 直播中：存在活跃场次
  if (input.sessions?.active_session_id != null) {
    return { tone: 'live', phrase: '直播中', detail: '' };
  }
  // d) 空闲
  return { tone: 'idle', phrase: '空闲', detail: '系统就绪 · 等待场次' };
}

// 决策心跳

export type HeartbeatTone = 'live' | 'fresh' | 'stale' | 'silent';

export interface Heartbeat {
  text: string;
  tone: HeartbeatTone;
}

/**
 * 决策心跳：事件流里最近一次 Planner 事件或主播发言距今的分级。
 * <60s 活跃 / <5m 新鲜 / <1h 沉寂 / 更久或从未触发为静默。
 */
export function heartbeatOf(events: FeedEvent[], nowMs: number): Heartbeat {
  let latest = 0;
  for (const event of events) {
    if (event.type === EVENT.streamerStage) continue;
    if (!isPlannerEvent(event.type) && event.type !== EVENT.streamerSpeech) continue;
    if (event.timestamp_ms > latest) latest = event.timestamp_ms;
  }
  if (latest === 0) return { text: '尚未触发', tone: 'silent' };
  const diffSec = Math.max(0, Math.floor((nowMs - latest) / 1000));
  if (diffSec < 60) return { text: `${diffSec}s 前`, tone: 'live' };
  if (diffSec < 300)
    return { text: `${Math.floor(diffSec / 60)}m ${diffSec % 60}s 前`, tone: 'fresh' };
  const text =
    diffSec < 3600 ? `${Math.floor(diffSec / 60)}m 前` : `${Math.floor(diffSec / 3600)}h 前`;
  return { text, tone: 'stale' };
}

// 活动脉搏

export interface PulseSeries {
  label: string;
  color: string;
  values: number[];
}

export interface BucketWindow {
  labels: string[];
  series: PulseSeries[];
}

/** 活动脉搏双系列的柱色（调用方从图表调色板运行时取值后传入） */
export interface PulseColors {
  audience: string;
  streamer: string;
}

/**
 * 活动脉搏：当前分钟对齐到 60s 边界，窗口 = [now-59min, now]（labels[i] 为
 * 窗口内第 i 分钟的 HH:MM）；room.message 计入观众系列，streamer.speech 计入
 * 主播系列，窗口外事件丢弃。
 */
export function buildPulse(events: FeedEvent[], nowMs: number, colors: PulseColors): BucketWindow {
  const windowEnd = Math.floor(nowMs / PULSE_BUCKET_MS) * PULSE_BUCKET_MS;
  const windowStart = windowEnd - (PULSE_BUCKETS - 1) * PULSE_BUCKET_MS;

  const audience = new Array<number>(PULSE_BUCKETS).fill(0);
  const streamer = new Array<number>(PULSE_BUCKETS).fill(0);
  const labels = new Array<string>(PULSE_BUCKETS);
  for (let i = 0; i < PULSE_BUCKETS; i++) {
    labels[i] = formatTimeHM(windowStart + i * PULSE_BUCKET_MS);
  }
  for (const event of events) {
    const ts = event.timestamp_ms;
    if (ts < windowStart || ts > windowEnd + PULSE_BUCKET_MS) continue;
    const idx = Math.floor((ts - windowStart) / PULSE_BUCKET_MS);
    if (idx < 0 || idx >= PULSE_BUCKETS) continue;
    if (event.type === EVENT.roomMessage) audience[idx] += 1;
    else if (event.type === EVENT.streamerSpeech) streamer[idx] += 1;
  }
  return {
    labels,
    series: [
      { label: '观众消息', color: colors.audience, values: audience },
      { label: '主播发言', color: colors.streamer, values: streamer },
    ],
  };
}

// 缓冲统计

export interface BufferCounts {
  danmaku: number;
  gift: number;
  superChat: number;
  enter: number;
}

/** 今日缓冲窗口按消息类型分桶（仅取 room.message，四类型外的计数不归入） */
export function bufferCounts(events: FeedEvent[]): BufferCounts {
  const result: BufferCounts = { danmaku: 0, gift: 0, superChat: 0, enter: 0 };
  for (const event of events) {
    if (event.type !== EVENT.roomMessage) continue;
    const data = event.data as Record<string, unknown> | undefined;
    const mt = (data?.message_type ?? '') as string;
    if (mt === 'danmaku') result.danmaku += 1;
    else if (mt === 'gift') result.gift += 1;
    else if (mt === 'super_chat') result.superChat += 1;
    else if (mt === 'enter') result.enter += 1;
  }
  return result;
}

// 时长格式化

/** 运行时长（毫秒）→ 短标签（"2h 5m / 3m 40s / 12s"）；分级口径与 formatElapsedMs 的秒表式不同 */
export function formatUptime(ms: number): string {
  const totalSec = Math.floor(ms / 1000);
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}
