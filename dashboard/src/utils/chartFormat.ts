// 图表数值格式化共享工具
//
// LLM 用量页的图表（TrendChart / ModelCostDonut）与页面卡片共用同一套
// 数值→文本规则，避免坐标轴刻度、悬停提示、图例各自维护造成口径漂移。

import { formatCost } from '@/utils/format';

/** 大数紧凑格式：1234 → 1.2k，3456000 → 3.5M；千以内原样 */
export function compactNumber(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 1_000_000) {
    return `${(value / 1_000_000).toFixed(1)}M`;
  }
  if (abs >= 1_000) {
    return `${(value / 1_000).toFixed(1)}k`;
  }
  return `${Math.round(value)}`;
}

/** 费用格式：图表轴专用短格式（两位小数），位数口径委托 formatCost */
export function costYuan(value: number): string {
  return formatCost(value, 2);
}

/**
 * 把量程上限取整到"好看"的刻度：1/2/5 × 10^n 向上取整。
 * 用于图表 Y 轴量程，让网格线落在整数刻度上（如 173 → 200，1734 → 2000）。
 */
export function niceCeil(value: number): number {
  if (value <= 0) return 1;
  const exponent = Math.floor(Math.log10(value));
  const base = Math.pow(10, exponent);
  const mantissa = value / base;
  const nice = mantissa <= 1 ? 1 : mantissa <= 2 ? 2 : mantissa <= 5 ? 5 : 10;
  return nice * base;
}
