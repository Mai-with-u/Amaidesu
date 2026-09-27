// useLiveStatus.ts 顶栏与环节横幅派生徽章的测试
//
// 覆盖：rundown.changed → 环节横幅（序号/标签/切换方/时刻回退）、
// streamer.stage → 阶段 chip、模拟器状态拉的标签与点亮矩阵、接口失败静默。

import { effectScope, type EffectScope } from 'vue';
import { createPinia, setActivePinia } from 'pinia';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { simulatorApi } from '@/api';
import { useEventsStore, useWebSocketStore } from '@/stores';
import { useLiveStatus } from '@/composables/live/useLiveStatus';
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

vi.mock('@/api', async importOriginal => ({
  ...(await importOriginal<object>()),
  simulatorApi: { getStatus: vi.fn(), start: vi.fn(), stop: vi.fn() },
}));

const getStatusMock = vi.mocked(simulatorApi.getStatus);

function event(
  id: string,
  type: string,
  tsMs: number,
  data: Record<string, unknown>,
): WebSocketMessage & { id: string } {
  return { id, type, timestamp_ms: tsMs, data };
}

describe('useLiveStatus', () => {
  let scope: EffectScope;

  beforeEach(() => {
    setActivePinia(createPinia());
    useWebSocketStore();
    scope = effectScope();
  });

  afterEach(() => {
    scope.stop();
    vi.clearAllMocks();
  });

  function mount() {
    return scope.run(() => useLiveStatus())!;
  }

  it('无相关事件时横幅与阶段 chip 均为空', () => {
    const { rundownBanner, stageChip } = mount();
    expect(rundownBanner.value).toBeNull();
    expect(stageChip.value).toBeNull();
  });

  it('rundown.changed：取最近一条，序号 = index+1，切换方映射标签', () => {
    const { rundownBanner } = mount();
    const store = useEventsStore();
    store.events = [
      event('1', 'rundown.changed', 1000, {
        index: 0,
        total: 3,
        segment_title: '开场',
        by: 'agent',
      }),
      event('2', 'rundown.changed', 2000, {
        index: 1,
        total: 3,
        segment_title: '唱歌',
        by: 'human',
      }),
    ];
    expect(rundownBanner.value).toMatchObject({
      order: 2,
      label: '唱歌',
      actionLabel: '手动切换',
      changedAtMs: 2000,
    });
  });

  it('rundown.changed：缺省回退——at_ms 缺失用信封时刻、无标题用"未命名环节"', () => {
    const { rundownBanner } = mount();
    const store = useEventsStore();
    store.events = [event('1', 'rundown.changed', 5000, { index: 2 })];
    expect(rundownBanner.value).toMatchObject({
      order: 3,
      label: '未命名环节',
      actionLabel: 'Agent 切换',
      changedAtMs: 5000,
    });
  });

  it('streamer.stage：未知阶段透传原文，running 随 agent_state', () => {
    const { stageChip } = mount();
    const store = useEventsStore();
    store.events = [
      event('1', 'streamer.stage', 1000, {
        stage: 'mystery',
        agent_state: 'running',
        detail: '思考中',
      }),
    ];
    expect(stageChip.value).toMatchObject({ label: 'mystery', running: true, detail: '思考中' });
  });

  it('模拟器徽章：生成中/回放待启/未启用的标签与点亮矩阵', async () => {
    const { simulatorChip, loadSimulatorStatus } = mount();
    getStatusMock.mockResolvedValue({ data: { mode: 'generate', is_running: true } } as never);
    await loadSimulatorStatus();
    expect(simulatorChip.value).toEqual({ label: '模拟器 · 生成中', on: true });

    getStatusMock.mockResolvedValue({ data: { mode: 'replay', is_running: false } } as never);
    await loadSimulatorStatus();
    expect(simulatorChip.value).toEqual({ label: '模拟器 · 回放待启', on: false });

    getStatusMock.mockResolvedValue({ data: { mode: 'off', is_running: false } } as never);
    await loadSimulatorStatus();
    expect(simulatorChip.value).toEqual({ label: '模拟器未启用', on: false });
  });

  it('模拟器接口失败时徽章置空不抛错', async () => {
    const { simulatorChip, loadSimulatorStatus } = mount();
    getStatusMock.mockRejectedValue(new Error('down'));
    await loadSimulatorStatus();
    expect(simulatorChip.value).toBeNull();
  });
});
