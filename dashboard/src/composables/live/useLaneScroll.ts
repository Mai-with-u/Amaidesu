/**
 * 双轨（后台视角）的贴底跟随：两列各有自己的滚动位置，各自在贴底时跟随新内容。
 *
 * 触发条件是"换了一批条目"而不是"长度变了"：条目缓冲按 MAX_ENTRIES 限长保尾，
 * 满额之后新条目挤掉旧条目、长度恒定不变——按长度监听会就此停摆，切进后台视角
 * 之后双轨再也不跟着新内容往下走（这一条是真实缺陷的成因，勿改回按长度监听）。
 *
 * 用户往上翻过的那一列不再跟随（阅读意图优先），滚回底部即恢复跟随；这一列
 * 期间落下的新内容计入 pending，页面据此显示"回到最新"。
 */

import { computed, nextTick, onMounted, onScopeDispose, ref, watch, type Ref } from 'vue';

import { countAddedEntries, type ShowEntry } from '@/utils/liveFeed';
import type { LaneId } from '@/utils/liveLanes';

/** 距底多少像素内视为贴底 */
const NEAR_BOTTOM_PX = 120;

/** 双轨的两列 */
const LANES: readonly LaneId[] = ['stage', 'game'];

export interface UseLaneScroll {
  /** 左列（主播侧）滚动容器 */
  stageCol: Ref<HTMLElement | null>;
  /** 右列（游戏侧）滚动容器 */
  gameCol: Ref<HTMLElement | null>;
  /** 取某一列的滚动容器 */
  columnOf: (lane: LaneId) => HTMLElement | null;
  /** 模板 @scroll 绑定：据此更新该列的跟随开关与待看计数 */
  onScroll: (lane: LaneId) => void;
  /** 该列不再贴底跟随（用户明确去看某一刻时调用） */
  stopFollowing: (lane: LaneId) => void;
  /** 两列都回到最新并恢复跟随（页面的"回到最新"按钮用） */
  scrollToLatest: () => void;
  /** 两列合计落下的新内容条数（页面据此显示"回到最新"按钮） */
  pendingCount: Ref<number>;
}

export function useLaneScroll(entries: Ref<ShowEntry[]>): UseLaneScroll {
  const stageCol = ref<HTMLElement | null>(null);
  const gameCol = ref<HTMLElement | null>(null);

  /** 每列各自的跟随开关：上翻即停，滚回底部即恢复 */
  const following: Record<LaneId, boolean> = { stage: true, game: true };
  /** 每列各自落下的新内容条数：该列没在跟随时累加，滚回底部或"回到最新"后清零 */
  const pending = ref<Record<LaneId, number>>({ stage: 0, game: 0 });

  const pendingCount = computed(() => pending.value.stage + pending.value.game);

  function columnOf(lane: LaneId): HTMLElement | null {
    return lane === 'stage' ? stageCol.value : gameCol.value;
  }

  function isNearBottom(element: HTMLElement): boolean {
    return element.scrollHeight - element.scrollTop - element.clientHeight < NEAR_BOTTOM_PX;
  }

  function onScroll(lane: LaneId): void {
    const element = columnOf(lane);
    if (!element) return;
    following[lane] = isNearBottom(element);
    // 回到底部即表示这一列落下的内容已经看完
    if (following[lane]) pending.value[lane] = 0;
  }

  function scrollToBottom(lane: LaneId): void {
    const element = columnOf(lane);
    if (element) element.scrollTop = element.scrollHeight;
  }

  function stopFollowing(lane: LaneId): void {
    following[lane] = false;
  }

  /** 两列都回到最新并恢复跟随：用户明确表示"我要看最新的" */
  function scrollToLatest(): void {
    for (const lane of LANES) {
      following[lane] = true;
      pending.value[lane] = 0;
      scrollToBottom(lane);
    }
  }

  /** 只滚处于跟随状态的列：用户上翻过的那一列保持原位 */
  function followToBottom(): void {
    for (const lane of LANES) {
      if (following[lane]) scrollToBottom(lane);
    }
  }

  // 换了一批条目就跟到底部。等一次渲染落定再滚——滚底要读列高，
  // 渲染之前读到的还是旧高度。
  watch(entries, async (next, prev) => {
    const added = countAddedEntries(next, prev ?? []);
    await nextTick();
    // 时间线被清空：此前落下的内容作废，两列重新从头跟随
    if (next.length === 0) {
      for (const lane of LANES) {
        following[lane] = true;
        pending.value[lane] = 0;
        scrollToBottom(lane);
      }
      return;
    }
    for (const lane of LANES) {
      if (following[lane]) {
        scrollToBottom(lane);
        pending.value[lane] = 0;
      } else {
        pending.value[lane] += added;
      }
    }
  });

  // 刚切进后台视角时两列的滚动位置从顶部开始，不主动落底就会停在整场最早的一屏
  onMounted(async () => {
    await nextTick();
    followToBottom();
  });

  // 列高变化（窗口缩放、面板开合）时维持贴底：仅靠条目变化触发不够——
  // 布局一变，最新卡片就滑出可视区且不再自动回位
  let resizeObserver: ResizeObserver | null = null;

  onMounted(() => {
    if (!stageCol.value || !gameCol.value) return;
    resizeObserver = new ResizeObserver(() => followToBottom());
    resizeObserver.observe(stageCol.value);
    resizeObserver.observe(gameCol.value);
  });

  onScopeDispose(() => {
    resizeObserver?.disconnect();
    resizeObserver = null;
  });

  return {
    stageCol,
    gameCol,
    columnOf,
    onScroll,
    stopFollowing,
    scrollToLatest,
    pendingCount,
  };
}
