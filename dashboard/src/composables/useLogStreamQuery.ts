/**
 * 日志类流式列表页的共享查询机械。
 *
 * EventLog（事件页）与 LogViewer（日志页）是同一页形的两个数据域：实时缓冲 +
 * 历史浏览窗口双列表、客户端谓词筛选、计数条、翻页后滚动锚定。本组合式收编
 * 这些机械部分——**谓词由页面自写**（类型/级别/模块/搜索词是各自的域知识），
 * 这里只负责执行与翻页锚定；空态判定由调用方对 filteredItems 派生。
 */

import { computed, nextTick, type ComputedRef, type Ref } from 'vue';

export interface LogStreamQueryOptions<TItem> {
  /** 实时缓冲（store 的 Ref） */
  live: Ref<TItem[]> | ComputedRef<TItem[]>;
  /** 历史浏览窗口；非 null 时优先于 live 显示 */
  window: Ref<TItem[] | null>;
  /** 窗口翻页与时间跳转（store 同名方法） */
  fetchOlder: () => Promise<unknown>;
  fetchNewer: () => Promise<unknown>;
  jumpTo: (timestampMs: number) => Promise<unknown>;
  /** 列表滚动容器（翻页/跳转锚定的目标） */
  listEl: Ref<HTMLElement | null>;
  /** 谓词组：全部命中才保留。闭包读筛选状态的 .value，响应式由 computed 追踪 */
  predicates: Array<(item: TItem) => boolean>;
}

export interface LogStreamQuery<TItem> {
  /** 显示列表：窗口优先，否则实时缓冲 */
  displayItems: ComputedRef<TItem[]>;
  /** 是否处于历史浏览窗口模式 */
  windowActive: ComputedRef<boolean>;
  /** 谓词全命中后的筛选结果 */
  filteredItems: ComputedRef<TItem[]>;
  /** 显示列表内计数（计数条/分面徽标用） */
  countBy: (match: (item: TItem) => boolean) => number;
  /** 列表滚动锚定（页面域内动作如搜索完成后复用） */
  scrollListTo: (position: 'top' | 'bottom') => void;
  /** 加载更早并锚定到顶部 */
  loadOlder: () => void;
  /** 加载更晚并锚定到底部 */
  loadNewer: () => void;
  /** 时间跳转并锚定到顶部 */
  jump: (timestampMs: number) => void;
}

export function useLogStreamQuery<TItem>(
  options: LogStreamQueryOptions<TItem>,
): LogStreamQuery<TItem> {
  const displayItems = computed(() => options.window.value ?? options.live.value);
  const windowActive = computed(() => options.window.value !== null);

  const filteredItems = computed(() => {
    let result = displayItems.value;
    for (const predicate of options.predicates) {
      result = result.filter(predicate);
    }
    return result;
  });

  function countBy(match: (item: TItem) => boolean): number {
    return displayItems.value.filter(match).length;
  }

  function scrollListTo(position: 'top' | 'bottom'): void {
    void nextTick(() => {
      const el = options.listEl.value;
      if (!el) return;
      el.scrollTop = position === 'top' ? 0 : el.scrollHeight;
    });
  }

  function loadOlder(): void {
    void options.fetchOlder().then(() => scrollListTo('top'));
  }

  function loadNewer(): void {
    void options.fetchNewer().then(() => scrollListTo('bottom'));
  }

  function jump(timestampMs: number): void {
    void options.jumpTo(timestampMs).then(() => scrollListTo('top'));
  }

  return {
    displayItems,
    windowActive,
    filteredItems,
    countBy,
    scrollListTo,
    loadOlder,
    loadNewer,
    jump,
  };
}
