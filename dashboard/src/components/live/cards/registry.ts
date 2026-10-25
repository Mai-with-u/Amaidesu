/**
 * 时间线卡片注册表：条目 kind → 卡片组件（全量映射，类型上穷尽 EntryKind）。
 *
 * FeedTimeline 是「滚动外壳 + 注册表分发」——外壳管列表布局、模式类、滚动与
 * 宿主上下文（context.ts）的下发；每种条目卡的版式与取值逻辑内聚在自己的
 * 卡片文件里。新接入一种卡 = 写卡片文件 + 在此注册一行；多个 kind 可映射
 * 同一张卡（观众消息族共用聊天气泡，环节/场次共用横贯行，安静单行族六 kind
 * 共用 WhisperCard）。
 *
 * 简洁档显隐口径暂在 utils/liveFeed 的 CONVERSATION_KINDS（上游条目过滤），
 * 收进注册表元数据待过滤路径一并迁移时再做，避免双事实源。
 */
import type { Component } from 'vue';
import type { ShowEntry } from '@/utils/liveFeed';
import AgentReplyCard from './AgentReplyCard.vue';
import BeatCard from './BeatCard.vue';
import ChatBubbleCard from './ChatBubbleCard.vue';
import DecisionCard from './DecisionCard.vue';
import GameCard from './GameCard.vue';
import MilestoneCard from './MilestoneCard.vue';
import ProcessStripCard from './ProcessStripCard.vue';
import PromptCard from './PromptCard.vue';
import SpeechCard from './SpeechCard.vue';
import ThinkingCard from './ThinkingCard.vue';
import ToolCard from './ToolCard.vue';
import WhisperCard from './WhisperCard.vue';

/** 卡片统一 props：条目本体 + 相对时间基准时刻（外壳 1s tick 维护） */
export interface CardProps {
  entry: ShowEntry;
  nowMs: number;
}

/** kind → 卡片组件（Record 全量：漏注册一种 kind 会被类型检查拦下） */
export const CARD_REGISTRY: Record<ShowEntry['kind'], Component> = {
  // 观众消息族：聊天气泡，chat--{kind} 修饰符区分配色
  danmaku: ChatBubbleCard,
  gift: ChatBubbleCard,
  super_chat: ChatBubbleCard,
  guard: ChatBubbleCard,
  game_chat: ChatBubbleCard,
  // 横贯分隔行：环节推进与场次边界
  rundown: BeatCard,
  boundary: BeatCard,
  // 会话模式过程折叠条（合成条目）
  process_group: ProcessStripCard,
  // 里程碑庆祝行
  milestone: MilestoneCard,
  // 安静单行族：阶段状态 / 进场 / 游戏身体事件 / TTS 失败 / 系统事件 / 工具健康
  stage: WhisperCard,
  enter: WhisperCard,
  game_body: WhisperCard,
  tts: WhisperCard,
  system: WhisperCard,
  tool_health: WhisperCard,
  // 思考行（合成条目）
  thinking: ThinkingCard,
  // 决策管线：决策/裁决共用大卡；Agent 每步响应
  decision: DecisionCard,
  verdict: DecisionCard,
  agent_reply: AgentReplyCard,
  // 工具调用
  tool: ToolCard,
  // 主播发言
  speech: SpeechCard,
  // 递话受理
  prompt: PromptCard,
  // 游戏 Agent 上报
  game: GameCard,
};
