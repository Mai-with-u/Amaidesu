// useTtsWaterLevel.ts 的事件聚合与空窗警戒判定测试
//
// 覆盖：started/finished/failed 聚合（在途配对、失败计数）、空窗口径
// （在途=0 / 无 settled=null / 距最近 settled）、2.5s 正常与 3s 警戒线判定。

import { effectScope, type EffectScope } from 'vue';
import { createPinia, setActivePinia } from 'pinia';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useEventsStore, useWebSocketStore } from '@/stores';
import {
  aggregateTtsWaterLevel,
  isGapWarning,
  useTtsWaterLevel,
} from '@/composables/live/useTtsWaterLevel';
import type { WebSocketMessage, WsMessageType } from '@/types';

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

function event(
  id: string,
  type: string,
  tsMs: number,
  data: Record<string, unknown> = {},
): WebSocketMessage & { id: string } {
  return { id, type: type as WsMessageType, timestamp_ms: tsMs, data };
}

describe('aggregateTtsWaterLevel', () => {
  it('无事件：在途/失败为 0，空窗为 null（无从判定）', () => {
    expect(aggregateTtsWaterLevel([], 10_000)).toEqual({
      inFlightCount: 0,
      failureCount: 0,
      gapMs: null,
    });
  });

  it('started 未 settled：计在途，空窗记 0（正在出声）', () => {
    const events = [
      event('1', 'tts.utterance.started', 8_000, { utterance_id: 'utt_a' }),
      event('2', 'tts.utterance.finished', 9_000, { utterance_id: 'utt_a' }),
      event('3', 'tts.utterance.started', 9_500, { utterance_id: 'utt_b' }),
    ];
    expect(aggregateTtsWaterLevel(events, 10_000)).toEqual({
      inFlightCount: 1,
      failureCount: 0,
      gapMs: 0,
    });
  });

  it('failed 同样使在途出清并计入失败、刷新空窗起点', () => {
    const events = [
      event('1', 'tts.utterance.started', 8_000, { utterance_id: 'utt_a' }),
      event('2', 'tts.utterance.failed', 8_500, { utterance_id: 'utt_a' }),
    ];
    expect(aggregateTtsWaterLevel(events, 10_000)).toEqual({
      inFlightCount: 0,
      failureCount: 1,
      gapMs: 1_500,
    });
  });

  it('空窗 = now - 最近一句 settled（finished 刷新起点）', () => {
    const events = [
      event('1', 'tts.utterance.finished', 7_000, { utterance_id: 'utt_a' }),
      event('2', 'tts.utterance.finished', 9_000, { utterance_id: 'utt_b' }),
    ];
    expect(aggregateTtsWaterLevel(events, 10_000).gapMs).toBe(1_000);
  });
});

describe('isGapWarning', () => {
  it('2.5s 正常冲入不警戒；>3s 警戒；null 不警戒', () => {
    expect(isGapWarning(2_500)).toBe(false);
    expect(isGapWarning(3_000)).toBe(false);
    expect(isGapWarning(3_001)).toBe(true);
    expect(isGapWarning(null)).toBe(false);
  });
});

describe('useTtsWaterLevel（store 接线）', () => {
  let scope: EffectScope;

  beforeEach(() => {
    setActivePinia(createPinia());
    useWebSocketStore();
    scope = effectScope();
  });

  afterEach(() => {
    scope.stop();
    vi.clearAllMocks();
  });

  it('从 events store 派生在途与失败计数', () => {
    const store = useEventsStore();
    store.events = [
      event('1', 'tts.utterance.started', 1_000, { utterance_id: 'utt_a' }),
      event('2', 'tts.utterance.failed', 2_000, { utterance_id: 'utt_b' }),
      event('3', 'tts.utterance.started', 3_000, { utterance_id: 'utt_c' }),
    ];
    const { waterLevel } = scope.run(() => useTtsWaterLevel())!;
    expect(waterLevel.value.inFlightCount).toBe(2);
    expect(waterLevel.value.failureCount).toBe(1);
  });
});
