import { describe, expect, it } from 'vitest';
import {
  buildWaterlineData,
  cacheRateLabel,
  consoleReplayLink,
  dwellLevel,
  formatDurationMs,
  formatMsAsSeconds,
  latencyLevel,
  minSlowThresholdMs,
  slowCallsLevel,
  splitWaterlinePoints,
  triggerReasonLabel,
  waterlineColorForProfile,
  WATERLINE_GAP_MS,
  WATERLINE_PROFILE_COLOR_VARS,
} from './sessionReview';
import type { WaterlinePoint } from '@/types/review';

function point(ts: number, tokens: number, session: number): WaterlinePoint {
  return { timestamp_ms: ts, prompt_tokens: tokens, session_id: session };
}

/** 测试用 CSS 变量读取替身：变量名 → 稳定色值，模拟主题运行时取色 */
function fakeReadVar(name: string): string {
  return `var${name}`;
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

  it('零输入笔按断点处理（对数轴下 log(0) 无定义）', () => {
    const data = splitWaterlinePoints([
      point(1_000, 5_000, 1),
      point(2_000, 0, 1),
      point(3_000, 6_000, 1),
    ]);
    expect(data).toEqual([
      [1_000, 5],
      [2_000, null],
      [3_000, 6],
    ]);
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
  it('已知 profile 按主题变量取色，未知 profile 按排序下标取兜底变量', () => {
    expect(waterlineColorForProfile('planner', ['planner'], fakeReadVar)).toBe(
      fakeReadVar(WATERLINE_PROFILE_COLOR_VARS.planner),
    );
    expect(waterlineColorForProfile('minecraft', ['planner'], fakeReadVar)).toBe(
      fakeReadVar(WATERLINE_PROFILE_COLOR_VARS.minecraft),
    );
    // 排序下标驱动兜底变量分配：排序后 alpha 第 0、zebra 第 3，取值不同
    const full = ['minecraft', 'planner', 'zebra-model', 'alpha-model'];
    expect(waterlineColorForProfile('alpha-model', full, fakeReadVar)).not.toBe(
      waterlineColorForProfile('zebra-model', full, fakeReadVar),
    );
    // 全集缺席时按下标 0 兜底，不抛错
    expect(waterlineColorForProfile('ghost', [], fakeReadVar)).toBe(
      waterlineColorForProfile('alpha-model', full, fakeReadVar),
    );
  });

  it('同 profile 在全集任意排列下取色一致（色圈与折线同色）', () => {
    const full = ['minecraft', 'planner', 'zebra-model', 'alpha-model'];
    for (const profile of full) {
      const fromFull = waterlineColorForProfile(profile, full, fakeReadVar);
      // 传入子集或乱序全集，同一 profile 色不变
      expect(waterlineColorForProfile(profile, [...full].reverse(), fakeReadVar)).toBe(fromFull);
    }
  });

  it('触发原因映射封闭词表，未收录原样透出', () => {
    expect(triggerReasonLabel('batch_full')).toBe('弹幕攒满');
    expect(triggerReasonLabel('proactive:signoff')).toBe('主动·收播道别');
    expect(triggerReasonLabel('dashboard:debug_test')).toBe('调试·手动决策');
    expect(triggerReasonLabel('proactive:future_kind')).toBe('proactive:future_kind');
  });

  it('缓存命中率标签与时长秒标签', () => {
    expect(cacheRateLabel(null)).toBe('—');
    expect(cacheRateLabel(0.5)).toBe('50%');
    expect(cacheRateLabel(0.836)).toBe('84%');
    expect(formatDurationMs(2_052_000)).toBe('34.2 分钟');
    expect(formatDurationMs(7_200_000)).toBe('2 小时');
    expect(formatMsAsSeconds(35_300)).toBe('35.3s');
    expect(formatMsAsSeconds(null)).toBe('—');
  });

  it('慢调用深链耗时下限取各 profile 阈值最小值，无配置用缺省', () => {
    expect(minSlowThresholdMs({ planner: 8_000, minecraft: 20_000 })).toBe(8_000);
    expect(minSlowThresholdMs({})).toBe(15_000);
  });
});

describe('consoleReplayLink / dwellLevel', () => {
  it('回看深链：无 at 只带场次，有 at 追加时刻', () => {
    expect(consoleReplayLink(7)).toBe('/live?session=7');
    expect(consoleReplayLink(7, 1234)).toBe('/live?session=7&at=1234');
  });

  it('停留染色：超一倍红、超 25% 黄、未封口/无预计不比', () => {
    expect(dwellLevel(2_000_001, 1_000_000, 9_999)).toBe('red');
    expect(dwellLevel(1_300_000, 1_000_000, 9_999)).toBe('yellow');
    expect(dwellLevel(1_000_000, 1_000_000, 9_999)).toBeNull();
    // 未封口（仍在该环节）与无预计不参与比较
    expect(dwellLevel(9_999_999, 1_000, null)).toBeNull();
    expect(dwellLevel(9_999_999, null, 9_999)).toBeNull();
    expect(dwellLevel(0, 1_000, 9_999)).toBeNull();
  });
});
