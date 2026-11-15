// useLiveSessions.ts 场次侧边栏组合式函数的测试
//
// 覆盖：列表加载与筛选透传、筛选防抖、模式切换（实时/回看）、选中语义、
// 生命周期开关（结束）、生命周期事件驱动的侧边栏刷新。
// 改名、改类型、删除已挪到场次管理页（那边另有测试）。

import { effectScope, nextTick, type EffectScope } from 'vue';
import { createPinia, setActivePinia } from 'pinia';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { liveSessionsApi } from '@/api';
import { ElMessage } from 'element-plus';
import { useEventsStore, useWebSocketStore } from '@/stores';
import { useLiveSessions } from '@/composables/live/useLiveSessions';
import { SESSION_FILTER_ALL } from '@/utils/sessionManager';
import { formatDateMD } from '@/utils/format';
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
    update: vi.fn(),
    setKind: vi.fn(),
    remove: vi.fn(),
    timeline: vi.fn(),
  },
}));

vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

function sessionItem(partial: Partial<LiveSessionItem>): LiveSessionItem {
  return {
    live_session_id: 1,
    source: 'manual',
    kind: 'live',
    title: null,
    notes: null,
    room_id: 'room',
    platform: 'simulator',
    started_at_ms: 9_000_000,
    ended_at_ms: null,
    message_count: 3,
    is_active: false,
    settled_at_ms: null,
    settle_attempts: 0,
    reviewed_at_ms: null,
    overview: null,
    ...partial,
  };
}

const listMock = vi.mocked(liveSessionsApi.list);
const openMock = vi.mocked(liveSessionsApi.open);
const closeMock = vi.mocked(liveSessionsApi.close);

describe('useLiveSessions', () => {
  let scope: EffectScope;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.useFakeTimers();
    // 场次生命周期事件 → 侧边栏刷新的 watch 依赖 events store
    useWebSocketStore();
    scope = effectScope();
    listMock.mockResolvedValue({ data: { items: [], active_session_id: null, total: 0 } } as never);
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
      data: { items: [sessionItem({ live_session_id: 7 })], active_session_id: 7, total: 1 },
    } as never);
    composable.sessionQuery.value = '周五';
    composable.sessionSourceFilter.value = 'replay';
    composable.sessionKindFilter.value = 'rehearsal';
    await composable.loadSessions();
    expect(listMock).toHaveBeenCalledWith({ source: 'replay', kind: 'rehearsal', q: '周五' });
    expect(composable.sessions.value).toHaveLength(1);
    expect(composable.activeSessionId.value).toBe(7);
    expect(composable.activeExplicitSession.value).toBe(true);
  });

  it('筛选值为"全部"时该维度不透传参数', async () => {
    const composable = mount();
    composable.sessionSourceFilter.value = SESSION_FILTER_ALL;
    composable.sessionKindFilter.value = SESSION_FILTER_ALL;
    await composable.loadSessions();
    expect(listMock).toHaveBeenCalledWith({ source: undefined, kind: undefined, q: undefined });
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

  it('openStartStream：只翻弹窗显隐，不直接调开场接口（开场交互迁入开播弹窗）', async () => {
    const composable = mount();
    composable.openStartStream();
    expect(composable.startStreamVisible.value).toBe(true);
    expect(openMock).not.toHaveBeenCalled();
  });

  it('onStreamOpened：回实时并刷新侧栏', async () => {
    const composable = mount();
    composable.sessionMode.value = 'replay';
    composable.selectedSession.value = sessionItem({ live_session_id: 2 });
    listMock.mockResolvedValue({ data: { items: [], active_session_id: 9, total: 0 } } as never);
    await composable.onStreamOpened();
    expect(composable.sessionMode.value).toBe('live');
    expect(composable.selectedSession.value).toBeNull();
    expect(composable.activeSessionId.value).toBe(9);
  });

  it('closeSession：无进行中场次时直接返回；有则结束并刷新', async () => {
    const composable = mount();
    await composable.closeSession();
    expect(closeMock).not.toHaveBeenCalled();

    listMock.mockResolvedValue({ data: { items: [], active_session_id: 4, total: 0 } } as never);
    await composable.loadSessions();
    await composable.closeSession();
    expect(closeMock).toHaveBeenCalledWith(4);
    expect(ElMessage.success).toHaveBeenCalledWith('场次已结束');
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

  it('sessionTitle / sessionTimeLabel 取值规则', () => {
    const composable = mount();
    expect(composable.sessionTitle(null)).toBe('');
    expect(composable.sessionTitle(sessionItem({ live_session_id: 3, title: null }))).toBe(
      '场次 #3',
    );
    // 时间标签带日期：同日终点只显时刻，跨天终点带日期
    const ended = sessionItem({ started_at_ms: 9_000_000, ended_at_ms: 9_060_000 });
    const label = composable.sessionTimeLabel(ended);
    expect(label).toContain('–');
    expect(label).toContain(formatDateMD(9_000_000));
    const crossDay = sessionItem({
      started_at_ms: 9_000_000,
      ended_at_ms: 9_000_000 + 26 * 3_600_000,
    });
    const crossLabel = composable.sessionTimeLabel(crossDay);
    expect(crossLabel).toContain(formatDateMD(9_000_000 + 26 * 3_600_000));
  });
});
