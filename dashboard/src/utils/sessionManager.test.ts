// utils/sessionManager.ts 场次显示口径的测试
//
// 覆盖：来源/类型显示名与选项、场次时长标签、整理状态映射、清单汇总、日历热力图。

import { describe, expect, it } from 'vitest';

import {
  SESSION_FILTER_ALL,
  SESSION_KIND_OPTIONS,
  SESSION_SOURCE_OPTIONS,
  buildHeatmap,
  defaultSessionTitle,
  heatmapLevel,
  kindLabel,
  sessionDurationLabel,
  settleStateOf,
  sourceLabel,
  summarizeSessions,
} from '@/utils/sessionManager';
import type { LiveSessionItem, SessionKind } from '@/types';

function item(partial: Partial<LiveSessionItem>): LiveSessionItem {
  return {
    live_session_id: 1,
    source: 'manual',
    kind: 'live',
    title: null,
    notes: null,
    room_id: 'room',
    platform: 'bilibili',
    started_at_ms: 1_000_000,
    ended_at_ms: null,
    message_count: 0,
    is_active: false,
    settled_at_ms: null,
    settle_attempts: 0,
    reviewed_at_ms: null,
    overview: null,
    ...partial,
  };
}

describe('来源与类型的取值与显示名', () => {
  it('显示名与选项来自同一份字典', () => {
    expect(sourceLabel('manual')).toBe('手动');
    expect(sourceLabel('replay')).toBe('回放');
    expect(sourceLabel('generate')).toBe('模拟');
    expect(sourceLabel('legacy')).toBe('历史');
    // 认不出来的来源原样透出
    expect(sourceLabel('other')).toBe('other');

    expect(kindLabel('live')).toBe('正式');
    expect(kindLabel('rehearsal')).toBe('彩排');

    expect(SESSION_SOURCE_OPTIONS.map(opt => opt.value)).toEqual([
      'manual',
      'replay',
      'generate',
      'legacy',
    ]);
    expect(SESSION_KIND_OPTIONS.map(opt => opt.value)).toEqual(['live', 'rehearsal']);
    expect(SESSION_FILTER_ALL).toBe('all');
  });

  it('默认标题带日期与星期', () => {
    const title = defaultSessionTitle(new Date(2026, 10, 14, 21, 42));
    expect(title).toBe('2026-11-14 周六 21:42');
  });
});

describe('sessionDurationLabel', () => {
  it('进行中场次给已播时长（随时钟走）', () => {
    const active = item({ is_active: true, started_at_ms: 1_000_000 });
    expect(sessionDurationLabel(active, 1_121_000)).toBe('已播 0:02:01');
  });

  it('已结束场次给用时（固定带小时位）', () => {
    const ended = item({ started_at_ms: 1_000_000, ended_at_ms: 1_180_000 });
    expect(sessionDurationLabel(ended, 9_999_999)).toBe('用时 0:03:00');
  });

  it('缺结束时刻且不是进行中不显示；起止倒挂也不显示', () => {
    expect(sessionDurationLabel(item({}), 1_000_000)).toBeNull();
    expect(
      sessionDurationLabel(item({ started_at_ms: 2_000_000, ended_at_ms: 1_000_000 }), 3_000_000),
    ).toBeNull();
  });
});

describe('settleStateOf', () => {
  it('按整理三列给出状态标签', () => {
    expect(settleStateOf(item({})).label).toBe('待整理');
    expect(settleStateOf(item({ settle_attempts: 2 })).label).toBe('整理失败');
    expect(settleStateOf(item({ settled_at_ms: 5_000 })).label).toBe('已整理待审');
    expect(settleStateOf(item({ settled_at_ms: 5_000, reviewed_at_ms: 6_000 })).label).toBe('已审');
    // 已审优先于其它状态
    expect(settleStateOf(item({ settle_attempts: 3, reviewed_at_ms: 6_000 })).label).toBe('已审');
  });
});

describe('summarizeSessions', () => {
  it('汇总场次数、时长、消息数与类型来源分布', () => {
    const items = [
      item({ live_session_id: 1, started_at_ms: 0, ended_at_ms: 3_600_000, message_count: 10 }),
      item({
        live_session_id: 2,
        source: 'replay',
        kind: 'rehearsal',
        started_at_ms: 0,
        ended_at_ms: 1_800_000,
        message_count: 5,
      }),
      item({
        live_session_id: 3,
        source: 'replay',
        started_at_ms: 0,
        is_active: true,
        message_count: 1,
      }),
    ];
    const summary = summarizeSessions(items, 3_600_000);
    expect(summary.count).toBe(3);
    // 总时长 = 1 小时 + 30 分 + 1 小时（进行中按当前时刻算）
    expect(summary.totalDurationMs).toBe(3_600_000 + 1_800_000 + 3_600_000);
    expect(summary.averageDurationMs).toBe(3_000_000);
    expect(summary.longestDurationMs).toBe(3_600_000);
    expect(summary.messageTotal).toBe(16);
    expect(summary.kindCounts).toEqual({ live: 2, rehearsal: 1 } satisfies Record<
      SessionKind,
      number
    >);
    expect(summary.sourceCounts).toEqual({ manual: 1, replay: 2 });
  });

  it('空清单给零值', () => {
    const summary = summarizeSessions([], 1_000);
    expect(summary.count).toBe(0);
    expect(summary.averageDurationMs).toBe(0);
    expect(summary.totalDurationMs).toBe(0);
  });
});

describe('buildHeatmap', () => {
  const day = (d: number, h = 21) => new Date(2026, 10, d, h, 0).getTime();

  it('按周排成列、周一在上；场次落在当周对应的那一格', () => {
    // 2026-11-09 是周一，窗口往前两周 = 2026-11-01
    const now = day(15, 12);
    const heat = buildHeatmap(
      [
        item({ live_session_id: 1, started_at_ms: day(11), ended_at_ms: day(11) + 600_000 }),
        item({
          live_session_id: 2,
          started_at_ms: day(11, 23),
          ended_at_ms: day(11, 23) + 600_000,
        }),
      ],
      now,
      { minWeeks: 2 },
    );
    // 三列：10-26 那周、11-02 那周、11-09 那周
    expect(heat.weeks).toHaveLength(3);
    const lastWeek = heat.weeks[2];
    // 周一起算，11-11 是周三 → 第三格
    expect(lastWeek.days[2]?.date).toBe('2026-11-11');
    expect(lastWeek.days[2]?.count).toBe(2);
    expect(lastWeek.days[2]?.durationMs).toBe(1_200_000);
    // 没播的日子也占格子（count 0），窗口外的日子留空
    expect(lastWeek.days[0]?.count).toBe(0);
    expect(heat.weeks[0].days[0]).toBeNull();
  });

  it('窗口至少铺满 minWeeks 周；更早的场次把窗口往前延（不藏数据）', () => {
    const now = day(15, 12);
    const short = buildHeatmap([], now, { minWeeks: 4 });
    // 窗口起点不一定落在周一，首列可能是半列——列数只会多不会少
    expect(short.weeks.length).toBeGreaterThanOrEqual(4);

    const long = buildHeatmap(
      [item({ live_session_id: 1, started_at_ms: new Date(2026, 0, 5, 20, 0).getTime() })],
      now,
      { minWeeks: 4 },
    );
    // 最早那场在 1 月，窗口一路延过去
    expect(long.weeks.length).toBeGreaterThan(40);
    expect(long.weeks[0].days.some(day => day?.count === 1)).toBe(true);
  });

  it('月份标签只在换月的那一列出现', () => {
    const heat = buildHeatmap([], day(15, 12), { minWeeks: 8 });
    const labels = heat.weeks.map(week => week.monthLabel).filter(Boolean);
    expect(labels.length).toBeGreaterThan(0);
    expect(labels).toContain('11月');
  });
});

describe('heatmapLevel', () => {
  it('没播是 0，场次越多档位越高（4 场封顶）', () => {
    expect(heatmapLevel(null)).toBe(0);
    expect(heatmapLevel({ date: '2026-11-11', count: 0, durationMs: 0 })).toBe(0);
    expect(heatmapLevel({ date: '2026-11-11', count: 1, durationMs: 0 })).toBe(1);
    expect(heatmapLevel({ date: '2026-11-11', count: 3, durationMs: 0 })).toBe(3);
    expect(heatmapLevel({ date: '2026-11-11', count: 9, durationMs: 0 })).toBe(4);
  });
});
