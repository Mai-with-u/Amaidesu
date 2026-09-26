// format.ts 展示格式化工具的测试
//
// 只锁定"阶梯边界 + 回退行为"两类事实；涉及本地化渲染（toLocaleString/toLocaleDateString）
// 的用例用宽松正则断言日期部分，避免 Node ICU 版本差异造成误报。

import { describe, expect, it } from 'vitest';

import {
  formatCost,
  formatDateTime,
  formatDurationShort,
  formatLatency,
  getLatencyClass,
  getProfileNameLabel,
  getProfileNameTag,
  relativeAge,
  truncateText,
} from './format';

describe('formatDurationShort', () => {
  it.each([
    [0, '刚刚'],
    [4, '刚刚'],
    [5, '5s 前'],
    [59, '59s 前'],
    [60, '1m 前'],
    [3599, '59m 前'],
    [3600, '1h 前'],
    [86399, '23h 前'],
    [86400, '1d 前'],
    [172800, '2d 前'],
  ])('%i 秒 → "%s"', (diffSec, expected) => {
    expect(formatDurationShort(diffSec)).toBe(expected);
  });
});

describe('formatLatency', () => {
  it('低于 1 秒按毫秒展示', () => {
    expect(formatLatency(850)).toBe('850ms');
    expect(formatLatency(999)).toBe('999ms');
  });

  it('达到 1 秒换算为两位小数的秒', () => {
    expect(formatLatency(1000)).toBe('1.00s');
    expect(formatLatency(1250)).toBe('1.25s');
  });
});

describe('formatCost', () => {
  it('缺省 6 位小数', () => {
    expect(formatCost(0.001234)).toBe('¥0.001234');
  });

  it('可收窄位数（图表轴等短格式场景）', () => {
    expect(formatCost(0.001234, 2)).toBe('¥0.00');
  });
});

describe('getLatencyClass', () => {
  it.each([
    [0, 'fast'],
    [999, 'fast'],
    [1000, 'normal'],
    [4999, 'normal'],
    [5000, 'slow'],
  ])('%i ms → "%s"', (ms, expected) => {
    expect(getLatencyClass(ms)).toBe(expected);
  });
});

describe('profile 名映射', () => {
  it('已知 profile 映射到 el-tag 类型色', () => {
    expect(getProfileNameTag('planner')).toBe('primary');
    expect(getProfileNameTag('replyer')).toBe('success');
    expect(getProfileNameTag('summary')).toBe('warning');
    expect(getProfileNameTag('vision')).toBe('danger');
    expect(getProfileNameTag('minecraft')).toBe('info');
  });

  it('映射之外的 profile 色回退 info、标签原样回退', () => {
    expect(getProfileNameTag('nope')).toBe('info');
    expect(getProfileNameLabel('nope')).toBe('nope');
  });

  it('已知 profile 映射到中文标签', () => {
    expect(getProfileNameLabel('planner')).toBe('主 LLM');
    expect(getProfileNameLabel('replyer')).toBe('回复');
    expect(getProfileNameLabel('minecraft')).toBe('游戏');
  });
});

describe('truncateText', () => {
  it('空值返回 "-"', () => {
    expect(truncateText('', 10)).toBe('-');
  });

  it('不超过上限原样返回', () => {
    expect(truncateText('abc', 10)).toBe('abc');
    expect(truncateText('abcdef', 6)).toBe('abcdef');
  });

  it('超长截断并补省略号（总长为上限 +1）', () => {
    expect(truncateText('abcdefg', 3)).toBe('abc…');
  });
});

describe('relativeAge', () => {
  const now = Date.UTC(2026, 8, 26, 12, 0, 0);

  it('空值（0/null 语义）显示 "—"', () => {
    expect(relativeAge(0, now)).toBe('—');
  });

  it('阶梯文案', () => {
    expect(relativeAge(now - 30_000, now)).toBe('刚刚');
    expect(relativeAge(now - 5 * 60_000, now)).toBe('5 分钟前');
    expect(relativeAge(now - 3 * 3_600_000, now)).toBe('3 小时前');
    expect(relativeAge(now - 2 * 86_400_000, now)).toBe('2 天前');
  });

  it('超过当月回落为日期', () => {
    const text = relativeAge(now - 60 * 86_400_000, now);
    expect(text).toMatch(/^\d{4}[/-]\d{1,2}[/-]\d{1,2}$/);
  });
});

describe('formatDateTime', () => {
  it('本地时区日期时间（断言结构而非具体小时，兼容时区与 ICU 差异）', () => {
    const text = formatDateTime(Date.UTC(2026, 8, 26, 12, 30, 5));
    expect(text).toMatch(/^\d{4}[/-]\d{2}[/-]\d{2}[ T]\d{2}:\d{2}:\d{2}$/);
  });
});
