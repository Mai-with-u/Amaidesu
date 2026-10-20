// useRawWebSocket.ts 小部件页裸 WebSocket 组合式的测试
//
// 覆盖：消息送达（onMessage 收原始 MessageEvent）、异常关闭后 3 秒重连、
// 作用域销毁后不再重连（disposed 守卫 + 清理重连定时器）、status 随连接
// 生命周期流转。node 环境运行：用假 WebSocket 类 + 假 window 替换全局。

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { effectScope, type EffectScope, type Ref } from 'vue';

import { useRawWebSocket, type WidgetSocketStatus } from '@/composables/useRawWebSocket';

/** 可手动驱动回调的假 WebSocket */
class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  url: string;
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  close(): void {
    this.closed = true;
    this.onclose?.();
  }

  /** 测试侧模拟连接建立 */
  emitOpen(): void {
    this.onopen?.();
  }

  /** 测试侧模拟服务端推送 */
  emitMessage(data: unknown): void {
    this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent);
  }

  /** 测试侧模拟异常关闭 */
  emitClose(): void {
    this.onclose?.();
  }

  /** 测试侧模拟连接错误（约定行为：onerror 后应调用 close） */
  emitError(): void {
    this.onerror?.();
  }
}

beforeEach(() => {
  FakeWebSocket.instances = [];
  vi.stubGlobal('WebSocket', FakeWebSocket);
  vi.stubGlobal('window', { location: { protocol: 'http:', host: 'widget.test' } });
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function connectInScope(path: string, onMessage: (event: MessageEvent) => void): EffectScope {
  const scope = effectScope();
  scope.run(() => {
    useRawWebSocket(path, { onMessage });
  });
  return scope;
}

describe('useRawWebSocket', () => {
  it('setup 内调用即连接，消息送达 onMessage（原始 MessageEvent）', () => {
    const received: unknown[] = [];
    connectInScope('/ws/widget/danmaku', event => {
      received.push(JSON.parse(event.data));
    });

    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(FakeWebSocket.instances[0]?.url).toBe('ws://widget.test/ws/widget/danmaku');

    FakeWebSocket.instances[0]?.emitMessage({ type: 'new_message', text: 'hi' });
    expect(received).toEqual([{ type: 'new_message', text: 'hi' }]);
  });

  it('异常关闭后 3 秒重连；onerror 触发 close', () => {
    connectInScope('/ws/widget/subtitle', () => {});
    const first = FakeWebSocket.instances[0]!;

    first.emitError();
    expect(first.closed).toBe(true);
    // onerror → close → onclose 调度重连
    expect(FakeWebSocket.instances).toHaveLength(1);
    vi.advanceTimersByTime(3000);
    expect(FakeWebSocket.instances).toHaveLength(2);
    expect(FakeWebSocket.instances[1]?.url).toBe('ws://widget.test/ws/widget/subtitle');

    // 重连后的实例继续同套行为：再次异常关闭 → 满 3 秒才重连
    FakeWebSocket.instances[1]!.emitClose();
    vi.advanceTimersByTime(2999);
    expect(FakeWebSocket.instances).toHaveLength(2);
    vi.advanceTimersByTime(1);
    expect(FakeWebSocket.instances).toHaveLength(3);
  });

  it('作用域销毁后 disposed 守卫生效：close 断开且不再重连', () => {
    const scope = connectInScope('/ws/widget/game2048', () => {});
    const first = FakeWebSocket.instances[0]!;

    scope.stop();
    expect(first.closed).toBe(true);

    // 销毁后的 close 不再调度重连
    first.emitClose();
    vi.advanceTimersByTime(10_000);
    expect(FakeWebSocket.instances).toHaveLength(1);

    // 销毁前已挂起的重连定时器被清理：销毁 → 3 秒内旧连接关闭 → 无幽灵重连
    const scope2 = connectInScope('/ws/widget/game2048', () => {});
    const second = FakeWebSocket.instances[1]!;
    second.emitClose();
    scope2.stop();
    vi.advanceTimersByTime(10_000);
    expect(FakeWebSocket.instances).toHaveLength(2);
  });

  it('status 随连接生命周期流转：connecting → connected → disconnected → 重连 connecting', () => {
    let status: Ref<WidgetSocketStatus> | undefined;
    const scope = effectScope();
    scope.run(() => {
      status = useRawWebSocket('/ws/widget/danmaku', { onMessage: () => {} }).status;
    });

    expect(status!.value).toBe('connecting');
    FakeWebSocket.instances[0]!.emitOpen();
    expect(status!.value).toBe('connected');

    FakeWebSocket.instances[0]!.emitClose();
    expect(status!.value).toBe('disconnected');

    vi.advanceTimersByTime(3000);
    expect(status!.value).toBe('connecting');
    FakeWebSocket.instances[1]!.emitOpen();
    expect(status!.value).toBe('connected');
    scope.stop();
  });
});
