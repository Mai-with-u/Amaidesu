/**
 * 滚动容器贴底跟随（Agents / Collectors / LiveObserver 共用的单一事实源）
 *
 * 语义：距底 ≤32px 视为"在底部"；新条目到达时仅当用户贴底才自动跟随，
 * 向上滚动过则不强制（由调用方决定是否在模板上绑 @scroll 维护 atBottom）。
 */

import { nextTick, ref, watch, type Ref } from 'vue';

/** 距底多少像素内视为"在底部"（全站统一阈值） */
export const SCROLL_BOTTOM_THRESHOLD_PX = 32;

export function useScrollFollow(source?: Ref<unknown>) {
  const scrollRef = ref<HTMLElement | null>(null);
  /** 用户是否停留在底部（需绑定 onScroll 才会随滚动更新） */
  const atBottom = ref(true);

  function isAtBottom(el: HTMLElement): boolean {
    return el.scrollHeight - el.scrollTop - el.clientHeight <= SCROLL_BOTTOM_THRESHOLD_PX;
  }

  /** 模板 @scroll.passive 绑定；维护 atBottom 状态 */
  function onScroll(): void {
    const el = scrollRef.value;
    if (el) atBottom.value = isAtBottom(el);
  }

  function scrollToBottom(): void {
    const el = scrollRef.value;
    if (el) el.scrollTop = el.scrollHeight;
  }

  if (source) {
    watch(source, async () => {
      await nextTick();
      const el = scrollRef.value;
      if (!el) return;
      // 用户滚到底 → 跟到底；用户向上滚动则不强制。
      if (isAtBottom(el)) {
        el.scrollTop = el.scrollHeight;
      }
    });
  }

  return { scrollRef, atBottom, onScroll, scrollToBottom };
}
