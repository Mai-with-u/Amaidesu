/**
 * 决策/裁决/响应卡的思考正文懒取缓存（FeedTimeline 拆出）。
 *
 * 决策/裁决卡的思考正文（llm_request_id → reasoning_content，懒取 + 一次性缓存）。
 * 与实时思考流不同源：这里是按请求整段的模型推理正文（LLM 请求历史出库合并回供），
 * 回看模式下没有逐 token 思考流，诊断回看这条。取到空串表示该请求无思考正文。
 */
import { ref, watch, type Ref } from 'vue';
import { llmApi } from '@/api';
import type { ShowEntry } from '@/utils/liveFeed';
import { visibleRequestIdsOf } from '@/composables/live/useRoundTokenStats';

export function useEntryReasoning(entries: Ref<ShowEntry[]>) {
  const reasoningByRequest = ref<Map<string, string>>(new Map());
  const reasoningFetching = new Set<string>();
  /** 已展开思考区的条目 id（v-if 挂载；与载荷懒渲染集合同款收展语义） */
  const expandedThinking = ref<Set<string>>(new Set());

  /** 思考正文懒取：已有缓存或在取中直接返回（一次性缓存，失败落空串不重试） */
  async function fetchReasoning(requestId: string): Promise<void> {
    if (reasoningByRequest.value.has(requestId) || reasoningFetching.has(requestId)) return;
    reasoningFetching.add(requestId);
    try {
      const response = await llmApi.getRequestById(requestId);
      reasoningByRequest.value.set(requestId, response.data?.reasoning_content ?? '');
    } catch (e) {
      console.warn(`[FeedTimeline] 思考正文获取失败: ${requestId}`, e);
      reasoningByRequest.value.set(requestId, '');
    } finally {
      reasoningFetching.delete(requestId);
    }
  }

  function toggleThinking(entry: ShowEntry): void {
    if (!entry.llmRequestId) return;
    const next = new Set(expandedThinking.value);
    if (next.has(entry.id)) {
      next.delete(entry.id);
      expandedThinking.value = next;
      return;
    }
    next.add(entry.id);
    expandedThinking.value = next;
    void fetchReasoning(entry.llmRequestId);
  }

  watch(
    entries,
    list => {
      // 离场条目的正文缓存顺手清掉，长直播下 map 不随轮次无限增长
      const visible = visibleRequestIdsOf(list);
      for (const key of reasoningByRequest.value.keys()) {
        if (!visible.has(key)) reasoningByRequest.value.delete(key);
      }
      // 懒渲染集合同步收缩：只保留当前在列条目，避免长会话下随事件 id 无限增长
      if (expandedThinking.value.size > 0) {
        const liveIds = new Set(list.map(entry => entry.id));
        let pruned = false;
        const next = new Set<string>();
        for (const id of expandedThinking.value) {
          if (liveIds.has(id)) next.add(id);
          else pruned = true;
        }
        if (pruned) expandedThinking.value = next;
      }
    },
    { immediate: true },
  );

  /** 思考区正文：null=未取到（展示加载中）；空串=已确认无思考正文 */
  function reasoningOf(entry: ShowEntry): string | null {
    if (!entry.llmRequestId) return null;
    const value = reasoningByRequest.value.get(entry.llmRequestId);
    return value ?? null;
  }

  return { expandedThinking, toggleThinking, reasoningOf };
}
