/**
 * 时间线滚动跟随的页面侧接线：贴底自动跟随、上滚后未读计数与"回到最新"、
 * 容器尺寸变化（窗口缩放、注入面板开合）时维持贴底。
 * 跟随基元（距底阈值判定）在 useScrollFollow；未读计数的重置入口供
 * 清空时间线等外部动作复用。
 */

import { nextTick, onMounted, onScopeDispose, ref, watch, type Ref } from 'vue';
import { useScrollFollow } from '@/composables/useScrollFollow';
import type { ShowEntry } from '@/utils/liveFeed';

export function useTimelineScroll(entries: Ref<ShowEntry[]>) {
  const unseen = ref(0);
  let resizeObserver: ResizeObserver | null = null;

  const { scrollRef, atBottom, onScroll: followOnScroll, scrollToBottom } = useScrollFollow();

  function onScroll(): void {
    followOnScroll();
    if (atBottom.value) unseen.value = 0;
  }

  function jumpToLatest(): void {
    atBottom.value = true;
    unseen.value = 0;
    scrollToBottom();
  }

  /** 未读计数清零：清空时间线等动作后时间线已为空，不再有"新内容"可回 */
  function resetUnseen(): void {
    unseen.value = 0;
  }

  /** 新增条目数：以上一帧末条 id 为锚，找不到锚点则视为全新 */
  function countAdded(next: ShowEntry[], prev: ShowEntry[]): number {
    const anchor = prev.length > 0 ? prev[prev.length - 1].id : null;
    if (!anchor) return next.length;
    const index = next.findIndex(entry => entry.id === anchor);
    return index === -1 ? next.length : next.length - 1 - index;
  }

  watch(entries, async (next, prev) => {
    const added = countAdded(next, prev ?? []);
    await nextTick();
    if (atBottom.value) {
      scrollToBottom();
      unseen.value = 0;
      return;
    }
    if (added > 0) unseen.value += added;
  });

  onMounted(async () => {
    await nextTick();
    scrollToBottom();
    // 容器尺寸变化（窗口缩放、注入面板开合）时维持贴底跟随。仅靠 entries
    // 变化触发不够——布局一变，最新条目就会滑出可视区且不再自动回位
    if (scrollRef.value) {
      resizeObserver = new ResizeObserver(() => {
        if (atBottom.value) scrollToBottom();
      });
      resizeObserver.observe(scrollRef.value);
    }
  });

  onScopeDispose(() => {
    resizeObserver?.disconnect();
    resizeObserver = null;
  });

  return { scrollRef, unseen, onScroll, jumpToLatest, resetUnseen };
}
