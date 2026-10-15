// websocket store 与底层 wsClient（断线重连状态机）测试
//
// store 与 client 都是模块级单例（messageHandlers / _initialized / ws 实例
// 挂在模块作用域），跨用例必须隔离：每例 vi.resetModules() 后动态重新 import，
// 得到全新的 store 与 wsClient。
//
// node 测试环境无浏览器 WebSocket 与 window.location，用极简 FakeWebSocket 桩
// + vi.stubGlobal 替换，直接驱动真实模块（不 mock 模块本身），重连退避、
// 订阅重放、ping/pong 心跳与 store 的消息分发均按真实代码路径走。

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import type { WebSocketMessage } from '@/types';

/** 极简 WebSocket 桩：记录 send 内容，readyState 与回调由测试手动驱动 */
class FakeWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;

  readyState: number = FakeWebSocket.CONNECTING;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: ((error: unknown) => void) | null = null;
  sent: string[] = [];

  constructor(public url: string) {
    instances.push(this);
  }

  send(data: string): void {
    this.sent.push(data);
  }

  close(): void {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.();
  }

  /** 测试辅助：模拟连接建立成功 */
  simulateOpen(): void {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }

  /** 测试辅助：模拟服务端断开（先改 readyState 再回调，与真实 close 一致） */
  simulateClose(): void {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.();
  }

  /** 测试辅助：模拟服务端下发一条 JSON 消息 */
  simulateMessage(msg: WebSocketMessage): void {
    this.onmessage?.({ data: JSON.stringify(msg) });
  }
}

// 当前用例创建的所有 FakeWebSocket 实例（connect 每次新建一个，取末位即"当前连接"）
let instances: FakeWebSocket[];

function currentSocket(): FakeWebSocket {
  return instances.at(-1)!;
}

/** 建好 pinia 并动态加载全新模块，返回 store 与底层单例 */
async function setupStore() {
  setActivePinia(createPinia());
  const { useWebSocketStore } = await import('@/stores/websocket');
  const store = useWebSocketStore();
  return { store };
}

/** 模拟一次完整建连：connect() → 套接字建立 → open 成功 */
async function connectAndOpen(store: { connect: () => void }): Promise<void> {
  store.connect();
  await vi.advanceTimersByTimeAsync(0);
  currentSocket().simulateOpen();
  await vi.advanceTimersByTimeAsync(0);
}

beforeEach(() => {
  instances = [];
  vi.useFakeTimers();
  vi.stubGlobal('window', { location: { protocol: 'http:', host: 'dashboard.test' } });
  vi.stubGlobal('WebSocket', FakeWebSocket);
  vi.resetModules();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('websocket store 订阅分发', () => {
  it('subscribe 后消息路由到处理器，多个处理器都收到', async () => {
    const { store } = await setupStore();
    await connectAndOpen(store);
    const receivedA: WebSocketMessage[] = [];
    const receivedB: WebSocketMessage[] = [];
    store.subscribe(msg => void receivedA.push(msg));
    store.subscribe(msg => void receivedB.push(msg));

    const msg: WebSocketMessage = { type: 'evt.a', timestamp_ms: 1000, data: {} };
    currentSocket().simulateMessage(msg);

    expect(receivedA).toEqual([msg]);
    expect(receivedB).toEqual([msg]);
  });

  it('同一处理器重复 subscribe 只注册一次，不重复投递', async () => {
    const { store } = await setupStore();
    await connectAndOpen(store);
    const received: WebSocketMessage[] = [];
    const handler = (msg: WebSocketMessage): void => void received.push(msg);
    store.subscribe(handler);
    store.subscribe(handler);

    currentSocket().simulateMessage({ type: 'evt.a', timestamp_ms: 1000, data: {} });

    expect(received).toHaveLength(1);
  });

  it('unsubscribe 后不再收到消息；退订未注册的处理器零操作不抛错', async () => {
    const { store } = await setupStore();
    await connectAndOpen(store);
    const received: WebSocketMessage[] = [];
    const handler = (msg: WebSocketMessage): void => void received.push(msg);
    store.subscribe(handler);
    store.unsubscribe(handler);
    // 未注册过的处理器，退订应零操作
    expect(() => store.unsubscribe(handler)).not.toThrow();

    currentSocket().simulateMessage({ type: 'evt.a', timestamp_ms: 1000, data: {} });

    expect(received).toHaveLength(0);
  });
});

describe('websocket store 连接状态', () => {
  it('connect() 建连成功后 isConnected 置真，并向服务端订阅全部事件', async () => {
    const { store } = await setupStore();
    expect(store.isConnected).toBe(false);

    await connectAndOpen(store);

    expect(store.isConnected).toBe(true);
    const request = JSON.parse(currentSocket().sent[0]!) as { action: string; events: string[] };
    expect(request).toEqual({ action: 'subscribe', events: ['*'] });
  });

  it('断线后 isConnected 置假，且未手动关闭时按退避调度重连', async () => {
    const { store } = await setupStore();
    await connectAndOpen(store);
    const first = currentSocket();

    first.simulateClose();
    expect(store.isConnected).toBe(false);

    // 退避 3s 后重连，新建套接字
    await vi.advanceTimersByTimeAsync(3000);
    expect(instances).toHaveLength(2);
    expect(currentSocket()).not.toBe(first);
  });

  it('disconnect() 手动断开：不触发自动重连，isConnected 置假', async () => {
    const { store } = await setupStore();
    await connectAndOpen(store);

    store.disconnect();

    expect(store.isConnected).toBe(false);
    await vi.advanceTimersByTimeAsync(60000);
    expect(instances).toHaveLength(1);
  });

  it('connect() 幂等：已连接时重复调用不再新建套接字', async () => {
    const { store } = await setupStore();
    await connectAndOpen(store);

    void store.connect();
    await vi.advanceTimersByTimeAsync(0);

    expect(instances).toHaveLength(1);
  });
});

describe('wsClient 断线重连退避状态机', () => {
  async function setupClient() {
    const { wsClient } = await import('@/api/websocket');
    return wsClient;
  }

  /** 建连并打开，返回当前套接字 */
  async function openOnce(
    wsClient: Awaited<ReturnType<typeof setupClient>>,
  ): Promise<FakeWebSocket> {
    void wsClient.connect().catch(() => undefined);
    await vi.advanceTimersByTimeAsync(0);
    const socket = currentSocket();
    socket.simulateOpen();
    return socket;
  }

  it('指数退避：连续失败重连按 3s → 6s → 12s → 24s → 30s 封顶递增', async () => {
    const wsClient = await setupClient();

    // 首连成功后开始断线；后续重连套接字不再 open（服务端持续不可达），
    // 退避才会逐轮递增（成功 open 会把退避重置回 3s，见下一用例）
    await openOnce(wsClient);
    const expectedDelays = [3000, 6000, 12000, 24000, 30000, 30000];
    for (const delay of expectedDelays) {
      const before = instances.length;
      currentSocket().simulateClose();
      // 到点前不重连
      await vi.advanceTimersByTimeAsync(delay - 1);
      expect(instances).toHaveLength(before);
      await vi.advanceTimersByTimeAsync(1);
      expect(instances).toHaveLength(before + 1);
    }
  });

  it('重连成功（open）后退避重置回 3s', async () => {
    const wsClient = await setupClient();

    // 断一次且重连套接字不 open，退避推进到 6s
    await openOnce(wsClient);
    currentSocket().simulateClose();
    await vi.advanceTimersByTimeAsync(3000);
    // 重连成功 open 后再断，下一轮应回到 3s 而非 6s
    currentSocket().simulateOpen();
    currentSocket().simulateClose();
    await vi.advanceTimersByTimeAsync(2999);
    expect(instances).toHaveLength(2);
    await vi.advanceTimersByTimeAsync(1);
    expect(instances).toHaveLength(3);
  });

  it('断线前积累的订阅在重连 open 后自动重放（含 "*"）', async () => {
    const wsClient = await setupClient();
    wsClient.subscribe(['evt.a', 'evt.b']);
    await openOnce(wsClient);

    currentSocket().simulateClose();
    await vi.advanceTimersByTimeAsync(3000);
    currentSocket().simulateOpen();

    const sent = currentSocket().sent.map(
      raw => JSON.parse(raw) as { action: string; events: string[] },
    );
    const subscribeCalls = sent.filter(r => r.action === 'subscribe');
    expect(subscribeCalls[0]).toEqual({ action: 'subscribe', events: ['evt.a', 'evt.b'] });
    expect(subscribeCalls[1]).toEqual({ action: 'subscribe', events: ['*'] });
  });

  it('应用层心跳：收到 ping 回 pong，且心跳不进入消息回调', async () => {
    const wsClient = await setupClient();
    const received: WebSocketMessage[] = [];
    wsClient.onMessage(msg => void received.push(msg));
    await openOnce(wsClient);

    currentSocket().simulateMessage({ type: 'ping', timestamp_ms: 1000, data: {} });

    const sent = currentSocket().sent.map(raw => JSON.parse(raw) as { type: string });
    expect(sent.at(-1)).toEqual({ type: 'pong' });
    expect(received).toHaveLength(0);
  });

  it('非 JSON 消息静默丢弃，不抛错、不影响后续消息', async () => {
    const wsClient = await setupClient();
    const received: WebSocketMessage[] = [];
    wsClient.onMessage(msg => void received.push(msg));
    await openOnce(wsClient);

    expect(() => currentSocket().onmessage?.({ data: 'not-json' })).not.toThrow();
    currentSocket().simulateMessage({ type: 'evt.ok', timestamp_ms: 1000, data: {} });

    expect(received.map(m => m.type)).toEqual(['evt.ok']);
  });
});
