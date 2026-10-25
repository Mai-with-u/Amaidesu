/**
 * keep-alive 路由下的周期轮询启停（多页面/组合式共用的单一事实源）。
 *
 * 解决的问题：全路由 keep-alive 下 onUnmounted 不会触发，组件各自手写
 * setInterval/clearInterval + onActivated/onDeactivated 时，漏掉一半就会在
 * 离开页面后留下永久常驻的定时器。本组合式把"轮询谁、多快、何时启停"收成
 * 一个声明：激活态（含 keep-alive 首次挂载）建 interval，失活态清除。
 *
 * 不改变各消费点的既有首启时机：默认只在 activated 启动（视图先例——首屏
 * 数据由 setup/onMounted 的显式拉取负责，轮询只做周期保鲜）；在 keep-alive
 * 之外消费的组合式（如 useAgentTasks）传 startOnSetup 保持 setup 同步启动，
 * 并可用 stopOnDispose 保留其原有的 onScopeDispose 兜底清理。
 */

import { onActivated, onDeactivated, onScopeDispose } from 'vue';

export interface UseKeepAlivePollingOptions {
  /** 轮询触发时传给 task 的实参（静默轮询先例：Agents 页传 silent=true，失败不弹错） */
  args?: readonly unknown[];
  /** setup 阶段即同步启动（消费方不在 keep-alive 内时 activated 不会到来的兜底） */
  startOnSetup?: boolean;
  /** 作用域销毁时兜底停止（keep-alive 之外卸载时防定时器常驻） */
  stopOnDispose?: boolean;
}

/**
 * 声明一个随组件激活态启停的周期任务。task 的周期触发为"发后不管"：
 * 任务自身负责轮询路径的静默与错误提示。
 */
export function useKeepAlivePolling<F extends (...args: never[]) => unknown>(
  task: F,
  intervalMs: number,
  options: UseKeepAlivePollingOptions = {},
): void {
  let timer: ReturnType<typeof setInterval> | null = null;

  const start = (): void => {
    if (timer !== null) return;
    timer = setInterval(() => {
      void task(...((options.args ?? []) as Parameters<F>));
    }, intervalMs);
  };

  const stop = (): void => {
    if (timer === null) return;
    clearInterval(timer);
    timer = null;
  };

  if (options.startOnSetup) start();
  onActivated(start);
  onDeactivated(stop);
  if (options.stopOnDispose) onScopeDispose(stop);
}
