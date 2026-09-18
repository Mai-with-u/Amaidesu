// 图表数值格式化共享工具
//
// LLM 用量页的手写 SVG 图表（TrendChart / ModelCostDonut）与页面卡片
// 共用同一套数值→文本规则，避免坐标轴刻度、悬停提示、图例各自维护
// 造成口径漂移。

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

/** 费用格式：固定两位小数，带人民币符号 */
export function costYuan(value: number): string {
  return `¥${value.toFixed(2)}`;
}

/** 百分比格式：0-1 比例 → "12.5%"（入参为 0-100 时会失真，调用方注意口径） */
export function percentFromRatio(ratio: number): string {
  return `${(ratio * 100).toFixed(1)}%`;
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
