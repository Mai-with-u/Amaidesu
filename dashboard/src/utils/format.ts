/**
 * 展示格式化工具（多页面共享的单一事实源）
 *
 * 从 llm-history 两组件、观众页与 Agents/Collectors 的本地复刻收敛而来。
 * 事件短标签（相对时间）在 utils/liveFeed.ts（relativeTime，毫秒入参）。
 */

// ==================== 时间与时长 ====================

/**
 * 时长差（秒）→ 短标签（"刚刚 / 12s 前 / 3m 前 / 2h 前 / 1d 前"）。
 * 事件轨迹时间戳与心跳年龄共用的单一事实源。
 */
export function formatDurationShort(diffSec: number): string {
  if (diffSec < 5) return '刚刚';
  if (diffSec < 60) return `${diffSec}s 前`;
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m 前`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h 前`;
  return `${Math.floor(diffSec / 86400)}d 前`;
}

/**
 * 日期时间（本地时区，含秒）。
 */
export function formatDateTime(timestamp: number): string {
  const date = new Date(timestamp);
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

/**
 * 延迟毫秒 → "850ms" / "1.25s"。
 */
export function formatLatency(ms: number): string {
  if (ms < 1000) {
    return `${ms}ms`;
  }
  return `${(ms / 1000).toFixed(2)}s`;
}

// ==================== LLM 历史页 ====================

/**
 * 费用 → "¥0.001234"（缺省 6 位小数；图表轴等短格式场景可收窄位数）。
 */
export function formatCost(cost: number, fractionDigits = 6): string {
  return `¥${cost.toFixed(fractionDigits)}`;
}

/** 延迟档位样式类（fast / normal / slow） */
export function getLatencyClass(ms: number): string {
  if (ms < 1000) return 'fast';
  if (ms < 5000) return 'normal';
  return 'slow';
}

/** LLM 用途 profile 名 → el-tag 类型色。映射之外的 profile 原样回退 info */
export function getProfileNameTag(profileName: string): string {
  const tagMap: Record<string, string> = {
    planner: 'primary',
    replyer: 'success',
    summary: 'warning',
    vision: 'danger',
    minecraft: 'info',
    simulator: 'info',
  };
  return tagMap[profileName] || 'info';
}

/** LLM 用途 profile 名 → 中文标签；映射之外的 profile 原样回退 */
export function getProfileNameLabel(profileName: string): string {
  const labelMap: Record<string, string> = {
    planner: '主 LLM',
    replyer: '回复',
    summary: '摘要',
    minecraft: '游戏',
    vision: '视觉',
    simulator: '模拟',
  };
  return labelMap[profileName] || profileName;
}

/**
 * 截断文本；空值返回 "-"。
 */
export function truncateText(text: string, maxLength: number): string {
  if (!text) return '-';
  if (text.length <= maxLength) return text;
  return `${text.substring(0, maxLength)}…`;
}

// ==================== 观众档案 ====================

/**
 * 毫秒时刻距今的长文案（"刚刚 / 5 分钟前 / 3 小时前 / 2 天前"，超出当月
 * 回落为日期）。空值（0/null 语义）显示 "—"。
 */
export function relativeAge(ms: number, now: number = Date.now()): string {
  if (!ms) return '—';
  const diffSec = Math.floor((now - ms) / 1000);
  if (diffSec < 60) return '刚刚';
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)} 分钟前`;
  if (diffSec < 86_400) return `${Math.floor(diffSec / 3600)} 小时前`;
  if (diffSec < 30 * 86_400) return `${Math.floor(diffSec / 86_400)} 天前`;
  return new Date(ms).toLocaleDateString('zh-CN');
}
