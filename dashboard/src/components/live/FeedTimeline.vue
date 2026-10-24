<template>
  <div
    class="feed-timeline"
    :class="{ 'is-compact': compact, 'is-chat': isChat, 'is-simple': simple }"
  >
    <div v-if="entries.length === 0" class="stage-empty">
      <el-icon class="stage-empty-icon"><Monitor /></el-icon>
      <p class="stage-empty-text">{{ emptyText }}</p>
    </div>

    <ol v-else class="feed">
      <li
        v-for="entry in feedRows"
        :key="entry.id"
        class="feed-row"
        :class="[rowAlignClass(entry), { 'is-anchored': entry.id === anchorEntryId }]"
        :data-entry-id="entry.id"
      >
        <!-- 注册表直派：卡片按 kind 查表渲染（registry.ts 是分发的唯一缝隙，
             全 kind 注册由类型穷尽保证）；宿主上下文经 provide 下发卡片 -->
        <Component :is="CARD_REGISTRY[entry.kind]" :entry="entry" :now-ms="nowMs" />

        <!-- 会话布局的主播侧头像：行级布局件（与左侧观众头像呼应，撑起发言行右对齐），
             不属于任何一张卡，留在外壳 -->
        <span
          v-if="entry.kind === 'speech' && isChat"
          class="avatar avatar--host"
          aria-hidden="true"
          >主</span
        >
      </li>
    </ol>
  </div>
</template>

<script setup lang="ts">
/**
 * 直播时间线视图（直播控制台 + 首页共用）
 *
 * 外壳只管三件事：列表布局与模式类（timeline/chat/compact/simple）、滚动锚定
 * 之外的行序合成（会话模式的过程折叠）、宿主上下文下发。全部行类型由
 * cards/registry.ts 按条目 kind 查表分发，卡片的版式与取值逻辑内聚在各自的
 * 卡片文件里；token 统计 / 思考正文 / 上下文分段三套懒取缓存在此单例持有，
 * 经 context.ts 的宿主上下文供卡片按条目取值。
 *
 * 输入：已折叠的 ShowEntry[]（顺序即展示顺序——上游负责按时间归并，
 *      控制台把思考行合成进事件条目流，首页只取事件条目）
 * 控制台独占能力（暂停/清空、注入面板、滚动跟随）留在 LiveObserver；
 * 本组件对上游数据来源无要求，可被任何 Vue 页面复用。
 *
 * 可选的决策轮点击（roundClickable）：开启后带轮次 ID 的决策/裁决/工具/发言行
 * 可点并上抛 roundClick，由页面决定跳转去向；默认关闭，首页复用不受影响。
 */
import { computed, provide, ref } from 'vue';
import { useRouter } from 'vue-router';
import { Monitor } from '@element-plus/icons-vue';
import { CARD_REGISTRY } from '@/components/live/cards/registry';
import { TIMELINE_CARD_HOST, type TimelineCardHost } from '@/components/live/cards/context';
import '@/components/live/cards/cards-shared.css';
import { formatNumber } from '@/utils/format';
import {
  buildChatRows,
  isChatProcessKind,
  isRoundLinkable,
  type ShowEntry,
} from '@/utils/liveFeed';
import { useNowTick } from '@/composables/useNowTick';
import { useRoundTokenStats } from '@/composables/live/useRoundTokenStats';
import { useEntryReasoning } from '@/composables/live/useEntryReasoning';
import { useContextBreakdowns } from '@/composables/live/useContextBreakdowns';

interface Props {
  /** 已折叠好的时间线条目（顺序即展示顺序） */
  entries: ShowEntry[];
  /** 紧凑模式：用于首页缩略展示，缩短行距/字号 */
  compact?: boolean;
  /** 行布局：timeline=单列沿脊线的时间线；chat=会话模式（观众左 / 主播右 / 过程行居中） */
  layout?: 'timeline' | 'chat';
  /** entries 为空时的提示语 */
  emptyText?: string;
  /** 决策轮可点：带轮次 ID 的决策/裁决/工具/发言行点击后上抛 roundClick
   *  （默认关闭——首页复用本组件时行不可点） */
  roundClickable?: boolean;
  /** 简洁档：藏卡面上的诊断胶囊行（缓存/Token/模型/上下文/完整请求），
   *  只留内容本体——管线诊断卡由页面层条目过滤排除，这里兜卡面上的统计胶囊 */
  simple?: boolean;
  /** 锚定高亮条目 id（回看深链 ?at= 定位；非空时该行加 is-anchored 类） */
  anchorEntryId?: string | null;
}

const router = useRouter();

const props = withDefaults(defineProps<Props>(), {
  compact: false,
  layout: 'timeline',
  emptyText: '静候消息与决策',
  roundClickable: false,
  simple: false,
  anchorEntryId: null,
});

const emit = defineEmits<{
  /** 决策轮行点击（roundClickable 开启且行带轮次 ID 时触发），页面据此跳 Agent 页 */
  roundClick: [entry: ShowEntry];
}>();

/** 行是否可点：仅四类带轮次 ID 的行（判定口径在 liveFeed.isRoundLinkable） */
function isRoundClickable(entry: ShowEntry): boolean {
  return props.roundClickable && isRoundLinkable(entry);
}

function openLlmRequest(requestId: string): void {
  void router.push({ path: '/llm/history', query: { request_id: requestId } });
}

function onRowClick(entry: ShowEntry): void {
  if (isRoundClickable(entry)) emit('roundClick', entry);
}

const isChat = computed(() => props.layout === 'chat');

/** 会话模式下已展开过程条的组（键为过程条合成条目的 id） */
const expandedChatGroups = ref<Set<string>>(new Set());

/** 渲染行序：会话模式把每轮过程折叠成一条过程条；时间线模式原样透传 */
const feedRows = computed<ShowEntry[]>(() =>
  isChat.value ? buildChatRows(props.entries, expandedChatGroups.value) : props.entries,
);

function toggleChatGroup(key: string): void {
  const next = new Set(expandedChatGroups.value);
  if (next.has(key)) next.delete(key);
  else next.add(key);
  expandedChatGroups.value = next;
}

/** 会话布局的行对齐类：观众消息靠左、主播发言靠右、过程行与折叠条随主播侧右对齐；
 *  时间线布局不加任何对齐修饰 */
function rowAlignClass(entry: ShowEntry): string {
  if (!isChat.value) return '';
  if (entry.kind === 'process_group') return 'row-strip';
  if (entry.kind === 'speech') return 'row-right';
  if (isChatProcessKind(entry.kind)) return 'row-process';
  return '';
}

// 1s tick：让相对时间标签（"刚刚 / 12s 前"）每秒刷新一次；独立维护不依赖父组件
const nowMs = useNowTick();

// 条目引用统一交给三套拉取缓存组合式（token 统计 / 思考正文 / 上下文分段），
// 它们各自 watch 条目变化做懒取与缓存收缩；单实例持有了才经宿主上下文下发卡片
const entriesRef = computed<ShowEntry[]>(() => props.entries);
const { statsOf, contextWindowOf } = useRoundTokenStats(entriesRef);
const { expandedThinking, toggleThinking, reasoningOf } = useEntryReasoning(entriesRef);
const { ctxBreakdownOf, onCtxPillShow } = useContextBreakdowns();

/** 万级以下直接显示，以上缩写为 k（d-meta 小字号场景，精确值在悬浮提示） */
function compactTokens(n: number): string {
  return n >= 10000 ? `${(n / 1000).toFixed(1)}k` : formatNumber(n);
}

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

/** token 徽标文案（"Token 75.4k/0.5k"，输入/输出） */
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

/** 弹幕 message_id → 时间线条目（用于发言/决策卡回复引用反查）。
 * 仅索引观众消息类（弹幕 / SC / 礼物）；同一 ID 重复出现时取首次，时间线按 tsMs 正序遍历保证幂等。 */
const messageIndex = computed<Map<string, ShowEntry>>(() => {
  const map = new Map<string, ShowEntry>();
  for (const item of props.entries) {
    if (!item.messageId) continue;
    if (item.kind !== 'danmaku' && item.kind !== 'super_chat' && item.kind !== 'gift') continue;
    if (!map.has(item.messageId)) map.set(item.messageId, item);
  }
  return map;
});

/** 把决策/发言卡的 replyTo 反查成 QQ 风格引用块所需的两字段。
 * 未命中（时间线被清空、消息被水位裁剪、回看数据缺失 message_id 等）返回 null，调用方按占位降级渲染。 */
function replyQuoteOf(entry: ShowEntry): { actor: string; text: string } | null {
  const targetId = entry.replyTo;
  if (!targetId) return null;
  const target = messageIndex.value.get(targetId);
  if (!target) return null;
  return { actor: target.actor, text: target.text };
}

// 宿主上下文：卡片侧经 useTimelineCardHost() 取用（compact 走 getter 保持响应）
provide(TIMELINE_CARD_HOST, {
  get compact() {
    return props.compact;
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
  isChatGroupExpanded: (id: string) => expandedChatGroups.value.has(id),
  toggleChatGroup,
} satisfies TimelineCardHost);
</script>

<style scoped>
/* 空态                                                          */
.stage-empty {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--spacing-sm);
  padding: 40px 0;
}
.stage-empty-icon {
  font-size: 44px;
  color: var(--border-color-dark);
}
.stage-empty-text {
  margin: 0;
  font-size: 13px;
  letter-spacing: 0.3px;
  color: var(--text-placeholder);
}

/* 流：单列居左时间轴脊线——所有条目沿轴排布，靠样式区分             */
.feed {
  position: relative;
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.feed::before {
  content: '';
  position: absolute;
  top: 4px;
  bottom: 4px;
  left: 14px;
  width: 1px;
  background: var(--border-color);
}

.feed-row {
  display: flex;
  flex-direction: column;
  animation: rowIn 0.22s cubic-bezier(0.33, 1, 0.68, 1);
}

/* 回看深链锚定高亮（?at= 定位的目标行，数秒后自动熄灭） */
.feed-row.is-anchored {
  outline: 2px solid var(--color-primary);
  outline-offset: 4px;
  border-radius: var(--radius-md);
  background: var(--bg-hover);
  transition: background 0.6s ease;
}

@keyframes rowIn {
  from {
    opacity: 0;
    transform: translateY(5px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* 主播侧头像（会话布局行级布局件）：与观众头像同形，紫色系归到主播 */
.avatar {
  width: 28px;
  height: 28px;
  flex-shrink: 0;
  border-radius: 50%;
  display: grid;
  place-items: center;
  font-size: 11px;
  font-weight: 700;
  background: var(--bg-card);
  border: 1px solid var(--color-collector);
  color: var(--color-collector);
  box-shadow: 0 0 0 3px var(--bg-card);
  z-index: 1;
}
.avatar--host {
  border-color: var(--color-agent);
  color: var(--color-agent);
  background: var(--color-agent-bg);
}

/* 紧凑模式（首页缩略视图）；卡片内部字号经 :deep 下探                */
.feed-timeline.is-compact .feed {
  gap: 6px;
}
.feed-timeline.is-compact .feed-row {
  /* 取消逐行入场动画，避免首页连续刷新时频繁闪烁 */
  animation: none;
}
.feed-timeline.is-compact .chat,
.feed-timeline.is-compact .decision,
.feed-timeline.is-compact .act {
  max-width: 100%;
}
.feed-timeline.is-compact :deep(.say),
.feed-timeline.is-compact :deep(.act-text) {
  font-size: 12px;
}
.feed-timeline.is-compact :deep(.stamp) {
  font-size: 9px;
}

/* 会话布局：观众左 / 主播右 / 过程行居中——对话流优先的显示模式
   （.chat / .decision / .act 此时是卡片根元素，继承本组件 scoped 属性，可直接命中） */
.feed-timeline.is-chat .feed::before {
  display: none;
}
.feed-timeline.is-chat .chat {
  max-width: min(72%, 560px);
}
.feed-timeline.is-chat .feed-row.row-right {
  flex-direction: row;
  justify-content: flex-end;
  gap: 10px;
  align-items: flex-start;
}
.feed-timeline.is-chat .feed-row.row-right .act.is-speech {
  margin-left: 0;
  max-width: min(78%, 560px);
}
/* 会话模式：过程行与折叠条跟随主播气泡右对齐（右内边距对齐气泡内缘），
   卡片与折叠条按内容自适应宽度，不再居中也不再撑满整行 */
.feed-timeline.is-chat .feed-row.row-process,
.feed-timeline.is-chat .feed-row.row-strip {
  align-items: flex-end;
  padding-right: 38px; /* 28px 主播头像 + 10px 间距，与发言气泡内缘对齐 */
}
.feed-timeline.is-chat .feed-row.row-process .decision,
.feed-timeline.is-chat .feed-row.row-process .act {
  margin-left: 0;
  max-width: min(78%, 560px);
}

/* 窄屏                                                          */
@media (max-width: 860px) {
  .chat,
  .chat--gift,
  .chat--super_chat,
  .decision,
  .act {
    max-width: 100%;
    margin-left: 0;
  }
}
</style>
