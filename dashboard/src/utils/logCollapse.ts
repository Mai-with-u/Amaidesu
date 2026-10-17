/**
 * 日志同因折叠：相邻且同级/同模块/同正文的连续日志归并为一组。
 *
 * 日志页降噪用——重复刷屏的同因告警（如连接失败每 30 秒重试）折叠成
 * 一行 ×N，点开再逐条展开。只折叠"相邻"的条目，跨开的事件不合并。
 */

/** 折叠分组键所需的最小日志形状（store 的 LogEntry 与 API 条目均满足） */
export interface LogEntryLike {
  timestamp: string;
  level: string;
  module: string;
  message: string;
}

export interface CollapsedLogRow<T extends LogEntryLike> {
  /** 组内首条的去重键（展开态记录用） */
  key: string;
  /** 组内全部条目（时间升序，展开时逐条渲染） */
  entries: T[];
}

export function collapseAdjacentLogs<T extends LogEntryLike>(entries: T[]): CollapsedLogRow<T>[] {
  const rows: CollapsedLogRow<T>[] = [];
  for (const entry of entries) {
    const last = rows[rows.length - 1];
    const lastEntry = last?.entries[last.entries.length - 1];
    if (
      lastEntry &&
      lastEntry.level === entry.level &&
      lastEntry.module === entry.module &&
      lastEntry.message === entry.message
    ) {
      last.entries.push(entry);
    } else {
      rows.push({ key: `${entry.timestamp}|${entry.module}|${entry.message}`, entries: [entry] });
    }
  }
  return rows;
}
