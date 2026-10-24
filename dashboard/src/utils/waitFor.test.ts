// utils/waitFor 有界轮询等待的测试
//
// 覆盖：首探即成立零等待、中途成立提前返回、超时分支（探测 tries 次后
// 返回 false）、探测节奏为先探测后等待。

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { waitFor } from '@/utils/waitFor';

describe('waitFor', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('条件首探即成立：立即返回 true，不等待', async () => {
    let probes = 0;
    const pending = waitFor(
      () => {
        probes += 1;
        return true;
      },
      { tries: 5, intervalMs: 100 },
    );
    expect(await pending).toBe(true);
    expect(probes).toBe(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('条件中途成立：提前返回 true，不再探测', async () => {
    let probes = 0;
    const pending = waitFor(
      () => {
        probes += 1;
        return probes >= 3;
      },
      { tries: 10, intervalMs: 100 },
    );

    await vi.advanceTimersByTimeAsync(250);
    expect(await pending).toBe(true);
    expect(probes).toBe(3);
  });

  it('超时分支：探测满 tries 次仍不成立返回 false', async () => {
    let probes = 0;
    const pending = waitFor(
      () => {
        probes += 1;
        return false;
      },
      { tries: 4, intervalMs: 100 },
    );

    await vi.advanceTimersByTimeAsync(400);
    expect(await pending).toBe(false);
    // 先探测后等待：4 次探测、3 次间隔等待
    expect(probes).toBe(4);
  });
});
