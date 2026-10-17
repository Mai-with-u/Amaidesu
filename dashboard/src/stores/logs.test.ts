// logs store 测试
//
// 覆盖：实时日志接收（log.entry / kind="log"）、暂停积压与恢复补入报告、
// 溢出丢最旧、历史窗口翻页（fetchOlder / fetchNewer）、时间跳转（jumpTo）、
// 服务端搜索（runSearch）、回实时（exitWindow）与合并去重升序。
//
// handleLog 等为模块私有，统一经 mock 的 websocket subscribe 捕获消息处理器驱动。

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import type { LogEntryItem } from '@/types';
import type { WebSocketMessage } from '@/types';

// websocket store mock：捕获 logs store 注册的消息处理器，测试直接投递消息
const { subscribe } = vi.hoisted(() => ({
  subscribe: vi.fn(),
}));
vi.mock('@/stores/websocket', () => ({
  useWebSocketStore: () => ({ subscribe }),
}));

// api mock：logsApi.list 供历史窗口使用
const { list } = vi.hoisted(() => ({
  list: vi.fn(),
}));
vi.mock('@/api', () => ({
  logsApi: { list },
}));

import { useLogsStore, type LogEntry } from '@/stores/logs';

/** 构造一条 WS 日志消息 */
function makeLogMessage(entry: Partial<LogEntry> & { timestamp: string }): WebSocketMessage {
  return {
    kind: 'log',
    type: 'log.entry',
    timestamp_ms: 1000,
    data: {
      level: 'INFO',
      module: 'ModA',
      message: 'hello',
      ...entry,
    },
  };
}

/** 构造一条 API 历史条目 */
function makeItem(timestamp: string, overrides: Partial<LogEntryItem> = {}): LogEntryItem {
  return { timestamp, level: 'WARNING', module: 'ModB', message: 'boom', ...overrides };
}

/** 建好 pinia 与 store，取回捕获的消息处理器 */
function setupStore() {
  setActivePinia(createPinia());
  const store = useLogsStore();
  expect(subscribe).toHaveBeenCalled();
  const handleMessage = subscribe.mock.calls.at(-1)![0] as (m: WebSocketMessage) => void;
  return { store, handleMessage };
}

beforeEach(() => {
  vi.clearAllMocks();
  list.mockResolvedValue({
    data: {
      entries: [],
      total: 0,
      has_more_older: false,
      has_more_newer: false,
      persist_available: true,
      window_start_ms: 0,
    },
  });
});

describe('logs store 实时接收', () => {
  it('log.entry（kind="log"）入实时缓冲；其他 kind 不入', () => {
    const { store, handleMessage } = setupStore();

    handleMessage(makeLogMessage({ timestamp: '2026-10-17T10:00:00' }));
    handleMessage({
      kind: 'event',
      type: 'log.entry',
      timestamp_ms: 1001,
      data: { timestamp: '2026-10-17T10:00:01', level: 'INFO', module: 'X', message: 'bus 混入' },
    } as WebSocketMessage);
    handleMessage({
      kind: 'transport',
      type: 'log.entry',
      timestamp_ms: 1002,
      data: { timestamp: '2026-10-17T10:00:02', level: 'INFO', module: 'X', message: '握手混入' },
    } as WebSocketMessage);

    expect(store.logs).toHaveLength(1);
    expect(store.logs[0]!.message).toBe('hello');
  });

  it('重复条目（同时间戳+模块+正文）去重', () => {
    const { store, handleMessage } = setupStore();
    handleMessage(makeLogMessage({ timestamp: '2026-10-17T10:00:00' }));
    handleMessage(makeLogMessage({ timestamp: '2026-10-17T10:00:00' }));
    expect(store.logs).toHaveLength(1);
  });

  it('载荷异常条目被丢弃，不抛错', () => {
    const { store, handleMessage } = setupStore();
    expect(() =>
      handleMessage({
        kind: 'log',
        type: 'log.entry',
        timestamp_ms: 1000,
        data: { timestamp: 1, level: 'INFO' },
      } as unknown as WebSocketMessage),
    ).not.toThrow();
    expect(store.logs).toHaveLength(0);
  });
});

describe('logs store 暂停与恢复', () => {
  it('暂停期间日志进积压不显示，恢复后补入并给出报告', () => {
    const { store, handleMessage } = setupStore();
    handleMessage(makeLogMessage({ timestamp: '2026-10-17T10:00:00', message: 'before' }));

    store.togglePause();
    handleMessage(makeLogMessage({ timestamp: '2026-10-17T10:00:01', message: 'during-1' }));
    handleMessage(makeLogMessage({ timestamp: '2026-10-17T10:00:02', message: 'during-2' }));
    expect(store.logs).toHaveLength(1); // 显示冻结

    store.togglePause();
    expect(store.isPaused).toBe(false);
    expect(store.logs.map(l => l.message)).toEqual(['before', 'during-1', 'during-2']);
    expect(store.resumeReport).toEqual({ added: 2, dropped: 0 });
  });

  it('暂停积压超上限丢最旧并计入 dropped', () => {
    const { store, handleMessage } = setupStore();
    store.togglePause();

    for (let i = 0; i < 1001; i++) {
      handleMessage(
        makeLogMessage({
          timestamp: `2026-10-17T10:${String(i % 60).padStart(2, '0')}:${String(i % 60).padStart(2, '0')}.${String(i).padStart(4, '0')}`,
          message: `m${i}`,
        }),
      );
    }

    store.togglePause();
    expect(store.resumeReport).toEqual({ added: 1000, dropped: 1 });
    // 积压全部补入后实时缓冲裁到 500：最旧的 m0..m500 被挤出
    expect(store.logs).toHaveLength(500);
    expect(store.logs.map(l => l.message)).not.toContain('m0');
    expect(store.logs.map(l => l.message)).toContain('m501');
  });
});

describe('logs store 历史浏览窗口', () => {
  it('fetchOlder：以基线最旧一条为 before 游标拉一页，并入窗口（升序去重）', async () => {
    const { store, handleMessage } = setupStore();
    handleMessage(makeLogMessage({ timestamp: '2026-10-17T10:00:00', message: 'live-old' }));
    handleMessage(makeLogMessage({ timestamp: '2026-10-17T10:00:05', message: 'live-new' }));
    list.mockResolvedValue({
      data: {
        entries: [makeItem('2026-10-17T09:59:58'), makeItem('2026-10-17T09:59:59')],
        total: 2,
        has_more_older: true,
        has_more_newer: false,
        persist_available: true,
        window_start_ms: 1000,
      },
    });

    await store.fetchOlder();

    expect(list).toHaveBeenCalledWith({
      before_timestamp_ms: Date.parse('2026-10-17T10:00:00'),
      limit: 200,
    });
    expect(store.windowLogs!.map(l => l.timestamp)).toEqual([
      '2026-10-17T09:59:58',
      '2026-10-17T09:59:59',
      '2026-10-17T10:00:00',
      '2026-10-17T10:00:05',
    ]);
    expect(store.windowHasOlder).toBe(true);
  });

  it('fetchNewer：以窗口最新 +1 为 start_ms 反向取页（take=oldest）', async () => {
    const { store, handleMessage } = setupStore();
    handleMessage(makeLogMessage({ timestamp: '2026-10-17T10:00:00', message: 'live' }));
    list.mockResolvedValue({
      data: {
        entries: [makeItem('2026-10-17T09:58:00')],
        total: 1,
        has_more_older: false,
        has_more_newer: false,
        persist_available: true,
        window_start_ms: 0,
      },
    });
    await store.fetchOlder();
    list.mockClear();
    list.mockResolvedValue({
      data: {
        entries: [makeItem('2026-10-17T10:00:01'), makeItem('2026-10-17T10:00:02')],
        total: 2,
        has_more_older: false,
        has_more_newer: false,
        persist_available: true,
        window_start_ms: 0,
      },
    });

    await store.fetchNewer();

    expect(list).toHaveBeenCalledWith({
      start_ms: Date.parse('2026-10-17T10:00:00') + 1,
      take: 'oldest',
      limit: 200,
    });
    // 窗口 = fetchOlder 的 [09:58, 10:00) + 实时锚点 10:00 + 新页 [10:00:01, 10:00:02]
    expect(store.windowLogs).toHaveLength(4);
    expect(store.windowHasNewer).toBe(false);
  });

  it('jumpTo：窗口整体替换为跳转时刻之后的一页', async () => {
    const { store } = setupStore();
    list.mockResolvedValue({
      data: {
        entries: [makeItem('2026-10-16T21:00:00'), makeItem('2026-10-16T21:00:30')],
        total: 2,
        has_more_older: true,
        has_more_newer: true,
        persist_available: true,
        window_start_ms: Date.parse('2026-10-16T21:00:00'),
      },
    });

    await store.jumpTo(Date.parse('2026-10-16T21:00:00'));

    expect(list).toHaveBeenCalledWith({
      start_ms: Date.parse('2026-10-16T21:00:00'),
      take: 'oldest',
      limit: 200,
    });
    expect(store.windowLogs).toHaveLength(2);
    expect(store.windowHasOlder).toBe(true);
    expect(store.windowHasNewer).toBe(true);
  });

  it('runSearch：带 q 查询服务端命中并进入窗口；空关键字不发请求', async () => {
    const { store } = setupStore();
    list.mockResolvedValue({
      data: {
        entries: [makeItem('2026-10-17T10:00:00', { message: 'connect fail' })],
        total: 1,
        has_more_older: false,
        has_more_newer: false,
        persist_available: true,
        window_start_ms: 0,
      },
    });

    await store.runSearch('connect fail');

    expect(list).toHaveBeenCalledWith({ q: 'connect fail', limit: 500 });
    expect(store.windowLogs).toHaveLength(1);

    list.mockClear();
    await store.runSearch('   ');
    expect(list).not.toHaveBeenCalled();
  });

  it('exitWindow：回到实时模式', async () => {
    const { store } = setupStore();
    list.mockResolvedValue({
      data: {
        entries: [makeItem('2026-10-17T10:00:00')],
        total: 1,
        has_more_older: true,
        has_more_newer: false,
        persist_available: true,
        window_start_ms: 0,
      },
    });
    await store.fetchOlder().then(() => store.exitWindow());

    expect(store.windowLogs).toBeNull();
    expect(store.windowHasOlder).toBe(true);
    expect(store.windowHasNewer).toBe(false);
  });

  it('窗口并入去重：历史块与基线重叠条目只保留一份', async () => {
    const { store, handleMessage } = setupStore();
    handleMessage(makeLogMessage({ timestamp: '2026-10-17T10:00:00', message: 'dup' }));
    list.mockResolvedValue({
      data: {
        entries: [makeItem('2026-10-17T10:00:00', { module: 'ModA', message: 'dup' })],
        total: 1,
        has_more_older: false,
        has_more_newer: false,
        persist_available: true,
        window_start_ms: 0,
      },
    });

    await store.fetchOlder();

    expect(store.windowLogs).toHaveLength(1);
  });
});
