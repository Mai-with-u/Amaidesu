/**
 * 回看时间线的时刻锚定：从复盘页指标卡深链（/live?session=&at=<ms>）跳入时，
 * 自动向前翻页直到加载范围覆盖目标时刻，滚动到目标条目并短暂高亮。
 *
 * 覆盖判定：已加载到头（无更早页）或最旧条目时刻 <= 目标，即认为目标时刻
 * 附近内容已在列表内；未覆盖前持续翻页（有页数上限，防诊断流缺失时白翻）。
 * 覆盖后取第一条 >= 目标时刻的条目（目标晚于最末条目时落到最末条目）；
 * 时间线为空则提示降级。
 */

import { nextTick, ref, type Ref } from 'vue';
import { ElMessage } from 'element-plus';
import type { ShowEntry } from '@/utils/liveFeed';

/** 自动向前翻页的页数上限（每页至多 500 条，24 页足够覆盖一场的正常回看） */
const MAX_PAGES = 24;
/** 高亮持续时长 */
const HIGHLIGHT_MS = 4_000;

const sleep = (ms: number) => new Promise<void>(resolve => setTimeout(resolve, ms));

export interface UseTimelineAnchorOptions {
  /** 展示条目（回看模式下 = 时间线经档位过滤后的结果） */
  entries: Ref<ShowEntry[]>;
  /** 是否还有更早条目（后端 has_more） */
  hasMore: Ref<boolean>;
  /** 翻页进行中（防重入；翻页循环里等它归位后再判） */
  loadingMore: Ref<boolean>;
  /** 加载更早一页（useLiveTimeline.loadOlderReplay） */
  loadOlder: () => Promise<void>;
  /** 时间线滚动容器（条目元素的查找根） */
  scrollRoot: Ref<HTMLElement | null>;
}

export function useTimelineAnchor(options: UseTimelineAnchorOptions) {
  const { entries, hasMore, loadingMore, loadOlder, scrollRoot } = options;

  /** 当前高亮条目 id（FeedTimeline 据此加 is-anchored 类） */
  const anchoredId = ref<string | null>(null);
  let highlightTimer: ReturnType<typeof setTimeout> | null = null;

  function firstAtOrAfter(targetMs: number): ShowEntry | null {
    for (const entry of entries.value) {
      if (entry.tsMs >= targetMs) return entry;
    }
    return null;
  }

  /** 定位到目标时刻：必要时自动向前翻页，命中后滚动 + 高亮 */
  async function anchorAt(targetMs: number): Promise<void> {
    let hit: ShowEntry | null = null;
    let pages = 0;
    for (;;) {
      const list = entries.value;
      const oldest = list[0];
      // 覆盖判定：没有更早页，或最旧条目已早于等于目标时刻
      if (!hasMore.value || (oldest !== undefined && oldest.tsMs <= targetMs)) {
        hit = firstAtOrAfter(targetMs) ?? list[list.length - 1] ?? null;
        break;
      }
      if (pages >= MAX_PAGES) break;
      while (loadingMore.value) {
        await sleep(100);
      }
      if (!hasMore.value) continue;
      const before = list.length;
      await loadOlder();
      pages += 1;
      // 一页下来没有新增且状态未变 → 再给一次机会后靠 covered/上限收束，防死循环
      if (entries.value.length === before && hasMore.value && !loadingMore.value) {
        await sleep(150);
      }
    }

    if (hit === null) {
      ElMessage.info('该时刻附近没有可展示的内容');
      return;
    }
    anchoredId.value = hit.id;
    await nextTick();
    const el = scrollRoot.value?.querySelector(`[data-entry-id="${CSS.escape(hit.id)}"]`);
    el?.scrollIntoView({ block: 'center', behavior: 'smooth' });
    if (highlightTimer) clearTimeout(highlightTimer);
    highlightTimer = setTimeout(() => {
      anchoredId.value = null;
    }, HIGHLIGHT_MS);
  }

  return { anchoredId, anchorAt };
}
