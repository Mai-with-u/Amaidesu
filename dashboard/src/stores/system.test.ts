// system store 测试
//
// 覆盖：fetchStatus 的 loading/error/status 流转、引用计数轮询的启停
// （1s 共享 setInterval 随消费方计数 0↔1 启停、多消费方共享、超额 stop 防负）。
//
// 轮询计数器 _pollInterval/_pollCount 挂在模块作用域，跨用例用
// vi.resetModules() + 动态 import 隔离；时钟用 vi.useFakeTimers 控制。
// store 的启停钩子若挂组件 activated/deactivated，组件级生命周期在本 node
// 测试基建下测不到（无组件挂载环境），此处只测 store 自身的引用计数与回调。

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import type { SystemStatusResponse } from '@/types';

// api mock：node 测试环境无后端；components store 复用同一 mock
const { getStatus, getAll } = vi.hoisted(() => ({
  getStatus: vi.fn(),
  getAll: vi.fn(),
}));
vi.mock('@/api', () => ({
  systemApi: { getStatus },
  componentApi: { getAll },
}));

/** 构造一条系统状态响应 */
function makeStatus(uptime_ms: number): { data: SystemStatusResponse } {
  const emptyGroup = { enabled: false, started: 0, total: 0 };
  return {
    data: {
      running: true,
      uptime_ms,
      version: '2.0.0',
      python_version: '3.12.0',
      groups: { collectors: emptyGroup, agents: emptyGroup, tools: emptyGroup },
      event_bus: { total_events: 0 },
    },
  };
}

/** 动态加载全新模块并建好 pinia（模块级轮询计数器随之复位） */
async function setupStore() {
  vi.resetModules();
  setActivePinia(createPinia());
  const { useSystemStore } = await import('@/stores/system');
  const { useComponentsStore } = await import('@/stores/components');
  return { store: useSystemStore(), componentsStore: useComponentsStore() };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers();
  getStatus.mockResolvedValue(makeStatus(100));
  getAll.mockResolvedValue({ data: { collectors: [], agents: [] } });
});

afterEach(() => {
  vi.useRealTimers();
});

describe('system store fetchStatus', () => {
  it('成功：status 写入响应数据，loading 完成后复位', async () => {
    const { store } = await setupStore();
    const pending = store.fetchStatus();
    expect(store.loading).toBe(true);
    await pending;

    expect(store.loading).toBe(false);
    expect(store.error).toBeNull();
    expect(store.status?.uptime_ms).toBe(100);
  });

  it('失败：error 记录消息，loading 复位，旧 status 保留', async () => {
    const { store } = await setupStore();
    await store.fetchStatus();
    getStatus.mockRejectedValue(new Error('network down'));

    await store.fetchStatus();

    expect(store.loading).toBe(false);
    expect(store.error).toBe('network down');
    // 旧数据不清空，页面不至于白屏
    expect(store.status?.uptime_ms).toBe(100);
  });

  it('失败且非 Error 抛出物：回退为通用错误文案', async () => {
    const { store } = await setupStore();
    getStatus.mockRejectedValue('boom');

    await store.fetchStatus();

    expect(store.error).toBe('Failed to fetch status');
  });
});

describe('system store 引用计数轮询', () => {
  it('startPolling 首次立即拉取一次，且同时刷新系统状态与组件清单', async () => {
    const { store } = await setupStore();
    store.startPolling();
    await vi.advanceTimersByTimeAsync(0);

    expect(getStatus).toHaveBeenCalledTimes(1);
    expect(getAll).toHaveBeenCalledTimes(1);
  });

  it('按传入间隔重复轮询（默认 1s）', async () => {
    const { store } = await setupStore();
    store.startPolling();
    await vi.advanceTimersByTimeAsync(0);
    expect(getStatus).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(1000);
    expect(getStatus).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1000);
    expect(getStatus).toHaveBeenCalledTimes(3);
    // 间隔未到不拉取
    await vi.advanceTimersByTimeAsync(500);
    expect(getStatus).toHaveBeenCalledTimes(3);
  });

  it('stopPolling 归零后停止轮询', async () => {
    const { store } = await setupStore();
    store.startPolling();
    await vi.advanceTimersByTimeAsync(0);

    store.stopPolling();
    const calls = getStatus.mock.calls.length;
    await vi.advanceTimersByTimeAsync(5000);

    expect(getStatus).toHaveBeenCalledTimes(calls);
  });

  it('多消费方共享同一轮询：一方 stop 不停，计数归零才停', async () => {
    const { store } = await setupStore();
    store.startPolling();
    store.startPolling();
    await vi.advanceTimersByTimeAsync(0);
    expect(getStatus).toHaveBeenCalledTimes(1);

    store.stopPolling();
    await vi.advanceTimersByTimeAsync(1000);
    const callsAfterFirstStop = getStatus.mock.calls.length;
    expect(callsAfterFirstStop).toBeGreaterThan(1);

    store.stopPolling();
    await vi.advanceTimersByTimeAsync(5000);
    expect(getStatus).toHaveBeenCalledTimes(callsAfterFirstStop);
  });

  it('超额 stopPolling 计数不下探负数，后续 startPolling 仍能正常轮询', async () => {
    const { store } = await setupStore();
    store.stopPolling(); // 未启动时超额 stop

    store.startPolling();
    await vi.advanceTimersByTimeAsync(0);
    expect(getStatus).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(1000);
    expect(getStatus).toHaveBeenCalledTimes(2);
  });

  it('计数归零后再 start：改用新传入的间隔', async () => {
    const { store } = await setupStore();
    store.startPolling(1000);
    await vi.advanceTimersByTimeAsync(0);
    store.stopPolling();

    store.startPolling(2000);
    await vi.advanceTimersByTimeAsync(0);
    const calls = getStatus.mock.calls.length;

    await vi.advanceTimersByTimeAsync(1999);
    expect(getStatus).toHaveBeenCalledTimes(calls);
    await vi.advanceTimersByTimeAsync(1);
    expect(getStatus).toHaveBeenCalledTimes(calls + 1);
  });

  it('已轮询中再次 startPolling：共享既有定时器，不重置间隔', async () => {
    const { store } = await setupStore();
    store.startPolling(1000);
    await vi.advanceTimersByTimeAsync(0);
    store.startPolling(9999); // 第二个消费方想要别的间隔，不影响共享定时器

    await vi.advanceTimersByTimeAsync(1000);
    expect(getStatus).toHaveBeenCalledTimes(2);
  });
});
