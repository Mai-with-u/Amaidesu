// utils/guards 通用取值守卫的测试（用例随守卫从 liveFeed.test.ts 迁入）

import { describe, expect, it } from 'vitest';

import { bool, isRecord, num, str } from '@/utils/guards';

describe('通用取值守卫', () => {
  it('isRecord：对象为真，数组/null 为假', () => {
    expect(isRecord({})).toBe(true);
    expect(isRecord([1])).toBe(false);
    expect(isRecord(null)).toBe(false);
    expect(isRecord('x')).toBe(false);
  });

  it('str：字符串取出并去首尾空白，其余回空串', () => {
    expect(str('  hi  ')).toBe('hi');
    expect(str(42)).toBe('');
  });

  it('num：有限数字放行，NaN/Infinity/其他回 null', () => {
    expect(num(3.5)).toBe(3.5);
    expect(num(Number.NaN)).toBe(null);
    expect(num(Number.POSITIVE_INFINITY)).toBe(null);
    expect(num('3')).toBe(null);
  });

  it('bool：仅严格 true 为真', () => {
    expect(bool(true)).toBe(true);
    expect(bool(1)).toBe(false);
    expect(bool('true')).toBe(false);
  });
});
