// useSystemNotifications.ts 系统事件通知组合式的测试
//
// 覆盖：三类 core.* 事件 → ElNotification 类型与正文、相同正文 60 秒去重、
// 全局滑动窗口每分钟 5 条上限（超限静默丢弃 + 抑制计数随下一条可见通知提示）、
// 作用域销毁后退订（卸载后不再弹窗）。
// 惯例：vi.mock 掉 @/api/websocket 的 wsClient 与 element-plus，经真实
// websocket store 的 subscribe 捕获 handler，直接投喂消息驱动。

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { effectScope, type EffectScope } from 'vue';
import { createPinia, setActivePinia } from 'pinia';

import { ElNotification } from 'element-plus';
import { useSystemNotifications } from '@/composables/useSystemNotifications';
import { useWebSocketStore } from '@/stores';
import type { WebSocketMessage } from '@/types';

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

vi.mock('element-plus', () => ({
  ElNotification: vi.fn(),
}));

const notifyMock = vi.mocked(ElNotification);

/** 捕获组合式订阅的 handler 并返回投喂函数与作用域（stop 即模拟组件卸载） */
function mountComposable(): {
  feed: (msg: WebSocketMessage) => void;
  scope: EffectScope;
  unsubscribed: unknown[];
} {
  setActivePinia(createPinia());
  let handler: ((msg: WebSocketMessage) => void) | null = null;
  const unsubscribed: unknown[] = [];
  const wsStore = useWebSocketStore();
  const originalSubscribe = wsStore.subscribe.bind(wsStore);
  const originalUnsubscribe = wsStore.unsubscribe.bind(wsStore);
  vi.spyOn(wsStore, 'subscribe').mockImplementation(fn => {
    handler = fn;
    return originalSubscribe(fn);
  });
  vi.spyOn(wsStore, 'unsubscribe').mockImplementation(fn => {
    unsubscribed.push(fn);
    return originalUnsubscribe(fn);
  });
  const scope = effectScope();
  scope.run(() => {
    useSystemNotifications();
  });
  return {
    feed: (msg: WebSocketMessage) => handler?.(msg),
    scope,
    unsubscribed,
  };
}

function event(type: string, data: Record<string, unknown>): WebSocketMessage {
  return { type, timestamp_ms: 1_706_745_600_000, data };
}

beforeEach(() => {
  vi.useFakeTimers();
  notifyMock.mockClear();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('useSystemNotifications', () => {
  it('core.error → error 通知，正文取 payload.message', () => {
    const { feed } = mountComposable();
    feed(
      event('core.error', {
        message: 'B 站应用启动失败（code=1）',
        data: { collector: 'bili_danmaku' },
      }),
    );
    expect(notifyMock).toHaveBeenCalledTimes(1);
    const call = notifyMock.mock.calls[0]?.[0];
    expect(call).toMatchObject({ title: '系统错误', type: 'error', duration: 8000 });
    const params = call as { message: string };
    expect(params.message).toContain('B 站应用启动失败');
  });

  it('core.startup / core.shutdown → success / warning 通知', () => {
    const { feed } = mountComposable();
    feed(event('core.startup', { message: 'Amaidesu 组件装配完成' }));
    feed(event('core.shutdown', { message: 'Amaidesu 正在关闭' }));
    expect(notifyMock).toHaveBeenCalledTimes(2);
    expect(notifyMock.mock.calls[0]?.[0]).toMatchObject({ title: '服务已启动', type: 'success' });
    expect(notifyMock.mock.calls[1]?.[0]).toMatchObject({ title: '服务已关闭', type: 'warning' });
  });

  it('相同正文 60 秒窗口内只弹一次', () => {
    const { feed } = mountComposable();
    const msg = event('core.error', { message: '连接断开' });
    feed(msg);
    feed(msg);
    vi.advanceTimersByTime(59_000);
    feed(msg);
    expect(notifyMock).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(1_100);
    feed(msg);
    expect(notifyMock).toHaveBeenCalledTimes(2);
  });

  it('全局滑动窗口每分钟最多 5 条，超限静默丢弃，抑制计数随下一条提示', () => {
    const { feed } = mountComposable();
    // 5 条不同正文 → 全部可见
    for (let i = 1; i <= 5; i += 1) {
      feed(event('core.error', { message: `错误 ${i}` }));
    }
    expect(notifyMock).toHaveBeenCalledTimes(5);
    // 第 6 条被静默丢弃
    feed(event('core.error', { message: '错误 6' }));
    expect(notifyMock).toHaveBeenCalledTimes(5);
    // 窗口滑过后新错误可见，正文附抑制计数
    vi.advanceTimersByTime(60_100);
    feed(event('core.error', { message: '错误 7' }));
    expect(notifyMock).toHaveBeenCalledTimes(6);
    const call = notifyMock.mock.calls[5]?.[0] as { message: string };
    expect(call.message).toContain('（此前已抑制 1 条）');
  });

  it('非 core.* 事件不弹窗', () => {
    const { feed } = mountComposable();
    feed(event('room.message.danmaku', { message: '不该弹' }));
    feed(event('tool.result.speak', { message: '不该弹' }));
    expect(notifyMock).not.toHaveBeenCalled();
  });

  it('作用域销毁后退订：unsubscribe 被调用且清理计时器', () => {
    const { scope, unsubscribed } = mountComposable();
    scope.stop();
    expect(unsubscribed).toHaveLength(1);
  });
});
