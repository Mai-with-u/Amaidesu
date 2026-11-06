// useReviewCues 的测试：本场线索卡的加载与来源筛选
//
// 覆盖：加载成功、切场清空并复位筛选、过期响应丢弃、错误态、空场次清空。

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { reviewApi } from '@/api';
import { useReviewCues } from './useReviewCues';

vi.mock('@/api', () => ({ reviewApi: { listCues: vi.fn() } }));

const listCuesMock = vi.mocked(reviewApi.listCues);

function cue(id: number, sources: string[]) {
  return {
    id,
    pushed_at_ms: 1_700_000_000_000 + id,
    card_kind: 'rule',
    label: `档#${id}`,
    title: `第 ${id} 行`,
    archive_line_id: id,
    commitment_id: null,
    sources,
    detail: {},
    self_triggered: false,
    used_at_ms: null,
  };
}

function listOk(items: ReturnType<typeof cue>[]) {
  return { data: { items } } as never;
}

function deferred() {
  let resolve!: (value: never) => void;
  const promise = new Promise<never>(res => {
    resolve = res;
  });
  return { promise, resolve };
}

describe('useReviewCues', () => {
  beforeEach(() => {
    listCuesMock.mockReset();
  });

  it('加载成功后写入卡片，按来源筛选只在本地做', async () => {
    listCuesMock.mockResolvedValueOnce(listOk([cue(1, ['danmaku']), cue(2, ['self'])]));
    const state = useReviewCues();

    const pending = state.load(7);
    expect(state.loading.value).toBe(true);
    await pending;

    expect(listCuesMock).toHaveBeenCalledWith(7);
    expect(state.sourceOptions.value).toEqual(['danmaku', 'self']);
    state.source.value = 'self';
    expect(state.visible.value.map(item => item.id)).toEqual([2]);
    expect(state.loading.value).toBe(false);
  });

  it('切场先清空并复位筛选，过期响应不覆盖后选的场次', async () => {
    const slow = deferred();
    listCuesMock
      .mockReturnValueOnce(slow.promise)
      .mockResolvedValueOnce(listOk([cue(9, ['place'])]));
    const state = useReviewCues();
    state.source.value = 'danmaku';

    const first = state.load(1);
    const second = state.load(2);
    expect(state.source.value).toBe('');
    await second;
    slow.resolve(listOk([cue(1, ['danmaku'])]));
    await first;

    expect(state.items.value.map(item => item.id)).toEqual([9]);
  });

  it('加载失败写错误文案、列表为空；没有场次直接清空', async () => {
    listCuesMock.mockRejectedValueOnce(new Error('网络断了'));
    const state = useReviewCues();

    await state.load(3);
    expect(state.error.value).not.toBe('');
    expect(state.items.value).toEqual([]);

    await state.load(null);
    expect(state.error.value).toBe('');
    expect(listCuesMock).toHaveBeenCalledTimes(1);
  });
});
