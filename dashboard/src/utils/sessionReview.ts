/**
 * 场次复盘页纯函数（SessionReview.vue 与其单测共享）
 *
 * 设计纪律：代码只做"计算 + 染色"，不做告警判定——超阈染色阈值是前端
 * 常量，卡片副文案自带口径便于人工核验。
 */

import type { WaterlineSeries, WaterlinePoint } from '@/types/review';

// ==================== 染色阈值常量（卡片副文案与阈值同源） ====================

/** 决策→发言时延 p90 超过 30s 染红（与设计定案/mockup 口径一致） */
export const LATENCY_P90_RED_MS = 30_000;
/** 慢调用超过 10 笔染黄 */
export const SLOW_CALLS_YELLOW = 10;

/** 慢调用聚合缺省阈值（毫秒）：后端未配置 profile 阈值时的兜底口径，与聚合侧一致 */
export const DEFAULT_SLOW_THRESHOLD_MS = 15_000;

/** 慢调用深链的统一耗时下限：取各 profile 阈值的最小值（无配置用缺省） */
export function minSlowThresholdMs(thresholds: Record<string, number>): number {
  const values = Object.values(thresholds);
  return values.length ? Math.min(...values) : DEFAULT_SLOW_THRESHOLD_MS;
}

/** 决策→发言时延 p90 染色：>30s 红，否则无色 */
export function latencyLevel(p90Ms: number | null): 'red' | null {
  if (p90Ms !== null && p90Ms > LATENCY_P90_RED_MS) return 'red';
  return null;
}

/** 慢调用计数染色：>10 黄，否则无色 */
export function slowCallsLevel(count: number): 'yellow' | null {
  return count > SLOW_CALLS_YELLOW ? 'yellow' : null;
}

// ==================== 水位图（ECharts option 构造） ====================

/**
 * 已知 profile → 主题 CSS 变量名。折线颜色随主题换档（深浅两套域色），
 * 运行时经 readVar 读实际色值，canvas 不解析 CSS 变量所以不能直接写变量名。
 */
export const WATERLINE_PROFILE_COLOR_VARS: Record<string, string> = {
  planner: '--color-planner',
  minecraft: '--color-game',
};

/** 未知 profile 的兜底色变量（按排序下标依次取用，与域色同带） */
const FALLBACK_COLOR_VARS = ['--color-live', '--color-warning', '--color-primary', '--color-info'];

/**
 * 按 profile 名全集的排序下标取展示色（用表色圈与水位图折线共用同一入口，
 * 保证任意 profile 集合下同一 profile 恒为同色）。已知专色 profile 不受
 * 下标影响；未知 profile 在全集缺席时按下标 0 兜底。
 *
 * readVar 读当前主题的 CSS 变量实际色值（useChartPalette 提供随主题重算的实现）。
 */
export function waterlineColorForProfile(
  profile: string,
  allProfiles: string[],
  readVar: (name: string) => string,
): string {
  const knownVar = WATERLINE_PROFILE_COLOR_VARS[profile];
  if (knownVar) return readVar(knownVar);
  const index = [...new Set(allProfiles)].sort().indexOf(profile);
  const fallbackVar = FALLBACK_COLOR_VARS[(index < 0 ? 0 : index) % FALLBACK_COLOR_VARS.length];
  return readVar(fallbackVar);
}

// ==================== 展示标签 ====================

/** 缓存命中率 → 展示文案（未上报显示 —） */
export function cacheRateLabel(rate: number | null): string {
  return rate === null ? '—' : `${Math.round(rate * 100)}%`;
}

/**
 * 决策触发原因 → 展示文案。
 * 词表是后端触发原因封闭枚举（src/agents/streamer/trigger_cause.py）的对外字符串
 * 形态：批次刷新与调试类原值、主动发言类带 proactive: 前缀、控制台调试两种形态。
 * 未收录的新枚举值原样透出，出现频率高了再补表，不为未知值编造翻译。
 */
const TRIGGER_REASON_LABELS: Record<string, string> = {
  forced: '付费消息触发',
  batch_full: '弹幕攒满',
  window_expired: '聚合窗到期',
  idle_compensation: '冷场补足',
  'proactive:external': '主动·外部唤醒',
  'proactive:schedule': '主动·定时开麦',
  'proactive:cold': '主动·冷场开麦',
  'proactive:reminder': '主动·递话催醒',
  'proactive:rundown': '主动·流程单推进',
  'proactive:game': '主动·游戏待定夺',
  'proactive:game_lead_idle_fallback': '主动·游戏冷场兜底',
  'proactive:signoff': '主动·收播道别',
  'proactive:resume': '主动·断线续播',
  'dashboard:debug_test': '调试·手动决策',
  'proactive:dashboard_debug': '调试·主动决策窗',
  unknown: '未知',
};

export function triggerReasonLabel(reason: string): string {
  return TRIGGER_REASON_LABELS[reason] ?? reason;
}

/**
 * 场间空窗判定的最小间隔：相邻两点跨场（session_id 变化）即断线，
 * 同场内间隔超过该值也断（防长时间无请求的直线伪影）。
 */
export const WATERLINE_GAP_MS = 5 * 60_000;

/**
 * 水位序列 → ECharts 多折线数据（单位：千 tokens）。
 *
 * 跨场拼接：把每条序列按"场边界或超时间隔"切成多段（段内 null 收尾断线），
 * 时间轴共用，段间自然留出空窗空隙。返回 [时刻ms, 千tokens] 数组的数组，
 * 空隙由段切分表达（ECharts 对 null 值断线）。
 */
export function buildWaterlineData(
  series: WaterlineSeries[],
): { profile: string; data: [number, number | null][] }[] {
  return series.map(s => ({ profile: s.profile_name, data: splitWaterlinePoints(s.points) }));
}

/** 单条序列 → 断线分段数据（跨场/超时间隔处补 null 断点） */
export function splitWaterlinePoints(points: WaterlinePoint[]): [number, number | null][] {
  const data: [number, number | null][] = [];
  let prev: WaterlinePoint | null = null;
  for (const point of points) {
    const isBoundary =
      prev !== null &&
      (prev.session_id !== point.session_id ||
        point.timestamp_ms - prev.timestamp_ms > WATERLINE_GAP_MS);
    // 零输入笔在对数轴上不可画（log(0) 无定义），按断点处理
    const valueK = point.prompt_tokens / 1000;
    const value = valueK > 0 ? valueK : null;
    if (isBoundary) {
      // 断点：前段收尾 null + 新段起点（两线段间留出空窗）
      data.push([prev!.timestamp_ms, null]);
      data.push([point.timestamp_ms, value]);
    } else {
      data.push([point.timestamp_ms, value]);
    }
    prev = point;
  }
  return data;
}

// ==================== 展示辅助 ====================

/** 毫秒时长 → 紧凑中文标签（34.2 分钟 / 2 小时）；非整数保留一位小数 */
export function formatDurationMs(ms: number): string {
  if (ms <= 0) return '0 分钟';
  const minutes = ms / 60_000;
  if (minutes < 60) {
    return `${Number.isInteger(minutes) ? minutes : minutes.toFixed(1)} 分钟`;
  }
  const hours = minutes / 60;
  return `${Number.isInteger(hours) ? hours : hours.toFixed(1)} 小时`;
}

/** 毫秒 → 秒标签（时延卡展示口径） */
export function formatMsAsSeconds(ms: number | null): string {
  if (ms === null) return '—';
  return `${(ms / 1000).toFixed(1)}s`;
}

// ==================== 时刻锚定与流程单执行 ====================

/** 控制台回看深链：/live?session=<id>[&at=<ms>]（at=毫秒时刻，回看定位锚点） */
export function consoleReplayLink(sessionId: number, atMs?: number | null): string {
  return atMs != null ? `/live?session=${sessionId}&at=${atMs}` : `/live?session=${sessionId}`;
}

/** 实际停留超预计 25% 染黄（阈值前端常量，与卡片口径同源） */
export const DWELL_OVER_YELLOW_RATIO = 1.25;
/** 实际停留超预计一倍染红 */
export const DWELL_OVER_RED_RATIO = 2.0;

/**
 * 流程单环节停留染色：>2 倍预计红、>1.25 倍预计黄，否则无色。
 * 未封口（left_at 空）或无预计时长不参与比较，恒无色。
 */
export function dwellLevel(
  dwellMs: number,
  expectedMs: number | null,
  leftAtMs: number | null,
): 'red' | 'yellow' | null {
  if (leftAtMs === null || expectedMs === null || expectedMs <= 0 || dwellMs <= 0) return null;
  const ratio = dwellMs / expectedMs;
  if (ratio > DWELL_OVER_RED_RATIO) return 'red';
  if (ratio > DWELL_OVER_YELLOW_RATIO) return 'yellow';
  return null;
}
