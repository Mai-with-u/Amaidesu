// useThinkingStream.ts 思考流视图层合成的测试
//
// 覆盖：WS 消息过滤、增量合批落状态、段累积（planner/minecraft/replyer）、
// 轮数上限保尾、隐藏水位过滤、作用域销毁时的订阅与计时器清理。

import { effectScope, type EffectScope } from 'vue';
import { createPinia, setActivePinia } from 'pinia';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useThinkingStream } from '@/composables/live/useThinkingStream';
import { useWebSocketStore } from '@/stores';
import type { WebSocketMessage } from '@/types';

type MessageHandler = (message: WebSocketMessage) => void;

// ws 客户端是模块级单例（构造读 window），node 测试环境替换为哑实现
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

/** 构造思考流 WS 信封（kind="stream" + thinking.delta + 批量增量） */
function streamMessage(
  deltas: Array<{ round_id: string; phase: string; step: number; text_delta: string }>,
  tsMs = 1000,
): WebSocketMessage {
  return {
    kind: 'stream',
    type: 'thinking.delta',
    timestamp_ms: tsMs,
    data: { deltas },
  } as WebSocketMessage;
}

describe('useThinkingStream', () => {
  let scope: EffectScope;
  let handler!: MessageHandler;
  let unsubscribeSpy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.useFakeTimers();
    // 捕获 composable 注册进 ws store 的消息处理器，测试直接投递信封
    const wsStore = useWebSocketStore();
    vi.spyOn(wsStore, 'subscribe').mockImplementation((h: MessageHandler) => {
      handler = h;
    });
    unsubscribeSpy = vi.fn();
    vi.spyOn(wsStore, 'unsubscribe').mockImplementation(unsubscribeSpy);
    scope = effectScope();
  });

  afterEach(() => {
    scope.stop();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  function mount() {
    const composable = scope.run(() => useThinkingStream())!;
    expect(handler).toBeTypeOf('function');
    return composable;
  }

  it('非思考流消息被忽略', () => {
    const { liveThinkingRows } = mount();
    handler({ kind: 'event', type: 'room.message', timestamp_ms: 1, data: {} } as WebSocketMessage);
    handler({
      kind: 'stream',
      type: 'other.stream',
      timestamp_ms: 1,
      data: {},
    } as WebSocketMessage);
    vi.advanceTimersByTime(200);
    expect(liveThinkingRows.value).toHaveLength(0);
  });

  it('增量按 150ms 间隔合批：缓冲期不渲染，间隔后一次性落状态', () => {
    const { liveThinkingRows } = mount();
    handler(
      streamMessage([{ round_id: 'r1', phase: 'planner', step: 1, text_delta: '思考中' }], 5000),
    );
    expect(liveThinkingRows.value).toHaveLength(0);
    vi.advanceTimersByTime(150);
    expect(liveThinkingRows.value).toHaveLength(1);
    expect(liveThinkingRows.value[0]).toMatchObject({
      id: 'think:r1:planner:1',
      tsMs: 5000,
      text: '思考中',
    });
  });

  it('同段后续增量原地追加（id 稳定，文本累加）', () => {
    const { liveThinkingRows } = mount();
    handler(streamMessage([{ round_id: 'r1', phase: 'planner', step: 1, text_delta: 'A' }]));
    vi.advanceTimersByTime(150);
    handler(streamMessage([{ round_id: 'r1', phase: 'planner', step: 1, text_delta: 'B' }]));
    vi.advanceTimersByTime(150);
    expect(liveThinkingRows.value).toHaveLength(1);
    expect(liveThinkingRows.value[0]).toMatchObject({ id: 'think:r1:planner:1', text: 'AB' });
  });

  it('replyer 段独立累积；planner 与 minecraft 按 (phase, step) 各自成段', () => {
    const { liveThinkingRows } = mount();
    handler(
      streamMessage([
        { round_id: 'r1', phase: 'planner', step: 2, text_delta: '规划' },
        { round_id: 'r1', phase: 'minecraft', step: 1, text_delta: '挖矿' },
        { round_id: 'r1', phase: 'replyer', step: 1, text_delta: '回复' },
      ]),
    );
    vi.advanceTimersByTime(150);
    expect(liveThinkingRows.value.map(row => row.actor)).toEqual([
      '思考 · 步骤 2',
      '游戏 Agent · 思考',
      '生成思考',
    ]);
  });

  it('轮数上限保尾：超过 20 轮时最旧轮被淘汰', () => {
    const { liveThinkingRows } = mount();
    for (let i = 0; i < 21; i += 1) {
      handler(
        streamMessage(
          [{ round_id: `r${i}`, phase: 'planner', step: 1, text_delta: `t${i}` }],
          1000 + i,
        ),
      );
    }
    vi.advanceTimersByTime(150);
    expect(liveThinkingRows.value).toHaveLength(20);
    expect(liveThinkingRows.value.some(row => row.text === 't0')).toBe(false);
    expect(liveThinkingRows.value.some(row => row.text === 't20')).toBe(true);
  });

  it('隐藏水位：tsMs ≤ 水位的思考段不进时间线', () => {
    const { liveThinkingRows, thinkingHiddenBeforeMs } = mount();
    handler(
      streamMessage([
        { round_id: 'r1', phase: 'planner', step: 1, text_delta: '早' },
        { round_id: 'r2', phase: 'planner', step: 1, text_delta: '晚' },
      ]),
    );
    // 同批增量共用信封时间戳，落状态后改用不同时间戳再造一段
    vi.advanceTimersByTime(150);
    handler(
      streamMessage([{ round_id: 'r3', phase: 'planner', step: 1, text_delta: '更晚' }], 9000),
    );
    vi.advanceTimersByTime(150);
    thinkingHiddenBeforeMs.value = 1000;
    expect(liveThinkingRows.value.map(row => row.text)).toEqual(['更晚']);
  });

  it('作用域销毁：解除订阅并清除合批计时器', () => {
    mount();
    handler(streamMessage([{ round_id: 'r1', phase: 'planner', step: 1, text_delta: 'x' }]));
    scope.stop();
    expect(unsubscribeSpy).toHaveBeenCalled();
    // 销毁后到期的合批不再落状态（计时器已清）
    vi.advanceTimersByTime(500);
  });
});
