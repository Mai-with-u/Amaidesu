/**
 * 切片页的相对时刻换算（纯函数）。
 *
 * 段的时间坐标是墙钟毫秒，界面上统一显示/输入"自开播起"的相对时刻
 * （`m:ss` / `h:mm:ss` / 裸秒数），换算回毫秒需要场次开播时刻。
 */

/** 相对毫秒 → `h:mm:ss`（不足 1 小时为 `m:ss`） */
export function formatRelClock(relMs: number): string {
  const totalSec = Math.max(0, Math.floor(relMs / 1000));
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  const mm = h > 0 ? String(m).padStart(2, '0') : String(m);
  const ss = String(s).padStart(2, '0');
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

/**
 * 解析人输入的相对时刻：`m:ss` / `h:mm:ss` / 裸秒数（可带小数）。
 * 非法输入返回 null。
 */
export function parseRelClock(text: string): number | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  if (/^\d+(\.\d+)?$/.test(trimmed)) {
    return Math.round(parseFloat(trimmed) * 1000);
  }
  const parts = trimmed.split(':');
  if (parts.length < 2 || parts.length > 3) return null;
  if (!parts.every(part => /^\d{1,2}$/.test(part))) return null;
  const nums = parts.map(Number);
  let seconds = 0;
  for (const num of nums) {
    seconds = seconds * 60 + num;
  }
  return seconds * 1000;
}

/** 把任意毫秒值夹进 [lowerMs, upperMs] */
export function clampMs(value: number, lowerMs: number, upperMs: number): number {
  return Math.min(upperMs, Math.max(lowerMs, value));
}

/** "YYYY-MM-DD HH:mm:ss"（秒可省）→ 本地墙钟毫秒；非法返回 null */
export function parseLocalDateTime(text: string): number | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?$/.exec(text.trim());
  if (match === null) return null;
  const [, y, mo, d, h, mi, sec] = match;
  const dt = new Date(
    Number(y),
    Number(mo) - 1,
    Number(d),
    Number(h),
    Number(mi),
    Number(sec ?? '0'),
  );
  const ms = dt.getTime();
  return Number.isNaN(ms) ? null : ms;
}

/** Date → "YYYY-MM-DD HH:mm:ss"（指派时刻输入的默认值用） */
export function formatLocalDateTime(ms: number): string {
  const date = new Date(ms);
  const pad = (n: number): string => String(n).padStart(2, '0');
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
  );
}
