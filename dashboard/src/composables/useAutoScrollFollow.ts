/**
 * 实时流列表页（LogViewer / EventLog）的 RAF 节流滚动跟随。
 *
 * 收编两页成对重复的滚动段：requestAnimationFrame 节流的滚底（同一帧多次
 * 触发合并，避免强制同步布局）、滚离底部的判定与页面反应、挂载时初始滚底、
 * 卸载时取消挂起的动画帧。"新数据到达时跟随"的门控（暂停/历史浏览等状态）
 * 是页面域知识，留在视图的 store 订阅回调里，经返回的 scrollToBottom 调用。
 */

import { nextTick, onMounted, onUnmounted, type Ref } from 'vue';

export interface UseAutoScrollFollowOptions {
  /** 列表滚动容器 */
  container: Ref<HTMLElement | null>;
  /** 滚动事件的生效门：返回 false 时跳过离底判定（如历史浏览/自由滚动态） */
  canHandleScroll: () => boolean;
  /** 用户滚离底部时的页面反应（切自由滚动/暂停跟随）；滚动中可能连发，需自身幂等 */
  onLeaveBottom?: () => void;
  /** 距底多少像素内视为贴底（两页既有口径为 50） */
  thresholdPx?: number;
}

export interface UseAutoScrollFollow {
  /** RAF 节流地滚到底部（供数据订阅回调与页面动作复用） */
  scrollToBottom: () => void;
  /** 模板 @scroll 绑定：贴底时不动作，滚离底部时回调 onLeaveBottom */
  handleScroll: () => void;
}

export function useAutoScrollFollow(options: UseAutoScrollFollowOptions): UseAutoScrollFollow {
  const thresholdPx = options.thresholdPx ?? 50;
  let scrollRAFId: number | null = null;
  let pendingScroll = false;

  function scrollToBottom(): void {
    if (pendingScroll) return;
    pendingScroll = true;

    scrollRAFId = requestAnimationFrame(() => {
      pendingScroll = false;
      nextTick(() => {
        const el = options.container.value;
        if (el) {
          el.scrollTop = el.scrollHeight;
        }
      });
    });
  }

  function handleScroll(): void {
    const el = options.container.value;
    if (!el || !options.canHandleScroll()) return;

    const { scrollTop, scrollHeight, clientHeight } = el;
    const isAtBottom = scrollHeight - scrollTop - clientHeight < thresholdPx;

    if (!isAtBottom) {
      options.onLeaveBottom?.();
    }
  }

  onMounted(() => {
    scrollToBottom();
  });

  onUnmounted(() => {
    if (scrollRAFId !== null) {
      cancelAnimationFrame(scrollRAFId);
    }
  });

  return { scrollToBottom, handleScroll };
}
