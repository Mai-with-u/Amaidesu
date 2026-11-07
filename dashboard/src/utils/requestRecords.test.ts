// requestRecords.ts 请求记录结构的测试
//
// 覆盖四件事：一轮的条目归到同一记录、工具卡拆成调用与结果两行、
// 记录内按角色排序（输入在前、产出在后）、无轮次条目合成零散记录。

import { describe, expect, it } from 'vitest';

import { makeEntry, type ShowEntry } from '@/utils/liveFeed';
import { buildRequestRecords, triggerLabel } from '@/utils/requestRecords';

function entry(patch: Parameters<typeof makeEntry>[0]): ShowEntry {
  return makeEntry(patch);
}

describe('buildRequestRecords 归组', () => {
  it('同一 round_id 的条目合成一条记录，输入排在生产之前', () => {
    const entries = [
      entry({
        id: 's1',
        kind: 'speech',
        tsMs: 1400,
        actor: '主播',
        text: '接住了',
        roundId: 'rnd_1',
      }),
      entry({
        id: 't1',
        kind: 'tool',
        tsMs: 1200,
        actor: 'minecraft_glance',
        text: 'what="self"',
        roundId: 'rnd_1',
        detail: { args: { what: 'self' }, result: { health: 5 } },
      }),
      entry({
        id: 'd1',
        kind: 'decision',
        tsMs: 1300,
        actor: '决策',
        text: '决定发言',
        roundId: 'rnd_1',
        detail: {
          should_reply: true,
          reply_guidance: '把话接住',
          batch: [{ user_name: '张三', text: '在看' }],
          planner_duration_ms: 12000,
        },
      }),
    ];

    const [record] = buildRequestRecords(entries);
    expect(record.roundId).toBe('rnd_1');
    expect(record.outcome).toBe('reply');
    expect(record.durationMs).toBe(12000);
    expect(record.messages.map(message => message.kind)).toEqual([
      'batch',
      'call',
      'result',
      'final',
    ]);
    expect(record.messages[0].text).toBe('张三：在看');
    expect(record.messages[1].toolName).toBe('minecraft_glance');
    expect(record.toolCount).toBe(1);
  });

  it('裁决记录把 reply 调用与议题摘要一起显示', () => {
    const entries = [
      entry({
        id: 'v1',
        kind: 'verdict',
        tsMs: 1000,
        actor: '决策',
        text: '回应',
        roundId: 'rnd_2',
        detail: { topic_summary: '接住她的玩笑', reply_guidance: '轻松点' },
      }),
    ];
    const [record] = buildRequestRecords(entries);
    expect(record.outcome).toBe('reply');
    const call = record.messages.find(message => message.kind === 'call');
    expect(call?.toolName).toBe('reply');
    expect(call?.text).toBe('接住她的玩笑');
  });

  it('沉默轮没有 final，只有一条说明', () => {
    const entries = [
      entry({
        id: 'd2',
        kind: 'decision',
        tsMs: 1000,
        actor: '决策',
        text: '沉默',
        roundId: 'rnd_3',
        detail: { should_reply: false, silent_reason: '没什么可接的' },
      }),
    ];
    const [record] = buildRequestRecords(entries);
    expect(record.outcome).toBe('silent');
    expect(record.messages.map(message => message.kind)).toEqual(['note']);
    expect(record.messages[0].text).toBe('没什么可接的');
  });

  it('无轮次条目合成零散记录，间隔超过五分钟另起一条', () => {
    const entries = [
      entry({ id: 's2', kind: 'speech', tsMs: 1000, actor: '主播', text: '第一句' }),
      entry({ id: 's3', kind: 'speech', tsMs: 60_000, actor: '主播', text: '第二句' }),
      entry({ id: 's4', kind: 'speech', tsMs: 900_000, actor: '主播', text: '很久以后' }),
    ];
    const records = buildRequestRecords(entries);
    expect(records).toHaveLength(2);
    expect(records[0].loose).toBe(true);
    expect(records[0].messages.map(message => message.text)).toEqual(['第一句', '第二句']);
    expect(records[1].messages.map(message => message.text)).toEqual(['很久以后']);
  });

  it('阶段、系统、观众消息不进入请求记录', () => {
    const entries = [
      entry({ id: 'g1', kind: 'stage', tsMs: 1000, actor: '阶段', text: '决策中' }),
      entry({ id: 'm1', kind: 'danmaku', tsMs: 1100, actor: '张三', text: '在吗' }),
    ];
    expect(buildRequestRecords(entries)).toHaveLength(0);
  });
});

describe('triggerLabel', () => {
  it('机器标识转成人话', () => {
    expect(triggerLabel('proactive')).toBe('主动');
    expect(triggerLabel('forced')).toBe('强制');
    expect(triggerLabel('dashboard:debug_test')).toBe('调试');
    expect(triggerLabel('proactive:silence')).toBe('主动·silence');
    expect(triggerLabel('')).toBe('');
  });
});
