// liveLanes.ts：双轨的轨道归属 / 环节分段 / 收活卡配对 / 任务区间。
//
// 轨道只决定左右位置：游戏 Agent 的条目及其世界事件进游戏轨，其余进现场轨，
// 任何条目都不会因为分轨而消失。分段的边界只来自流程单（真实环节），
// 不用固定时间宽度硬切。

import { describe, expect, it } from 'vitest';

import {
  annotateTaskEnds,
  buildDelegateReceipts,
  buildLaneSections,
  buildLeadSpans,
  buildTaskWindows,
  collapseGoalPolls,
  laneOf,
  leadActorAt,
  type TaskFact,
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
    expect(
      laneOf(entryOf({ id: 'rc', tsMs: 1, kind: 'delegate_receipt', actor: '游戏 Agent' })),
    ).toBe('game');
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

/** framework_delegate 派活卡条目：detail 里带线上形状的入参/结果（result.task_id 是收活配对键） */
function delegateEntry(
  id: string,
  tsMs: number,
  taskId: string,
  instruction = '去村里买面包',
): ShowEntry {
  return makeEntry({
    id,
    kind: 'tool',
    tsMs,
    actor: 'framework_delegate',
    text: '',
    detail: {
      args: { agent: 'minecraft', instruction },
      result: { accepted: true, task_id: taskId },
    },
  });
}

describe('收活卡配对（buildDelegateReceipts）', () => {
  const accepted = (taskId: string, tsMs: number): TaskFact => ({
    taskId,
    status: 'accepted',
    tsMs,
  });

  it('受理回执按 task_id 配到派活卡，合成收活卡并按受理时刻插进时间线', () => {
    const entries = [
      delegateEntry('dele1', 1_000, 'deleg_1', '去村里买面包'),
      entryOf({ id: 's1', tsMs: 3_000, kind: 'speech', actor: '主播', text: '话' }),
    ];
    const merged = buildDelegateReceipts(entries, [accepted('deleg_1', 2_000)]);

    expect(merged.map(entry => entry.id)).toEqual(['dele1', 'delegate_receipt:deleg_1', 's1']);
    const receipt = merged[1]!;
    expect(receipt.kind).toBe('delegate_receipt');
    expect(receipt.text).toBe('去村里买面包');
    expect(receipt.delegateEntryId).toBe('dele1');
    expect(receipt.delegateTsMs).toBe(1_000);
  });

  it('指令摘要超过 20 字截断', () => {
    const long = '这是一条非常长的派活指令，需要被截断以保持收活卡的轻量形态';
    const merged = buildDelegateReceipts(
      [delegateEntry('dele', 1_000, 'deleg_2', long)],
      [accepted('deleg_2', 2_000)],
    );
    expect(merged[1]!.text).toBe(`${long.slice(0, 20)}…`);
  });

  it('受理配不到派活卡时不渲染收活卡', () => {
    const entries = [delegateEntry('dele1', 1_000, 'deleg_1')];
    const merged = buildDelegateReceipts(entries, [accepted('deleg_404', 2_000)]);
    expect(merged).toEqual(entries);
  });

  it('终态回执不上收活卡（终态由 execute 卡徽标承载）', () => {
    const entries = [delegateEntry('dele1', 1_000, 'deleg_1')];
    const merged = buildDelegateReceipts(entries, [
      { taskId: 'deleg_1', status: 'succeeded', tsMs: 5_000 },
    ]);
    expect(merged).toEqual(entries);
  });

  it('两次派活时间交错时按身份配对，不按时间近邻猜（受理顺序与派活顺序相反也不串）', () => {
    // 派活甲 1s / 派活乙 2s；游戏 Agent 乙先受理（2.5s）、甲后受理（3s）。
    // 时间近邻会把甲的受理猜给乙；身份配对各自落到正确的派活卡
    const entries = [
      delegateEntry('dele_a', 1_000, 'deleg_a', '任务甲'),
      delegateEntry('dele_b', 2_000, 'deleg_b', '任务乙'),
    ];
    const merged = buildDelegateReceipts(entries, [
      accepted('deleg_b', 2_500),
      accepted('deleg_a', 3_000),
    ]);

    const receipts = merged.filter(entry => entry.kind === 'delegate_receipt');
    expect(receipts.map(entry => entry.delegateEntryId)).toEqual(['dele_b', 'dele_a']);
    expect(receipts.map(entry => entry.text)).toEqual(['任务乙', '任务甲']);
    expect(merged.map(entry => entry.tsMs)).toEqual(
      merged.map(entry => entry.tsMs).sort((a, b) => a - b),
    );
  });

  it('同一任务号的重复受理只出一张收活卡', () => {
    const entries = [delegateEntry('dele1', 1_000, 'deleg_1')];
    const merged = buildDelegateReceipts(entries, [
      accepted('deleg_1', 2_000),
      accepted('deleg_1', 2_500),
    ]);
    expect(merged.filter(entry => entry.kind === 'delegate_receipt')).toHaveLength(1);
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
      result: {
        ok: true,
        data: { goal_id: goalId, state: 'finished', result: { summary: '到了' } },
      },
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
    expect(annotated!.taskEnd).toMatchObject({
      statusLabel: '失败',
      durationMs: 3_000,
      failed: true,
    });
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

describe('主控时段（buildLeadSpans / leadActorAt）', () => {
  it('每条 lead_change 开启一段（actor=切权后主控方），同主控方连续切权并入同段', () => {
    const entries = [
      entryOf({ id: 'a', tsMs: 1_000, kind: 'danmaku' }),
      entryOf({ id: 'l1', tsMs: 2_000, kind: 'lead_change', actor: 'minecraft' }),
      entryOf({ id: 'l2', tsMs: 3_000, kind: 'lead_change', actor: 'minecraft' }),
      entryOf({ id: 'l3', tsMs: 4_000, kind: 'lead_change', actor: 'streamer' }),
    ];
    expect(buildLeadSpans(entries)).toEqual([
      { actor: 'minecraft', startMs: 2_000, endMs: 3_000 },
      { actor: 'streamer', startMs: 4_000, endMs: 4_000 },
    ]);
  });

  it('流里没有切权条目时返回空数组', () => {
    expect(buildLeadSpans([entryOf({ id: 'a', tsMs: 1, kind: 'speech' })])).toEqual([]);
  });

  it('leadActorAt：时刻之前最近一段的主控方，无切权信息回退主播', () => {
    const spans = buildLeadSpans([
      entryOf({ id: 'l1', tsMs: 2_000, kind: 'lead_change', actor: 'minecraft' }),
      entryOf({ id: 'l2', tsMs: 5_000, kind: 'lead_change', actor: 'streamer' }),
    ]);
    expect(leadActorAt([], 10_000)).toBe('streamer');
    expect(leadActorAt(spans, 1_000)).toBe('streamer');
    expect(leadActorAt(spans, 2_000)).toBe('minecraft');
    expect(leadActorAt(spans, 4_999)).toBe('minecraft');
    expect(leadActorAt(spans, 5_000)).toBe('streamer');
  });
});
