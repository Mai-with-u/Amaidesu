// useThinkingStream.ts 思考流视图层合成的测试
//
// 覆盖：WS 消息过滤、增量合批落状态、段累积（planner/minecraft/replyer）、
// 轮数/步数上限保尾、隐藏水位过滤、单例语义（重复调用状态保留）。
//
// 状态是模块级单例（切页保留）——每个用例用 vi.resetModules + 动态 import
// 取得全新模块实例做隔离；stores 也必须取自重置后的同一模块图，否则 spy
// 加在与 composable 不同的 store 实例上。

import { createPinia, setActivePinia } from 'pinia';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { WebSocketMessage } from '@/types';

type MessageHandler = (message: WebSocketMessage) => void;

type ThinkingStreamModule = typeof import('@/composables/live/useThinkingStream');
type StoresModule = typeof import('@/stores');

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
  let handler!: MessageHandler;
  let mod!: ThinkingStreamModule;

  beforeEach(async () => {
    setActivePinia(createPinia());
    vi.resetModules();
    vi.useFakeTimers();
    // stores 与被测组合式取自同一重置后的模块图，保证 spy 生效
    const stores: StoresModule = await import('@/stores');
    const wsStore = stores.useWebSocketStore();
    vi.spyOn(wsStore, 'subscribe').mockImplementation((h: MessageHandler) => {
      handler = h;
    });
    vi.spyOn(wsStore, 'unsubscribe').mockImplementation(vi.fn());
    mod = await import('@/composables/live/useThinkingStream');
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  function mount() {
    const composable = mod.useThinkingStream();
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

  it('单轮步骤上限保尾：超过 100 步时最旧段被淘汰', () => {
    const { liveThinkingRows } = mount();
    const deltas: Array<{ round_id: string; phase: string; step: number; text_delta: string }> = [];
    for (let step = 1; step <= 105; step += 1) {
      deltas.push({ round_id: 'r1', phase: 'planner', step, text_delta: `s${step}` });
    }
    handler(streamMessage(deltas, 1000));
    vi.advanceTimersByTime(150);
    expect(liveThinkingRows.value).toHaveLength(100);
    // 最旧的 1..5 步被淘汰，保留最近的 6..105 步
    expect(liveThinkingRows.value[0].id).toBe('think:r1:planner:6');
    expect(liveThinkingRows.value[99].id).toBe('think:r1:planner:105');
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

  it('单例语义：重复调用（模拟切页重进）不重置状态，已落思考行保留', () => {
    const first = mount();
    handler(streamMessage([{ round_id: 'r1', phase: 'minecraft', step: 1, text_delta: '挖矿中' }]));
    vi.advanceTimersByTime(150);
    expect(first.liveThinkingRows.value).toHaveLength(1);

    // 第二次调用即切页重进后的重新挂接：同一份模块级状态
    const second = mod.useThinkingStream();
    expect(second.liveThinkingRows.value).toHaveLength(1);
    expect(second.liveThinkingRows.value[0].text).toBe('挖矿中');
    expect(second.liveThinkingRows.value).toBe(first.liveThinkingRows.value);
  });
});
