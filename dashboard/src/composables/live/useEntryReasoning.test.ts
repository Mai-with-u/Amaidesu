// useEntryReasoning.ts 思考正文懒取缓存的测试
//
// 覆盖：展开触发懒取与一次性缓存、取到空串表示无思考正文、失败落空串、
// 收起不重复取、无指针条目不动作、离场条目的正文缓存与懒渲染集合收缩。

import { effectScope, ref, type EffectScope, type Ref } from 'vue';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { llmApi } from '@/api';
import { useEntryReasoning } from '@/composables/live/useEntryReasoning';
import { makeEntry, type ShowEntry } from '@/utils/liveFeed';

vi.mock('@/api', async importOriginal => ({
  ...(await importOriginal<object>()),
  llmApi: {
    getRequestById: vi.fn(),
  },
}));

const getRequestByIdMock = vi.mocked(llmApi.getRequestById);

function decisionEntry(id: string, requestId: string): ShowEntry {
  return makeEntry({ id, kind: 'decision', tsMs: 1000, text: '决策', llmRequestId: requestId });
}

describe('useEntryReasoning', () => {
  let scope: EffectScope;
  let entries: Ref<ShowEntry[]>;

  beforeEach(() => {
    scope = effectScope();
    entries = ref<ShowEntry[]>([]);
    getRequestByIdMock.mockReset();
  });

  afterEach(() => {
    scope.stop();
    vi.clearAllMocks();
  });

  function mount() {
    return scope.run(() => useEntryReasoning(entries))!;
  }

  it('展开触发懒取并缓存正文；收起不重复取；再展开命中缓存', async () => {
    getRequestByIdMock.mockResolvedValue({
      data: { reasoning_content: '推理过程……' },
    } as never);
    const composable = mount();
    const entry = decisionEntry('e1', 'r1');
    entries.value = [entry];

    // 未取到：null 表示加载中
    expect(composable.reasoningOf(entry)).toBeNull();

    composable.toggleThinking(entry);
    await new Promise<void>(resolve => setTimeout(resolve, 0));
    await new Promise<void>(resolve => setTimeout(resolve, 0));
    expect(getRequestByIdMock).toHaveBeenCalledTimes(1);
    expect(getRequestByIdMock).toHaveBeenCalledWith('r1');
    expect(composable.reasoningOf(entry)).toBe('推理过程……');

    await composable.toggleThinking(entry); // 收起
    await composable.toggleThinking(entry); // 再展开
    expect(getRequestByIdMock).toHaveBeenCalledTimes(1); // 一次性缓存
  });

  it('请求无思考正文取到空串：reasoningOf 返回空串而非 null', async () => {
    getRequestByIdMock.mockResolvedValue({ data: { reasoning_content: '' } } as never);
    const composable = mount();
    const entry = decisionEntry('e1', 'r1');
    entries.value = [entry];
    composable.toggleThinking(entry);
    await new Promise<void>(resolve => setTimeout(resolve, 0));
    await new Promise<void>(resolve => setTimeout(resolve, 0));
    expect(composable.reasoningOf(entry)).toBe('');
  });

  it('获取失败静默落空串，不向调用方抛错', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    getRequestByIdMock.mockRejectedValue(new Error('请求不存在'));
    const composable = mount();
    const entry = decisionEntry('e1', 'r1');
    entries.value = [entry];
    // toggleThinking 同步收展、取数在后台：失败不向调用方抛错
    composable.toggleThinking(entry);
    await new Promise<void>(resolve => setTimeout(resolve, 0));
    expect(composable.reasoningOf(entry)).toBe('');
    warnSpy.mockRestore();
  });

  it('无请求指针的条目不展开也不取数', async () => {
    const composable = mount();
    const entry = makeEntry({ id: 'p1', kind: 'danmaku', tsMs: 1, text: 'hi' });
    composable.toggleThinking(entry);
    await new Promise<void>(resolve => setTimeout(resolve, 0));
    expect(getRequestByIdMock).not.toHaveBeenCalled();
  });

  it('离场条目的正文缓存与懒渲染集合同步收缩', async () => {
    getRequestByIdMock.mockResolvedValue({
      data: { reasoning_content: '正文' },
    } as never);
    const composable = mount();
    const entry = decisionEntry('e1', 'r1');
    entries.value = [entry];
    composable.toggleThinking(entry);
    await new Promise<void>(resolve => setTimeout(resolve, 0));
    await new Promise<void>(resolve => setTimeout(resolve, 0));
    expect(composable.expandedThinking.value.has('e1')).toBe(true);

    entries.value = [];
    await new Promise<void>(resolve => setTimeout(resolve, 0));
    expect(composable.expandedThinking.value.has('e1')).toBe(false);

    // 回归后懒渲染集合为空，重新展开会再次取（正文缓存也已收缩）
    const revived = decisionEntry('e1', 'r1');
    entries.value = [revived];
    composable.toggleThinking(revived);
    await new Promise<void>(resolve => setTimeout(resolve, 0));
    expect(getRequestByIdMock).toHaveBeenCalledTimes(2);
  });
});
