// useRoundTokenStats.ts 批量 token 统计与上下文窗口缓存的测试
//
// 覆盖：可见指针合批懒取（防抖合并）、prompt 为 0 不入缓存、失败静默且
// 可重试、离场条目缓存收缩、上下文窗口整页一次取（失败下批重试）、
// statsOf / contextWindowOf 读取口径。

import { effectScope, ref, type EffectScope, type Ref } from 'vue';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { llmApi } from '@/api';
import { useRoundTokenStats } from '@/composables/live/useRoundTokenStats';
import { makeEntry, type ShowEntry } from '@/utils/liveFeed';

vi.mock('@/api', async importOriginal => ({
  ...(await importOriginal<object>()),
  llmApi: {
    getRequestStats: vi.fn(),
    getUsage: vi.fn(),
  },
}));

const getRequestStatsMock = vi.mocked(llmApi.getRequestStats);
const getUsageMock = vi.mocked(llmApi.getUsage);

function decisionEntry(id: string, requestId: string): ShowEntry {
  return makeEntry({ id, kind: 'decision', tsMs: 1000, text: '决策', llmRequestId: requestId });
}

describe('useRoundTokenStats', () => {
  let scope: EffectScope;
  let entries: Ref<ShowEntry[]>;

  beforeEach(() => {
    vi.useFakeTimers();
    scope = effectScope();
    entries = ref<ShowEntry[]>([]);
    getRequestStatsMock.mockReset();
    getUsageMock.mockReset();
  });

  afterEach(() => {
    scope.stop();
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  function mount() {
    return scope.run(() => useRoundTokenStats(entries))!;
  }

  /** 推进条目并走完 150ms 防抖窗口 */
  async function flush(): Promise<void> {
    await vi.advanceTimersByTimeAsync(200);
  }

  it('可见条目的请求指针经防抖合批懒取，统计写入缓存', async () => {
    getRequestStatsMock.mockResolvedValue({
      data: {
        stats: {
          r1: {
            prompt_tokens: 1000,
            completion_tokens: 200,
            cache_hit_tokens: 600,
            model_name: 'glm-4',
          },
        },
      },
    } as never);
    const composable = mount();
    entries.value = [decisionEntry('e1', 'r1')];
    // 防抖窗口内不立即请求
    await vi.advanceTimersByTimeAsync(100);
    expect(getRequestStatsMock).not.toHaveBeenCalled();

    await flush();
    expect(getRequestStatsMock).toHaveBeenCalledTimes(1);
    expect(getRequestStatsMock).toHaveBeenCalledWith(['r1']);
    const stats = composable.statsOf(entries.value[0]);
    expect(stats).toEqual({
      promptTokens: 1000,
      completionTokens: 200,
      hitTokens: 600,
      modelName: 'glm-4',
    });
  });

  it('防抖窗口内的多次条目变化合并为一次批量请求', async () => {
    getRequestStatsMock.mockResolvedValue({ data: { stats: {} } } as never);
    mount();
    entries.value = [decisionEntry('e1', 'r1')];
    await vi.advanceTimersByTimeAsync(50);
    entries.value = [...entries.value, decisionEntry('e2', 'r2')];
    await flush();
    expect(getRequestStatsMock).toHaveBeenCalledTimes(1);
    expect(getRequestStatsMock).toHaveBeenCalledWith(['r1', 'r2']);
  });

  it('prompt 为 0（含上游未上报）的请求不入缓存', async () => {
    getRequestStatsMock.mockResolvedValue({
      data: {
        stats: {
          r1: { prompt_tokens: 0, completion_tokens: 5, cache_hit_tokens: 0, model_name: 'm' },
        },
      },
    } as never);
    const composable = mount();
    entries.value = [decisionEntry('e1', 'r1')];
    await flush();
    expect(composable.statsOf(entries.value[0])).toBeNull();
  });

  it('批量获取失败静默：不抛错且释放取中标记，下批可重试', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    getRequestStatsMock.mockRejectedValue(new Error('后端未装配'));
    const composable = mount();
    entries.value = [decisionEntry('e1', 'r1')];
    await flush();
    expect(composable.statsOf(entries.value[0])).toBeNull();

    // 失败后标记已释放：条目变化触发下一批仍会带上 r1
    getRequestStatsMock.mockResolvedValue({
      data: {
        stats: {
          r1: { prompt_tokens: 10, completion_tokens: 1, cache_hit_tokens: 0, model_name: 'm' },
        },
      },
    } as never);
    entries.value = [decisionEntry('e1', 'r1'), decisionEntry('e2', 'r2')];
    await flush();
    expect(getRequestStatsMock).toHaveBeenLastCalledWith(['r1', 'r2']);
    expect(warnSpy).toHaveBeenCalled();
    warnSpy.mockRestore();
  });

  it('离场条目的缓存收缩，回归后重新懒取', async () => {
    getRequestStatsMock.mockResolvedValue({
      data: {
        stats: {
          r1: { prompt_tokens: 10, completion_tokens: 1, cache_hit_tokens: 0, model_name: 'm' },
        },
      },
    } as never);
    const composable = mount();
    entries.value = [decisionEntry('e1', 'r1')];
    await flush();
    expect(composable.statsOf(entries.value[0])).not.toBeNull();

    entries.value = [];
    await flush();
    expect(getRequestStatsMock).toHaveBeenCalledTimes(1); // 空集合不触发懒取

    entries.value = [decisionEntry('e1', 'r1')];
    await flush();
    // 缓存已收缩：回归后作为缺失指针重新请求
    expect(getRequestStatsMock).toHaveBeenCalledTimes(2);
    expect(composable.statsOf(entries.value[0])).not.toBeNull();
  });

  it('上下文窗口整页取一次：按模型名读窗口，未配置模型返回 0', async () => {
    getUsageMock.mockResolvedValue({
      data: { 'glm-4': { context_window: 128000 }, legacy: { context_window: 0 } },
    } as never);
    getRequestStatsMock.mockResolvedValue({
      data: {
        stats: {
          r1: {
            prompt_tokens: 1000,
            completion_tokens: 1,
            cache_hit_tokens: 0,
            model_name: 'glm-4',
          },
        },
      },
    } as never);
    const composable = mount();
    entries.value = [decisionEntry('e1', 'r1')];
    await flush();
    expect(getUsageMock).toHaveBeenCalledTimes(1);
    expect(composable.contextWindowOf(entries.value[0])).toBe(128000);

    // 窗口运行期不变：后续条目变化不再重复取
    entries.value = [decisionEntry('e1', 'r1'), decisionEntry('e2', 'r2')];
    await flush();
    expect(getUsageMock).toHaveBeenCalledTimes(1);
  });

  it('上下文窗口获取失败不落"已加载"，下批重试', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    getUsageMock.mockRejectedValue(new Error('网络异常'));
    getRequestStatsMock.mockResolvedValue({
      data: {
        stats: {
          r1: {
            prompt_tokens: 1000,
            completion_tokens: 1,
            cache_hit_tokens: 0,
            model_name: 'glm-4',
          },
        },
      },
    } as never);
    const composable = mount();
    entries.value = [decisionEntry('e1', 'r1')];
    await flush();
    expect(composable.contextWindowOf(entries.value[0])).toBe(0);

    getUsageMock.mockResolvedValue({
      data: { 'glm-4': { context_window: 32000 } },
    } as never);
    entries.value = [decisionEntry('e1', 'r1'), decisionEntry('e2', 'r2')];
    await flush();
    expect(composable.contextWindowOf(entries.value[0])).toBe(32000);
    warnSpy.mockRestore();
  });

  it('无指针条目与未知请求读取均返回 null / 0', () => {
    const composable = mount();
    const plain = makeEntry({ id: 'p1', kind: 'danmaku', tsMs: 1, text: 'hi' });
    expect(composable.statsOf(plain)).toBeNull();
    expect(composable.contextWindowOf(plain)).toBe(0);
  });
});
