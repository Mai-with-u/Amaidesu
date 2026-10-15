/**
 * 平台额度（上下文面板 Usage 区）的展示格式化，文案对齐 Claude Desktop 的用量面板。
 */

const CURRENCY_SYMBOLS: Record<string, string> = { CNY: '¥', USD: '$' };

/** 距重置的剩余时长："<1 min" / "19 min" / "4 hr 59 min" / "2 days 3 hr"；已过重置时刻返回 "soon" */
export function formatResetIn(resetsAtMs: number, now: number): string {
  const diffMin = Math.floor((resetsAtMs - now) / 60_000);
  if (resetsAtMs <= now) return 'soon';
  if (diffMin < 1) return '<1 min';
  if (diffMin < 60) return `${diffMin} min`;
  const hours = Math.floor(diffMin / 60);
  if (hours < 24) {
    const minutes = diffMin % 60;
    return minutes > 0 ? `${hours} hr ${minutes} min` : `${hours} hr`;
  }
  const days = Math.floor(hours / 24);
  const restHours = hours % 24;
  const dayText = `${days} ${days === 1 ? 'day' : 'days'}`;
  return restHours > 0 ? `${dayText} ${restHours} hr` : dayText;
}

/** 金额：已知币种用符号（¥12.50 / $3.00），其余币种把代码写在后面 */
export function formatQuotaAmount(amount: number, currency: string | null): string {
  const value = amount.toFixed(2);
  const symbol = currency ? CURRENCY_SYMBOLS[currency.toUpperCase()] : undefined;
  if (symbol) return `${symbol}${value}`;
  return currency ? `${value} ${currency}` : value;
}

/** 用量进度条档位：70% 起提醒，90% 起告急 */
export function quotaLevel(usedPercent: number): 'normal' | 'warning' | 'danger' {
  if (usedPercent >= 90) return 'danger';
  if (usedPercent >= 70) return 'warning';
  return 'normal';
}
