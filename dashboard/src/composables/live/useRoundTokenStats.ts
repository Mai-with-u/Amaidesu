/**
 * 决策/发言卡的批量 token 统计与模型上下文窗口缓存（FeedTimeline 拆出）。
 *
 * token 统计：planner.decision 只带请求指针不带 token 数，统计按可见指针合批
 * 懒取（POST /llm/history/stats，仅统计列）；speech 带的是 Replyer 表达请求
 * 指针，同一套懒取。缓存口径取 hit/prompt_tokens 而非聚合的
 * hit/(hit+miss)：OpenAI 风格只上报 cached_tokens 不上报 miss，后者会算出假
 * 100%；命中为 0（含上游未上报）不渲染缓存徽标。
 *
 * 上下文窗口：model_name → token 总量，取自 GET /llm/usage 装配期快照，
 * 整页只取一次——窗口来自 [[llm_models]].context_window 配置，改配置需重启
 * 应用，运行期不变。未配置（0）的模型不进映射，水位胶囊按 v-if 隐藏。
 */
import { onScopeDispose, ref, watch, type Ref } from 'vue';
import { llmApi } from '@/api';
import type { ShowEntry } from '@/utils/liveFeed';

/** 本轮 Planner/Replyer 请求的统计徽标数据 */
export interface RoundTokenStats {
  promptTokens: number;
  completionTokens: number;
  hitTokens: number;
  /** 本轮 Planner 请求实际使用的模型标识（取自请求历史记录） */
  modelName: string;
}

/** 可见条目携带的 LLM 请求指针集合（懒取与缓存收缩的统一口径）。
 * 真实流程里 decision 轮末会合并进先到的 verdict 卡（llmRequestId 一并回填），
 * 独立 decision 卡只出现在无裁决的失败/静默轮，两种都要取数；
 * speech 卡带的是 Replyer 表达请求的指针（与决策卡的 Planner 请求互补），同样取数；
 * agent_reply 响应卡带本步请求指针，同一套懒取 */
export function visibleRequestIdsOf(entries: ShowEntry[]): Set<string> {
  const visible = new Set<string>();
  for (const entry of entries) {
    if (
      (entry.kind === 'decision' ||
        entry.kind === 'verdict' ||
        entry.kind === 'speech' ||
        entry.kind === 'agent_reply') &&
      entry.llmRequestId
    ) {
      visible.add(entry.llmRequestId);
    }
  }
  return visible;
}

export function useRoundTokenStats(entries: Ref<ShowEntry[]>) {
  const roundTokenStats = ref<Map<string, RoundTokenStats>>(new Map());
  const tokenStatsFetching = new Set<string>();

  async function fetchTokenStatsBatch(requestIds: string[]): Promise<void> {
    for (const requestId of requestIds) tokenStatsFetching.add(requestId);
    try {
      const response = await llmApi.getRequestStats(requestIds);
      const stats = response.data?.stats ?? {};
      for (const [requestId, item] of Object.entries(stats)) {
        // 与旧逐卡口径一致：prompt 为 0（含上游未上报）不渲染统计胶囊
        if (item.prompt_tokens > 0) {
          roundTokenStats.value.set(requestId, {
            promptTokens: item.prompt_tokens,
            completionTokens: item.completion_tokens,
            hitTokens: item.cache_hit_tokens,
            modelName: item.model_name,
          });
        }
      }
    } catch (e) {
      console.warn('[FeedTimeline] 批量 token 统计获取失败', e);
    } finally {
      for (const requestId of requestIds) tokenStatsFetching.delete(requestId);
    }
  }

  /** 合批调度：一次回看首屏可达数百个请求指针，逐卡取详情会回传整段请求
   * 正文并触发读侧文件回查（整应用冻结）；短防抖合并同批可见指针为一次
   * 轻量批量请求（POST /llm/history/stats，仅 usage/model 列） */
  let tokenStatsTimer: ReturnType<typeof setTimeout> | null = null;

  function scheduleTokenStats(missingIds: string[]): void {
    if (tokenStatsTimer) clearTimeout(tokenStatsTimer);
    tokenStatsTimer = setTimeout(() => {
      tokenStatsTimer = null;
      void fetchTokenStatsBatch(missingIds);
    }, 150);
  }

  onScopeDispose(() => {
    if (tokenStatsTimer) clearTimeout(tokenStatsTimer);
  });

  const contextWindows = ref<Map<string, number>>(new Map());
  let contextWindowsLoading = false;
  let contextWindowsLoaded = false;

  async function ensureContextWindows(): Promise<void> {
    if (contextWindowsLoaded || contextWindowsLoading) return;
    contextWindowsLoading = true;
    try {
      const response = await llmApi.getUsage();
      const next = new Map<string, number>();
      for (const [model, stats] of Object.entries(response.data ?? {})) {
        if (stats.context_window > 0) next.set(model, stats.context_window);
      }
      contextWindows.value = next;
      contextWindowsLoaded = true;
    } catch (e) {
      console.warn('[FeedTimeline] 模型上下文窗口获取失败（下一条目到达时重试）', e);
    } finally {
      contextWindowsLoading = false;
    }
  }

  watch(
    entries,
    list => {
      const visible = visibleRequestIdsOf(list);
      // 离场条目的徽标顺手清掉，长直播下 map 不随轮次无限增长
      for (const key of roundTokenStats.value.keys()) {
        if (!visible.has(key)) roundTokenStats.value.delete(key);
      }
      const missing = [...visible].filter(
        requestId => !roundTokenStats.value.has(requestId) && !tokenStatsFetching.has(requestId),
      );
      if (missing.length > 0) scheduleTokenStats(missing);
      if (visible.size > 0) void ensureContextWindows();
    },
    { immediate: true },
  );

  /** 该条目请求的统计（未取到/无指针返回 null） */
  function statsOf(entry: ShowEntry): RoundTokenStats | null {
    return entry.llmRequestId ? (roundTokenStats.value.get(entry.llmRequestId) ?? null) : null;
  }

  /** 本条目请求所用模型的上下文窗口（未配置/未加载返回 0） */
  function contextWindowOf(entry: ShowEntry): number {
    const stats = statsOf(entry);
    if (!stats || !stats.modelName) return 0;
    return contextWindows.value.get(stats.modelName) ?? 0;
  }

  return { statsOf, contextWindowOf };
}
