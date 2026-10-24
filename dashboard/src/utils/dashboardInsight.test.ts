// dashboardInsight.ts 运营判定与数据合成纯逻辑的测试
//
// 覆盖四条主线：
//   1. 结论条状态机 verdictOf（四态全路径 + core.error 300s 窗口边界）
//   2. 决策心跳 heartbeatOf（60s/300s/3600s 分级边界）
//   3. 活动脉搏 buildPulse（分钟对齐分桶 / 越界丢弃 / 双系列独立计数）
//   4. 缓冲统计 bufferCounts（message_type 四桶）

import { describe, expect, it } from 'vitest';

import type { WsMessageType } from '@/types';
import {
  PULSE_BUCKETS,
  PULSE_BUCKET_MS,
  type CollectorSummary,
  bufferCounts,
  buildPulse,
  formatUptime,
  heartbeatOf,
  verdictOf,
  type PulseColors,
  type VerdictInput,
} from './dashboardInsight';
import type { FeedEvent } from './liveFeed';

let seq = 0;

/** 构造 FeedEvent（events store 入口形状：WebSocketMessage + 去重 id） */
function evt(type: string, data: Record<string, unknown> = {}, tsMs = 1000): FeedEvent {
  seq += 1;
  return { id: `e${seq}`, type: type as WsMessageType, timestamp_ms: tsMs, data };
}

function collector(overrides: Partial<CollectorSummary> = {}): CollectorSummary {
  return { name: 'c1', is_started: true, is_enabled: true, ...overrides };
}

function verdictInput(overrides: Partial<VerdictInput> = {}): VerdictInput {
  return {
    tools: [],
    events: [],
    collectors: [],
    streamerAvailable: true,
    sessions: null,
    ...overrides,
  };
}

const NOW = 10_000_000;
const COLORS: PulseColors = { audience: '#111111', streamer: '#222222' };

describe('verdictOf 结论条状态机', () => {
  it('空闲：一切正常', () => {
    expect(verdictOf(verdictInput(), NOW)).toEqual({
      tone: 'idle',
      phrase: '空闲',
      detail: '系统就绪 · 等待场次',
    });
  });

  it('异常：工具熔断（取第一个熔断工具名）', () => {
    const input = verdictInput({
      tools: [
        { name: 't2', health: { state: 'tripped' } },
        { name: 't1', health: { state: 'tripped' } },
      ] as VerdictInput['tools'],
    });
    expect(verdictOf(input, NOW)).toEqual({
      tone: 'error',
      phrase: '异常',
      detail: '工具 t2 熔断',
    });
  });

  it.each([
    ['恰在窗口内（300_000ms 整），判异常', 300_000, true],
    ['刚出窗口（300_001ms），不判异常', 300_001, false],
  ])('core.error 时间边界：%s', (_name, ageMs, isError) => {
    const input = verdictInput({ events: [evt('core.error', {}, NOW - ageMs)] });
    const verdict = verdictOf(input, NOW);
    if (isError) {
      expect(verdict).toEqual({
        tone: 'error',
        phrase: '异常',
        detail: '最近 5 分钟内出现系统错误',
      });
    } else {
      expect(verdict.tone).not.toBe('error');
    }
  });

  it('降级：启用但未运行的采集器（最多点名 3 个）', () => {
    const input = verdictInput({
      collectors: [
        collector({ name: 'a', is_started: false }),
        collector({ name: 'b', is_started: false }),
        collector({ name: 'c', is_started: false }),
        collector({ name: 'd', is_started: false }),
      ],
    });
    expect(verdictOf(input, NOW)).toEqual({
      tone: 'warn',
      phrase: '降级',
      detail: '采集器 a、b、c 未运行',
    });
  });

  it('降级：未启用的采集器不触发点名', () => {
    const input = verdictInput({
      collectors: [collector({ is_enabled: false, is_started: false })],
    });
    expect(verdictOf(input, NOW).tone).toBe('idle');
  });

  it('降级：主播 Agent 未注册', () => {
    expect(verdictOf(verdictInput({ streamerAvailable: false }), NOW)).toEqual({
      tone: 'warn',
      phrase: '降级',
      detail: '主播 Agent 未注册',
    });
  });

  it('直播中：存在活跃场次（detail 为空）', () => {
    const input = verdictInput({
      sessions: { items: [], active_session_id: 1 } as VerdictInput['sessions'],
    });
    expect(verdictOf(input, NOW)).toEqual({ tone: 'live', phrase: '直播中', detail: '' });
  });

  it('优先级：熔断压过直播中；窗口外 error 压不过未注册主播', () => {
    const live = verdictInput({
      tools: [{ name: 't', health: { state: 'tripped' } }] as VerdictInput['tools'],
      sessions: { items: [], active_session_id: 1 } as VerdictInput['sessions'],
    });
    expect(verdictOf(live, NOW).tone).toBe('error');

    const staleErrorThenIdle = verdictInput({
      events: [evt('core.error', {}, NOW - 300_001)],
    });
    expect(verdictOf(staleErrorThenIdle, NOW).tone).toBe('idle');
  });
});

describe('heartbeatOf 决策心跳', () => {
  it('无匹配事件：尚未触发 / silent', () => {
    expect(heartbeatOf([evt('room.message', {}, NOW)], NOW)).toEqual({
      text: '尚未触发',
      tone: 'silent',
    });
  });

  it('streamer.stage 被跳过，不参与心跳', () => {
    const events = [evt('streamer.stage', { stage: 'idle' }, NOW)];
    expect(heartbeatOf(events, NOW).tone).toBe('silent');
  });

  it('最新时刻取 planner 事件与主播发言的最大值', () => {
    const events = [
      evt('planner.decision', {}, NOW - 500_000),
      evt('streamer.speech', {}, NOW - 5_000),
    ];
    expect(heartbeatOf(events, NOW)).toEqual({ text: '5s 前', tone: 'live' });
  });

  it.each([
    [0, '0s 前', 'live'],
    [59, '59s 前', 'live'],
    [60, '1m 0s 前', 'fresh'],
    [299, '4m 59s 前', 'fresh'],
    [300, '5m 前', 'stale'],
    [3599, '59m 前', 'stale'],
    [3600, '1h 前', 'stale'],
  ])('距今 %is → "%s" / %s', (ageSec, text, tone) => {
    const events = [evt('planner.verdict', {}, NOW - ageSec * 1000)];
    expect(heartbeatOf(events, NOW)).toEqual({ text, tone });
  });
});

describe('buildPulse 活动脉搏', () => {
  // 取一个整分钟时刻，便于按桶下标断言
  const base = Math.floor(NOW / PULSE_BUCKET_MS) * PULSE_BUCKET_MS;

  it('labels 覆盖 60 个桶且按分钟推进', () => {
    const win = buildPulse([], base, COLORS);
    expect(win.labels).toHaveLength(PULSE_BUCKETS);
    expect(win.series.map(s => s.label)).toEqual(['观众消息', '主播发言']);
    expect(win.series.map(s => s.color)).toEqual([COLORS.audience, COLORS.streamer]);
  });

  it('room.message 与 streamer.speech 分系列计数', () => {
    const win = buildPulse(
      [
        evt('room.message', {}, base - 1000),
        evt('room.message', {}, base - 1000),
        evt('streamer.speech', {}, base - 2000),
      ],
      base,
      COLORS,
    );
    // base 是整分钟 → 当前桶为空；base 前最后一分钟落在倒数第二桶（下标 58）
    expect(win.series[0].values[58]).toBe(2);
    expect(win.series[1].values[58]).toBe(1);
    expect(win.series[0].values.filter((v, i) => i !== 58 && v !== 0)).toEqual([]);
  });

  it('窗口起点之前的事件丢弃', () => {
    const before = buildPulse(
      [evt('room.message', {}, base - PULSE_BUCKETS * PULSE_BUCKET_MS)],
      base,
      COLORS,
    );
    expect(before.series[0].values.every(v => v === 0)).toBe(true);
  });

  it('窗口末端一个桶宽度内仍计入（含右端宽容），再远丢弃', () => {
    const edge = buildPulse([evt('room.message', {}, base + PULSE_BUCKET_MS)], base, COLORS);
    expect(edge.series[0].values.every(v => v === 0)).toBe(true);
  });

  it('nowMs 对齐到当前分钟，非整点 now 也不偏移', () => {
    const midMinute = base + 30_000;
    const win = buildPulse([evt('room.message', {}, base)], midMinute, COLORS);
    expect(win.series[0].values[59]).toBe(1);
  });
});

describe('bufferCounts 缓冲统计', () => {
  it('按 message_type 四桶计数，非 room.message 忽略', () => {
    const events = [
      evt('room.message', { message_type: 'danmaku' }),
      evt('room.message', { message_type: 'danmaku' }),
      evt('room.message', { message_type: 'gift' }),
      evt('room.message', { message_type: 'super_chat' }),
      evt('room.message', { message_type: 'enter' }),
      // 四类型外的消息不计数
      evt('room.message', { message_type: 'guard' }),
      evt('room.message', {}),
      // 非 room.message 事件整体忽略
      evt('streamer.speech', {}),
    ];
    expect(bufferCounts(events)).toEqual({ danmaku: 2, gift: 1, superChat: 1, enter: 1 });
  });

  it('空事件流：四桶全 0', () => {
    expect(bufferCounts([])).toEqual({ danmaku: 0, gift: 0, superChat: 0, enter: 0 });
  });
});

describe('formatUptime 时长格式化', () => {
  it.each([
    [0, '0s'],
    [12_000, '12s'],
    [220_000, '3m 40s'],
    [3_600_000, '1h 0m'],
    [7_560_000, '2h 6m'],
  ])('%ims → "%s"', (ms, text) => {
    expect(formatUptime(ms)).toBe(text);
  });
});
