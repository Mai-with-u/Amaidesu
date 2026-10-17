// logCollapse（日志同因折叠）测试：相邻同级别/同模块/同正文归并、跨开不合并

import { describe, expect, it } from 'vitest';
import { collapseAdjacentLogs, type LogEntryLike } from '@/utils/logCollapse';

function entry(overrides: Partial<LogEntryLike> & { timestamp: string }): LogEntryLike {
  return { level: 'INFO', module: 'ModA', message: 'hello', ...overrides };
}

describe('collapseAdjacentLogs 同因折叠', () => {
  it('相邻同级/同模块/同正文归并为一组，组内保序', () => {
    const rows = collapseAdjacentLogs([
      entry({ timestamp: 't1' }),
      entry({ timestamp: 't2' }),
      entry({ timestamp: 't3' }),
    ]);

    expect(rows).toHaveLength(1);
    expect(rows[0]!.entries.map(e => e.timestamp)).toEqual(['t1', 't2', 't3']);
    expect(rows[0]!.key).toBe('t1|ModA|hello');
  });

  it('级别/模块/正文任一不同即断组，跨开的相同日志不合并', () => {
    const rows = collapseAdjacentLogs([
      entry({ timestamp: 't1' }),
      entry({ timestamp: 't2', level: 'ERROR' }),
      entry({ timestamp: 't3' }),
      entry({ timestamp: 't4', module: 'ModB' }),
    ]);

    expect(rows).toHaveLength(4);
  });

  it('相同正文但被异因行隔开时分成两组', () => {
    const rows = collapseAdjacentLogs([
      entry({ timestamp: 't1' }),
      entry({ timestamp: 't2', message: 'other' }),
      entry({ timestamp: 't3' }),
    ]);

    expect(rows).toHaveLength(3);
    expect(rows[0]!.entries).toHaveLength(1);
    expect(rows[2]!.entries).toHaveLength(1);
  });

  it('空列表返回空', () => {
    expect(collapseAdjacentLogs([])).toEqual([]);
  });
});
