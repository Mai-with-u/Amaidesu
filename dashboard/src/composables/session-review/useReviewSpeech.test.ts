// useReviewSpeech 的测试：本场台词加载与删除的状态机
//
// 覆盖：加载成功、切场清空与过期响应丢弃、错误态、空场次清空、
// 删除成功返回回执、删除失败写文案并返回 null、busy 标记复位。

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { reviewApi } from '@/api';
import { useReviewSpeech } from './useReviewSpeech';

vi.mock('@/api', () => ({ reviewApi: { listSpeech: vi.fn(), deleteSpeech: vi.fn() } }));

const listSpeechMock = vi.mocked(reviewApi.listSpeech);
const deleteSpeechMock = vi.mocked(reviewApi.deleteSpeech);

function line(chatId: number, dropped = false) {
  return {
    chat_id: chatId,
    timestamp_ms: 1_700_000_000_000 + chatId,
    content: `第 ${chatId} 句`,
    dropped,
    memory_refs: [],
  };
}

function listOk(items: ReturnType<typeof line>[]) {
  return { data: { items } } as never;
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

describe('useReviewSpeech', () => {
  beforeEach(() => {
    listSpeechMock.mockReset();
    deleteSpeechMock.mockReset();
  });

  it('加载成功后写入台词并结束 loading', async () => {
    listSpeechMock.mockResolvedValueOnce(listOk([line(1), line(2, true)]));
    const state = useReviewSpeech();

    const pending = state.load(7);
    expect(state.loading.value).toBe(true);
    await pending;

    expect(listSpeechMock).toHaveBeenCalledWith(7);
    expect(state.items.value.map(item => item.chat_id)).toEqual([1, 2]);
    expect(state.items.value[1].dropped).toBe(true);
    expect(state.loading.value).toBe(false);
  });

  it('切场先清空，过期响应不覆盖后选的场次', async () => {
    const first = deferred();
    const second = deferred();
    listSpeechMock
      .mockReturnValueOnce(first.promise as never)
      .mockReturnValueOnce(second.promise as never);
    const state = useReviewSpeech();
    state.items.value = [line(99)];

    const older = state.load(1);
    const newer = state.load(2);
    expect(state.items.value).toEqual([]);

    second.resolve(listOk([line(2)]));
    await newer;
    expect(state.items.value.map(item => item.chat_id)).toEqual([2]);

    first.resolve(listOk([line(1)]));
    await older;
    expect(state.items.value.map(item => item.chat_id)).toEqual([2]);
  });

  it('加载失败写文案并清空台词；空场次清空且不发请求', async () => {
    listSpeechMock.mockRejectedValueOnce(new Error('boom'));
    const state = useReviewSpeech();
    state.items.value = [line(1)];

    await state.load(3);
    expect(state.error.value).toBe('boom');
    expect(state.items.value).toEqual([]);

    listSpeechMock.mockClear();
    await state.load(null);
    expect(state.items.value).toEqual([]);
    expect(state.loading.value).toBe(false);
    expect(listSpeechMock).not.toHaveBeenCalled();
  });

  it('删除成功返回回执并复位 busy', async () => {
    const payload = { deleted: true, archive_lines_removed: 2, commitments_cleared: 1 };
    deleteSpeechMock.mockResolvedValueOnce({ data: payload } as never);
    const state = useReviewSpeech();

    const pending = state.remove(42);
    expect(state.busyChatId.value).toBe(42);
    await expect(pending).resolves.toEqual(payload);
    expect(deleteSpeechMock).toHaveBeenCalledWith(42);
    expect(state.busyChatId.value).toBeNull();
  });

  it('删除失败写 actionError、保留列表、返回 null 并复位 busy', async () => {
    deleteSpeechMock.mockRejectedValueOnce(new Error('404 找不到这句台词'));
    const state = useReviewSpeech();
    state.items.value = [line(1)];

    const result = await state.remove(42);
    expect(result).toBeNull();
    expect(state.actionError.value).toBe('404 找不到这句台词');
    expect(state.error.value).toBe('');
    expect(state.items.value).toHaveLength(1);
    expect(state.busyChatId.value).toBeNull();
  });
});
