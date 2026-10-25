// alerts store 告警收件箱的测试
//
// 覆盖：入账（core.error / tts.utterance.failed / game.attention_required /
// 无关事件过滤）、相同 title+source 60 秒窗口去重合并（累加计数、刷新时刻、
// 重新置未读）、窗口外不再合并、滚动上限 200 条裁最旧、未读数派生、
// 单条已读与全部已读。
// 惯例：vi.mock 掉 @/api/websocket 的 wsClient，经真实 websocket store 的
// subscribe 捕获 handler，直接投喂消息驱动；时间用传入 ts_ms 表达，不靠真实时钟。

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';

import { useAlertsStore, type AlertItem } from '@/stores/alerts';
import { useWebSocketStore } from '@/stores';
import { EVENT, PROTOCOL } from '@/types/events';
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

/** 捕获 store 订阅的 WS handler，返回投喂函数 */
function feedFn(): (msg: WebSocketMessage) => void {
  const wsStore = useWebSocketStore();
  let handler: ((msg: WebSocketMessage) => void) | null = null;
  vi.spyOn(wsStore, 'subscribe').mockImplementation(fn => {
    handler = fn;
    return () => undefined;
  });
  // 首次访问 store 触发其 setup 内订阅
  useAlertsStore();
  return (msg: WebSocketMessage) => handler?.(msg);
}

function event(type: WsMessageType, data: Record<string, unknown>, tsMs: number): WebSocketMessage {
  return { kind: 'event', type, timestamp_ms: tsMs, data };
}

/** 首个告警基准时刻（Unix 毫秒） */
const BASE_MS = 1_706_745_600_000;

function titles(store: { alerts: AlertItem[] }): string[] {
  return store.alerts.map(item => item.title);
}

beforeEach(() => {
  setActivePinia(createPinia());
});

describe('alerts store 入账', () => {
  it('core.error → error 级条目，title 取 message，落到日志页', () => {
    const feed = feedFn();
    feed(event(EVENT.coreError, { message: 'B 站连接断开' }, BASE_MS));
    const store = useAlertsStore();
    expect(store.alerts).toHaveLength(1);
    expect(store.alerts[0]).toMatchObject({
      level: 'error',
      title: 'B 站连接断开',
      source_page: '/logs',
      ts_ms: BASE_MS,
      read: false,
      count: 1,
    });
    expect(store.unreadCount).toBe(1);
  });

  it('tts.utterance.failed / game.attention_required 入账，级别与页面各异', () => {
    const feed = feedFn();
    feed(event(EVENT.ttsUtteranceFailed, { reason: 'TTS 服务超时' }, BASE_MS));
    feed(event(EVENT.gameAttentionRequired, { message: '存档冲突' }, BASE_MS + 1));
    const store = useAlertsStore();
    expect(store.alerts).toHaveLength(2);
    expect(store.alerts[0]).toMatchObject({ level: 'error', source_page: '/live' });
    expect(store.alerts[1]).toMatchObject({ level: 'warning', source_page: '/live' });
  });

  it('非订阅事件与日志通道消息不入账', () => {
    const feed = feedFn();
    feed(event(EVENT.roomMessage, { message: '弹幕不算告警' }, BASE_MS));
    feed({
      kind: 'log',
      type: PROTOCOL.logEntry,
      timestamp_ms: BASE_MS,
      data: { level: 'ERROR', module: 'x', message: '日志流水不入收件箱', timestamp: '' },
    });
    feed({ kind: 'transport', type: PROTOCOL.connected, timestamp_ms: BASE_MS, data: {} });
    expect(useAlertsStore().alerts).toHaveLength(0);
  });

  it('message 与 reason 均缺失的告警载荷不入账（无可用文案的 core.error）', () => {
    const feed = feedFn();
    feed(event(EVENT.coreError, { data: { collector: 'x' } }, BASE_MS));
    expect(useAlertsStore().alerts).toHaveLength(0);
  });
});

describe('alerts store 去重合并', () => {
  it('60 秒窗口内相同 title+source 合并：计数累加、时刻刷新、重置未读', () => {
    const feed = feedFn();
    feed(event(EVENT.coreError, { message: '连接断开' }, BASE_MS));
    const store = useAlertsStore();
    store.markRead(store.alerts[0]!.id);
    feed(event(EVENT.coreError, { message: '连接断开' }, BASE_MS + 30_000));
    expect(store.alerts).toHaveLength(1);
    expect(store.alerts[0]).toMatchObject({ ts_ms: BASE_MS + 30_000, count: 2, read: false });
    expect(store.unreadCount).toBe(1);
  });

  it('不同 source 同文案不合并；60 秒窗口外同源也不合并', () => {
    const feed = feedFn();
    feed(event(EVENT.coreError, { message: '连接断开' }, BASE_MS));
    // 窗口内、不同页面：新增
    useAlertsStore().recordAlert({
      level: 'error',
      title: '连接断开',
      source_page: '/tools',
      ts_ms: BASE_MS + 10_000,
    });
    // 窗口外、同页面：新增
    feed(event(EVENT.coreError, { message: '连接断开' }, BASE_MS + 61_000));
    const store = useAlertsStore();
    expect(titles(store)).toEqual(['连接断开', '连接断开', '连接断开']);
    expect(store.alerts.map(item => item.source_page)).toEqual(['/logs', '/tools', '/logs']);
    expect(store.alerts.every(item => item.count === 1)).toBe(true);
  });
});

describe('alerts store 上限与已读', () => {
  it('滚动上限 200 条：超出裁掉最旧', () => {
    const store = useAlertsStore();
    for (let i = 0; i < 205; i += 1) {
      store.recordAlert({
        level: 'error',
        title: `告警 ${i}`,
        source_page: '/logs',
        ts_ms: BASE_MS + i,
      });
    }
    expect(store.alerts).toHaveLength(200);
    expect(store.alerts[0]?.title).toBe('告警 5');
    expect(store.alerts[199]?.title).toBe('告警 204');
  });

  it('未读数只数未读；全部已读后归零，单条已读只消一条', () => {
    const feed = feedFn();
    feed(event(EVENT.coreError, { message: '甲' }, BASE_MS));
    feed(event(EVENT.coreError, { message: '乙' }, BASE_MS + 1));
    const store = useAlertsStore();
    expect(store.unreadCount).toBe(2);
    store.markRead(store.alerts[0]!.id);
    expect(store.unreadCount).toBe(1);
    store.markAllRead();
    expect(store.unreadCount).toBe(0);
    expect(store.alerts.every(item => item.read)).toBe(true);
  });
});
