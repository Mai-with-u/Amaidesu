// @vitest-environment happy-dom
// useAgentMemo.ts 备忘录/待办面板组合式的测试
//
// 覆盖：挂载即拉双快照、streamer.notebook.changed 触发主播侧刷新、
// tool.result.*（minecraft_notebook/todo/report 的 tool_name）触发 minecraft
// 侧刷新、无关事件不触发、30 秒轮询兜底、失败静默保留旧值。

import { effectScope, type EffectScope } from 'vue';
import { createPinia, setActivePinia } from 'pinia';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { agentsApi } from '@/api';
import { useEventsStore } from '@/stores';
import { MEMO_POLL_INTERVAL_MS, useAgentMemo } from '@/composables/useAgentMemo';

vi.mock('@/api', async importOriginal => ({
  ...(await importOriginal<object>()),
  agentsApi: { getAgentMemo: vi.fn() },
}));

// events store 用响应式假体替代真实模块（真实链路会在 node 测试环境实例化
// WebSocket 客户端）；组合式只读 events 数组，假体同形即可
vi.mock('@/stores', async () => {
  const { reactive } = await import('vue');
  const eventsStore = reactive<{ events: unknown[] }>({ events: [] });
  return { useEventsStore: () => eventsStore };
});

const getAgentMemoMock = vi.mocked(agentsApi.getAgentMemo);

/** 快照响应体（按目标给不同形态：主播只有 notebook；minecraft 三元组） */
function memoResponse(name: string, overrides: Record<string, unknown> = {}) {
  const base =
    name === 'streamer'
      ? { name, notebook: '主播笔记' }
      : { name, notebook: '', todos: [], reports: [] };
  return { data: { ...base, ...overrides } } as never;
}

let seq = 0;
/** 往事件 store 塞一条 WS 事件（shape 与 events store 入口一致） */
function pushEvent(type: string, data: Record<string, unknown> = {}): void {
  const store = useEventsStore();
  seq += 1;
  store.events = [...store.events, { id: `e${seq}`, type, timestamp_ms: 1000, data }];
}

describe('useAgentMemo', () => {
  let scope: EffectScope;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.useFakeTimers();
    scope = effectScope();
    getAgentMemoMock.mockReset();
    getAgentMemoMock.mockImplementation(name => Promise.resolve(memoResponse(name)));
    useEventsStore().events = [];
  });

  afterEach(() => {
    scope.stop();
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  function mount() {
    return scope.run(() => useAgentMemo())!;
  }

  /** 指定目标的快照拉取次数 */
  const calledWith = (name: string) =>
    getAgentMemoMock.mock.calls.filter(([n]) => n === name).length;

  it('挂载即拉主播与 minecraft 两个快照', async () => {
    const composable = mount();
    await vi.advanceTimersByTimeAsync(0);
    expect(calledWith('streamer')).toBe(1);
    expect(calledWith('minecraft')).toBe(1);
    expect(composable.streamerNotebook.value).toBe('主播笔记');
    expect(composable.minecraftMemo.value).toEqual({ notebook: '', todos: [], reports: [] });
  });

  it('streamer.notebook.changed 触发主播侧重拉，minecraft 不动', async () => {
    getAgentMemoMock.mockImplementation(name =>
      Promise.resolve(memoResponse(name, name === 'streamer' ? { notebook: '新全文' } : {})),
    );
    const composable = mount();
    await vi.advanceTimersByTimeAsync(0);

    pushEvent('streamer.notebook.changed', { notebook: '新全文' });
    await vi.advanceTimersByTimeAsync(0);
    expect(calledWith('streamer')).toBe(2);
    expect(calledWith('minecraft')).toBe(1);
    expect(composable.streamerNotebook.value).toBe('新全文');
  });

  it('tool.result 的 minecraft_notebook/todo/report 触发 minecraft 侧重拉', async () => {
    getAgentMemoMock.mockImplementation(name =>
      Promise.resolve(memoResponse(name, name === 'minecraft' ? { notebook: '已拿齐小麦' } : {})),
    );
    const composable = mount();
    await vi.advanceTimersByTimeAsync(0);

    pushEvent('tool.result.minecraft_notebook', { tool_name: 'minecraft_notebook' });
    pushEvent('tool.result.minecraft_todo', { tool_name: 'minecraft_todo' });
    await vi.advanceTimersByTimeAsync(0);
    expect(calledWith('minecraft')).toBe(2);
    expect(calledWith('streamer')).toBe(1);
    expect(composable.minecraftMemo.value.notebook).toBe('已拿齐小麦');
  });

  it('无关事件与无关 tool_name 不触发刷新', async () => {
    mount();
    await vi.advanceTimersByTimeAsync(0);

    pushEvent('planner.decision');
    pushEvent('tool.result.minecraft_speak', { tool_name: 'minecraft_speak' });
    pushEvent('tool.result.streamer_reply', { tool_name: 'streamer_reply' });
    await vi.advanceTimersByTimeAsync(0);
    expect(calledWith('streamer')).toBe(1);
    expect(calledWith('minecraft')).toBe(1);
  });

  it('无事件时轮询兜底刷新双快照', async () => {
    mount();
    await vi.advanceTimersByTimeAsync(0);
    expect(calledWith('streamer')).toBe(1);

    await vi.advanceTimersByTimeAsync(MEMO_POLL_INTERVAL_MS);
    expect(calledWith('streamer')).toBe(2);
    expect(calledWith('minecraft')).toBe(2);
  });

  it('快照失败静默保留旧值，不抛出', async () => {
    const composable = mount();
    await vi.advanceTimersByTimeAsync(0);
    expect(composable.streamerNotebook.value).toBe('主播笔记');

    getAgentMemoMock.mockRejectedValue(new Error('后端未装配'));
    pushEvent('streamer.notebook.changed');
    await vi.advanceTimersByTimeAsync(0);
    expect(composable.streamerNotebook.value).toBe('主播笔记');
  });
});
