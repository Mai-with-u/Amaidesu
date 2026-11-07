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
     （framework_delegate / framework_prompt）→ 游戏 Agent 的回报，靠卡片上的
     「派活 → 游戏 · 回报 N 条」「← 派活：<指令>」标记表达，点标记跳到对侧那张卡。 -->
<script setup lang="ts">
import { computed, ref } from 'vue';

import { CARD_REGISTRY } from './cards/registry';
import { useCardHost } from './cards/useCardHost';
import '@/components/live/cards/cards-shared.css';
import { useLaneScroll } from '@/composables/live/useLaneScroll';
import {
  buildLaneSections,
  delegationOfReply,
  delegationWindows,
  taskOfEntry,
  type LaneId,
  type LaneSection,
  type TaskWindow,
} from '@/utils/liveLanes';
import type { ShowEntry } from '@/utils/liveFeed';
import { formatLatency, formatTimeHMS, truncateText } from '@/utils/format';
import { useNowTick } from '@/composables/useNowTick';

const props = withDefaults(
  defineProps<{
    entries: ShowEntry[];
    emptyText?: string;
    /** 任务区间（task.changed 的受理 + 终态）：游戏列据此把一次工具调用的前后收成一组 */
    tasks?: TaskWindow[];
  }>(),
  { emptyText: '暂无内容', tasks: () => [] },
);

const sections = computed<LaneSection[]>(() => buildLaneSections(props.entries));
const windows = computed(() => delegationWindows(props.entries));

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
// 的动作与落下的新内容条数
defineExpose({ scrollToLatest, pendingCount });

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

function onCardClick(entry: ShowEntry, lane: LaneId, event: MouseEvent): void {
  const target = event.target;
  if (target instanceof Element && target.closest(INTERACTIVE)) return;
  alignFrom(entry, lane);
}

/** 点卡片空白处：把对侧滚到同一时刻 */
function alignFrom(entry: ShowEntry, lane: LaneId): void {
  const other: LaneId = lane === 'stage' ? 'game' : 'stage';
  reveal(other, nearestIn(other, entry.tsMs));
}

/** 跳到某条指定卡（回报卡上的「← 派活」用它回到对侧的指令卡） */
function focusEntry(entryId: string, lane: LaneId): void {
  const column = columnOf(lane);
  if (!column) return;
  const card = column.querySelector<HTMLElement>(`[data-entry-id="${CSS.escape(entryId)}"]`);
  if (card) reveal(lane, card);
}

// ---------------------------------------------------------------------------
// 游戏侧任务分组：同一次工具调用的受理与终态之间，卡片收成一组
// ---------------------------------------------------------------------------

function taskOf(entry: ShowEntry): TaskWindow | null {
  return props.tasks.length ? taskOfEntry(entry, props.tasks) : null;
}

/**
 * 组头只在"值得说明"时出现：组内有两次以上调用，或这次调用本身够久。
 * 每次点击级别的短调用都挂一行组头，反而把游戏列读成表格。
 */
function showTaskHead(entries: ShowEntry[], index: number): boolean {
  const window = taskOf(entries[index]);
  if (!window) return false;
  const prev = index > 0 ? taskOf(entries[index - 1]) : null;
  if (prev?.taskId === window.taskId) return false;
  let count = 1;
  for (let i = index + 1; i < entries.length; i += 1) {
    if (taskOf(entries[i])?.taskId !== window.taskId) break;
    count += 1;
  }
  return count >= 2 || window.endMs - window.startMs >= 2_000;
}

/** 组头短名：能力名去掉命名空间前缀，过长截断 */
function taskLabel(window: TaskWindow): string {
  return truncateText(window.label.replace(/^maicraft:/, ''), 24) || '游戏侧任务';
}

function taskWindowOf(entry: ShowEntry): TaskWindow {
  return taskOf(entry) as TaskWindow;
}

// ---------------------------------------------------------------------------
// 跨轨标记
// ---------------------------------------------------------------------------

/** 派活卡上的标记：这条指令之后共收到多少条回报 */
function delegateChip(entry: ShowEntry): string {
  const window = windows.value.find(item => item.delegate.id === entry.id);
  if (!window) return '';
  return `派活 → 游戏 · 回报 ${window.replies} 条`;
}

/** 派活卡上的指令原文（tool 条目的 detail.args.instruction） */
function delegateInstruction(entry: ShowEntry): string {
  const args = (entry.detail as { args?: unknown } | undefined)?.args;
  if (!args || typeof args !== 'object') return '';
  const instruction = (args as { instruction?: unknown }).instruction;
  return typeof instruction === 'string' ? instruction : '';
}

/** 回报卡上的来处：写清是哪条指令的回报；指令在另一列，点一下滚过去 */
function replyChip(entry: ShowEntry): string {
  const window = delegationOfReply(windows.value, entry);
  if (!window) return '';
  const instruction = delegateInstruction(window.delegate);
  const head = instruction ? truncateText(instruction, 16) : formatTimeHMS(window.delegate.tsMs);
  return `← 派活：${head}`;
}

function focusDelegate(entry: ShowEntry): void {
  const owner = delegationOfReply(windows.value, entry);
  if (owner) focusEntry(owner.delegate.id, 'stage');
}
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
          <header class="section-head" @click="toggleSection(section)">
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
          </header>
          <div v-show="!isCollapsed(section)" class="lane-body">
            <div
              v-for="entry in section.stage"
              :key="entry.id"
              class="lane-card"
              :class="{ 'is-delegate': delegateChip(entry) }"
              :data-entry-id="entry.id"
              :data-entry-ts="entry.tsMs"
              @click="onCardClick(entry, 'stage', $event)"
            >
              <p v-if="delegateChip(entry)" class="lane-chip is-from">{{ delegateChip(entry) }}</p>
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
          <header class="section-head" @click="toggleSection(section)">
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
          </header>
          <div v-show="!isCollapsed(section)" class="lane-body">
            <template v-for="(entry, index) in section.game" :key="entry.id">
              <p v-if="showTaskHead(section.game, index)" class="lane-task">
                <span class="lane-task-name">{{ taskLabel(taskWindowOf(entry)) }}</span>
                <span class="lane-task-meta">
                  {{ formatTimeHMS(taskWindowOf(entry).startMs) }} ·
                  {{ formatLatency(taskWindowOf(entry).endMs - taskWindowOf(entry).startMs) }}
                </span>
                <span
                  class="lane-task-status"
                  :class="{ 'is-bad': taskWindowOf(entry).statusLabel !== '完成' }"
                  >{{ taskWindowOf(entry).statusLabel }}</span
                >
              </p>
              <div
                class="lane-card"
                :class="{ 'is-reply': replyChip(entry) }"
                :data-entry-id="entry.id"
                :data-entry-ts="entry.tsMs"
                @click="onCardClick(entry, 'game', $event)"
              >
                <button
                  v-if="replyChip(entry)"
                  type="button"
                  class="lane-chip is-to"
                  title="跳到这次派活的指令卡"
                  @click.stop="focusDelegate(entry)"
                >
                  {{ replyChip(entry) }}
                </button>
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

.lane-task {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin: 10px 0 4px;
  padding: 2px 6px 2px 8px;
  border-left: 2px solid var(--color-game);
  color: var(--text-secondary);
  font-size: 11px;
}

.lane-task-name {
  color: var(--text-primary);
  font-weight: 600;
}

.lane-task-meta {
  font-variant-numeric: tabular-nums;
}

.lane-task-status {
  margin-left: auto;
  padding: 0 5px;
  border-radius: 3px;
  background: var(--bg-inset);
  color: var(--text-secondary);
}

.lane-task-status.is-bad {
  color: var(--color-danger);
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

/* 节头也吸在列顶（让开列标签那一行）：滚动时始终知道处在哪个环节 */
.section-head {
  position: sticky;
  top: 22px;
  z-index: 2;
  display: flex;
  gap: 10px;
  align-items: baseline;
  padding: 5px 10px;
  background: var(--bg-hover);
  border-bottom: 1px solid var(--border-color-light);
  font-size: 11px;
  color: var(--text-secondary);
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

/* 参数药丸在双轨里压成一行：超出部分省略，完整参数仍在"参数 / 结果"折叠区 */
.lanes :deep(.act-args) {
  flex-wrap: nowrap;
  overflow: hidden;
}

.lanes :deep(.act-args .arg-pill) {
  flex: none;
  max-width: 240px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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

.lane-chip {
  margin: 0 0 3px;
  font-size: 10px;
  letter-spacing: 0.2px;
}

.lane-chip.is-from {
  color: var(--color-primary);
}

.lane-chip.is-to {
  cursor: pointer;
  background: none;
  border: none;
  padding: 0;
  font: inherit;
  text-align: left;
  color: var(--color-primary);
}

.lane-chip.is-to:hover {
  text-decoration: underline;
}

.lane-card.is-flash {
  outline: 2px solid var(--color-primary);
  outline-offset: 2px;
  transition: outline-color 0.2s;
}
</style>
