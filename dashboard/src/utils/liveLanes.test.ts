// liveLanes.ts：双轨的轨道归属 / 环节分段 / 派活生命期。
//
// 轨道只决定左右位置：游戏 Agent 的条目及其世界事件进游戏轨，其余进现场轨，
// 任何条目都不会因为分轨而消失。分段的边界只来自流程单（真实环节），
// 不用固定时间宽度硬切。

import { describe, expect, it } from 'vitest';

import {
  buildLaneSections,
  buildTaskWindows,
  delegationOfReply,
  delegationWindows,
  laneOf,
  taskOfEntry,
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
      {
        taskId: 'g1',
        status: 'accepted',
        tsMs: 1_000,
        summary: '已受理',
        ability: 'maicraft:travel',
      },
      {
        taskId: 'g1',
        status: 'succeeded',
        tsMs: 13_400,
        summary: '走到了目的地',
        ability: 'maicraft:travel',
      },
    ]);
    expect(windows).toHaveLength(1);
    expect(windows[0]).toMatchObject({
      taskId: 'g1',
      startMs: 1_000,
      endMs: 13_400,
      statusLabel: '完成',
      label: 'maicraft:travel',
    });
  });

  it('只有终态回执的任务不成区间（旧数据没有起点，画出来是零长度假分组）', () => {
    expect(
      buildTaskWindows([
        { taskId: 'g2', status: 'succeeded', tsMs: 5_000, summary: '完成', ability: '' },
      ]),
    ).toHaveLength(0);
  });

  it('按开始时刻排序，且条目能找回自己所在的区间', () => {
    const windows = buildTaskWindows([
      { taskId: 'b', status: 'accepted', tsMs: 9_000, summary: '', ability: 'maicraft:find' },
      { taskId: 'b', status: 'failed', tsMs: 9_800, summary: '', ability: '' },
      { taskId: 'a', status: 'accepted', tsMs: 1_000, summary: '', ability: 'maicraft:travel' },
      { taskId: 'a', status: 'cancelled', tsMs: 4_000, summary: '', ability: '' },
    ]);
    expect(windows.map(window => window.taskId)).toEqual(['a', 'b']);
    expect(windows[1].statusLabel).toBe('失败');
    expect(
      taskOfEntry(
        entryOf({ id: 'e1', tsMs: 9_500, kind: 'tool', actor: 'maicraft_execute' }),
        windows,
      )?.taskId,
    ).toBe('b');
    expect(
      taskOfEntry(
        entryOf({ id: 'e2', tsMs: 20_000, kind: 'tool', actor: 'maicraft_execute' }),
        windows,
      ),
    ).toBeNull();
  });
});
