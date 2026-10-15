/**
 * 决策/发言卡水位胶囊的悬停明细缓存（FeedTimeline 拆出）。
 *
 * llm_request_id → 该次调用的分段占用。弹层首次展开时才拉取（按请求 ID
 * 精确取行），同请求缓存复用；失败静默降级为空态文案，不影响胶囊本体与
 * 统计徽标。
 */
import { ref } from 'vue';
import { llmApi } from '@/api';
import type { LLMContextBreakdown } from '@/types';
import type { ShowEntry } from '@/utils/liveFeed';

export function useContextBreakdowns() {
  const ctxBreakdowns = ref<Map<string, LLMContextBreakdown>>(new Map());
  const ctxBreakdownFetching = new Set<string>();

  function ctxBreakdownOf(entry: ShowEntry): LLMContextBreakdown | null {
    if (!entry.llmRequestId) return null;
    return ctxBreakdowns.value.get(entry.llmRequestId) ?? null;
  }

  /** 弹层展开钩子：首次展开按请求 ID 拉取分段占用，已缓存或在取中直接返回 */
  async function onCtxPillShow(entry: ShowEntry): Promise<void> {
    const requestId = entry.llmRequestId;
    if (!requestId || ctxBreakdowns.value.has(requestId) || ctxBreakdownFetching.has(requestId))
      return;
    ctxBreakdownFetching.add(requestId);
    try {
      const response = await llmApi.getContextBreakdownByRequest(requestId);
      if (response.data) ctxBreakdowns.value.set(requestId, response.data);
    } catch (e) {
      console.warn(`[FeedTimeline] 上下文分段明细获取失败: ${requestId}`, e);
    } finally {
      ctxBreakdownFetching.delete(requestId);
    }
  }

  return { ctxBreakdownOf, onCtxPillShow };
}
