// useLiveStatus.ts 顶栏派生徽章的测试
//
// 覆盖：streamer.stage → 阶段 chip、观众模拟器状态拉的标签与点亮矩阵、接口失败静默。

import { effectScope, type EffectScope } from 'vue';
import { createPinia, setActivePinia } from 'pinia';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { simulatorApi } from '@/api';
import { useEventsStore, useWebSocketStore } from '@/stores';
import { useLiveStatus } from '@/composables/live/useLiveStatus';
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
  return { id, type: type as WsMessageType, timestamp_ms: tsMs, data };
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

  it('无相关事件时阶段 chip 为空', () => {
    const { stageChip } = mount();
    expect(stageChip.value).toBeNull();
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

  it('观众模拟器徽章：生成中/回放待启/未启用的标签与点亮矩阵', async () => {
    const { simulatorChip, loadSimulatorStatus } = mount();
    getStatusMock.mockResolvedValue({ data: { mode: 'generate', is_running: true } } as never);
    await loadSimulatorStatus();
    expect(simulatorChip.value).toEqual({ label: '观众模拟器 · 生成中', on: true });

    getStatusMock.mockResolvedValue({ data: { mode: 'replay', is_running: false } } as never);
    await loadSimulatorStatus();
    expect(simulatorChip.value).toEqual({ label: '观众模拟器 · 回放待启', on: false });

    getStatusMock.mockResolvedValue({ data: { mode: 'off', is_running: false } } as never);
    await loadSimulatorStatus();
    expect(simulatorChip.value).toEqual({ label: '观众模拟器未启用', on: false });
  });

  it('观众模拟器接口失败时徽章置空不抛错', async () => {
    const { simulatorChip, loadSimulatorStatus } = mount();
    getStatusMock.mockRejectedValue(new Error('down'));
    await loadSimulatorStatus();
    expect(simulatorChip.value).toBeNull();
  });
});
