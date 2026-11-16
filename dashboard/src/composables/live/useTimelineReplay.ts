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
 * 播放节奏（pace）有两种，倍速的含义随节奏变化：
 * - realtime（按实际时间）：下一步等待 = 两条事件的真实时差 ÷ 倍率，
 *   倍率档 [1,4,16,64,256] 是相对真实时间的加速倍率。用于看"隔了多久才回应"。
 *   长空档（冷场）按此规则会等很久，故设 MAX_STEP_WAIT_MS 上限把单次等待
 *   压到上限值——真实间隔在时间轴读数上仍然如实呈现，只是推进不原地干等。
 * - uniform（按条目匀速）：下一步等待 = 基础间隔 ÷ 档位，档位 [1,2,4,8,16]
 *   是每秒推进的条目数。用于逐条读内容逻辑。
 * 切换节奏时倍速夹到新节奏的档位集合内；两套档位都含 4，故默认倍速不跳变。
 *
 * 归档完整性：载入时如实带回 events_pruned（部分时段的事件诊断数据被清理）
 * 与翻页触顶（更大的历史未取到）两个标记，由视图层提示，避免"看不全的归档"
 * 与"完整归档"在界面上长得一样。
 *
 * keep-alive 生命周期与定时器：播放由单 setTimeout 链驱动（步进间隔按当前
 * 节奏与倍速计算），每次到点推进一步并按当前节奏重排；页面失活由视图层
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

/** 匀速节奏下 1 倍速时每条事件的停留时长（毫秒） */
const BASE_STEP_INTERVAL_MS = 1000;
/** 单次等待上限（毫秒）：长时间空档压到上限，避免播放被冷场原地干等 */
const MAX_STEP_WAIT_MS = 2000;
/** 全量翻页的页数上限（每页至多 2000 条，兜底防异常场次白翻） */
const MAX_PAGES = 50;

/** 播放节奏：realtime=按实际时间推进，uniform=按条目匀速推进 */
export type ReplayPace = 'realtime' | 'uniform';

/** 实际时间节奏的倍速档位：相对真实时间的加速倍率 */
export const REPLAY_REALTIME_SPEEDS = [1, 4, 16, 64, 256] as const;
/** 条目匀速节奏的档位：每秒推进的条目数 */
export const REPLAY_UNIFORM_SPEEDS = [1, 2, 4, 8, 16] as const;

/** 某节奏下的可选倍速档位（供控制条渲染选项与内部校验共用） */
export function replaySpeedOptions(pace: ReplayPace): readonly number[] {
  return pace === 'realtime' ? REPLAY_REALTIME_SPEEDS : REPLAY_UNIFORM_SPEEDS;
}

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
  /** 播放节奏（默认按实际时间，复盘首要问题是"隔了多久"） */
  const pace = ref<ReplayPace>('realtime');
  /** 当前倍速：含义随节奏变化（实际时间是加速倍率，匀速是每秒条数） */
  const speed = ref<number>(4);
  /** 归档不完整标记：部分时段的事件诊断数据被清理 */
  const pruned = ref(false);
  /** 归档被截断标记：翻页触顶，仍有更早的历史未取到 */
  const truncated = ref(false);

  const totalCount = computed(() => archive.value.length);
  const active = computed(() => phase.value !== 'idle');
  /** 归档首条时刻（时间轴左端） */
  const archiveStartMs = computed(() => archive.value[0]?.tsMs ?? 0);
  /** 归档末条时刻（时间轴右端；视图层估算回放期间错过的实时条数也用它） */
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

  // 播放定时器：单 setTimeout 链，到点推进一步后按当前节奏重排；
  // 节奏/倍速变更即时生效（watch 重排下一拍的等待间隔）
  let timer: ReturnType<typeof setTimeout> | null = null;

  function stopTimer(): void {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  }

  /**
   * 下一步的等待时长（毫秒）：
   * - 匀速：基础间隔 ÷ 档位（档位即每秒推进的条目数）
   * - 实际时间：两条事件的真实时差 ÷ 倍率；长空档压到 MAX_STEP_WAIT_MS 上限，
   *   真实间隔在时间轴读数上仍如实呈现，只是推进不原地干等
   */
  function nextWaitMs(): number {
    if (pace.value === 'uniform') return BASE_STEP_INTERVAL_MS / speed.value;
    const current = archive.value[cursor.value];
    const next = archive.value[cursor.value + 1];
    if (!current || !next) return BASE_STEP_INTERVAL_MS;
    const gapMs = Math.max(0, next.tsMs - current.tsMs);
    return Math.min(gapMs / speed.value, MAX_STEP_WAIT_MS);
  }

  function scheduleNextStep(): void {
    stopTimer();
    if (!playing.value) return;
    if (cursor.value >= totalCount.value - 1) {
      // 已在末条：无下一条可放，停在末条并归位暂停态
      playing.value = false;
      return;
    }
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
    }, nextWaitMs());
  }

  watch([playing, speed, pace], () => {
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
    if (!replaySpeedOptions(pace.value).includes(value)) return;
    speed.value = value;
  }

  /**
   * 切换播放节奏：倍速含义随节奏变化，故把倍速夹到新节奏的档位集合内
   * （取距离原值最近的档位；两套档位都含 4，默认倍速在切换时不跳变）。
   */
  function setPace(value: ReplayPace): void {
    if (value === pace.value) return;
    pace.value = value;
    const allowed = replaySpeedOptions(value);
    if (allowed.includes(speed.value)) return;
    speed.value = allowed.reduce((best, option) =>
      Math.abs(option - speed.value) < Math.abs(best - speed.value) ? option : best,
    );
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
    pruned.value = false;
    truncated.value = false;
  }

  /** 进入回放：拉取该场全量归档并定位到起始时刻（缺省从第一条开始，暂停态） */
  async function start(item: LiveSessionItem, startAtMs?: number): Promise<void> {
    reset();
    phase.value = 'loading';
    session.value = item;
    try {
      const loaded = await loadFullArchive(item.live_session_id);
      if (loaded.items.length === 0) {
        ElMessage.error('该场次没有可回放的归档');
        reset();
        return;
      }
      archive.value = buildReplayEntries(loaded.items);
      pruned.value = loaded.pruned;
      truncated.value = loaded.truncated;
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
    /** 播放节奏（实际时间 / 条目匀速） */
    pace,
    /** 当前倍速（含义随节奏变化） */
    speed,
    active,
    /** 游标下标（归档口径，0 基；-1=未开始；进度显示"第 cursor+1 条"） */
    cursor,
    cursorTsMs,
    /** 归档首条时刻（时间轴左端） */
    archiveStartMs,
    /** 归档末条时刻（时间轴右端） */
    archiveEndMs,
    /** 部分时段的事件诊断数据被清理 */
    pruned,
    /** 翻页触顶，仍有更早的历史没取到 */
    truncated,
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
    setPace,
    setSpeed,
    jumpToTime,
  };
}

/** 归档载入结果：条目 + 两个完整性标记（部分被清理 / 翻页触顶未取尽） */
interface ReplayArchive {
  items: SessionTimelineItem[];
  /** 任一页回报事件诊断数据被清理 */
  pruned: boolean;
  /** 翻页触顶，仍有更早的历史没取到 */
  truncated: boolean;
}

/** 全量翻页：升序页按序 unshift 前插，取尽（has_more=false）或达页数上限为止 */
async function loadFullArchive(sessionId: number): Promise<ReplayArchive> {
  const pages: SessionTimelineItem[] = [];
  let cursorMs: number | undefined;
  let pruned = false;
  let truncated = false;
  for (let page = 0; page < MAX_PAGES; page += 1) {
    const response = await liveSessionsApi.timeline(
      sessionId,
      cursorMs !== undefined ? { before_timestamp_ms: cursorMs } : undefined,
    );
    const items = response.data.items;
    if (items.length === 0) break;
    pages.unshift(...items);
    pruned = pruned || response.data.events_pruned;
    if (!response.data.has_more) break;
    cursorMs = items[0]!.ts_ms;
    // 页数用尽时仍有更早页：更早的历史没取到
    if (page === MAX_PAGES - 1) truncated = true;
  }
  return { items: pages, pruned, truncated };
}
