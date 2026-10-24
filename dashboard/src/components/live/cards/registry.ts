/**
 * 时间线卡片注册表：条目 kind → 卡片组件。
 *
 * FeedTimeline 是「滚动外壳 + 注册表分发」——外壳管列表布局、模式类与滚动，
 * 每种条目卡的版式与取值逻辑内聚在自己的卡片文件里。新接入一种卡 = 写卡片
 * 文件 + 在此注册一行；多个 kind 可映射同一张卡（观众消息族共用聊天气泡，
 * 环节推进与场次边界共用横贯行）。
 *
 * 渐进迁移：未注册的 kind 仍由 FeedTimeline 内联分支渲染，批量迁移完成后
 * 注册表收全、内联分支退役。简洁档显隐口径暂在 utils/liveFeed 的
 * CONVERSATION_KINDS，批量迁移切片时随过滤路径一并收进注册表元数据。
 */
import type { Component } from 'vue';
import type { ShowEntry } from '@/utils/liveFeed';
import BeatCard from './BeatCard.vue';
import ChatBubbleCard from './ChatBubbleCard.vue';

/** 卡片统一 props：条目本体 + 相对时间基准时刻（外壳 1s tick 维护） */
export interface CardProps {
  entry: ShowEntry;
  nowMs: number;
}

/** kind → 卡片组件（未注册 kind 由 FeedTimeline 内联分支兜底，迁移过渡态） */
export const CARD_REGISTRY: Partial<Record<ShowEntry['kind'], Component>> = {
  // 观众消息族：聊天气泡，chat--{kind} 修饰符区分配色
  danmaku: ChatBubbleCard,
  gift: ChatBubbleCard,
  super_chat: ChatBubbleCard,
  guard: ChatBubbleCard,
  game_chat: ChatBubbleCard,
  // 横贯分隔行：环节推进与场次边界
  rundown: BeatCard,
  boundary: BeatCard,
};
