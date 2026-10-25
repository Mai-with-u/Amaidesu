// events store（时间线状态机）测试
//
// 覆盖：历史/实时合并按 id 去重（历史优先）与时间升序、限长保尾、
// history 载荷校验过滤、观测流（kind="stream"）不入事件通道、
// 游标推进规则（数字 id 才入游标并持久化）、backfill 缺口回填与静默失败。
//
// mergeEvents / handleMessage 均为模块私有，统一经 mock 的 websocket
// subscribe 捕获消息处理器驱动，再断言 store.events。

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import type { WebSocketMessage } from '@/types';

// websocket store mock：捕获 events store 注册的消息处理器，测试直接投递消息
const { subscribe, list } = vi.hoisted(() => ({
  subscribe: vi.fn(),
  list: vi.fn(),
}));
vi.mock('@/stores/websocket', () => ({
  useWebSocketStore: () => ({ subscribe }),
}));

// api mock：eventsApi.list 供 backfill 使用，默认成功返回空缺口
vi.mock('@/api', () => ({
  eventsApi: { list },
}));

import { useEventsStore } from '@/stores/events';

/** 构造一条实时事件消息（WS 协议：总线事件带 kind="event"）。
 *  type 允许合成词汇（去重/排序测试不依赖真实事件名），显式断言 = 线上字符串按契约收口 */
function makeMessage(
  overrides: Partial<Omit<WebSocketMessage, 'type'>> & { type: string },
): WebSocketMessage {
  return {
    kind: 'event',
    timestamp_ms: 1000,
    data: {},
    ...overrides,
  } as WebSocketMessage;
}

/** 构造一条 history/REST 载荷条目 */
function makeRecord(id: string, timestamp_ms: number, data: unknown = {}): unknown {
  return { id, type: `evt.${id}`, timestamp_ms, data };
}

/** 极简 localStorage 桩：node 测试环境无 localStorage，验证游标持久化用 */
function installLocalStorageStub(): Map<string, string> {
  const backing = new Map<string, string>();
  const stub = {
    getItem: (key: string) => (backing.has(key) ? backing.get(key)! : null),
    setItem: (key: string, value: string) => void backing.set(key, value),
    removeItem: (key: string) => void backing.delete(key),
    clear: () => backing.clear(),
  };
  vi.stubGlobal('localStorage', stub);
  return backing;
}

/** 建好 pinia 与 store，取回捕获的消息处理器 */
function setupStore() {
  setActivePinia(createPinia());
  const store = useEventsStore();
  expect(subscribe).toHaveBeenCalled();
  const handleMessage = subscribe.mock.calls.at(-1)![0] as (m: WebSocketMessage) => void;
  return { store, handleMessage };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
  installLocalStorageStub();
  list.mockResolvedValue({ data: { events: [] } });
});

describe('events store 合并与去重', () => {
  it('历史与实时按 id 去重，冲突时历史优先，结果按时间升序', () => {
    const { store, handleMessage } = setupStore();

    // 先到两条实时（乱序时间）
    handleMessage(makeMessage({ type: 'evt.b', timestamp_ms: 2000, id: '2' }));
    handleMessage(makeMessage({ type: 'evt.c', timestamp_ms: 3000, id: '3' }));
    // 后到历史：id=2 与实时冲突（历史优先），另带一条更早的新事件
    handleMessage({
      type: 'events.history',
      timestamp_ms: 0,
      data: { events: [makeRecord('2', 2000), makeRecord('1', 1000)] },
    });

    const events = store.events;
    expect(events.map(e => e.id)).toEqual(['1', '2', '3']);
    // 冲突条目取历史版本
    expect(events.find(e => e.id === '2')!.type).toBe('evt.2');
  });

  it('历史后到不吞掉已到达的实时事件（合并幂等，任意到达顺序）', () => {
    const { store, handleMessage } = setupStore();

    handleMessage({
      type: 'events.history',
      timestamp_ms: 0,
      data: { events: [makeRecord('1', 1000)] },
    });
    handleMessage(makeMessage({ type: 'evt.live', timestamp_ms: 2000, id: '9' }));
    // 重连后历史重发（含同一 id），实时事件仍在
    handleMessage({
      type: 'events.history',
      timestamp_ms: 0,
      data: { events: [makeRecord('1', 1000)] },
    });

    expect(store.events.map(e => e.id)).toEqual(['1', '9']);
  });

  it('同批历史内部重复 id 只保留一条', () => {
    const { store, handleMessage } = setupStore();
    handleMessage({
      type: 'events.history',
      timestamp_ms: 0,
      data: { events: [makeRecord('1', 1000), makeRecord('1', 1000)] },
    });
    expect(store.events).toHaveLength(1);
  });

  it('超出限长时保留最近的尾部（限 1000 条）', () => {
    const { store, handleMessage } = setupStore();
    const events = Array.from({ length: 1005 }, (_, i) => makeRecord(String(i + 1), i + 1));
    handleMessage({ type: 'events.history', timestamp_ms: 0, data: { events } });

    expect(store.events).toHaveLength(1000);
    expect(store.events[0]!.id).toBe('6');
    expect(store.events.at(-1)!.id).toBe('1005');
  });
});

describe('events store history 载荷校验', () => {
  it('逐条校验：缺 id / 空 id / 非有限 timestamp_ms 的条目被丢弃', () => {
    const { store, handleMessage } = setupStore();
    handleMessage({
      type: 'events.history',
      timestamp_ms: 0,
      data: {
        events: [
          makeRecord('1', 1000),
          { type: 'x', timestamp_ms: 1000 }, // 缺 id
          { id: '', type: 'x', timestamp_ms: 1000 }, // 空 id
          { id: '2', type: 'x', timestamp_ms: '1000' }, // 非数字时间
          { id: '3', type: 'x', timestamp_ms: Number.NaN }, // NaN 时间
          'not-an-object', // 非对象
        ],
      },
    });
    expect(store.events.map(e => e.id)).toEqual(['1']);
  });

  it('events 非数组时整体视为空历史，不抛错', () => {
    const { store, handleMessage } = setupStore();
    expect(() =>
      handleMessage({ type: 'events.history', timestamp_ms: 0, data: { events: 'oops' } }),
    ).not.toThrow();
    expect(store.events).toHaveLength(0);
  });

  it('条目 data 非对象时回退为空对象', () => {
    const { store, handleMessage } = setupStore();
    handleMessage({
      type: 'events.history',
      timestamp_ms: 0,
      data: { events: [{ id: '1', type: 'x', timestamp_ms: 1000, data: 'bad' }] },
    });
    expect(store.events[0]!.data).toEqual({});
  });
});

describe('events store 消息通道', () => {
  it('观测流（kind="stream"，如 thinking.delta）不入事件通道', () => {
    const { store, handleMessage } = setupStore();
    handleMessage(
      makeMessage({
        kind: 'stream',
        type: 'thinking.delta',
        timestamp_ms: 1000,
        data: { deltas: [] },
      }),
    );
    expect(store.events).toHaveLength(0);
  });

  it('无 id 实时事件：生成 live- 前缀 id 入列，但不推进游标', () => {
    const { store, handleMessage } = setupStore();
    handleMessage(makeMessage({ type: 'evt.a', timestamp_ms: 1000 }));

    expect(store.events).toHaveLength(1);
    expect(store.events[0]!.id).toMatch(/^live-/);
    expect(store.cursor).toBe('');
  });

  it('数字 id 实时事件：入列并推进游标且持久化', () => {
    const { store, handleMessage } = setupStore();
    handleMessage(makeMessage({ type: 'evt.a', timestamp_ms: 1000, id: '42' }));

    expect(store.events[0]!.id).toBe('42');
    expect(store.cursor).toBe('42');
    expect(localStorage.getItem('amaidesu-events-cursor')).toBe('42');
  });

  it('合成条目（stub-* 等非数字 id）不入游标', () => {
    const { store, handleMessage } = setupStore();
    handleMessage(makeMessage({ type: 'evt.stub', timestamp_ms: 1000, id: 'stub-1' }));

    expect(store.events).toHaveLength(1);
    expect(store.cursor).toBe('');
  });

  it('uuid 形式的事件 id（后端实际形态）入列并推进游标', () => {
    const { store, handleMessage } = setupStore();
    const uuid = '73a5ce59-b574-4018-a37e-79c9a1c15f7b';
    handleMessage(makeMessage({ type: 'evt.a', timestamp_ms: 1000, id: uuid }));

    expect(store.cursor).toBe(uuid);
    expect(localStorage.getItem('amaidesu-events-cursor')).toBe(uuid);
  });

  it('日志通道（kind="log"）与握手帧（kind="transport"）不入事件缓冲', () => {
    const { store, handleMessage } = setupStore();
    handleMessage(makeMessage({ kind: 'log', type: 'log.entry', timestamp_ms: 1000, data: {} }));
    handleMessage(
      makeMessage({ kind: 'transport', type: 'connected', timestamp_ms: 1000, data: {} }),
    );
    handleMessage(
      makeMessage({ kind: 'transport', type: 'subscribe_response', timestamp_ms: 1001, data: {} }),
    );

    expect(store.events).toHaveLength(0);
  });

  it('clearEvents 清空时间线', () => {
    const { store, handleMessage } = setupStore();
    handleMessage(makeMessage({ type: 'evt.a', timestamp_ms: 1000, id: '1' }));
    store.clearEvents();
    expect(store.events).toHaveLength(0);
  });
});

describe('events store backfill 缺口回填', () => {
  it('无游标（首次访问）跳过回填，不调用 API', async () => {
    const { store } = setupStore();
    await store.backfill();
    expect(list).not.toHaveBeenCalled();
    expect(store.events).toHaveLength(0);
  });

  it('有游标时按 since_id 拉缺口，合并去重并推进游标', async () => {
    const { store, handleMessage } = setupStore();
    handleMessage(makeMessage({ type: 'evt.a', timestamp_ms: 1000, id: '10' }));
    list.mockResolvedValue({
      data: { events: [makeRecord('11', 2000), makeRecord('12', 3000)] },
    });

    await store.backfill();

    expect(list).toHaveBeenCalledWith({ since_id: '10', limit: 1000 });
    expect(store.events.map(e => e.id)).toEqual(['10', '11', '12']);
    expect(store.cursor).toBe('12');
  });

  it('回填幂等：第二次调用不再拉取', async () => {
    const { store, handleMessage } = setupStore();
    handleMessage(makeMessage({ type: 'evt.a', timestamp_ms: 1000, id: '10' }));
    list.mockResolvedValue({ data: { events: [makeRecord('11', 2000)] } });
    await store.backfill();
    await store.backfill();
    expect(list).toHaveBeenCalledTimes(1);
  });

  it('回填失败静默：不抛错、时间线不变', async () => {
    const { store, handleMessage } = setupStore();
    handleMessage(makeMessage({ type: 'evt.a', timestamp_ms: 1000, id: '10' }));
    list.mockRejectedValue(new Error('network down'));

    await expect(store.backfill()).resolves.toBeUndefined();
    expect(store.events.map(e => e.id)).toEqual(['10']);
  });

  it('回填结果为空缺口时不推进游标', async () => {
    const { store, handleMessage } = setupStore();
    handleMessage(makeMessage({ type: 'evt.a', timestamp_ms: 1000, id: '10' }));
    await store.backfill();
    expect(store.cursor).toBe('10');
  });
});

describe('events store 历史浏览窗口', () => {
  function setupWithEvents(handleMessage: (m: WebSocketMessage) => void) {
    handleMessage(makeMessage({ type: 'evt.a', timestamp_ms: 5000, id: 'a5' }));
    handleMessage(makeMessage({ type: 'evt.b', timestamp_ms: 6000, id: 'b6' }));
  }

  it('fetchOlder：以基线最旧一条为 before 游标拉一页，并入窗口并更新续翻信号', async () => {
    const { store, handleMessage } = setupStore();
    setupWithEvents(handleMessage);
    list.mockResolvedValue({
      data: { events: [makeRecord('z1', 3000), makeRecord('z2', 4000)], has_more: true },
    });

    await store.fetchOlder();

    expect(list).toHaveBeenCalledWith({ before_timestamp_ms: 5000, limit: 500 });
    expect(store.windowEvents!.map(e => e.id)).toEqual(['z1', 'z2', 'a5', 'b6']);
    expect(store.windowHasOlder).toBe(true);
  });

  it('fetchNewer：以窗口最新 +1 为 start_ms 反向取页（take=oldest）', async () => {
    const { store, handleMessage } = setupStore();
    setupWithEvents(handleMessage);
    list.mockResolvedValue({
      data: { events: [makeRecord('z1', 3000), makeRecord('z2', 4000)], has_more: true },
    });
    await store.fetchOlder();
    list.mockClear();
    list.mockResolvedValue({
      data: { events: [makeRecord('n1', 7000)], has_more: false, has_more_newer: false },
    });

    await store.fetchNewer();

    expect(list).toHaveBeenCalledWith({ start_ms: 6001, take: 'oldest', limit: 500 });
    expect(store.windowEvents!.map(e => e.id)).toEqual(['z1', 'z2', 'a5', 'b6', 'n1']);
    expect(store.windowHasNewer).toBe(false);
  });

  it('jumpTo：窗口整体替换为跳转时刻之后的一页', async () => {
    const { store, handleMessage } = setupStore();
    setupWithEvents(handleMessage);
    list.mockResolvedValue({
      data: {
        events: [makeRecord('j1', 8000), makeRecord('j2', 9000)],
        has_more: true,
        has_more_newer: true,
      },
    });

    await store.jumpTo(8000);

    expect(list).toHaveBeenCalledWith({ start_ms: 8000, take: 'oldest', limit: 500 });
    expect(store.windowEvents!.map(e => e.id)).toEqual(['j1', 'j2']);
    expect(store.windowHasOlder).toBe(true);
    expect(store.windowHasNewer).toBe(true);
  });

  it('exitWindow：回到实时模式（窗口清空、续翻信号复位）', async () => {
    const { store, handleMessage } = setupStore();
    setupWithEvents(handleMessage);
    list.mockResolvedValue({ data: { events: [makeRecord('z1', 3000)], has_more: true } });
    await store.fetchOlder();

    store.exitWindow();

    expect(store.windowEvents).toBeNull();
    expect(store.windowHasOlder).toBe(true);
    expect(store.windowHasNewer).toBe(false);
  });

  it('clearEvents 同时清空窗口', async () => {
    const { store, handleMessage } = setupStore();
    setupWithEvents(handleMessage);
    list.mockResolvedValue({ data: { events: [makeRecord('z1', 3000)], has_more: true } });
    await store.fetchOlder();

    store.clearEvents();

    expect(store.events).toHaveLength(0);
    expect(store.windowEvents).toBeNull();
  });
});
