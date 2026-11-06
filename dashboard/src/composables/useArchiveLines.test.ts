// useArchiveLines 的测试：记忆页档案面板与复盘页共用的切场语义
//
// 覆盖：加载成功、切场先清空、过期响应（成功与失败）不覆盖后选场次、
// 同一场刷新保留旧行、错误与空态分离、空场次清空。

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ledgerApi } from '@/api';
import { useArchiveLines } from './useArchiveLines';

vi.mock('@/api', () => ({ ledgerApi: { listArchive: vi.fn() } }));

const listArchiveMock = vi.mocked(ledgerApi.listArchive);

function line(id: number) {
  return {
    id,
    live_session_id: 1,
    text: `标题${id}`,
    quote: `原话${id}`,
    evidence_chat_id: id,
    game: null,
    save_id: '',
    save_name: '',
    human_locked: false,
    in_snapshot: false,
    lasting: false,
    pinned: false,
  };
}

function ok(items: ReturnType<typeof line>[]) {
  return { data: { live_session_id: 1, items } } as never;
}

function deferred() {
  let resolve!: (value: never) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<never>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe('useArchiveLines', () => {
  beforeEach(() => {
    listArchiveMock.mockReset();
  });

  it('加载成功后写入档案行并结束 loading', async () => {
    listArchiveMock.mockResolvedValueOnce(ok([line(1)]));
    const state = useArchiveLines();

    const pending = state.load(5);
    expect(state.loading.value).toBe(true);
    await pending;

    expect(listArchiveMock).toHaveBeenCalledWith(5);
    expect(state.lines.value.map(item => item.id)).toEqual([1]);
    expect(state.loading.value).toBe(false);
    expect(state.error.value).toBe('');
  });

  it('审核状态与最新快照概况随行一起写入，切场时一起清空', async () => {
    listArchiveMock.mockResolvedValueOnce({
      data: {
        live_session_id: 5,
        items: [line(1)],
        reviewed_at_ms: null,
        settled: true,
        snapshot_session_id: 7,
        snapshot_pinned_count: 2,
        snapshot_pinned_max: 4,
      },
    } as never);
    const state = useArchiveLines();
    await state.load(5);
    expect(state.summary.value).toEqual({
      live_session_id: 5,
      reviewed_at_ms: null,
      settled: true,
      snapshot_session_id: 7,
      snapshot_pinned_count: 2,
      snapshot_pinned_max: 4,
    });

    listArchiveMock.mockReturnValueOnce(deferred().promise as never);
    void state.load(6);
    expect(state.summary.value).toBeNull();
  });

  it('切场先清空旧行，过期响应不覆盖后选的场次', async () => {
    const first = deferred();
    const second = deferred();
    listArchiveMock
      .mockReturnValueOnce(first.promise as never)
      .mockReturnValueOnce(second.promise as never);
    const state = useArchiveLines();
    state.lines.value = [line(9)];

    const older = state.load(1);
    const newer = state.load(2);
    // 切场先清空：新场次下不短暂显示上一场的行
    expect(state.lines.value).toEqual([]);

    second.resolve(ok([line(2)]));
    await newer;
    expect(state.lines.value.map(item => item.id)).toEqual([2]);

    first.resolve(ok([line(1)]));
    await older;
    expect(state.lines.value.map(item => item.id)).toEqual([2]);
  });

  it('同一场刷新保留旧行直到新结果到达', async () => {
    listArchiveMock.mockResolvedValueOnce(ok([line(1)]));
    const state = useArchiveLines();
    await state.load(5, { clearLines: true });

    const refresh = deferred();
    listArchiveMock.mockReturnValueOnce(refresh.promise as never);
    const pending = state.load(5, { clearLines: false });
    expect(state.lines.value.map(item => item.id)).toEqual([1]);

    refresh.resolve(ok([line(1), line(2)]));
    await pending;
    expect(state.lines.value.map(item => item.id)).toEqual([1, 2]);
  });

  it('失败写错误文案并清空行；过期的失败不回写', async () => {
    const first = deferred();
    const second = deferred();
    listArchiveMock
      .mockReturnValueOnce(first.promise as never)
      .mockReturnValueOnce(second.promise as never);
    const state = useArchiveLines();

    const older = state.load(1);
    const newer = state.load(2);
    second.reject(new Error('boom'));
    await newer;
    expect(state.error.value).toBe('boom');
    expect(state.lines.value).toEqual([]);
    expect(state.loading.value).toBe(false);

    first.reject(new Error('stale boom'));
    await older;
    expect(state.error.value).toBe('boom');
  });

  it('没有选中场次时清空并结束 loading，不发请求', async () => {
    const state = useArchiveLines();
    state.lines.value = [line(1)];

    await state.load(null);

    expect(state.lines.value).toEqual([]);
    expect(state.loading.value).toBe(false);
    expect(listArchiveMock).not.toHaveBeenCalled();
  });
});
