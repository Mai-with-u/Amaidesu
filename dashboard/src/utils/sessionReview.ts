/**
 * 场次复盘页纯函数（SessionReview.vue 与其单测共享）
 *
 * 设计纪律：代码只做"计算 + 染色"，不做告警判定——超阈染色阈值是前端
 * 常量，卡片副文案自带口径便于人工核验（设计定案 §1 收口）。
 */

import type { WaterlineSeries, WaterlinePoint } from '@/types/review';

// ==================== 染色阈值常量（卡片副文案与阈值同源） ====================

/** 决策→发言时延 p90 超过 30s 染红（与设计定案/mockup 口径一致） */
export const LATENCY_P90_RED_MS = 30_000;
/** 慢调用超过 10 笔染黄 */
export const SLOW_CALLS_YELLOW = 10;

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

/** 水位图两条线的展示色（planner 靛蓝 / minecraft 粉，与 mockup 一致） */
export const WATERLINE_COLORS: Record<string, string> = {
  planner: '#6366f1',
  minecraft: '#ec4899',
};

/** 兜底配色（未知 profile 依次取用） */
const FALLBACK_COLORS = ['#14b8a6', '#e6a23c', '#409eff', '#909399'];

export function waterlineColor(profile: string, index: number): string {
  return WATERLINE_COLORS[profile] ?? FALLBACK_COLORS[index % FALLBACK_COLORS.length];
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
    if (isBoundary) {
      // 断点：前段收尾 null + 新段起点（两线段间留出空窗）
      data.push([prev!.timestamp_ms, null]);
      data.push([point.timestamp_ms, point.prompt_tokens / 1000]);
    } else {
      data.push([point.timestamp_ms, point.prompt_tokens / 1000]);
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
