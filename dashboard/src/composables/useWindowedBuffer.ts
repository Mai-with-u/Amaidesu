// 浏览窗口机械工厂：events / logs 两个 store 共享的历史浏览窗口状态机。
//
// 窗口语义：window 为 null 表示实时模式（显示实时缓冲），非 null 表示正在浏览
// 一个从历史拉取的窗口。"加载更早/更晚"从当前浏览基线（窗口为 null 时用实时
// 缓冲）的最旧/最新一条向前/向后翻一页，归并去重后按窗口上限沿页进方向裁剪
// 对侧（older 裁尾保头、newer 裁头保尾），保证渲染面积有界。
//
// 数据源差异（API 参数组装、响应字段名归一、页元数据边车）由各 store 的
// fetchPage 适配层承担，工厂只认归一后的 PageResult。

import { ref, shallowRef } from 'vue';
import type { Ref, ShallowRef } from 'vue';

/** 翻页方向：older 向过去翻页 / newer 向实时边缘追赶 / jump 时间跳转整窗替换 */
export type WindowDirection = 'older' | 'newer' | 'jump';

/** 翻页游标：fromMs 为基线锚点时刻（older=基线最旧一条，newer=最新一条 +1，jump=跳转时刻） */
export interface PageCursor {
  direction: WindowDirection;
  fromMs: number;
}

/** 归一化的一页结果：响应字段名差异（has_more / has_more_older 等）由适配层归一 */
export interface PageResult<T> {
  items: T[];
  hasOlder: boolean;
  hasNewer: boolean;
  /** 页元数据边车（如 logs 的落盘可用性与窗口起点），经 onPageMeta 交还 store */
  meta?: unknown;
}

export interface WindowedBufferOptions<T> {
  /** 单页条数（events 500 / logs 200） */
  pageSize: number;
  /** 窗口容量上限，超出沿页进方向裁剪对侧（events 1000 / logs 600） */
  maxWindow: number;
  /** 去重键：归并时基线在前、新页在后，同键新页覆盖基线 */
  entryKey: (item: T) => string;
  /** 条目时刻（Unix 毫秒）：默认排序依据，也是翻页游标的取值来源 */
  entryTime: (item: T) => number;
  /** 实时缓冲 getter：窗口为 null 时翻页基线用它 */
  liveList: () => T[];
  /** 拉一页：参数组装与响应归一由各 store 的适配层实现 */
  fetchPage: (cursor: PageCursor) => Promise<PageResult<T>>;
  /** 排序注入口：默认按 entryTime 升序；条目自带可直读的时间串且其字典序
   *  与时刻序存在精度差异时（如 ISO 串），可注入原串比较保持既有语义 */
  sortCompare?: (a: T, b: T) => number;
  /** 页元数据边车回调（可选）：每次成功翻页后以 meta 调用 */
  onPageMeta?: (meta: unknown) => void;
}

export interface WindowedBuffer<T> {
  /** 浏览窗口：null = 实时模式；非 null = 显示该窗口（升序、去重、限长） */
  window: ShallowRef<T[] | null>;
  hasOlder: Ref<boolean>;
  hasNewer: Ref<boolean>;
  loading: Ref<boolean>;
  fetchOlder: () => Promise<void>;
  fetchNewer: () => Promise<void>;
  jumpTo: (timestampMs: number) => Promise<void>;
  exitWindow: () => void;
}

/** 归并去重：基线在前、新页在后（同键新页覆盖基线），排序后按方向裁剪到窗口上限 */
function mergeWindow<T>(
  chunk: T[],
  base: T[],
  keepHead: boolean,
  maxWindow: number,
  entryKey: (item: T) => string,
  compare: (a: T, b: T) => number,
): T[] {
  const byKey = new Map<string, T>();
  for (const item of [...base, ...chunk]) {
    byKey.set(entryKey(item), item);
  }
  const merged = [...byKey.values()].sort(compare);
  return keepHead ? merged.slice(0, maxWindow) : merged.slice(-maxWindow);
}

/** 创建浏览窗口状态机（语义与事件页/日志页既有实现逐条对齐） */
export function createWindowedBuffer<T>(options: WindowedBufferOptions<T>): WindowedBuffer<T> {
  // pageSize 不在本机械内消费——翻页 limit 参数由 fetchPage 适配层组装，此处仅作契约声明
  const { maxWindow, entryKey, entryTime, liveList, fetchPage, onPageMeta } = options;
  const compare = options.sortCompare ?? ((a: T, b: T) => entryTime(a) - entryTime(b));

  const window = shallowRef<T[] | null>(null);
  const hasOlder = ref(true);
  const hasNewer = ref(false);
  const loading = ref(false);

  /** 当前浏览基线：窗口模式用窗口，实时模式用实时缓冲 */
  function browsingBase(): T[] {
    return window.value ?? liveList();
  }

  /** loading 守卫：已有翻页进行中时本次调用直接跳过 */
  function guardLoading(): boolean {
    if (loading.value) return false;
    loading.value = true;
    return true;
  }

  /** "加载更早"：从当前浏览基线最旧一条向前翻一页（实时模式下即进入窗口模式） */
  async function fetchOlder(): Promise<void> {
    const base = browsingBase();
    const oldest = base[0];
    if (!oldest || !guardLoading()) return;
    try {
      const page = await fetchPage({ direction: 'older', fromMs: entryTime(oldest) });
      window.value = mergeWindow(page.items, base, false, maxWindow, entryKey, compare);
      hasOlder.value = page.hasOlder;
      // newer 信号只在尚未置真时采纳，避免翻旧页时把已到边缘的信号冲掉
      if (!hasNewer.value) hasNewer.value = page.hasNewer;
      onPageMeta?.(page.meta);
    } catch (error) {
      console.error('加载更早内容失败:', error);
    } finally {
      loading.value = false;
    }
  }

  /** "加载更晚"：从窗口最新一条向后补一页（时间跳转后追赶实时边缘） */
  async function fetchNewer(): Promise<void> {
    const base = browsingBase();
    const newest = base[base.length - 1];
    if (!newest || !guardLoading()) return;
    try {
      const page = await fetchPage({ direction: 'newer', fromMs: entryTime(newest) + 1 });
      window.value = mergeWindow(page.items, base, true, maxWindow, entryKey, compare);
      hasNewer.value = page.hasNewer;
      onPageMeta?.(page.meta);
    } catch (error) {
      console.error('加载更晚内容失败:', error);
    } finally {
      loading.value = false;
    }
  }

  /** 时间跳转：窗口整体替换为跳转时刻之后的一页（不做归并与裁剪） */
  async function jumpTo(timestampMs: number): Promise<void> {
    if (!guardLoading()) return;
    try {
      const page = await fetchPage({ direction: 'jump', fromMs: timestampMs });
      window.value = page.items;
      hasOlder.value = page.hasOlder;
      hasNewer.value = page.hasNewer;
      onPageMeta?.(page.meta);
    } catch (error) {
      console.error('时间跳转失败:', error);
    } finally {
      loading.value = false;
    }
  }

  /** 回到实时：丢弃浏览窗口并复位续翻信号 */
  function exitWindow(): void {
    window.value = null;
    hasOlder.value = true;
    hasNewer.value = false;
  }

  return { window, hasOlder, hasNewer, loading, fetchOlder, fetchNewer, jumpTo, exitWindow };
}
