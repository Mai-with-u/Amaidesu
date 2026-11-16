// 环节横幅数据归一化的测试（纯函数）
//
// 覆盖：执行事实与计划环节合成有序行、当前段判定（未封口 / 回看末段）、
// 实时快照覆盖当前段已播时长、超出预计标记、没开场次时退到模板环节。

import { describe, expect, it } from 'vitest';

import { buildRundownRows, rundownActionLabel, rundownActorLabel } from '@/utils/rundownConsole';
import type {
  RundownPlannedSegment,
  RundownSegmentRun,
  RundownSnapshot,
  SessionRundownExecutionResponse,
} from '@/types';

const BASE = 1_700_000_000_000;

function run(partial: Partial<RundownSegmentRun>): RundownSegmentRun {
  return {
    seg_id: 's1',
    seg_title: '开场交代',
    seg_index: 0,
    entered_at_ms: BASE,
    left_at_ms: null,
    dwell_ms: 0,
    expected_ms: 300_000,
    pause_ms: 0,
    pause_count: 0,
    by: 'system',
    action: 'begin',
    ...partial,
  };
}

function planned(partial: Partial<RundownPlannedSegment>): RundownPlannedSegment {
  return {
    seq: 0,
    seg_id: 's1',
    title: '开场交代',
    task_description: '交代今天的玩法',
    expected_ms: 300_000,
    min_duration_ms: null,
    notes: null,
    mode: null,
    beats: [],
    ...partial,
  };
}

function execution(
  partial: Partial<SessionRundownExecutionResponse>,
): SessionRundownExecutionResponse {
  return {
    live_session_id: 67,
    rundown_id: 'minecraft_session',
    segments: [],
    planned_segments: [],
    pause_total_ms: 0,
    transitions_count: 0,
    ...partial,
  };
}

describe('buildRundownRows', () => {
  it('走过的段在前、没到的段按计划顺序在后', () => {
    const rows = buildRundownRows(
      execution({
        segments: [
          run({ seg_id: 's1', seg_index: 0, left_at_ms: BASE + 300_000, dwell_ms: 300_000 }),
          run({
            seg_id: 's2',
            seg_index: 1,
            seg_title: '推进主段',
            left_at_ms: null,
            dwell_ms: 0,
            expected_ms: 2_400_000,
            by: 'agent',
            action: 'next',
          }),
        ],
        planned_segments: [
          planned({ seq: 0 }),
          planned({ seq: 1, seg_id: 's2', title: '推进主段', expected_ms: 2_400_000 }),
          planned({ seq: 2, seg_id: 's3', title: '应变处置', expected_ms: 600_000 }),
        ],
      }),
      null,
    );

    expect(rows.map(row => [row.segId, row.state])).toEqual([
      ['s1', 'done'],
      ['s2', 'current'],
      ['s3', 'upcoming'],
    ]);
    expect(rows[1].by).toBe('agent');
    expect(rows[2].expectedMs).toBe(600_000);
  });

  it('回看模式：末段已封口时作为"停在这段"', () => {
    const rows = buildRundownRows(
      execution({
        segments: [
          run({ seg_id: 's1', left_at_ms: BASE + 300_000, dwell_ms: 300_000 }),
          run({
            seg_id: 's2',
            seg_index: 1,
            left_at_ms: BASE + 900_000,
            dwell_ms: 600_000,
            expected_ms: 2_400_000,
          }),
        ],
      }),
      null,
    );

    expect(rows.map(row => row.state)).toEqual(['done', 'current']);
    expect(rows[1].dwellMs).toBe(600_000);
  });

  it('实时快照覆盖当前段的已播时长与提纲要点', () => {
    const snapshot: RundownSnapshot = {
      status: 'running',
      rundown_id: 'rd-1',
      title: '游戏场',
      current: {
        id: 's2',
        title: '推进主段',
        expected_ms: 2_400_000,
        elapsed_ms: 700_000,
        remaining_ms: 1_700_000,
        beats: ['讲清楚目标'],
      },
      index: 1,
      total: 4,
      progress_percent: 25,
      paused: false,
    };

    const rows = buildRundownRows(
      execution({
        segments: [
          run({ seg_id: 's1', left_at_ms: BASE + 300_000, dwell_ms: 300_000 }),
          run({ seg_id: 's2', seg_index: 1, left_at_ms: null, expected_ms: 2_400_000 }),
        ],
        planned_segments: [
          planned({ seq: 0 }),
          planned({ seq: 1, seg_id: 's2', title: '推进主段', expected_ms: 2_400_000 }),
        ],
      }),
      snapshot,
    );

    expect(rows[1].state).toBe('current');
    expect(rows[1].dwellMs).toBe(700_000);
    expect(rows[1].beats).toEqual(['讲清楚目标']);
  });

  it('停留超出预计时长时标记出来', () => {
    const rows = buildRundownRows(
      execution({
        segments: [run({ left_at_ms: BASE + 400_000, dwell_ms: 400_000, expected_ms: 300_000 })],
      }),
      null,
    );

    expect(rows[0].overExpected).toBe(true);
  });

  it('暂停次数与暂停时长随段带出', () => {
    const rows = buildRundownRows(
      execution({
        segments: [
          run({
            left_at_ms: BASE + 400_000,
            dwell_ms: 400_000,
            pause_ms: 60_000,
            pause_count: 2,
          }),
        ],
      }),
      null,
    );

    expect(rows[0].pauseCount).toBe(2);
    expect(rows[0].pauseMs).toBe(60_000);
  });

  it('没有执行事实时退到模板环节 + 实时快照的当前段', () => {
    const snapshot: RundownSnapshot = {
      status: 'running',
      rundown_id: 'rd-1',
      title: '游戏场',
      current: {
        id: 's1',
        title: '开场交代',
        expected_ms: 300_000,
        elapsed_ms: 30_000,
        remaining_ms: 270_000,
      },
      index: 0,
      total: 2,
      progress_percent: 0,
      paused: false,
    };

    const rows = buildRundownRows(null, snapshot, [
      // 模板环节用的是流程单环节定义（id 而不是 seg_id）
      {
        id: 's1',
        title: '开场交代',
        task_description: '交代玩法',
        expected_ms: 300_000,
        min_duration_ms: null,
        notes: null,
        mode: null,
        beats: ['说清玩法'],
      },
      {
        id: 's2',
        title: '推进主段',
        task_description: '',
        expected_ms: 2_400_000,
        min_duration_ms: null,
        notes: null,
        mode: null,
        beats: [],
      },
    ]);

    expect(rows.map(row => [row.segId, row.state])).toEqual([
      ['s1', 'current'],
      ['s2', 'upcoming'],
    ]);
    expect(rows[0].dwellMs).toBe(30_000);
    expect(rows[0].beats).toEqual(['说清玩法']);
  });

  it('什么都没有时返回空列表', () => {
    expect(buildRundownRows(null, null)).toEqual([]);
  });
});

describe('触发方与动作文案', () => {
  it('谁切的', () => {
    expect(rundownActorLabel('agent')).toBe('Agent');
    expect(rundownActorLabel('human')).toBe('你');
    expect(rundownActorLabel('system')).toBe('系统');
    expect(rundownActorLabel('')).toBe('');
  });

  it('什么动作进的', () => {
    expect(rundownActionLabel('begin')).toBe('开始');
    expect(rundownActionLabel('next')).toBe('推进');
    expect(rundownActionLabel('restore')).toBe('恢复');
    expect(rundownActionLabel('unknown_action')).toBe('unknown_action');
  });
});
