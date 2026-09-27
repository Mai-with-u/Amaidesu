/**
 * 时间线内容合成：实时缓冲（暂停冻结 / 清空水位 / 条目重建节流）+
 * 场次回看（REST 明细映射）+ 双来源按时间归并后的统一展示条目。
 *
 * 页面私有状态。思考行与隐藏水位由 useThinkingStream 持有、参数注入：
 * 本组合式函数只读思考行引用，清空时推进水位（只写这一处）。
 * 场次模式与选中场次由 useLiveSessions 持有、参数注入（回看加载的触发源）。
 */

import { computed, onScopeDispose, ref, watch, type Ref } from 'vue';
import { storeToRefs } from 'pinia';
import { ElMessage } from 'element-plus';
import { useEventsStore } from '@/stores';
import { liveSessionsApi } from '@/api';
import { buildReplayEntries } from '@/utils/replayFeed';
import {
  MAX_ENTRIES,
  agentGroupOf,
  buildLiveEntries,
  mergeEntriesByTime,
  type AgentGroup,
  type FeedEvent,
  type ShowEntry,
} from '@/utils/liveFeed';
import type { LiveSessionItem } from '@/types';

export interface UseLiveTimelineOptions {
  /** 实时思考行（useThinkingStream.liveThinkingRows） */
  liveThinkingRows: Ref<ShowEntry[]>;
  /** 思考行隐藏水位（useThinkingStream.thinkingHiddenBeforeMs）；清空时间线时推进 */
  thinkingHiddenBeforeMs: Ref<number>;
  /** 场次模式（useLiveSessions.sessionMode） */
  sessionMode: Ref<'live' | 'replay'>;
  /** 回看中的场次（useLiveSessions.selectedSession） */
  selectedSession: Ref<LiveSessionItem | null>;
}

export function useLiveTimeline(options: UseLiveTimelineOptions) {
  const { liveThinkingRows, thinkingHiddenBeforeMs, sessionMode, selectedSession } = options;
  const { events } = storeToRefs(useEventsStore());

  const paused = ref(false);
  /** 清空水位：记下当时缓冲区里的事件 id，之后重建时永久跳过（store 仍不丢数据） */
  const hiddenIds = ref<Set<string>>(new Set());
  const liveEntries = ref<ShowEntry[]>([]);

  /** 暂停期思考行快照：暂停时锁存当前思考行，恢复后回到实时
   *  （事件流靠 watch 跳过重建实现冻结，思考行是 computed、需单独锁存） */
  const frozenThinkingRows = ref<ShowEntry[] | null>(null);

  /** 时间线显示模式：timeline=单列沿脊线；chat=会话模式（观众左/主播右气泡对齐，
   * 原独立会话调试页的显示形态） */
  const displayMode = ref<'timeline' | 'chat'>('timeline');

  /** 来源过滤：实时模式下按 Agent 组别过滤展示条目（观众消息与场次边界不过滤——观众始终可见）；
   *  回看模式不生效（场次条目全量呈现） */
  const agentFilter = ref<'all' | AgentGroup>('all');

  /** chips 点击处理：el-check-tag 在「勾选→取消勾选」时都会触发 change；
   *  排他语义下只接受「点亮」动作，避免误触把已选中态切走 */
  function handleAgentChip(value: 'all' | AgentGroup, checked: boolean): void {
    if (checked) agentFilter.value = value;
  }

  /** 条目重建节流：每条事件到达都会触发 buildLiveEntries 全量折叠，复杂任务期间
   * 工具结果高频涌入时按固定间隔合并重建（尾沿触发，静默后最终态仍会落地） */
  const REBUILD_INTERVAL_MS = 250;
  let rebuildTimer: ReturnType<typeof setTimeout> | null = null;
  let lastRebuildMs = 0;
  let pendingRebuild: Array<FeedEvent[]> | null = null;
  let pendingHidden: Set<string> | null = null;

  watch(
    [events, paused, hiddenIds],
    ([list, isPaused, hidden]) => {
      if (isPaused) return;
      pendingRebuild = [list as FeedEvent[]];
      pendingHidden = hidden;
      if (rebuildTimer) return;
      const wait = Math.max(0, REBUILD_INTERVAL_MS - (Date.now() - lastRebuildMs));
      rebuildTimer = setTimeout(() => {
        rebuildTimer = null;
        lastRebuildMs = Date.now();
        // 暂停期不重建（冻结语义）；恢复时 watch 会再排程
        if (paused.value || !pendingRebuild || pendingHidden === null) return;
        liveEntries.value = buildLiveEntries(pendingRebuild[0], pendingHidden);
      }, wait);
    },
    { immediate: true },
  );

  // 回看时间线：REST 明细 → ShowEntry（映射规则见 utils/replayFeed.ts）

  const replayEntries = ref<ShowEntry[]>([]);
  const replayLoading = ref(false);

  async function loadReplayTimeline(item: LiveSessionItem): Promise<void> {
    replayLoading.value = true;
    try {
      const response = await liveSessionsApi.timeline(item.live_session_id);
      replayEntries.value = buildReplayEntries(response.data.items);
    } catch (error) {
      ElMessage.error(error instanceof Error ? error.message : '回看加载失败');
      replayEntries.value = [];
    } finally {
      replayLoading.value = false;
    }
  }

  watch(
    [sessionMode, selectedSession],
    ([mode, selected]) => {
      if (mode === 'replay' && selected) {
        void loadReplayTimeline(selected);
      }
    },
    { immediate: true },
  );

  /** 展示条目：实时模式把思考行与事件条目按时间归并后过 agentFilter；
   *  回看模式取 REST 时间线全量（思考流不落库，回看没有思考行）。
   *  过滤只针对 Agent 产生的卡，观众消息与场次边界（room 组）始终可见。
   *  事件条目本身已按 MAX_ENTRIES 限长，但思考行归并会突破上限（实测长任务
   *  805/400）；展示条目统一限长保尾，与计数显示口径一致 */
  const entries = computed<ShowEntry[]>(() => {
    if (sessionMode.value === 'replay') return replayEntries.value;
    const thinkingRows = paused.value ? (frozenThinkingRows.value ?? []) : liveThinkingRows.value;
    const list = mergeEntriesByTime(liveEntries.value, thinkingRows);
    const filtered =
      agentFilter.value === 'all'
        ? list
        : list.filter(
            entry => agentGroupOf(entry) === 'room' || agentGroupOf(entry) === agentFilter.value,
          );
    return filtered.slice(-MAX_ENTRIES);
  });

  function togglePause(): void {
    const next = !paused.value;
    // 暂停沿锁存当前思考行，恢复沿放回实时流（事件条目的冻结由 watch 跳过重建实现）
    frozenThinkingRows.value = next ? liveThinkingRows.value : null;
    paused.value = next;
  }

  function clearTimeline(): void {
    hiddenIds.value = new Set(events.value.map(event => event.id));
    liveEntries.value = [];
    // 思考行按时间水位隐藏（与 hiddenIds 同语义：只藏不删）
    const rows = paused.value ? (frozenThinkingRows.value ?? []) : liveThinkingRows.value;
    thinkingHiddenBeforeMs.value = rows.reduce(
      (max, row) => Math.max(max, row.tsMs),
      thinkingHiddenBeforeMs.value,
    );
  }

  onScopeDispose(() => {
    if (rebuildTimer) clearTimeout(rebuildTimer);
  });

  return {
    paused,
    displayMode,
    agentFilter,
    handleAgentChip,
    entries,
    togglePause,
    clearTimeline,
  };
}
