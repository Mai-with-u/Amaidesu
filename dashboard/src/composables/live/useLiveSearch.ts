/**
 * 时间线内容搜索：关键词 → 命中清单 → 跳到那一刻。
 *
 * 检索范围有两层：
 * - 整场（服务端 GET /live-sessions/{id}/search）：覆盖已加载窗口之外的内容。
 *   实时档尤其有用——视图只保留最近 400 条，落库的场次内容能查到更早的
 * - 已加载窗口（本地匹配）：没有可检索的场次（测试模式不落库）、服务端不可用时兜底
 *
 * 搜索源跟随当前档位：观众视角搜观众可见的内容，后台视角搜全部条目，
 * 决策轨迹搜已加载的请求记录——服务端命中是条目粒度，拼不成请求轮次，
 * 这一档只查已加载的部分，并在界面上说清楚。
 */

import { computed, ref, watch, type Ref } from 'vue';
import { liveSessionsApi } from '@/api';
import { getApiErrorMessage } from '@/utils/apiError';
import {
  countByCategory,
  hitFromEntry,
  hitFromRecord,
  hitsFromItems,
  matchEntries,
  type LiveHitCategory,
  type SearchHit,
} from '@/utils/liveSearch';
import type { RequestRecord } from '@/utils/requestRecords';
import type { ShowEntry } from '@/utils/liveFeed';

/** 搜索防抖：停手 300ms 再检索（整场检索要扫场次，立即发请求会连成一片） */
const SEARCH_DEBOUNCE_MS = 300;

/** 服务端单次返回的命中上限 */
const SEARCH_LIMIT = 200;

export interface UseLiveSearchOptions {
  /** 当前档位（决定检索源与说明文案） */
  view: Ref<'audience' | 'backstage' | 'trajectory'>;
  /** 条目源：观众视角 = 观众可见条目；后台视角 = 全部条目 */
  entries: Ref<ShowEntry[]>;
  /** 请求记录源：决策轨迹 */
  records: Ref<RequestRecord[]>;
  /** 可检索的场次主键（null = 没有可检索的场次，只做本地检索） */
  sessionId: Ref<number | null>;
  /** 跳到某条命中（页面按档位实现定位） */
  jump: (hit: SearchHit) => void | Promise<void>;
}

export function useLiveSearch(options: UseLiveSearchOptions) {
  const { view, entries, records, sessionId, jump } = options;

  const query = ref('');
  const hits = ref<SearchHit[]>([]);
  /** 当前命中的序号：filteredHits 内的下标（-1 = 没有选中）。导航与选中都
   * 基于过滤后的清单——键盘选中的项必须能在清单里看见 */
  const index = ref(-1);
  const loading = ref(false);
  /** 检索范围：整场 / 已加载 / 已加载记录（决策轨迹档） */
  const scope = ref<'session' | 'local' | 'records'>('local');
  /** 检索范围说明：这一行告诉用户"搜到的是哪一段范围、为什么" */
  const scopeNote = ref('');
  /** 命中清单是否展开 */
  const expanded = ref(false);
  /** 服务端还有更早的命中没返回 */
  const hasMore = ref(false);
  /** 当前选中的分类（'all' = 不过滤；过滤只作用于清单，竖条标记仍全量） */
  const activeCategory = ref<'all' | LiveHitCategory>('all');

  /** 命中时刻集合：主时间线据此给命中的行加标记（按时刻而非 id 匹配——
   * 服务端命中的 id 与本地条目不同，时刻才是两边一致的东西）。取全量命中，
   * 不随分类过滤收窄——清单滤掉的是"看"的方式，不是命中本身 */
  const matchedTimestamps = computed<Set<number>>(() => new Set(hits.value.map(hit => hit.tsMs)));

  /** 过滤后的命中清单（分类 chips 的作用对象） */
  const filteredHits = computed<SearchHit[]>(() =>
    activeCategory.value === 'all'
      ? hits.value
      : hits.value.filter(hit => hit.category === activeCategory.value),
  );

  /** 各分类的命中计数（全量口径；chips 上的数字不随过滤变） */
  const categoryCounts = computed(() => countByCategory(hits.value));

  /** 当前命中的那一条 */
  const activeHit = computed<SearchHit | null>(() =>
    index.value >= 0 ? (filteredHits.value[index.value] ?? null) : null,
  );

  let timer: ReturnType<typeof setTimeout> | null = null;
  /** 检索序号：只有最后一次检索的结果算数（防止慢响应覆盖新结果） */
  let requestSeq = 0;

  /** 本地匹配：已加载窗口里的命中（整场检索不可用时兜底） */
  function localHits(keyword: string): SearchHit[] {
    if (view.value === 'trajectory') {
      return records.value
        .map(record => hitFromRecord(record, keyword))
        .filter((hit): hit is SearchHit => hit !== null);
    }
    return matchEntries(entries.value, keyword).map(entry => hitFromEntry(entry, keyword));
  }

  /** 检索一次：整场优先，不可用时退回已加载窗口。新结果落位时分类过滤
   *  一并归零——旧过滤是针对上一轮结果的，套在新结果上没有意义 */
  async function runSearch(): Promise<void> {
    const keyword = query.value.trim();
    if (!keyword) {
      hits.value = [];
      index.value = -1;
      activeCategory.value = 'all';
      scopeNote.value = '';
      hasMore.value = false;
      expanded.value = false;
      return;
    }
    const seq = ++requestSeq;
    loading.value = true;
    try {
      // 决策轨迹这一档的整场检索拿不到请求轮次，只查已加载的记录
      if (view.value === 'trajectory') {
        const local = localHits(keyword);
        if (seq !== requestSeq) return;
        hits.value = local;
        activeCategory.value = 'all';
        index.value = local.length ? 0 : -1;
        scope.value = 'records';
        scopeNote.value = '决策轨迹只检索已加载的请求记录';
        hasMore.value = false;
        expanded.value = local.length > 0;
        return;
      }

      const sid = sessionId.value;
      if (sid === null) {
        const local = localHits(keyword);
        if (seq !== requestSeq) return;
        hits.value = local;
        activeCategory.value = 'all';
        index.value = local.length ? 0 : -1;
        scope.value = 'local';
        scopeNote.value = '当前没有可检索的场次，只搜到已加载的内容';
        hasMore.value = false;
        expanded.value = local.length > 0;
        return;
      }

      const response = await liveSessionsApi.search(sid, { q: keyword, limit: SEARCH_LIMIT });
      if (seq !== requestSeq) return;
      const found = hitsFromItems(response.data.items, keyword);
      hits.value = found;
      activeCategory.value = 'all';
      index.value = found.length ? 0 : -1;
      hasMore.value = response.data.has_more;
      scope.value = 'session';
      scopeNote.value = found.length
        ? `整场找到 ${found.length} 条${response.data.has_more ? '（还有更早的没返回）' : ''}`
        : '整场没有匹配的内容';
      expanded.value = found.length > 0;
    } catch (error) {
      if (seq !== requestSeq) return;
      // 服务端检索不可用时退回已加载窗口，不把"搜不到"说成"没有"
      const local = localHits(keyword);
      hits.value = local;
      activeCategory.value = 'all';
      index.value = local.length ? 0 : -1;
      hasMore.value = false;
      scope.value = 'local';
      scopeNote.value = `整场检索不可用（${getApiErrorMessage(error, '请求失败')}），只搜到已加载的内容`;
      expanded.value = local.length > 0;
    } finally {
      if (seq === requestSeq) loading.value = false;
    }
  }

  /** 关键词变化：停手 300ms 再检索 */
  watch(query, () => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => {
      void runSearch();
    }, SEARCH_DEBOUNCE_MS);
  });

  /** 档位与场次变化：检索源变了，重新检索 */
  watch([view, sessionId], () => {
    if (!query.value.trim()) return;
    void runSearch();
  });

  /** 跳到过滤后清单的第 i 条命中（越界时忽略） */
  async function goTo(i: number): Promise<void> {
    const hit = filteredHits.value[i];
    if (!hit) return;
    index.value = i;
    await jump(hit);
  }

  /** 下一条命中（到末尾回到第一条） */
  async function next(): Promise<void> {
    const total = filteredHits.value.length;
    if (!total) return;
    const i = total > 1 ? (index.value + 1) % total : 0;
    await goTo(i);
  }

  /** 上一条命中（到第一条回到最后一条） */
  async function previous(): Promise<void> {
    const total = filteredHits.value.length;
    if (!total) return;
    const i = total > 1 ? (index.value - 1 + total) % total : 0;
    await goTo(i);
  }

  /** 切换分类过滤：清单换血，选中回到第一条 */
  function setCategory(category: 'all' | LiveHitCategory): void {
    if (activeCategory.value === category) return;
    activeCategory.value = category;
    index.value = filteredHits.value.length ? 0 : -1;
  }

  /** 清空搜索：关键词、命中、说明一起归零（挂起的防抖一并取消） */
  function clear(): void {
    if (timer) clearTimeout(timer);
    query.value = '';
    hits.value = [];
    index.value = -1;
    activeCategory.value = 'all';
    scope.value = 'local';
    scopeNote.value = '';
    hasMore.value = false;
    expanded.value = false;
  }

  return {
    query,
    hits,
    filteredHits,
    categoryCounts,
    activeCategory,
    setCategory,
    index,
    activeHit,
    loading,
    scope,
    scopeNote,
    expanded,
    hasMore,
    matchedTimestamps,
    runSearch,
    goTo,
    next,
    previous,
    clear,
  };
}
