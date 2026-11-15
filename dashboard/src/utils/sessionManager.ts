/**
 * 场次显示口径（场次管理页与直播控制台共用）。
 *
 * 这里放的是"场次这件事怎么显示"的纯函数：来源与类型的取值和显示名、场次时长
 * 标签、整理状态标签、清单汇总与按日分布。页面只做装配与交互，判定与算术都在
 * 这里，避免同一口径在两处各写一份后漂移。
 */

import { formatDateYMD, formatElapsedMs } from '@/utils/format';
import type { LiveSessionItem, SessionKind } from '@/types';

/**
 * 筛选取值里的"不限"：来源与类型两个筛选框都按这个值表示不筛。
 * 不用空串——Element Plus 的选项在 value 为空时按 label 注册，空串选项永远选不中，
 * 下拉框只能落回"请选择"占位，读者看不出这个框筛的是什么。
 */
export const SESSION_FILTER_ALL = 'all';

/** 场次来源的取值与显示名（筛选框的选项直接取这里，来源只有这一处定义） */
export const SOURCE_LABEL = {
  manual: '手动',
  replay: '回放',
  generate: '模拟',
  legacy: '历史',
} as const;

/** 场次类型的取值与显示名 */
export const KIND_LABEL = {
  live: '正式',
  rehearsal: '彩排',
} as const satisfies Record<SessionKind, string>;

/** 场次来源取值（筛选框按这些值过滤） */
export type SessionSource = keyof typeof SOURCE_LABEL;

/** 筛选框的来源选项（顺序即下拉框顺序） */
export const SESSION_SOURCE_OPTIONS = (
  Object.entries(SOURCE_LABEL) as [SessionSource, string][]
).map(([value, label]) => ({ value, label }));

/** 筛选框的类型选项（顺序即下拉框顺序） */
export const SESSION_KIND_OPTIONS = (Object.entries(KIND_LABEL) as [SessionKind, string][]).map(
  ([value, label]) => ({ value, label }),
);

/** 来源显示名（认不出来的来源原样透出） */
export function sourceLabel(source: string): string {
  const labels: Record<string, string> = SOURCE_LABEL;
  return labels[source] ?? source;
}

/** 类型显示名 */
export function kindLabel(kind: SessionKind): string {
  return KIND_LABEL[kind] ?? kind;
}

/**
 * 场次默认标题（yyyy-MM-dd 周X HH:mm，按给定时刻）：开场次预填与未命名场次
 * 改名时预填共用。
 */
export function defaultSessionTitle(at: Date = new Date()): string {
  const pad = (n: number): string => String(n).padStart(2, '0');
  const weekday = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][at.getDay()] ?? '';
  return `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())} ${weekday} ${pad(at.getHours())}:${pad(at.getMinutes())}`;
}

/**
 * 场次时长标签：进行中场次走传入的当前时刻（已播），已结束场次用结束时刻减开始
 * 时刻（用时）。缺结束时刻且不是进行中（残留场次）不显示。
 * 时长固定带小时位：短场次（0:04:00）与长场次（3:01:00）形状一致，不会把
 * 5:00（5 分钟）误读成 5 小时。
 */
export function sessionDurationLabel(
  item: Pick<LiveSessionItem, 'is_active' | 'started_at_ms' | 'ended_at_ms'>,
  nowMs: number,
): string | null {
  if (item.is_active) {
    return `已播 ${formatElapsedMs(nowMs - item.started_at_ms, { alwaysHours: true })}`;
  }
  if (item.ended_at_ms == null) return null;
  const duration = item.ended_at_ms - item.started_at_ms;
  if (duration < 0) return null;
  return `用时 ${formatElapsedMs(duration, { alwaysHours: true })}`;
}

/** 整理状态的口径：整理三列（整理完成 / 重新整理尝试次数 / 标已审）算出的标签与色调 */
export type SessionSettleTone = 'default' | 'warning' | 'danger' | 'success';

export interface SessionSettleState {
  label: string;
  tone: SessionSettleTone;
}

/**
 * 整理状态：标过已审的算已审；整理完成但没标已审的算待审；整理失败（试过又没成）
 * 单独标出；其余是待整理。
 */
export function settleStateOf(
  item: Pick<LiveSessionItem, 'settled_at_ms' | 'settle_attempts' | 'reviewed_at_ms'>,
): SessionSettleState {
  if (item.reviewed_at_ms != null) return { label: '已审', tone: 'success' };
  if (item.settled_at_ms != null) return { label: '已整理待审', tone: 'warning' };
  if (item.settle_attempts > 0) return { label: '整理失败', tone: 'danger' };
  return { label: '待整理', tone: 'default' };
}

/** 清单汇总（全部由当页这批场次算出，不另查库） */
export interface SessionLedgerSummary {
  /** 命中的场次数 */
  count: number;
  /** 总时长（毫秒；进行中的场次按当前时刻算） */
  totalDurationMs: number;
  /** 平均时长（毫秒；无场次时为 0） */
  averageDurationMs: number;
  /** 最长一场的时长（毫秒） */
  longestDurationMs: number;
  /** 消息条数合计 */
  messageTotal: number;
  /** 正式场 / 彩排场各多少场 */
  kindCounts: Record<SessionKind, number>;
  /** 各来源多少场 */
  sourceCounts: Record<string, number>;
}

/**
 * 汇总当前这批场次：场次数、总时长、平均与最长时长、消息合计、类型与来源分布。
 * 进行中的场次时长按传入的当前时刻算（随时钟在走）。
 */
export function summarizeSessions(
  items: readonly LiveSessionItem[],
  nowMs: number,
): SessionLedgerSummary {
  const kindCounts: Record<SessionKind, number> = { live: 0, rehearsal: 0 };
  const sourceCounts: Record<string, number> = {};
  let totalDurationMs = 0;
  let longestDurationMs = 0;
  let messageTotal = 0;

  for (const item of items) {
    kindCounts[item.kind] = (kindCounts[item.kind] ?? 0) + 1;
    sourceCounts[item.source] = (sourceCounts[item.source] ?? 0) + 1;
    messageTotal += item.message_count;
    const duration = item.is_active
      ? Math.max(0, nowMs - item.started_at_ms)
      : item.ended_at_ms == null
        ? 0
        : Math.max(0, item.ended_at_ms - item.started_at_ms);
    totalDurationMs += duration;
    if (duration > longestDurationMs) longestDurationMs = duration;
  }

  const count = items.length;
  return {
    count,
    totalDurationMs,
    averageDurationMs: count > 0 ? Math.round(totalDurationMs / count) : 0,
    longestDurationMs,
    messageTotal,
    kindCounts,
    sourceCounts,
  };
}

// ==================== 场次日历热力图 ====================

const DAY_MS = 86_400_000;

/** 热力图的一天：该日的场次数与时长合计 */
export interface HeatmapDay {
  /** yyyy-MM-DD */
  date: string;
  count: number;
  durationMs: number;
}

/** 热力图的一周（列）：周一到周日 7 天 */
export interface HeatmapWeek {
  /** 该周第一个月的标签（同月不重复标；空串表示这周不标） */
  monthLabel: string;
  /** 周一→周日；落在窗口外的日子为 null（不画格子） */
  days: (HeatmapDay | null)[];
}

export interface Heatmap {
  weeks: HeatmapWeek[];
}

/**
 * 场次日历热力图：按开播日铺格子，颜色深浅看当天播了几场。
 *
 * 窗口 = 今天往前 ``minWeeks`` 周，同时不早于最早那场（有更早的场次就把窗口
 * 往前延，不藏数据；格子铺得宽时由界面横向滚动）。格子按周排成列、周一在上
 * （与日历习惯一致）。
 */
export function buildHeatmap(
  items: readonly LiveSessionItem[],
  nowMs: number,
  options?: { minWeeks?: number },
): Heatmap {
  const minWeeks = Math.max(1, options?.minWeeks ?? 26);
  const today = startOfDayMs(nowMs);
  const earliest = items.length
    ? Math.min(...items.map(item => startOfDayMs(item.started_at_ms)))
    : today;
  const windowStart = Math.min(earliest, today - minWeeks * 7 * DAY_MS);

  const byDate = new Map<string, HeatmapDay>();
  for (const item of items) {
    const date = formatDateYMD(item.started_at_ms);
    const day = byDate.get(date) ?? { date, count: 0, durationMs: 0 };
    day.count += 1;
    day.durationMs += item.is_active
      ? Math.max(0, nowMs - item.started_at_ms)
      : item.ended_at_ms == null
        ? 0
        : Math.max(0, item.ended_at_ms - item.started_at_ms);
    byDate.set(date, day);
  }

  // 窗口起点对齐到周一，让每列都是完整的一周
  const startWeekday = (new Date(windowStart).getDay() + 6) % 7;
  const gridStart = windowStart - startWeekday * DAY_MS;

  const weeks: HeatmapWeek[] = [];
  let cursor = gridStart;
  let lastMonth = -1;
  while (cursor <= today) {
    const days: (HeatmapDay | null)[] = [];
    for (let offset = 0; offset < 7; offset += 1) {
      const dayMs = cursor + offset * DAY_MS;
      if (dayMs > today || dayMs < windowStart) {
        days.push(null);
        continue;
      }
      const date = formatDateYMD(dayMs);
      days.push(byDate.get(date) ?? { date, count: 0, durationMs: 0 });
    }
    const month = new Date(cursor).getMonth();
    weeks.push({ monthLabel: month !== lastMonth ? `${month + 1}月` : '', days });
    lastMonth = month;
    cursor += 7 * DAY_MS;
  }
  return { weeks };
}

/** 热力图格子的深浅档位：0=没播，1/2/3 各一档，4 场及以上算最深 */
export function heatmapLevel(day: HeatmapDay | null): number {
  if (!day || day.count <= 0) return 0;
  return Math.min(4, day.count);
}

/** 时刻 → 当天零点的毫秒（本地时区） */
function startOfDayMs(ms: number): number {
  const at = new Date(ms);
  return new Date(at.getFullYear(), at.getMonth(), at.getDate()).getTime();
}
