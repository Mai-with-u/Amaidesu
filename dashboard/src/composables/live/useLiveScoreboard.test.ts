// useLiveScoreboard.ts 的事件计数与 LLM 汇总派生测试
//
// 覆盖：礼物折 CNY 口径（金瓜子 1000=1 元、银瓜子/未知币种不计、SC 并入）、
// 发言/弹幕计数、token 汇总与 hit/prompt_tokens 缓存命中率、summary 拉取
// 失败静默。

import { ref } from 'vue';
import { effectScope, type EffectScope } from 'vue';
import { createPinia, setActivePinia } from 'pinia';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { llmApi } from '@/api';
import { useEventsStore, useWebSocketStore } from '@/stores';
import {
  giftPriceToCny,
  llmTotalsOf,
  summarizeFeed,
  useLiveScoreboard,
} from '@/composables/live/useLiveScoreboard';
import type { LiveSessionItem, WebSocketMessage, WsMessageType } from '@/types';

vi.mock('@/api/websocket', () => ({
  wsClient: {
    onConnect: vi.fn(),
    onDisconnect: vi.fn(),
    onMessage: vi.fn(),
    subscribe: vi.fn(),
    unsubscribe: vi.fn(),
    connect: vi.fn().mockResolvedValue(undefined),
    disconnect: vi.fn(),
    isConnected: vi.fn(() => false),
  },
}));

vi.mock('@/api', async importOriginal => ({
  ...(await importOriginal<object>()),
  llmApi: { getUsageSummary: vi.fn() },
}));

const getUsageSummaryMock = vi.mocked(llmApi.getUsageSummary);

function event(
  id: string,
  type: string,
  tsMs: number,
  data: Record<string, unknown> = {},
): WebSocketMessage & { id: string } {
  return { id, type: type as WsMessageType, timestamp_ms: tsMs, data };
}

describe('giftPriceToCny', () => {
  it('金瓜子 1000 = 1 元；空币种按金瓜子兜底', () => {
    expect(giftPriceToCny(1000, 'bilibili_gold_coin')).toBe(1);
    expect(giftPriceToCny(500, '')).toBe(0.5);
  });

  it('银瓜子等免费币种与非法金额不计入', () => {
    expect(giftPriceToCny(1000, 'bilibili_silver_coin')).toBeNull();
    expect(giftPriceToCny(1000, 'unknown_coin')).toBeNull();
    expect(giftPriceToCny(0, 'bilibili_gold_coin')).toBeNull();
    expect(giftPriceToCny(undefined, 'bilibili_gold_coin')).toBeNull();
  });
});

describe('summarizeFeed', () => {
  it('发言/弹幕计数，礼物金瓜子与 SC 金额并入折算合计', () => {
    const events = [
      event('1', 'streamer.speech', 1_000, { text: '大家好' }),
      event('2', 'streamer.speech', 2_000, { text: '欢迎回来' }),
      event('3', 'room.message', 3_000, { message_type: 'danmaku', content: '666' }),
      event('4', 'room.message', 4_000, {
        message_type: 'gift',
        gift: { name: '小花花', count: 1, total_price: 1000, currency: 'bilibili_gold_coin' },
      }),
      event('5', 'room.message', 5_000, {
        message_type: 'super_chat',
        sc: { amount: 30 },
      }),
      // 银瓜子免费礼物不入折算
      event('6', 'room.message', 6_000, {
        message_type: 'gift',
        gift: { name: '辣条', count: 1, total_price: 100, currency: 'bilibili_silver_coin' },
      }),
    ];
    expect(summarizeFeed(events)).toEqual({
      speechCount: 2,
      danmakuCount: 1,
      giftCny: 31,
    });
  });

  it('无可折算付费时 giftCny 为 null（与 0 元区分）', () => {
    expect(summarizeFeed([event('1', 'streamer.speech', 1_000)])).toEqual({
      speechCount: 1,
      danmakuCount: 0,
      giftCny: null,
    });
  });
});

describe('llmTotalsOf', () => {
  it('命中率取 hit/prompt_tokens 口径', () => {
    expect(
      llmTotalsOf({ total_tokens: 1500, total_prompt_tokens: 1000, cache_hit_tokens: 800 }),
    ).toEqual({ totalTokens: 1500, cacheHitRate: 0.8 });
  });

  it('无 prompt 用量或无 summary 时命中率为 null', () => {
    expect(llmTotalsOf({ total_tokens: 10, total_prompt_tokens: 0, cache_hit_tokens: 0 })).toEqual({
      totalTokens: 10,
      cacheHitRate: null,
    });
    expect(llmTotalsOf(null)).toEqual({ totalTokens: null, cacheHitRate: null });
  });
});

describe('useLiveScoreboard（store 接线）', () => {
  let scope: EffectScope;

  const activeSession: LiveSessionItem = {
    live_session_id: 1,
    source: 'manual',
    kind: 'live',
    title: null,
    room_id: 'r',
    platform: 'bilibili',
    started_at_ms: 1_000,
    ended_at_ms: null,
    message_count: 0,
    is_active: true,
  };

  beforeEach(() => {
    setActivePinia(createPinia());
    useWebSocketStore();
    scope = effectScope();
  });

  afterEach(() => {
    scope.stop();
    vi.clearAllMocks();
  });

  it('从 events store 计数、由场次起点推时长', () => {
    // 共享时钟（useNowTick）在模块导入时已初始化为真实时刻，不打 fake timers，
    // 场次起点取"当前时刻 - 60s"，时长断言允许秒级时钟一个刻度的误差
    const session: LiveSessionItem = {
      ...activeSession,
      started_at_ms: Date.now() - 60_000,
    };
    const store = useEventsStore();
    store.events = [
      event('1', 'streamer.speech', 2_000),
      event('2', 'room.message', 3_000, { message_type: 'danmaku' }),
    ];
    const { counts, durationMs } = scope.run(() => useLiveScoreboard(ref(session)))!;
    expect(counts.value.speechCount).toBe(1);
    expect(counts.value.danmakuCount).toBe(1);
    expect(durationMs.value).toBeGreaterThanOrEqual(59_000);
    expect(durationMs.value).toBeLessThanOrEqual(62_000);
  });

  it('refreshSummary 拉取汇总；失败静默保持上次值', async () => {
    getUsageSummaryMock.mockResolvedValue({
      data: { total_tokens: 2000, total_prompt_tokens: 1000, cache_hit_tokens: 500 },
    } as never);
    const { llmTotals, refreshSummary } = scope.run(() => useLiveScoreboard(ref(null)))!;
    await refreshSummary();
    expect(llmTotals.value).toEqual({ totalTokens: 2000, cacheHitRate: 0.5 });

    getUsageSummaryMock.mockRejectedValue(new Error('down'));
    await refreshSummary();
    expect(llmTotals.value).toEqual({ totalTokens: 2000, cacheHitRate: 0.5 });
  });
});
