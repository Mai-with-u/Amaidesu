/**
 * 时间线卡片宿主：把卡片需要的跨条目能力（token 统计 / 思考正文 / 上下文分段 /
 * 回复反查 / 轮次点击 / LLM 请求跳转）收成一份实现，供两种外壳复用——
 * 单列时间线（FeedTimeline）与双轨（LiveLanes）都只是布局，卡片契约不变。
 *
 * 三套懒取缓存 watch 的是整个条目列表，因此每个外壳只持有本组合式一次，
 * 再经 provide 下发给卡片；卡片自己 inject 不到即硬错（装配缺陷不静默降级）。
 */
import { computed, provide, type ComputedRef, type Ref } from 'vue';
import { useRouter } from 'vue-router';

import { formatNumber } from '@/utils/format';
import { isRoundLinkable, type ShowEntry } from '@/utils/liveFeed';
import { useRoundTokenStats } from '@/composables/live/useRoundTokenStats';
import { useEntryReasoning } from '@/composables/live/useEntryReasoning';
import { useContextBreakdowns } from '@/composables/live/useContextBreakdowns';
import { TIMELINE_CARD_HOST, type ReplyQuote, type TimelineCardHost } from './context';

export interface CardHostOptions {
  /** 外壳展示的条目（渲染顺序）；缓存组合式据此 watch 懒取 */
  entries: Ref<ShowEntry[]> | ComputedRef<ShowEntry[]>;
  /** 紧凑模式（首页缩略）：卡片据此隐藏次要元素 */
  compact?: () => boolean;
  /** 决策轮是否可点（默认不可点） */
  roundClickable?: () => boolean;
  /** 可点行被点击时上抛（由外壳转成路由跳转或页面事件） */
  onRoundClick?: (entry: ShowEntry) => void;
}

/** 万级以下直接显示，以上缩写为 k（小字号场景，精确值在悬浮提示） */
function compactTokens(n: number): string {
  return n >= 10000 ? `${(n / 1000).toFixed(1)}k` : formatNumber(n);
}

export function useCardHost(options: CardHostOptions): {
  host: TimelineCardHost;
  isRoundClickable: (entry: ShowEntry) => boolean;
  onRowClick: (entry: ShowEntry) => void;
  openLlmRequest: (requestId: string) => void;
  replyQuoteOf: (entry: ShowEntry) => ReplyQuote | null;
} {
  const router = useRouter();

  function isRoundClickable(entry: ShowEntry): boolean {
    return (options.roundClickable?.() ?? false) && isRoundLinkable(entry);
  }

  function openLlmRequest(requestId: string): void {
    void router.push({ path: '/llm/history', query: { request_id: requestId } });
  }

  function onRowClick(entry: ShowEntry): void {
    if (isRoundClickable(entry)) options.onRoundClick?.(entry);
  }

  // 条目引用统一交给三套拉取缓存组合式（token 统计 / 思考正文 / 上下文分段）
  const { statsOf, contextWindowOf } = useRoundTokenStats(options.entries);
  const { expandedThinking, toggleThinking, reasoningOf } = useEntryReasoning(options.entries);
  const { ctxBreakdownOf, onCtxPillShow } = useContextBreakdowns();

  /** 缓存徽标文案（"缓存 62%"）；未取到 / 上游未上报返回空串（调用方按 v-if 不渲染） */
  function cacheLabelOf(entry: ShowEntry): string {
    const stats = statsOf(entry);
    if (!stats || stats.hitTokens <= 0) return '';
    return `缓存 ${Math.round((stats.hitTokens / stats.promptTokens) * 100)}%`;
  }

  /** 缓存徽标悬浮提示：绝对 token 数 */
  function cacheTitleOf(entry: ShowEntry): string {
    const stats = statsOf(entry);
    if (!stats) return '';
    return `缓存命中 ${formatNumber(stats.hitTokens)} / ${formatNumber(stats.promptTokens)} tokens`;
  }

  /** token 徽标文案（"75.4k/0.5k"，输入/输出） */
  function tokensLabelOf(entry: ShowEntry): string {
    const stats = statsOf(entry);
    if (!stats) return '';
    return `${compactTokens(stats.promptTokens)}/${compactTokens(stats.completionTokens)}`;
  }

  /** token 徽标悬浮提示：精确值 */
  function tokensTitleOf(entry: ShowEntry): string {
    const stats = statsOf(entry);
    if (!stats) return '';
    return `输入 ${formatNumber(stats.promptTokens)} · 输出 ${formatNumber(stats.completionTokens)} tokens`;
  }

  /** 模型名徽标：本轮 Planner 请求实际使用的模型标识（空串不渲染） */
  function modelNameOf(entry: ShowEntry): string {
    return statsOf(entry)?.modelName ?? '';
  }

  /** 上下文水位（prompt_tokens / context_window）；窗口未配置返回 null（胶囊不渲染） */
  function ctxRatioOf(entry: ShowEntry): number | null {
    const stats = statsOf(entry);
    const win = contextWindowOf(entry);
    if (!stats || win <= 0) return null;
    return stats.promptTokens / win;
  }

  /** 上下文进度条填充宽度（钳到 100%） */
  function ctxBarStyleOf(entry: ShowEntry): string {
    const ratio = ctxRatioOf(entry) ?? 0;
    return `width: ${Math.min(100, ratio * 100).toFixed(1)}%`;
  }

  /** 上下文徽标文案（"上下文 31%"） */
  function ctxLabelOf(entry: ShowEntry): string {
    const ratio = ctxRatioOf(entry) ?? 0;
    return `${Math.round(ratio * 100)}%`;
  }

  /** 上下文徽标悬浮提示：分子/分母绝对值 */
  function ctxTitleOf(entry: ShowEntry): string {
    const stats = statsOf(entry);
    const win = contextWindowOf(entry);
    return `本轮输入 ${formatNumber(stats?.promptTokens ?? 0)} / 窗口 ${formatNumber(win)} tokens`;
  }

  /** 弹幕 message_id → 时间线条目（发言/决策卡回复引用反查）。
   * 仅索引观众消息类（弹幕 / SC / 礼物）；同一 ID 重复出现时取首次，按 tsMs 正序遍历保证幂等。 */
  const messageIndex = computed<Map<string, ShowEntry>>(() => {
    const map = new Map<string, ShowEntry>();
    for (const item of options.entries.value) {
      if (!item.messageId) continue;
      if (item.kind !== 'danmaku' && item.kind !== 'super_chat' && item.kind !== 'gift') continue;
      if (!map.has(item.messageId)) map.set(item.messageId, item);
    }
    return map;
  });

  /** 把决策/发言卡的 replyTo 反查成引用块所需的两字段。未命中返回 null，卡片按占位降级。 */
  function replyQuoteOf(entry: ShowEntry): ReplyQuote | null {
    const targetId = entry.replyTo;
    if (!targetId) return null;
    const target = messageIndex.value.get(targetId);
    if (!target) return null;
    return { actor: target.actor, text: target.text };
  }

  const host = {
    get compact() {
      return options.compact?.() ?? false;
    },
    isRoundClickable,
    onRowClick,
    openLlmRequest,
    replyQuoteOf,
    cacheLabelOf,
    cacheTitleOf,
    tokensLabelOf,
    tokensTitleOf,
    modelNameOf,
    ctxRatioOf,
    ctxBarStyleOf,
    ctxLabelOf,
    ctxTitleOf,
    onCtxPillShow,
    ctxBreakdownOf,
    isThinkingExpanded: (entry: ShowEntry) => expandedThinking.value.has(entry.id),
    toggleThinking,
    reasoningOf,
  } satisfies TimelineCardHost;

  provide(TIMELINE_CARD_HOST, host);

  return {
    host,
    isRoundClickable,
    onRowClick,
    openLlmRequest,
    replyQuoteOf,
  };
}
