/**
 * 时间线滚动跟随在页面侧的组装：贴底自动跟随、上滚后未读计数与"回到最新"、
 * 容器尺寸变化（窗口缩放、注入面板开合）时维持贴底。
 * 跟随基元（距底阈值判定）在 useScrollFollow；未读计数的重置入口供
 * 清空时间线等外部动作复用。
 *
 * 向上滚动宽限：浏览器的 scroll 事件分发晚于 Vue 的响应式更新（标签页在
 * 后台被节流时可达秒级），这段窗口里"是否贴底"标记还是旧值，贴底跟随会把
 * 用户刚滚上去的位置强行拽回。滚轮向上（deltaY < 0）即视为阅读意图，
 * 宽限期内不贴底；真实滚回底部（onScroll 判定在底）随即解除。
 *
 * 生命周期：滚轮监听 onMounted 挂、onScopeDispose 摘；keep-alive 隐藏期间
 * 容器不在文档中收不到滚轮事件，无需按激活态启停。
 */

import { nextTick, onMounted, onScopeDispose, ref, watch, type Ref } from 'vue';
import { useScrollFollow } from '@/composables/useScrollFollow';
import type { ShowEntry } from '@/utils/liveFeed';

/** 用户向上滚动后不贴底跟随的宽限时长：须盖过后台标签页的滚动事件分发延迟 */
const SCROLL_UP_GRACE_MS = 2000;

export function useTimelineScroll(entries: Ref<ShowEntry[]>) {
  const unseen = ref(0);
  let resizeObserver: ResizeObserver | null = null;
  /** 最近一次向上滚轮的时刻；0 表示当前没有未过期的阅读意图 */
  let lastScrollUpAt = 0;

  const { scrollRef, atBottom, onScroll: followOnScroll, scrollToBottom } = useScrollFollow();

  /** 距上次向上滚轮已过宽限期，才允许贴底跟随把位置拽回底部 */
  function followAllowed(): boolean {
    return Date.now() - lastScrollUpAt > SCROLL_UP_GRACE_MS;
  }

  function onWheel(event: WheelEvent): void {
    if (event.deltaY < 0) lastScrollUpAt = Date.now();
  }

  function onScroll(): void {
    followOnScroll();
    if (atBottom.value) {
      unseen.value = 0;
      lastScrollUpAt = 0;
    }
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
    if (atBottom.value && followAllowed()) {
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
        if (atBottom.value && followAllowed()) scrollToBottom();
      });
      resizeObserver.observe(scrollRef.value);
      scrollRef.value.addEventListener('wheel', onWheel, { passive: true });
    }
  });

  onScopeDispose(() => {
    resizeObserver?.disconnect();
    resizeObserver = null;
    scrollRef.value?.removeEventListener('wheel', onWheel);
  });

  return { scrollRef, unseen, onScroll, jumpToLatest, resetUnseen };
}
