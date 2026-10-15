import { describe, expect, it } from 'vitest';
import { formatQuotaAmount, formatResetIn, quotaLevel } from './providerQuota';

const NOW = 1_800_000_000_000;
const MIN = 60_000;

describe('formatResetIn', () => {
  it.each([
    [NOW - MIN, 'soon'],
    [NOW + 30_000, '<1 min'],
    [NOW + 19 * MIN, '19 min'],
    [NOW + (4 * 60 + 59) * MIN, '4 hr 59 min'],
    [NOW + 3 * 60 * MIN, '3 hr'],
    [NOW + 24 * 60 * MIN, '1 day'],
    [NOW + (2 * 24 + 3) * 60 * MIN, '2 days 3 hr'],
  ])('%s → %s', (resetsAt, expected) => {
    expect(formatResetIn(resetsAt, NOW)).toBe(expected);
  });
});

describe('formatQuotaAmount', () => {
  it('known currencies use symbols, others append the code', () => {
    expect(formatQuotaAmount(12.5, 'CNY')).toBe('¥12.50');
    expect(formatQuotaAmount(3, 'usd')).toBe('$3.00');
    expect(formatQuotaAmount(1.234, 'EUR')).toBe('1.23 EUR');
    expect(formatQuotaAmount(0, null)).toBe('0.00');
  });
});

describe('quotaLevel', () => {
  it('maps usage to bar levels', () => {
    expect(quotaLevel(9)).toBe('normal');
    expect(quotaLevel(70)).toBe('warning');
    expect(quotaLevel(84)).toBe('warning');
    expect(quotaLevel(90)).toBe('danger');
  });
});
