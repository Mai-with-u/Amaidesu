/**
 * clipsTime 纯函数测试：相对时刻格式化与解析、毫秒夹取。
 */
import { describe, expect, it } from 'vitest';
import { clampMs, formatRelClock, parseRelClock } from '@/utils/clipsTime';

describe('formatRelClock', () => {
  it('不足一小时显示 m:ss', () => {
    expect(formatRelClock(0)).toBe('0:00');
    expect(formatRelClock(65_000)).toBe('1:05');
    expect(formatRelClock(3_600_000 - 1000)).toBe('59:59');
  });

  it('满一小时显示 h:mm:ss', () => {
    expect(formatRelClock(3_600_000)).toBe('1:00:00');
    expect(formatRelClock(3_725_000)).toBe('1:02:05');
  });

  it('负值截为 0', () => {
    expect(formatRelClock(-5)).toBe('0:00');
  });
});

describe('parseRelClock', () => {
  it('解析 m:ss / h:mm:ss / 裸秒数', () => {
    expect(parseRelClock('1:05')).toBe(65_000);
    expect(parseRelClock('1:02:05')).toBe(3_725_000);
    expect(parseRelClock('90')).toBe(90_000);
    expect(parseRelClock('1.5')).toBe(1500);
    expect(parseRelClock(' 2:30 ')).toBe(150_000);
  });

  it('非法输入返回 null', () => {
    expect(parseRelClock('')).toBeNull();
    expect(parseRelClock('abc')).toBeNull();
    expect(parseRelClock('1:2:3:4')).toBeNull();
    expect(parseRelClock('12:345')).toBeNull();
    expect(parseRelClock('-1:00')).toBeNull();
  });
});

describe('clampMs', () => {
  it('夹进区间', () => {
    expect(clampMs(5, 10, 20)).toBe(10);
    expect(clampMs(25, 10, 20)).toBe(20);
    expect(clampMs(15, 10, 20)).toBe(15);
  });
});
