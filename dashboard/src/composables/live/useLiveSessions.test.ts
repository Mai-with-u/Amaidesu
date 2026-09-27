// useLiveSessions.ts 场次侧边栏组合式函数的测试
//
// 覆盖：列表加载与筛选透传、筛选防抖、模式切换（实时/回看）、选中语义、
// 生命周期开关（开启/结束/删除）、生命周期事件驱动的侧边栏刷新。

import { effectScope, nextTick, type EffectScope } from 'vue';
import { createPinia, setActivePinia } from 'pinia';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { liveSessionsApi } from '@/api';
import { ElMessage, ElMessageBox } from 'element-plus';
import { useEventsStore, useWebSocketStore } from '@/stores';
import { useLiveSessions } from '@/composables/live/useLiveSessions';
import type { LiveSessionItem } from '@/types';

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
  liveSessionsApi: {
    list: vi.fn(),
    open: vi.fn(),
    close: vi.fn(),
    remove: vi.fn(),
    timeline: vi.fn(),
  },
}));

vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  ElMessageBox: { prompt: vi.fn() },
}));

function sessionItem(partial: Partial<LiveSessionItem>): LiveSessionItem {
  return {
    live_session_id: 1,
    source: 'manual',
    title: null,
    room_id: 'room',
    platform: 'simulator',
    started_at_ms: 9_000_000,
    ended_at_ms: null,
    message_count: 3,
    is_active: false,
    ...partial,
  };
}

const listMock = vi.mocked(liveSessionsApi.list);
const openMock = vi.mocked(liveSessionsApi.open);
const closeMock = vi.mocked(liveSessionsApi.close);
const removeMock = vi.mocked(liveSessionsApi.remove);
const promptMock = vi.mocked(ElMessageBox.prompt);

describe('useLiveSessions', () => {
  let scope: EffectScope;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.useFakeTimers();
    // 场次生命周期事件 → 侧边栏刷新的 watch 依赖 events store
    useWebSocketStore();
    scope = effectScope();
    listMock.mockResolvedValue({ data: { items: [], active_session_id: null } } as never);
  });

  afterEach(() => {
    scope.stop();
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  function mount() {
    return scope.run(() => useLiveSessions())!;
  }

  it('loadSessions：填充列表与进行中场次主键，筛选参数透传服务端', async () => {
    const composable = mount();
    listMock.mockResolvedValue({
      data: { items: [sessionItem({ live_session_id: 7 })], active_session_id: 7 },
    } as never);
    composable.sessionQuery.value = '周五';
    composable.sessionSourceFilter.value = 'replay';
    await composable.loadSessions();
    expect(listMock).toHaveBeenCalledWith({ source: 'replay', q: '周五' });
    expect(composable.sessions.value).toHaveLength(1);
    expect(composable.activeSessionId.value).toBe(7);
    expect(composable.activeExplicitSession.value).toBe(true);
  });

  it('筛选变化经 250ms 防抖后重新拉取', async () => {
    const composable = mount();
    composable.sessionQuery.value = 'x';
    await vi.advanceTimersByTimeAsync(100);
    expect(listMock).toHaveBeenCalledTimes(0);
    composable.sessionQuery.value = 'xy';
    await vi.advanceTimersByTimeAsync(250);
    expect(listMock).toHaveBeenCalledTimes(1);
  });

  it('loadSessions 失败静默：侧边栏保持空态', async () => {
    const composable = mount();
    listMock.mockRejectedValue(new Error('network'));
    await composable.loadSessions();
    expect(composable.sessions.value).toEqual([]);
  });

  it('selectSession：进行中场次回实时；历史场次进回看并选中', () => {
    const composable = mount();
    const active = sessionItem({ live_session_id: 1, is_active: true });
    const history = sessionItem({ live_session_id: 2, is_active: false });
    composable.selectSession(active);
    expect(composable.sessionMode.value).toBe('live');
    composable.selectSession(history);
    expect(composable.sessionMode.value).toBe('replay');
    expect(composable.selectedSession.value?.live_session_id).toBe(2);
    composable.backToLive();
    expect(composable.sessionMode.value).toBe('live');
    expect(composable.selectedSession.value).toBeNull();
  });

  it('isSelected：实时模式跟随 is_active；回看模式跟随选中场次', () => {
    const composable = mount();
    const live = sessionItem({ live_session_id: 1, is_active: true });
    const idle = sessionItem({ live_session_id: 2, is_active: false });
    expect(composable.isSelected(live)).toBe(true);
    expect(composable.isSelected(idle)).toBe(false);
    composable.selectSession(idle);
    expect(composable.isSelected(live)).toBe(false);
    expect(composable.isSelected(idle)).toBe(true);
  });

  it('openSession：取消输入不调接口；确认后开场次且回实时', async () => {
    const composable = mount();
    promptMock.mockRejectedValueOnce('cancel');
    await composable.openSession();
    expect(openMock).not.toHaveBeenCalled();

    promptMock.mockResolvedValueOnce({ value: ' 周五晚间场 ' } as never);
    openMock.mockResolvedValue({ data: { live_session_id: 9 } } as never);
    composable.sessionMode.value = 'replay';
    await composable.openSession();
    expect(openMock).toHaveBeenCalledWith({ title: '周五晚间场' });
    expect(ElMessage.success).toHaveBeenCalledWith('场次已开启');
    expect(composable.sessionMode.value).toBe('live');
  });

  it('openSession：接口失败提示错误且不切换模式', async () => {
    const composable = mount();
    promptMock.mockResolvedValueOnce({ value: '' } as never);
    openMock.mockRejectedValue(new Error('boom'));
    await composable.openSession();
    expect(openMock).toHaveBeenCalledWith({ title: undefined });
    expect(ElMessage.error).toHaveBeenCalledWith('开启场次失败：boom');
    expect(composable.activeExplicitSession.value).toBe(false);
  });

  it('closeSession：无进行中场次时直接返回', async () => {
    const composable = mount();
    await composable.closeSession();
    expect(closeMock).not.toHaveBeenCalled();
  });

  it('removeSession：删除后刷新列表；删除的是回看中场次则退出回看', async () => {
    const composable = mount();
    const target = sessionItem({ live_session_id: 2 });
    removeMock.mockResolvedValue({ data: { success: true, detail: '' } } as never);
    composable.selectSession(target);
    await composable.removeSession(target);
    expect(removeMock).toHaveBeenCalledWith(2);
    expect(composable.sessionMode.value).toBe('live');
    expect(listMock).toHaveBeenCalled();
  });

  it('live.ended 生命周期事件触发侧边栏刷新', async () => {
    mount();
    const store = useEventsStore();
    await nextTick();
    listMock.mockClear();
    store.events = [
      { id: '1', type: 'room.message', timestamp_ms: 1, data: {} },
      { id: '2', type: 'live.ended', timestamp_ms: 2, data: {} },
    ];
    await nextTick();
    await vi.advanceTimersByTimeAsync(0);
    expect(listMock).toHaveBeenCalled();
  });

  it('sessionTitle / sourceLabel / sessionTimeLabel 取值规则', () => {
    const composable = mount();
    expect(composable.sessionTitle(null)).toBe('');
    expect(composable.sessionTitle(sessionItem({ live_session_id: 3, title: null }))).toBe(
      '场次 #3',
    );
    expect(composable.sourceLabel('replay')).toBe('回放');
    expect(composable.sourceLabel('other')).toBe('other');
    const ended = sessionItem({ started_at_ms: 9_000_000, ended_at_ms: 9_060_000 });
    const label = composable.sessionTimeLabel(ended);
    expect(label).toContain('–');
  });
});
