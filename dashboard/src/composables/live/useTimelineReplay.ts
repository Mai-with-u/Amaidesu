/**
 * 时间线回放调试器：把整条时间线切到指定场次的归档数据，按事件逐条步进回放。
 *
 * 与场次回看（useLiveSessions.sessionMode='replay'，一次呈现整段归档供滚动
 * 浏览）互补：回放调试是事件级播放——游标之前的归档条目渐进可见，可播放/
 * 暂停/单步/跳跃/调速，用于复盘决策链的先后关系。
 *
 * 数据载入策略：全量翻页（timeline 接口按 ts_ms 升序返回，has_more 指示还有
 * 更早页，以最旧条目 ts_ms 为游标续翻直至取尽）。选全量而非按需翻页的理由：
 * 一场时间线通常数百条、一次拉全在百 KB 量级；游标步进/跳跃/进度展示
 * （第 x/共 y 条）都要求知道总条数，全量在手后播放全程零额外请求，实现最简。
 * 翻页有页数上限兜底，异常大的场次在最坏情况下回放开头可用、更早历史缺失。
 *
 * 游标语义：cursor 指向全量归档数组中的下标，可见条目 = 全量前缀
 * [0..cursor]（经来源过滤后交给视图）；过滤只影响展示、不影响游标推进，
 * 保证"第 x/共 y 条"始终按归档原始口径计数。
 *
 * keep-alive 生命周期与定时器：播放由单 setTimeout 链驱动（步进间隔 =
 * 基础间隔 / 倍速），每次到点推进一步并按当前倍速重排；页面失活由视图层
 * 调 pause() 停表，重激活不自动续播（回放调试是主动操作，静默续播易误导）。
 * 组件卸载（onScopeDispose）停表并保留已载归档，退出回放（exit）才清空。
 */

import { computed, onScopeDispose, ref, watch, type Ref } from 'vue';
import { ElMessage } from 'element-plus';
import { liveSessionsApi } from '@/api';
import { getApiErrorMessage } from '@/utils/apiError';
import { buildReplayEntries } from '@/utils/replayFeed';
import { agentGroupOf, type AgentGroup, type ShowEntry } from '@/utils/liveFeed';
import type { LiveSessionItem, SessionTimelineItem } from '@/types';

/** 倍速 1 倍时每条事件的停留时长（毫秒） */
const BASE_STEP_INTERVAL_MS = 1000;
/** 全量翻页的页数上限（每页至多 2000 条，兜底防异常场次白翻） */
const MAX_PAGES = 50;

/** 可选倍速档位（供控制条渲染选项与 composable 内部校验共用） */
export const REPLAY_SPEED_OPTIONS = [1, 2, 4, 8, 16] as const;

/** 回放状态机：idle=未进入；loading=归档拉取中；ready=可播放/步进 */
export type ReplayPhase = 'idle' | 'loading' | 'ready';

export interface UseTimelineReplayOptions {
  /** 来源过滤状态（与实时/回看共用 agentFilter）：'all' 或 Agent 组别名 */
  agentFilter: Ref<'all' | AgentGroup>;
}

export function useTimelineReplay(options: UseTimelineReplayOptions) {
  const phase = ref<ReplayPhase>('idle');
  /** 回放中的场次（退出即清空；用于头部标题与深链回写） */
  const session = ref<LiveSessionItem | null>(null);
  /** 归档全量（时间升序；载入完成即固定，回放期间实时新事件不进入） */
  const archive = ref<ShowEntry[]>([]);
  /** 游标：归档数组下标，-1 表示尚未开始（可见为空） */
  const cursor = ref(-1);
  const playing = ref(false);
  const speed = ref<number>(1);

  const totalCount = computed(() => archive.value.length);
  const active = computed(() => phase.value !== 'idle');
  /** 归档末条时刻（视图层估算回放期间错过的实时条数用） */
  const archiveEndMs = computed(() => archive.value[archive.value.length - 1]?.tsMs ?? 0);

  /** 游标处条目时刻（URL 深链回写值；游标无效返回 null 删键） */
  const cursorTsMs = computed<number | null>(() => archive.value[cursor.value]?.tsMs ?? null);

  /** 可见条目：全量前缀 [0..cursor] 过来源过滤（room 组与观众消息始终可见，
   * 与实时/回看的过滤口径一致）；过滤不改游标口径 */
  const visibleEntries = computed<ShowEntry[]>(() => {
    if (cursor.value < 0) return [];
    const prefix = archive.value.slice(0, cursor.value + 1);
    const filter = options.agentFilter.value;
    if (filter === 'all') return prefix;
    return prefix.filter(entry => agentGroupOf(entry) === 'room' || agentGroupOf(entry) === filter);
  });

  // 播放定时器：单 setTimeout 链，到点推进一步后按当前倍速重排；
  // 倍速变更即时生效（watch 重排下一拍的等待间隔）
  let timer: ReturnType<typeof setTimeout> | null = null;

  function stopTimer(): void {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  }

  function scheduleNextStep(): void {
    stopTimer();
    if (!playing.value) return;
    timer = setTimeout(() => {
      timer = null;
      if (!playing.value) return;
      if (cursor.value >= totalCount.value - 1) {
        // 播放到末尾：停在最后一条并归位暂停态
        playing.value = false;
        return;
      }
      cursor.value += 1;
      scheduleNextStep();
    }, BASE_STEP_INTERVAL_MS / speed.value);
  }

  watch([playing, speed], () => {
    if (phase.value !== 'ready') return;
    scheduleNextStep();
  });

  function pause(): void {
    playing.value = false;
    stopTimer();
  }

  function togglePlay(): void {
    if (phase.value !== 'ready' || totalCount.value === 0) return;
    playing.value = !playing.value;
  }

  /** 单步前进：调试语义下步进即暂停（手动逐条查看时不应被定时器抢跑） */
  function stepForward(): void {
    if (phase.value !== 'ready') return;
    pause();
    if (cursor.value < totalCount.value - 1) cursor.value += 1;
  }

  /** 单步后退：游标到 -1（可见清空）为止，不越界 */
  function stepBack(): void {
    if (phase.value !== 'ready') return;
    pause();
    if (cursor.value >= 0) cursor.value -= 1;
  }

  function setSpeed(value: number): void {
    if (!REPLAY_SPEED_OPTIONS.includes(value as (typeof REPLAY_SPEED_OPTIONS)[number])) return;
    speed.value = value;
  }

  /** 全量归档第一条 ts_ms >= 目标时刻的下标；目标晚于末条时落在末条 */
  function firstIndexAtOrAfter(targetMs: number): number {
    for (let i = 0; i < archive.value.length; i += 1) {
      if (archive.value[i]!.tsMs >= targetMs) return i;
    }
    return archive.value.length - 1;
  }

  /** 跳跃到指定时刻（毫秒）：暂停态下重定位游标 */
  function jumpToTime(targetMs: number): void {
    if (phase.value !== 'ready' || archive.value.length === 0) return;
    pause();
    cursor.value = firstIndexAtOrAfter(targetMs);
  }

  function reset(): void {
    stopTimer();
    playing.value = false;
    session.value = null;
    archive.value = [];
    cursor.value = -1;
    phase.value = 'idle';
  }

  /** 进入回放：拉取该场全量归档并定位到起始时刻（缺省从第一条开始，暂停态） */
  async function start(item: LiveSessionItem, startAtMs?: number): Promise<void> {
    reset();
    phase.value = 'loading';
    session.value = item;
    try {
      const items = await loadFullArchive(item.live_session_id);
      archive.value = buildReplayEntries(items);
    } catch (error) {
      ElMessage.error(getApiErrorMessage(error, '回放归档加载失败'));
      reset();
      return;
    }
    phase.value = 'ready';
    cursor.value = startAtMs != null ? firstIndexAtOrAfter(startAtMs) : 0;
  }

  /** 退出回放：停表清归档回到实时流；错过条数由视图层在退出前读 archiveEndMs 估算 */
  function exit(): void {
    reset();
  }

  onScopeDispose(stopTimer);

  return {
    // 状态（只读消费：phase/cursor 等经视图模板读取，此处不做 readonly 包裹
    // 以保持与同目录其他组合式的返回风格一致）
    phase,
    session,
    playing,
    speed,
    active,
    /** 游标下标（归档口径，0 基；-1=未开始；进度显示"第 cursor+1 条"） */
    cursor,
    cursorTsMs,
    archiveEndMs,
    // 数据
    visibleEntries,
    totalCount,
    // 动作
    start,
    exit,
    togglePlay,
    pause,
    stepForward,
    stepBack,
    setSpeed,
    jumpToTime,
  };
}

/** 全量翻页：升序页按序 unshift 前插，取尽（has_more=false）或达页数上限为止 */
async function loadFullArchive(sessionId: number): Promise<SessionTimelineItem[]> {
  const pages: SessionTimelineItem[] = [];
  let cursorMs: number | undefined;
  for (let page = 0; page < MAX_PAGES; page += 1) {
    const response = await liveSessionsApi.timeline(
      sessionId,
      cursorMs !== undefined ? { before_timestamp_ms: cursorMs } : undefined,
    );
    const items = response.data.items;
    if (items.length === 0) break;
    pages.unshift(...items);
    if (!response.data.has_more) break;
    cursorMs = items[0]!.ts_ms;
  }
  return pages;
}
