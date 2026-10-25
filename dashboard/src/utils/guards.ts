/**
 * 通用取值守卫（多页面/纯映射层共享的单一事实源）
 *
 * 服务 WS 事件 payload / REST 明细这类"结构不受本侧约束"的宽松数据：
 * 先判形再取值，取不到回安全的空默认。原为 utils/liveFeed.ts 的"通用取值
 * 助手"节，因回看映射（replayFeed）、决策轮归组（decisionRounds）等非直播
 * 时间线消费方也要用，按归属迁出独立成模块。
 */

/** 对象守卫（数组不算对象；WS payload 判形用） */
export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** 字符串取出并去首尾空白，其余回空串 */
export function str(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

/** 有限数字放行，NaN/Infinity/其他回 null */
export function num(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

/** 布尔守卫：仅严格 true 为真（payload 布尔开关的缺省安全读） */
export function bool(value: unknown): boolean {
  return value === true;
}
