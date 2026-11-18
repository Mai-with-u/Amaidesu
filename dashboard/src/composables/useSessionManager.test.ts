// useSessionManager.ts 场次管理页组合式函数的测试
//
// 覆盖：清单取数与筛选透传（含分页）、清单汇总（同口径全集 + 视图形状映射）、
// 筛选防抖、URL 还原筛选、逐场处置（改名 / 备注 / 改类型 / 删除 / 重新整理 /
// 标已审）与批量处置。

import { effectScope, type EffectScope } from 'vue';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ledgerApi, liveSessionsApi, reviewApi } from '@/api';
import { ElMessage, ElMessageBox } from 'element-plus';
import { useSessionManager } from '@/composables/useSessionManager';
import { SESSION_FILTER_ALL } from '@/utils/sessionManager';
import type { LiveSessionItem } from '@/types';

const replaceMock = vi.fn();
let mockQuery: Record<string, unknown> = {};

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: mockQuery }),
  useRouter: () => ({ replace: replaceMock }),
}));

vi.mock('@/api', async importOriginal => ({
  ...(await importOriginal<object>()),
  liveSessionsApi: {
    list: vi.fn(),
    summary: vi.fn(),
    update: vi.fn(),
    setKind: vi.fn(),
    remove: vi.fn(),
  },
  ledgerApi: {
    resettle: vi.fn(),
    markReviewed: vi.fn(),
  },
  reviewApi: {
    exportUrl: vi.fn((ids: number[]) => `/api/v1/review/export?ids=${ids.join(',')}`),
  },
}));

vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  ElMessageBox: { prompt: vi.fn(), confirm: vi.fn() },
}));

function sessionItem(partial: Partial<LiveSessionItem>): LiveSessionItem {
  return {
    live_session_id: 1,
    source: 'manual',
    kind: 'live',
    title: '周五场',
    notes: null,
    room_id: 'room',
    platform: 'bilibili',
    started_at_ms: 9_000_000,
    ended_at_ms: 9_600_000,
    message_count: 12,
    is_active: false,
    settled_at_ms: null,
    settle_attempts: 0,
    reviewed_at_ms: null,
    overview: null,
    ...partial,
  };
}

const listMock = vi.mocked(liveSessionsApi.list);
const summaryMock = vi.mocked(liveSessionsApi.summary);
const updateMock = vi.mocked(liveSessionsApi.update);
const setKindMock = vi.mocked(liveSessionsApi.setKind);
const removeMock = vi.mocked(liveSessionsApi.remove);
const resettleMock = vi.mocked(ledgerApi.resettle);
const markReviewedMock = vi.mocked(ledgerApi.markReviewed);
const exportUrlMock = vi.mocked(reviewApi.exportUrl);
const promptMock = vi.mocked(ElMessageBox.prompt);
const confirmMock = vi.mocked(ElMessageBox.confirm);

describe('useSessionManager', () => {
  let scope: EffectScope;

  beforeEach(() => {
    mockQuery = {};
    scope = effectScope();
    listMock.mockResolvedValue({
      data: { items: [sessionItem({ live_session_id: 7 })], active_session_id: null, total: 1 },
    } as never);
    summaryMock.mockResolvedValue({
      data: {
        total: 45,
        total_duration_ms: 87_131_000,
        average_duration_ms: 1_936_244,
        longest_duration_ms: 11_080_000,
        message_total: 3_434,
        kind_counts: { live: 45 },
        source_counts: { manual: 45 },
        daily: [{ date: '2026-11-17', count: 1, duration_ms: 2_926_000 }],
      },
    } as never);
    updateMock.mockResolvedValue({ data: { success: true, detail: 'ok' } } as never);
    setKindMock.mockResolvedValue({ data: { success: true, detail: 'ok' } } as never);
    removeMock.mockResolvedValue({ data: { success: true, detail: 'ok' } } as never);
    resettleMock.mockResolvedValue({ data: { success: true, detail: 'ok' } } as never);
    markReviewedMock.mockResolvedValue({ data: { success: true, detail: 'ok' } } as never);
  });

  afterEach(() => {
    scope.stop();
    vi.clearAllMocks();
  });

  function mount() {
    return scope.run(() => useSessionManager())!;
  }

  it('load：筛选与分页参数透传服务端，填列表与总数', async () => {
    const composable = mount();
    composable.query.value = ' 周五 ';
    composable.sourceFilter.value = 'manual';
    composable.kindFilter.value = SESSION_FILTER_ALL;
    composable.page.value = 3;
    composable.pageSize.value = 20;
    await composable.load();
    expect(listMock).toHaveBeenCalledWith(
      expect.objectContaining({
        q: '周五',
        source: 'manual',
        kind: undefined,
        limit: 20,
        offset: 40,
      }),
    );
    expect(composable.items.value).toHaveLength(1);
    expect(composable.total.value).toBe(1);
  });

  it('load：清单汇总同口径取全集并映射成视图形状（缺的类型补零）', async () => {
    const composable = mount();
    composable.query.value = ' 周五 ';
    composable.kindFilter.value = 'rehearsal';
    await composable.load();
    // 汇总不带分页参数，带 now_ms 供进行中场次截算时长
    expect(summaryMock).toHaveBeenCalledWith(
      expect.objectContaining({ q: '周五', kind: 'rehearsal', now_ms: expect.any(Number) }),
    );
    expect(composable.summary.value).toEqual({
      count: 45,
      totalDurationMs: 87_131_000,
      averageDurationMs: 1_936_244,
      longestDurationMs: 11_080_000,
      messageTotal: 3_434,
      // 响应里没有 rehearsal 键，视图形状补零
      kindCounts: { live: 45, rehearsal: 0 },
      sourceCounts: { manual: 45 },
    });
    expect(composable.dailyBuckets.value).toEqual([
      { date: '2026-11-17', count: 1, durationMs: 2_926_000 },
    ]);
  });

  it('筛选变化：回到第一页并经 250ms 防抖后重取', async () => {
    vi.useFakeTimers();
    try {
      const composable = mount();
      composable.page.value = 4;
      // 页码变化本身会立刻重取一次；等它落定后再看关键字的防抖
      await vi.advanceTimersByTimeAsync(0);
      listMock.mockClear();
      composable.query.value = 'x';
      await vi.advanceTimersByTimeAsync(249);
      expect(listMock).not.toHaveBeenCalled();
      await vi.advanceTimersByTimeAsync(1);
      expect(listMock).toHaveBeenCalledTimes(1);
      expect(composable.page.value).toBe(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it('applyQueryFromUrl：认 URL 上的筛选，非法值忽略', () => {
    mockQuery = { q: '周五', source: 'replay', kind: 'rehearsal', state: 'review' };
    const composable = mount();
    composable.applyQueryFromUrl();
    expect(composable.query.value).toBe('周五');
    expect(composable.sourceFilter.value).toBe('replay');
    expect(composable.kindFilter.value).toBe('rehearsal');
    expect(composable.settleFilter.value).toBe('review');
  });

  it('applyQueryFromUrl：非法取值落回不限', () => {
    mockQuery = { source: 'hacker', kind: 'fake', state: 'nope' };
    const composable = mount();
    composable.applyQueryFromUrl();
    expect(composable.sourceFilter.value).toBe(SESSION_FILTER_ALL);
    expect(composable.kindFilter.value).toBe(SESSION_FILTER_ALL);
    expect(composable.settleFilter.value).toBe('all');
  });

  it('renameSession：确认后调更新接口并刷新', async () => {
    const composable = mount();
    promptMock.mockResolvedValueOnce({ value: ' 新名字 ' } as never);
    await composable.renameSession(sessionItem({ live_session_id: 7 }));
    expect(updateMock).toHaveBeenCalledWith(7, { title: '新名字' });
    expect(listMock).toHaveBeenCalled();
  });

  it('renameSession：取消输入不调接口', async () => {
    const composable = mount();
    promptMock.mockRejectedValueOnce('cancel');
    await composable.renameSession(sessionItem({ live_session_id: 7 }));
    expect(updateMock).not.toHaveBeenCalled();
  });

  it('saveNotes：写备注走更新接口', async () => {
    const composable = mount();
    await composable.saveNotes(sessionItem({ live_session_id: 7 }), ' 这场翻车了 ');
    expect(updateMock).toHaveBeenCalledWith(7, { notes: '这场翻车了' });
  });

  it('toggleSessionKind：确认后改类型；取消则不改', async () => {
    const composable = mount();
    confirmMock.mockRejectedValueOnce('cancel');
    await composable.toggleSessionKind(sessionItem({ live_session_id: 7, kind: 'live' }));
    expect(setKindMock).not.toHaveBeenCalled();

    confirmMock.mockResolvedValueOnce('confirm' as never);
    await composable.toggleSessionKind(sessionItem({ live_session_id: 7, kind: 'live' }));
    expect(setKindMock).toHaveBeenCalledWith(7, 'rehearsal');
  });

  it('removeSession：确认后删除并刷新', async () => {
    const composable = mount();
    confirmMock.mockResolvedValueOnce('confirm' as never);
    await composable.removeSession(sessionItem({ live_session_id: 7 }));
    expect(removeMock).toHaveBeenCalledWith(7);
    expect(listMock).toHaveBeenCalled();
  });

  it('batchSetKind：逐场改类型，失败的场次单独报数', async () => {
    const composable = mount();
    confirmMock.mockResolvedValueOnce('confirm' as never);
    setKindMock.mockRejectedValueOnce(new Error('busy'));
    await composable.batchSetKind('rehearsal', [7, 8]);
    expect(setKindMock).toHaveBeenCalledTimes(2);
    expect(ElMessage.warning).toHaveBeenCalledWith('1 场改不了（进行中的场次不能改类型）');
  });

  it('batchRemove：逐场删除', async () => {
    const composable = mount();
    confirmMock.mockResolvedValueOnce('confirm' as never);
    await composable.batchRemove([7, 8]);
    expect(removeMock).toHaveBeenCalledTimes(2);
  });

  it('批量操作没勾选时只提示不动手', async () => {
    const composable = mount();
    await composable.batchSetKind('live', []);
    await composable.batchRemove([]);
    expect(setKindMock).not.toHaveBeenCalled();
    expect(removeMock).not.toHaveBeenCalled();
    expect(ElMessage.info).toHaveBeenCalled();
  });

  it('resettle / markReviewed：走记忆账本的整理端点', async () => {
    const composable = mount();
    await composable.resettle(sessionItem({ live_session_id: 7 }));
    expect(resettleMock).toHaveBeenCalledWith(7);
    await composable.markReviewed(sessionItem({ live_session_id: 7 }));
    expect(markReviewedMock).toHaveBeenCalledWith(7);
  });

  it('exportUrl：用勾选的场次拼导出地址；没勾选只提示', () => {
    const composable = mount();
    composable.setSelection([7, 8]);
    expect(composable.exportUrl()).toBe('/api/v1/review/export?ids=7,8');
    expect(exportUrlMock).toHaveBeenCalledWith([7, 8]);

    composable.clearSelection();
    expect(composable.exportUrl()).toBeNull();
    expect(ElMessage.info).toHaveBeenCalled();
  });
});
