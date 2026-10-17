import { describe, expect, it } from 'vitest';
import {
  buildWaterlineData,
  formatDurationMs,
  formatMsAsSeconds,
  latencyLevel,
  slowCallsLevel,
  splitWaterlinePoints,
  waterlineColor,
  waterlineColorForProfile,
  WATERLINE_GAP_MS,
} from './sessionReview';
import type { WaterlinePoint } from '@/types/review';

function point(ts: number, tokens: number, session: number): WaterlinePoint {
  return { timestamp_ms: ts, prompt_tokens: tokens, session_id: session };
}

describe('latencyLevel / slowCallsLevel', () => {
  it('p90 超过 30s 染红，空样本无色', () => {
    expect(latencyLevel(30_000)).toBeNull();
    expect(latencyLevel(30_001)).toBe('red');
    expect(latencyLevel(null)).toBeNull();
  });

  it('慢调用超过 10 笔染黄', () => {
    expect(slowCallsLevel(10)).toBeNull();
    expect(slowCallsLevel(11)).toBe('yellow');
  });
});

describe('splitWaterlinePoints', () => {
  it('跨场边界断线留空隙', () => {
    const data = splitWaterlinePoints([
      point(1_000, 10_000, 1),
      point(2_000, 20_000, 1),
      point(3_600_000, 30_000, 2),
    ]);
    expect(data).toEqual([
      [1_000, 10],
      [2_000, 20],
      [2_000, null],
      [3_600_000, 30],
    ]);
  });

  it('同场超时间隔同样断线', () => {
    const data = splitWaterlinePoints([point(0, 1_000, 1), point(WATERLINE_GAP_MS + 1, 2_000, 1)]);
    expect(data).toEqual([
      [0, 1],
      [0, null],
      [WATERLINE_GAP_MS + 1, 2],
    ]);
  });

  it('空序列返回空数据', () => {
    expect(splitWaterlinePoints([])).toEqual([]);
  });
});

describe('buildWaterlineData', () => {
  it('按 profile 分组并换算千 tokens', () => {
    const result = buildWaterlineData([
      { profile_name: 'planner', points: [point(1_000, 123_456, 1)] },
    ]);
    expect(result).toEqual([{ profile: 'planner', data: [[1_000, 123.456]] }]);
  });
});

describe('配色与格式化', () => {
  it('已知 profile 用专色，未知 profile 依次取兜底色', () => {
    expect(waterlineColor('planner', 0)).toBe('#6366f1');
    expect(waterlineColor('unknown', 0)).toBe(waterlineColor('other', 0));
  });

  it('同 profile 在全集任意排列下取色一致（色圈与折线同色）', () => {
    const full = ['minecraft', 'planner', 'zebra-model', 'alpha-model'];
    for (const profile of full) {
      const fromFull = waterlineColorForProfile(profile, full);
      // 传入子集或乱序全集，同一 profile 色不变
      expect(waterlineColorForProfile(profile, [...full].reverse())).toBe(fromFull);
    }
    // 排序下标驱动兜底色分配：排序后 alpha 第 0、zebra 第 3
    expect(waterlineColorForProfile('alpha-model', full)).toBe(waterlineColor('alpha-model', 0));
    expect(waterlineColorForProfile('zebra-model', full)).toBe(waterlineColor('zebra-model', 3));
    // 全集缺席时按下标 0 兜底，不抛错
    expect(waterlineColorForProfile('ghost', [])).toBe(waterlineColor('ghost', 0));
  });

  it('时长与秒标签', () => {
    expect(formatDurationMs(2_052_000)).toBe('34.2 分钟');
    expect(formatDurationMs(7_200_000)).toBe('2 小时');
    expect(formatMsAsSeconds(35_300)).toBe('35.3s');
    expect(formatMsAsSeconds(null)).toBe('—');
  });
});
