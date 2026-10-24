/**
 * 时间线卡片宿主上下文：外壳经 provide 下发、卡片 inject 消费的服务面。
 *
 * 三套懒取缓存（token 统计 / 思考正文 / 上下文分段）watch 的是整个条目列表，
 * 必须保持外壳单实例——卡片只拿到自己的 entry，因此按条目取值的统计胶囊、
 * 思考流、回复反查、轮次点击这些跨壳能力全部走本上下文，而不进卡片 props
 * （统一 props 恒为 entry + nowMs，见 registry.CardProps）。
 *
 * 卡片只在 FeedTimeline 内渲染：inject 不到即硬错（缺宿主是装配缺陷，不应静默降级）。
 */
import { inject, type InjectionKey } from 'vue';
import type { useEntryReasoning } from '@/composables/live/useEntryReasoning';
import type { useContextBreakdowns } from '@/composables/live/useContextBreakdowns';
import type { ShowEntry } from '@/utils/liveFeed';

type ReasoningOf = ReturnType<typeof useEntryReasoning>['reasoningOf'];
type CtxBreakdownOf = ReturnType<typeof useContextBreakdowns>['ctxBreakdownOf'];
type OnCtxPillShow = ReturnType<typeof useContextBreakdowns>['onCtxPillShow'];

/** 被回复弹幕反查结果（引用块两字段；未命中返回 null，卡片按占位降级） */
export interface ReplyQuote {
  actor: string;
  text: string;
}

export interface TimelineCardHost {
  /** 紧凑模式（首页缩略）：卡片据此隐藏次要元素（如工具卡成功徽标、载荷折叠区） */
  readonly compact: boolean;
  /** 决策轮可点（roundClickable 开启且行带轮次 ID）与点击上抛（外壳转发 roundClick） */
  isRoundClickable(entry: ShowEntry): boolean;
  onRowClick(entry: ShowEntry): void;
  /** 打开 LLM 请求历史（路由跳转） */
  openLlmRequest(requestId: string): void;
  /** 被回复弹幕反查（replyTo → 引用块；未命中 null） */
  replyQuoteOf(entry: ShowEntry): ReplyQuote | null;
  /** LLM 统计胶囊文案与悬浮提示（按轮次懒取；空串表示不渲染） */
  cacheLabelOf(entry: ShowEntry): string;
  cacheTitleOf(entry: ShowEntry): string;
  tokensLabelOf(entry: ShowEntry): string;
  tokensTitleOf(entry: ShowEntry): string;
  modelNameOf(entry: ShowEntry): string;
  /** 上下文水位胶囊 */
  ctxRatioOf(entry: ShowEntry): number | null;
  ctxBarStyleOf(entry: ShowEntry): string;
  ctxLabelOf(entry: ShowEntry): string;
  ctxTitleOf(entry: ShowEntry): string;
  onCtxPillShow: OnCtxPillShow;
  ctxBreakdownOf: CtxBreakdownOf;
  /** 思考正文（决策/响应卡）：展开态、切换、内容 */
  isThinkingExpanded(entry: ShowEntry): boolean;
  toggleThinking(entry: ShowEntry): void;
  reasoningOf: ReasoningOf;
  /** 会话模式过程条的展开态与切换（过程条卡） */
  isChatGroupExpanded(id: string): boolean;
  toggleChatGroup(id: string): void;
}

export const TIMELINE_CARD_HOST: InjectionKey<TimelineCardHost> = Symbol('timeline-card-host');

/** 卡片取宿主上下文（必须在 FeedTimeline 内使用） */
export function useTimelineCardHost(): TimelineCardHost {
  const host = inject(TIMELINE_CARD_HOST);
  if (!host) {
    throw new Error('时间线卡片必须在 FeedTimeline 内使用：缺少 TimelineCardHost');
  }
  return host;
}
