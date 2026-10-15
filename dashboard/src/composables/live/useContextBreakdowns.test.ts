// useContextBreakdowns.ts 水位胶囊悬停明细缓存的测试
//
// 覆盖：首次展开懒取并缓存、同请求复用不重复取、无分段（null 响应）不缓存
// 可再取、失败静默降级、无指针条目直接空。

import { effectScope, type EffectScope } from 'vue';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { llmApi } from '@/api';
import { useContextBreakdowns } from '@/composables/live/useContextBreakdowns';
import { makeEntry, type ShowEntry } from '@/utils/liveFeed';

vi.mock('@/api', async importOriginal => ({
  ...(await importOriginal<object>()),
  llmApi: {
    getContextBreakdownByRequest: vi.fn(),
  },
}));

const breakdownMock = vi.mocked(llmApi.getContextBreakdownByRequest);

function decisionEntry(id: string, requestId: string): ShowEntry {
  return makeEntry({ id, kind: 'decision', tsMs: 1000, text: '决策', llmRequestId: requestId });
}

const breakdown = { segments: [] } as never;

describe('useContextBreakdowns', () => {
  let scope: EffectScope;

  beforeEach(() => {
    scope = effectScope();
    breakdownMock.mockReset();
  });

  afterEach(() => {
    scope.stop();
    vi.clearAllMocks();
  });

  function mount() {
    return scope.run(() => useContextBreakdowns())!;
  }

  it('首次展开懒取并缓存，同请求复用不重复取', async () => {
    breakdownMock.mockResolvedValue({ data: breakdown } as never);
    const composable = mount();
    const entry = decisionEntry('e1', 'r1');
    expect(composable.ctxBreakdownOf(entry)).toBeNull();

    await composable.onCtxPillShow(entry);
    expect(breakdownMock).toHaveBeenCalledTimes(1);
    expect(breakdownMock).toHaveBeenCalledWith('r1');
    expect(composable.ctxBreakdownOf(entry)).toStrictEqual(breakdown);

    await composable.onCtxPillShow(entry);
    expect(breakdownMock).toHaveBeenCalledTimes(1);
  });

  it('无分段（null 响应）不缓存：下次展开重新取', async () => {
    breakdownMock.mockResolvedValue({ data: null } as never);
    const composable = mount();
    const entry = decisionEntry('e1', 'r1');
    await composable.onCtxPillShow(entry);
    expect(composable.ctxBreakdownOf(entry)).toBeNull();

    breakdownMock.mockResolvedValue({ data: breakdown } as never);
    await composable.onCtxPillShow(entry);
    expect(breakdownMock).toHaveBeenCalledTimes(2);
    expect(composable.ctxBreakdownOf(entry)).toStrictEqual(breakdown);
  });

  it('获取失败静默降级为空，不向调用方抛错', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    breakdownMock.mockRejectedValue(new Error('网络异常'));
    const composable = mount();
    const entry = decisionEntry('e1', 'r1');
    await expect(composable.onCtxPillShow(entry)).resolves.toBeUndefined();
    expect(composable.ctxBreakdownOf(entry)).toBeNull();

    // 失败释放取中标记：再次展开可重试
    breakdownMock.mockResolvedValue({ data: breakdown } as never);
    await composable.onCtxPillShow(entry);
    expect(breakdownMock).toHaveBeenCalledTimes(2);
    warnSpy.mockRestore();
  });

  it('无请求指针的条目直接空、不发起请求', async () => {
    const composable = mount();
    const entry = makeEntry({ id: 'p1', kind: 'danmaku', tsMs: 1, text: 'hi' });
    expect(composable.ctxBreakdownOf(entry)).toBeNull();
    await composable.onCtxPillShow(entry);
    expect(breakdownMock).not.toHaveBeenCalled();
  });
});
