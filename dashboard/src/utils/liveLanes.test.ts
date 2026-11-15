// liveLanes.ts：双轨的轨道归属 / 环节分段 / 派活生命期。
//
// 轨道只决定左右位置：游戏 Agent 的条目及其世界事件进游戏轨，其余进现场轨，
// 任何条目都不会因为分轨而消失。分段的边界只来自流程单（真实环节），
// 不用固定时间宽度硬切。

import { describe, expect, it } from 'vitest';

import {
  annotateTaskEnds,
  buildLaneSections,
  buildTaskWindows,
  collapseGoalPolls,
  delegationOfReply,
  delegationWindows,
  laneOf,
} from '@/utils/liveLanes';
import { makeEntry, type ShowEntry } from '@/utils/liveFeed';

function entryOf(patch: Partial<ShowEntry> & { id: string; tsMs: number }): ShowEntry {
  return makeEntry({
    kind: 'speech',
    actor: '主播',
    text: '',
    ...patch,
  } as Parameters<typeof makeEntry>[0]);
}

describe('laneOf', () => {
  it('游戏世界事件与游戏 Agent 回执进游戏轨', () => {
    expect(laneOf(entryOf({ id: 'g', tsMs: 1, kind: 'game', actor: 'minecraft' }))).toBe('game');
    expect(laneOf(entryOf({ id: 'a', tsMs: 1, kind: 'agent_reply', actor: '游戏 Agent' }))).toBe(
      'game',
    );
  });

  it('工具按调用来源分轨：游戏 Agent 命名下调用的进游戏轨，主播侧调用留在现场轨', () => {
    expect(
      laneOf(
        entryOf({
          id: 't1',
          tsMs: 1,
          kind: 'tool',
          actor: 'maicraft_execute',
          source: '游戏 Agent',
        }),
      ),
    ).toBe('game');
    expect(
      laneOf(
        entryOf({ id: 't2', tsMs: 1, kind: 'tool', actor: 'streamer_reply', source: '主播决策' }),
      ),
    ).toBe('stage');
  });

  it('发言、决策、观众消息都留在现场轨', () => {
    for (const kind of ['speech', 'decision', 'danmaku'] as const) {
      expect(laneOf(entryOf({ id: kind, tsMs: 1, kind }))).toBe('stage');
    }
  });
});

describe('buildLaneSections', () => {
  it('流程单条目切段，段标题用环节名，流程单自身归入新段', () => {
    const entries = [
      entryOf({ id: 'd1', tsMs: 1_000, kind: 'danmaku', actor: '观众甲', text: '在吗' }),
      entryOf({ id: 'r1', tsMs: 2_000, kind: 'rundown', actor: '流程单', text: '开场交代' }),
      entryOf({ id: 's1', tsMs: 3_000, kind: 'speech', actor: '主播', text: '在' }),
      entryOf({ id: 'r2', tsMs: 4_000, kind: 'rundown', actor: '流程单', text: '打怪' }),
    ];
    const sections = buildLaneSections(entries);

    expect(sections.map(section => section.title)).toEqual(['开场前', '开场交代', '打怪']);
    expect(sections[0].stage.map(entry => entry.id)).toEqual(['d1']);
    expect(sections[1].stage.map(entry => entry.id)).toEqual(['r1', 's1']);
    expect(sections[2].stage.map(entry => entry.id)).toEqual(['r2']);
  });

  it('段尾时间跟着段内最后一个条目走，空段不产出', () => {
    const entries = [
      entryOf({ id: 'r1', tsMs: 1_000, kind: 'rundown', actor: '流程单', text: '甲' }),
      entryOf({ id: 's1', tsMs: 5_000, kind: 'speech', actor: '主播', text: '话' }),
      entryOf({ id: 'r2', tsMs: 9_000, kind: 'rundown', actor: '流程单', text: '乙' }),
      entryOf({ id: 'r3', tsMs: 10_000, kind: 'rundown', actor: '流程单', text: '丙' }),
    ];
    const sections = buildLaneSections(entries);

    expect(sections.map(section => section.title)).toEqual(['甲', '乙', '丙']);
    expect(sections[0].endMs).toBe(5_000);
    // 乙、丙 之间的空段不存在（丙 直接把 乙 收段）
    expect(sections[1].stage.map(entry => entry.id)).toEqual(['r2']);
  });

  it('段内两条轨道分开装，游戏条目不会串到现场列', () => {
    const entries = [
      entryOf({ id: 'r1', tsMs: 1_000, kind: 'rundown', actor: '流程单', text: '甲' }),
      entryOf({ id: 's1', tsMs: 2_000, kind: 'speech', actor: '主播', text: '话' }),
      entryOf({ id: 'g1', tsMs: 3_000, kind: 'game', actor: 'minecraft', text: '掉血' }),
    ];
    const [section] = buildLaneSections(entries);

    expect(section.stage.map(entry => entry.id)).toEqual(['r1', 's1']);
    expect(section.game.map(entry => entry.id)).toEqual(['g1']);
  });

  it('窗口里有流程单条目时头桶沿用"开场前"（向后兼容）', () => {
    const entries = [
      entryOf({ id: 'd1', tsMs: 1_000, kind: 'danmaku', actor: '观众甲', text: '在吗' }),
      entryOf({ id: 'r1', tsMs: 2_000, kind: 'rundown', actor: '流程单', text: '打怪' }),
    ];
    const sections = buildLaneSections(entries, [{ title: '打怪', startMs: 500, endMs: 9_000 }]);

    expect(sections.map(section => section.title)).toEqual(['开场前', '打怪']);
  });

  it('窗口里没有流程单条目时按窗口起点所在环节取名', () => {
    const entries = [
      entryOf({ id: 's1', tsMs: 3_000, kind: 'speech', actor: '主播', text: '话' }),
      entryOf({ id: 's2', tsMs: 4_000, kind: 'speech', actor: '主播', text: '话' }),
    ];
    const sections = buildLaneSections(entries, [
      { title: '开场交代', startMs: 0, endMs: 2_000 },
      { title: '打怪', startMs: 2_000, endMs: null },
    ]);

    expect(sections).toHaveLength(1);
    expect(sections[0]!.title).toBe('打怪');
  });

  it('查不到环节边界时用中性文案"更早内容"（实时模式无边界信息渠道时沿用"开场前"）', () => {
    const entries = [entryOf({ id: 's1', tsMs: 3_000, kind: 'speech', actor: '主播', text: '话' })];

    expect(buildLaneSections(entries, [])[0]!.title).toBe('更早内容');
    expect(buildLaneSections(entries)[0]!.title).toBe('开场前');
  });
});

describe('派活生命期', () => {
  it('派活窗口止于下一次派活，回报按时间归入窗口', () => {
    const entries = [
      entryOf({ id: 'dele1', tsMs: 1_000, kind: 'tool', actor: 'framework_delegate' }),
      entryOf({ id: 'reply1', tsMs: 2_000, kind: 'agent_reply', actor: '游戏 Agent' }),
      entryOf({ id: 'reply2', tsMs: 3_000, kind: 'agent_reply', actor: '游戏 Agent' }),
      entryOf({ id: 'dele2', tsMs: 4_000, kind: 'tool', actor: 'framework_delegate' }),
      entryOf({ id: 'reply3', tsMs: 5_000, kind: 'agent_reply', actor: '游戏 Agent' }),
    ];
    const windows = delegationWindows(entries);

    expect(windows).toHaveLength(2);
    expect(windows[0].replies).toBe(2);
    expect(windows[1].replies).toBe(1);
    expect(delegationOfReply(windows, entries[1]!)?.delegate.id).toBe('dele1');
    expect(delegationOfReply(windows, entries[4]!)?.delegate.id).toBe('dele2');
  });

  it('最后一次派活以最后一条回报收尾，之后没有回报时窗口收在下达时刻', () => {
    const withReply = [
      entryOf({ id: 'dele', tsMs: 1_000, kind: 'tool', actor: 'framework_delegate' }),
      entryOf({ id: 'reply', tsMs: 7_000, kind: 'agent_reply', actor: '游戏 Agent' }),
    ];
    expect(delegationWindows(withReply)[0]!.endMs).toBe(7_001);

    const lonely = [
      entryOf({ id: 'dele', tsMs: 1_000, kind: 'tool', actor: 'framework_delegate' }),
    ];
    expect(delegationWindows(lonely)[0]!.endMs).toBe(1_001);
  });

  it('非回报条目、窗口外的回报都不归任何派活', () => {
    const entries = [
      entryOf({ id: 'dele', tsMs: 5_000, kind: 'tool', actor: 'framework_delegate' }),
      entryOf({ id: 'early', tsMs: 1_000, kind: 'agent_reply', actor: '游戏 Agent' }),
      entryOf({ id: 'speech', tsMs: 6_000, kind: 'speech', actor: '主播', text: '话' }),
    ];
    const windows = delegationWindows(entries);

    expect(delegationOfReply(windows, entries[1]!)).toBeNull();
    expect(delegationOfReply(windows, entries[2]!)).toBeNull();
  });
});

describe('任务区间（task.changed 受理 + 终态）', () => {
  it('同一任务号的受理与终态合成带时长的区间', () => {
    const windows = buildTaskWindows([
      { taskId: 'maicraft-goal-7', status: 'accepted', tsMs: 1_000 },
      { taskId: 'maicraft-goal-7', status: 'succeeded', tsMs: 13_400 },
    ]);
    expect(windows).toHaveLength(1);
    expect(windows[0]).toMatchObject({
      taskId: 'maicraft-goal-7',
      startMs: 1_000,
      endMs: 13_400,
      statusLabel: '完成',
    });
  });

  it('只有终态回执的任务不成区间（旧数据没有起点，没有耗时可说）', () => {
    expect(
      buildTaskWindows([{ taskId: 'maicraft-goal-8', status: 'succeeded', tsMs: 5_000 }]),
    ).toHaveLength(0);
  });

  it('按开始时刻排序，失败/取消终态也成区间', () => {
    const windows = buildTaskWindows([
      { taskId: 'maicraft-goal-2', status: 'accepted', tsMs: 9_000 },
      { taskId: 'maicraft-goal-2', status: 'failed', tsMs: 9_800 },
      { taskId: 'maicraft-goal-1', status: 'accepted', tsMs: 1_000 },
      { taskId: 'maicraft-goal-1', status: 'cancelled', tsMs: 4_000 },
    ]);
    expect(windows.map(window => window.taskId)).toEqual(['maicraft-goal-1', 'maicraft-goal-2']);
    expect(windows[1]!.statusLabel).toBe('失败');
  });
});

/** maicraft_execute 卡条目：detail 里带线上形状的入参/结果（goal_id 是配对键） */
function executeEntry(id: string, tsMs: number, goalId: number): ShowEntry {
  return makeEntry({
    id,
    kind: 'tool',
    tsMs,
    actor: 'maicraft_execute',
    source: '游戏 Agent',
    text: '',
    detail: {
      args: { goal: { ability: 'maicraft:travel', purpose: '去村庄' } },
      result: { ok: true, data: { goal_id: goalId, state: 'finished', result: { summary: '到了' } } },
    },
  });
}

/** maicraft_goal 进度查询卡条目 */
function goalPollEntry(id: string, tsMs: number, goalId: number, operation = 'get'): ShowEntry {
  return makeEntry({
    id,
    kind: 'tool',
    tsMs,
    actor: 'maicraft_goal',
    source: '游戏 Agent',
    text: '',
    detail: { args: { operation, goal_id: goalId } },
  });
}

describe('任务终态回填到 execute 卡（annotateTaskEnds）', () => {
  const windows = buildTaskWindows([
    { taskId: 'maicraft-goal-7', status: 'accepted', tsMs: 1_000 },
    { taskId: 'maicraft-goal-7', status: 'succeeded', tsMs: 10_800 },
  ]);

  it('goal_id 配对成功的 execute 卡带上终态与耗时', () => {
    const execute = executeEntry('exec', 1_200, 7);
    const [annotated] = annotateTaskEnds([execute], windows);
    expect(annotated!.taskEnd).toEqual({
      statusLabel: '完成',
      durationMs: 9_800,
      failed: false,
    });
  });

  it('goal_id 配不上的 execute 卡维持原样（taskEnd 不写入）', () => {
    const execute = executeEntry('exec', 1_200, 99);
    const [annotated] = annotateTaskEnds([execute], windows);
    expect(annotated!.taskEnd).toBeUndefined();
    expect(annotated).toBe(execute);
  });

  it('失败终态标记 failed，徽标据此用红色', () => {
    const failWindows = buildTaskWindows([
      { taskId: 'maicraft-goal-3', status: 'accepted', tsMs: 1_000 },
      { taskId: 'maicraft-goal-3', status: 'failed', tsMs: 4_000 },
    ]);
    const [annotated] = annotateTaskEnds([executeEntry('exec', 1_100, 3)], failWindows);
    expect(annotated!.taskEnd).toMatchObject({ statusLabel: '失败', durationMs: 3_000, failed: true });
  });

  it('非 execute 条目不参与配对', () => {
    const danmaku = entryOf({ id: 'd', tsMs: 2_000, kind: 'danmaku', actor: '观众', text: '好' });
    const [annotated] = annotateTaskEnds([danmaku], windows);
    expect(annotated).toBe(danmaku);
  });
});

describe('进度查询收拢（collapseGoalPolls）', () => {
  it('goal_id 相等的 goal 查询卡挂到对应 execute 条目的 polls 上，其余条目留在原位', () => {
    const entries = [
      goalPollEntry('p0', 500, 7),
      executeEntry('exec', 1_000, 7),
      goalPollEntry('p1', 2_000, 7),
      goalPollEntry('p2', 3_000, 8),
      entryOf({ id: 'look', tsMs: 4_000, kind: 'tool', actor: 'maicraft_lookup', text: '' }),
    ];
    const result = collapseGoalPolls(entries);

    // execute 前出现的查询卡也按 goal_id 归属（身份配对，不看先后）
    expect(result.map(entry => entry.id)).toEqual(['exec', 'p2', 'look']);
    expect(result[0]!.polls!.map(poll => poll.id)).toEqual(['p0', 'p1']);
    // goal_id 对不上的查询卡留在原位，且不带 polls 字段（不是 execute 卡）
    expect(result[1]!.polls).toBeUndefined();
    expect(result[2]!.polls).toBeUndefined();
  });

  it('answer/cancel 等操作不是查询，不平铺收拢', () => {
    const entries = [
      executeEntry('exec', 1_000, 7),
      goalPollEntry('answer', 2_000, 7, 'answer'),
      goalPollEntry('cancel', 3_000, 7, 'cancel'),
    ];
    const result = collapseGoalPolls(entries);
    expect(result.map(entry => entry.id)).toEqual(['exec', 'answer', 'cancel']);
    expect(result[0]!.polls).toHaveLength(0);
  });
});
