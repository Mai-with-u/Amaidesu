<!-- 后台视角=双轨：左列主播 Agent 看到的（观众消息、她自己的决策与调用），
     右列游戏 Agent 看到的（世界事件、它的调用与回报）。

     两列各自独立滚动。为什么不是「同一滚动容器里两列并排」：卡片的垂直位置由内容高度
     决定，两列共享滚动时位置会被读成时间——同一高度上左右两张卡其实不是同一刻，画横线
     对齐也只是视觉上的假对齐。各读各的、需要对照时点一下卡片把对侧滚到同一刻，比强行
     对齐更诚实。代价是横跨两列的节头不再成立，因此节头下沉到每列内部。

     分节仍按流程单（rundown.changed）真实给出的环节切，不用固定分钟数硬切——分钟窗口
     会把一次派活、一轮决策从中间劈开。折叠状态由两列共享：点任一侧节头，两列同时收起。

     两列渲染的是同一套卡片（CARD_REGISTRY），不是摘要行：卡面版式、参数/结果折叠、
     统计胶囊、完整请求链接都与单列时间线完全一致。跨轨因果只有一种：主播派活
     （framework_delegate）→ 游戏 Agent 受理，靠游戏列的收活卡表达（task.changed
     受理回执按 task_id 与派活卡返回的任务号身份配对合成），点收活卡跳回对侧那张派活卡。 -->
<script setup lang="ts">
import { computed, ref } from 'vue';

import { CARD_REGISTRY } from './cards/registry';
import { useCardHost } from './cards/useCardHost';
import '@/components/live/cards/cards-shared.css';
import { useLaneScroll } from '@/composables/live/useLaneScroll';
import {
  annotateTaskEnds,
  buildDelegateReceipts,
  buildLaneSections,
  collapseGoalPolls,
  type LaneId,
  type LaneSection,
  type SegmentBoundary,
  type TaskFact,
  type TaskWindow,
} from '@/utils/liveLanes';
import type { ShowEntry } from '@/utils/liveFeed';
import { formatTimeHMS } from '@/utils/format';
import { useNowTick } from '@/composables/useNowTick';

const props = withDefaults(
  defineProps<{
    entries: ShowEntry[];
    emptyText?: string;
    /** 任务区间（task.changed 的受理 + 终态）：按 goal_id 回填到发起它的 execute 卡 */
    tasks?: TaskWindow[];
    /** 任务事实（task.changed 逐条，含未终态的受理）：收活卡的身份配对数据源 */
    taskFacts?: TaskFact[];
    /**
     * 环节边界（环节名 + 起止时刻）：回看窗口内没有流程单条目时，用它判断
     * 窗口内容属于哪个环节。undefined 表示没有这个信息渠道（实时模式），
     * 空数组表示本场查不到环节事实。
     */
    boundaries?: SegmentBoundary[] | undefined;
    /** 搜索命中时刻集合：命中的卡加竖条标记（按时刻匹配，与单列时间线同语义） */
    matchedTs?: Set<number>;
  }>(),
  {
    emptyText: '暂无内容',
    tasks: () => [],
    taskFacts: () => [],
    boundaries: undefined,
    matchedTs: undefined,
  },
);

// 合成条目先插入（收活卡）、再回填终态（taskEnd），都发生在分节之前：
// 游戏列拿到的是带上收活卡与终态徽标的完整视图
const withReceipts = computed(() => buildDelegateReceipts(props.entries, props.taskFacts));
const annotated = computed(() => annotateTaskEnds(withReceipts.value, props.tasks));
const sections = computed<LaneSection[]>(() =>
  buildLaneSections(annotated.value, props.boundaries),
);

// 相对时间标签（"12s 前"）每秒刷新；卡片 props 恒为 entry + nowMs
const nowMs = useNowTick();

// 卡片宿主：与单列时间线共用同一份实现，两条轨道因此可以直挂卡片
// 双轨不提供"这轮决策"链接：每一行都挂一个同款链接会把版面切碎，
// 轮次跳转留给决策轨迹的块头（一轮一次），这里只保留卡片本身的信息。
useCardHost({
  entries: computed(() => props.entries),
  roundClickable: () => false,
});

/** 折叠的节（键为节 key）：整节收起来只留节头，方便跳过不关心的环节。两列共享同一份状态 */
const collapsed = ref<Set<string>>(new Set());

function isCollapsed(section: LaneSection): boolean {
  return collapsed.value.has(section.key);
}

function toggleSection(section: LaneSection): void {
  const next = new Set(collapsed.value);
  if (next.has(section.key)) next.delete(section.key);
  else next.add(section.key);
  collapsed.value = next;
}

function rangeLabel(section: LaneSection): string {
  return `${formatTimeHMS(section.startMs)} – ${formatTimeHMS(section.endMs)}`;
}

/** 节头的双侧条数：本列的那一份正常显示，另一列的那一份压暗。
 * 独立滚动之后，光看自己这一列的长度猜不出对面这一段有多密，
 * 写出来就不用两边各翻一遍。 */
function sideCounts(section: LaneSection): { lane: LaneId; label: string; count: number }[] {
  return [
    { lane: 'stage', label: '主播', count: section.stage.length },
    { lane: 'game', label: '游戏', count: section.game.length },
  ];
}

// ---------------------------------------------------------------------------
// 独立滚动：两列各有自己的滚动位置，各自贴底跟随
// （跟随机制在 useLaneScroll：换批条目即跟随，不按长度判断）
// ---------------------------------------------------------------------------

const {
  stageCol,
  gameCol,
  columnOf,
  onScroll: onLaneScroll,
  stopFollowing,
  scrollToLatest,
  pendingCount,
} = useLaneScroll(computed(() => props.entries));

// 页面的"回到最新"按钮驱动的是双轨（这一档外层容器不滚）：暴露两列回到底部
// 的动作与落下的新内容条数；搜索跳转用 focusEntry（有本地条目 id 时）与
// revealNearest（服务端命中只有时刻，两列里找最近的卡）。
// focusEntry / revealNearest 是函数声明（提升可用），在此处暴露成立。
defineExpose({ scrollToLatest, pendingCount, focusEntry, revealNearest });

// ---------------------------------------------------------------------------
// 按需对齐：点一张卡 → 对侧列滚到同一时刻；对侧没有内容时什么也不做
// ---------------------------------------------------------------------------

/** 对侧列里离目标时刻最近的那张卡：两列事件不一一对应，只能找最近的 */
function nearestIn(lane: LaneId, tsMs: number): HTMLElement | null {
  const column = columnOf(lane);
  if (!column) return null;
  const cards = [...column.querySelectorAll<HTMLElement>('[data-entry-id]')];
  let best: HTMLElement | null = null;
  let bestGap = Number.POSITIVE_INFINITY;
  for (const card of cards) {
    const own = Number(card.dataset.entryTs);
    if (!Number.isFinite(own)) continue;
    const gap = Math.abs(own - tsMs);
    if (gap < bestGap) {
      bestGap = gap;
      best = card;
    }
  }
  return best;
}

/** 把某一列滚到能看见目标卡的位置（垂直居中），并闪一下 */
function reveal(lane: LaneId, card: HTMLElement | null): void {
  const column = columnOf(lane);
  if (!column || !card) return;
  const top = card.offsetTop - column.clientHeight / 2 + card.offsetHeight / 2;
  column.scrollTo({ top: Math.max(0, top), behavior: 'smooth' });
  // 用户明确选择了看这一刻，该列不再继续贴底跟随
  stopFollowing(lane);
  card.classList.add('is-flash');
  globalThis.setTimeout(() => card.classList.remove('is-flash'), 1200);
}

/** 卡片内的可交互元素自己处理点击：展开参数、点链接不该顺带把对侧滚走 */
const INTERACTIVE = 'button, a, summary, input, textarea, select, [role="button"]';

/** 卡片激活的公共逻辑：点击与键盘触发（Enter/Space）走同一条路 */
function activateCard(entry: ShowEntry, lane: LaneId, target: EventTarget | null): void {
  if (target instanceof Element && target.closest(INTERACTIVE)) return;
  // 收活卡的点击语义是"跳到这次派活的指令卡"（身份配对已给出目标），不走对侧近邻对齐
  if (entry.kind === 'delegate_receipt' && entry.delegateEntryId) {
    focusEntry(entry.delegateEntryId, 'stage');
    return;
  }
  alignFrom(entry, lane);
}

function onCardClick(entry: ShowEntry, lane: LaneId, event: MouseEvent): void {
  activateCard(entry, lane, event.target);
}

function onCardKeydown(entry: ShowEntry, lane: LaneId, event: KeyboardEvent): void {
  activateCard(entry, lane, event.target);
}

/** 点卡片空白处：把对侧滚到同一时刻 */
function alignFrom(entry: ShowEntry, lane: LaneId): void {
  const other: LaneId = lane === 'stage' ? 'game' : 'stage';
  reveal(other, nearestIn(other, entry.tsMs));
}

/** 跳到某条指定卡（游戏列收活卡用它回到主播列对应的派活卡；搜索跳转在
 *  命中有本地条目时也走这里） */
function focusEntry(entryId: string, lane: LaneId): void {
  const column = columnOf(lane);
  if (!column) return;
  const card = column.querySelector<HTMLElement>(`[data-entry-id="${CSS.escape(entryId)}"]`);
  if (card) reveal(lane, card);
}

/** 按时刻近邻定位：两列里离目标时刻最近的卡滚到可视区（搜索的服务端命中
 *  没有本地条目 id 可用，时刻是唯一能对上的东西） */
function revealNearest(tsMs: number): void {
  const stageCard = nearestIn('stage', tsMs);
  const gameCard = nearestIn('game', tsMs);
  const stageGap = stageCard ? Math.abs(Number(stageCard.dataset.entryTs) - tsMs) : Infinity;
  const gameGap = gameCard ? Math.abs(Number(gameCard.dataset.entryTs) - tsMs) : Infinity;
  if (stageGap <= gameGap && stageCard) reveal('stage', stageCard);
  else if (gameCard) reveal('game', gameCard);
}

// ---------------------------------------------------------------------------
// 游戏侧进度查询收拢：maicraft_goal(operation=get) 且 goal_id 与某张 execute 卡
// 相等的查询卡，从原位摘除挂到那张 execute 条目的 polls 字段，卡体内部渲染
// （ToolCard 的专用卡面负责"进度查询 ×N"与明细展开）
// ---------------------------------------------------------------------------
</script>

<template>
  <!-- 卡面样式全部挂在 `.feed-timeline` 作用域下（cards-shared.css 是全局样式表），
       因此双轨外壳也带上这个类：卡片契约与观感都和单列时间线一致 -->
  <div class="lanes feed-timeline">
    <p v-if="sections.length === 0" class="lanes-empty">{{ emptyText }}</p>

    <template v-else>
      <div ref="stageCol" class="lane-col lane-stage" @scroll.passive="onLaneScroll('stage')">
        <p class="lane-hint">
          主播 Agent 看到的<span class="lane-tip">点卡片 → 对侧跳到同一刻</span>
        </p>
        <section v-for="section in sections" :key="`stage-${section.key}`" class="lane-section">
          <button
            type="button"
            class="section-head"
            :aria-expanded="!isCollapsed(section)"
            @click="toggleSection(section)"
          >
            <span class="section-caret" :class="{ 'is-collapsed': isCollapsed(section) }">▸</span>
            <span class="section-title">{{ section.title }}</span>
            <span v-if="section.note" class="section-note">{{ section.note }}</span>
            <span class="section-range mono">{{ rangeLabel(section) }}</span>
            <span class="section-count">
              <span
                v-for="side in sideCounts(section)"
                :key="side.lane"
                class="side-count"
                :class="{ 'is-own': side.lane === 'stage' }"
              >
                {{ side.label }} {{ side.count }}
              </span>
            </span>
          </button>
          <div v-show="!isCollapsed(section)" class="lane-body">
            <div
              v-for="entry in section.stage"
              :key="entry.id"
              class="lane-card"
              :class="{ 'is-matched': matchedTs?.has(entry.tsMs) }"
              :data-entry-id="entry.id"
              :data-entry-ts="entry.tsMs"
              tabindex="0"
              @click="onCardClick(entry, 'stage', $event)"
              @keydown.enter.prevent="onCardKeydown(entry, 'stage', $event)"
              @keydown.space.prevent="onCardKeydown(entry, 'stage', $event)"
            >
              <Component :is="CARD_REGISTRY[entry.kind]" :entry="entry" :now-ms="nowMs" />
            </div>
            <p v-if="section.stage.length === 0" class="lane-empty">本节主播侧无内容</p>
          </div>
        </section>
      </div>

      <div ref="gameCol" class="lane-col lane-game" @scroll.passive="onLaneScroll('game')">
        <p class="lane-hint">
          游戏 Agent 看到的<span class="lane-tip">点卡片 → 对侧跳到同一刻</span>
        </p>
        <section v-for="section in sections" :key="`game-${section.key}`" class="lane-section">
          <button
            type="button"
            class="section-head"
            :aria-expanded="!isCollapsed(section)"
            @click="toggleSection(section)"
          >
            <span class="section-caret" :class="{ 'is-collapsed': isCollapsed(section) }">▸</span>
            <span class="section-title">{{ section.title }}</span>
            <span v-if="section.note" class="section-note">{{ section.note }}</span>
            <span class="section-range mono">{{ rangeLabel(section) }}</span>
            <span class="section-count">
              <span
                v-for="side in sideCounts(section)"
                :key="side.lane"
                class="side-count"
                :class="{ 'is-own': side.lane === 'game' }"
              >
                {{ side.label }} {{ side.count }}
              </span>
            </span>
          </button>
          <div v-show="!isCollapsed(section)" class="lane-body">
            <template v-for="entry in collapseGoalPolls(section.game)" :key="entry.id">
              <div
                class="lane-card"
                :class="{ 'is-matched': matchedTs?.has(entry.tsMs) }"
                :data-entry-id="entry.id"
                :data-entry-ts="entry.tsMs"
                tabindex="0"
                @click="onCardClick(entry, 'game', $event)"
                @keydown.enter.prevent="onCardKeydown(entry, 'game', $event)"
                @keydown.space.prevent="onCardKeydown(entry, 'game', $event)"
              >
                <Component :is="CARD_REGISTRY[entry.kind]" :entry="entry" :now-ms="nowMs" />
              </div>
            </template>
            <p v-if="section.game.length === 0" class="lane-empty">本节游戏侧无内容</p>
          </div>
        </section>
      </div>
    </template>
  </div>
</template>

<style scoped>
/* 两列等宽、各自滚动；高度由页面给的定高容器撑满，窄于 1200px 不在支持范围内 */
.lanes {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  height: 100%;
  min-height: 0;
  padding: 10px 12px;
}

.lanes-empty {
  grid-column: 1 / -1;
  margin: 32px 0;
  text-align: center;
  color: var(--text-placeholder);
  font-size: 13px;
}

.lane-col {
  position: relative;
  overflow-y: auto;
  overscroll-behavior: contain;
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
  min-height: 0;
  padding: 0 6px 12px;
  scrollbar-width: thin;
}

.lane-stage {
  border-left: 2px solid var(--color-success);
}

.lane-game {
  /* 强调色只用"这是游戏侧"这一个含义：失败由卡片自己的红徽标表达，
     两处都染红会让"游戏"和"出错"混淆 */
  border-left: 2px solid var(--color-game);
}

/* 列标签吸在列顶：独立滚动后它同时承担"我在看哪一侧"的定位 */
.lane-hint {
  position: sticky;
  top: 0;
  z-index: 3;
  margin: 0;
  padding: 4px 2px 5px;
  /* 必须是不透明底色：吸顶标签下面就是滚动中的卡片，透明会把卡片文字透出来 */
  background: var(--bg-card);
  font-size: 11px;
  color: var(--text-secondary);
}

/* 列是 flex 容器：子项必须禁止收缩，否则整列的节会被压扁到视口高度里，
   滚动高度恒等于可视高度，两列都滚不动 */
.lane-col > * {
  flex: none;
}

.lane-tip {
  margin-left: 8px;
  color: var(--text-placeholder);
  font-size: 10px;
}

.lane-section {
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-md);
  /* 用 clip 而不是 hidden：hidden 会让本节成为独立滚动容器，吸顶的节头于是
     相对本节定位、被顶下去一整个 top 偏移，压住第一张卡 */
  overflow: clip;
}

/* 节头也吸在列顶（让开列标签那一行）：滚动时始终知道处在哪个环节。
   原生 button 承载（键盘可达）：重置按钮默认外观，视觉与原 header 一致 */
.section-head {
  position: sticky;
  top: 22px;
  z-index: 2;
  display: flex;
  width: 100%;
  gap: 10px;
  align-items: baseline;
  padding: 5px 10px;
  background: var(--bg-hover);
  border: none;
  border-bottom: 1px solid var(--border-color-light);
  font: inherit;
  font-size: 11px;
  color: var(--text-secondary);
  text-align: left;
  cursor: pointer;
  user-select: none;
}

.section-caret {
  font-size: 9px;
  transition: transform 0.15s ease;
  transform: rotate(90deg);
}

.section-caret.is-collapsed {
  transform: rotate(0deg);
}

.section-title {
  color: var(--text-primary);
  font-weight: 600;
}

.section-note {
  padding: 0 5px;
  border-radius: var(--radius-sm);
  background: var(--bg-card);
  font-size: 10px;
}

.section-range {
  color: var(--text-secondary);
}

.section-count {
  margin-left: auto;
  display: inline-flex;
  gap: 8px;
  font-variant-numeric: tabular-nums;
}

.side-count {
  color: var(--text-placeholder);
}

/* 本列那一份要能一眼认出来，另一列只作参照 */
.side-count.is-own {
  color: var(--text-primary);
  font-weight: 600;
}

.lane-body {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 6px 8px;
  background: var(--bg-card);
}

.lane-empty {
  margin: 6px 0;
  font-size: 11px;
  color: var(--text-placeholder);
}

/* 双轨里来源由左右分栏表达（左=主播侧，右=游戏侧），卡片内再标一次是重复信息；
   "工具调用"这个类别前缀同理由卡片形态自明。省下的横向空间留给参数正文。 */
.lanes :deep(.act-source),
.lanes :deep(.act-kind) {
  display: none;
}

.lane-card {
  position: relative;
  min-width: 0;
  cursor: pointer;
}

.lane-card:hover {
  outline: 1px solid var(--border-color);
  outline-offset: 1px;
  border-radius: var(--radius-sm);
}

.lane-card.is-flash {
  outline: 2px solid var(--color-primary);
  outline-offset: 2px;
  transition: outline-color 0.2s;
}

/* 搜索命中标记：与单列时间线同语义（竖条，不隐藏不重排） */
.lane-card.is-matched {
  box-shadow: inset 3px 0 0 var(--color-primary);
}
</style>
