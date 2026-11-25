// liveTools.ts 工具条目专用解析的测试
//
// 覆盖 maicraft_execute 与 framework_delegate 两类专用卡面的取值函数与条目判定。
// 条目样本经 liveFeed 的 makeEntry / toEntry 构造：专用解析的输入就是时间线条目。

import { describe, expect, it } from 'vitest';

import type { WsMessageType } from '@/types';
import { makeEntry, toEntry, type FeedEvent } from './liveFeed';
import {
  frameworkDelegateInstruction,
  frameworkDelegateSummary,
  isFrameworkDelegateCard,
  isFrameworkDelegateEntry,
  isMinecraftExecuteEntry,
  maicraftExecuteSummary,
} from './liveTools';

let seq = 0;

/** 构造 FeedEvent（events store 入口形状：WebSocketMessage + 去重 id）。
 *  type 允许合成词汇（unknown.type 类），显式断言 = 线上字符串按契约收口 */
function evt(type: string, data: Record<string, unknown>, tsMs = 1000): FeedEvent {
  seq += 1;
  return { id: `e${seq}`, type: type as WsMessageType, timestamp_ms: tsMs, data };
}

describe('maicraft_execute 专用卡面解析', () => {
  /** 完整的 maicraft_execute 事件入参/结果样本（形状对照线上数据） */
  function executeArgs(extra: Record<string, unknown> = {}): Record<string, unknown> {
    return {
      goal: {
        ability: 'maicraft:obtain',
        purpose: '挖 12 层以下的钻石并带回',
        target: { kind: 'position', x: 128, y: -58, z: -240 },
        parameters: { item: 'diamond', count: 5 },
        steps: [
          { ability: 'maicraft:descend', purpose: '下到深层' },
          { ability: 'maicraft:mine', purpose: '挖钻石' },
        ],
      },
      ...extra,
    };
  }

  it('maicraftExecuteSummary：取 ability（去前缀）/purpose/要点/steps', () => {
    const s = maicraftExecuteSummary(executeArgs(), null);
    expect(s.ability).toBe('obtain');
    expect(s.purpose).toBe('挖 12 层以下的钻石并带回');
    expect(s.keyPoints).toBe('坐标 128,-58,-240 · diamond ×5');
    expect(s.steps).toEqual([
      { ability: 'descend', purpose: '下到深层' },
      { ability: 'mine', purpose: '挖钻石' },
    ]);
    expect(s.dryRun).toBe(false);
    expect(s.state).toBe('');
    expect(s.summary).toBe('');
  });

  it('ability 已无前缀时原样保留；target 非坐标时取 kind/name', () => {
    const s = maicraftExecuteSummary(
      {
        goal: {
          ability: 'travel',
          purpose: '回出生点',
          target: { kind: 'block', name: 'oak_door' },
        },
      },
      null,
    );
    expect(s.ability).toBe('travel');
    expect(s.keyPoints).toBe('oak_door');
  });

  it('dry_run=true 时置位预演标记', () => {
    const s = maicraftExecuteSummary(executeArgs({ dry_run: true }), null);
    expect(s.dryRun).toBe(true);
  });

  it('state=running：state 取 running，summary 为空', () => {
    const s = maicraftExecuteSummary(executeArgs(), {
      ok: true,
      data: { goal_id: 'g1', state: 'running' },
    });
    expect(s.state).toBe('running');
    expect(s.summary).toBe('');
  });

  it('state=finished：取 result.data.result.summary', () => {
    const s = maicraftExecuteSummary(executeArgs(), {
      ok: true,
      data: {
        goal_id: 'g1',
        state: 'finished',
        result: { status: 'success', summary: '读完 12 层存成图纸' },
      },
    });
    expect(s.state).toBe('finished');
    expect(s.summary).toBe('读完 12 层存成图纸');
  });

  it('steps 缺失/含非对象成员时安全跳过', () => {
    const s = maicraftExecuteSummary(
      { goal: { ability: 'obtain', purpose: 'x', steps: [{ ability: 'mine' }, 'junk', null] } },
      null,
    );
    expect(s.steps).toEqual([{ ability: 'mine', purpose: '' }]);
  });

  it('入参非对象时返回全空摘要', () => {
    expect(maicraftExecuteSummary(null, null)).toEqual({
      ability: '',
      purpose: '',
      keyPoints: '',
      dryRun: false,
      state: '',
      summary: '',
      steps: [],
    });
  });

  it('isMinecraftExecuteEntry：maicraft/minecraft 两种拼写都命中，非 tool 卡不命中', () => {
    const tool = makeEntry({
      id: '1',
      kind: 'tool',
      tsMs: 1000,
      actor: 'maicraft_execute',
      text: '',
    });
    const alt = makeEntry({
      id: '2',
      kind: 'tool',
      tsMs: 1000,
      actor: 'minecraft_execute',
      text: '',
    });
    const other = makeEntry({ id: '3', kind: 'tool', tsMs: 1000, actor: 'glance', text: '' });
    const speech = makeEntry({
      id: '4',
      kind: 'speech',
      tsMs: 1000,
      actor: 'maicraft_execute',
      text: '',
    });
    expect(isMinecraftExecuteEntry(tool)).toBe(true);
    expect(isMinecraftExecuteEntry(alt)).toBe(true);
    expect(isMinecraftExecuteEntry(other)).toBe(false);
    expect(isMinecraftExecuteEntry(speech)).toBe(false);
  });

  it('fromToolResult：maicraft_execute 事件的 actor 取 tool_name，供专用卡面分流', () => {
    const entry = toEntry(
      evt('tool.result.maicraft_execute', {
        tool_name: 'maicraft_execute',
        status: 'ok',
        arguments: executeArgs(),
        caller_source: 'minecraft-react',
        result: { ok: true, data: { goal_id: 'g1', state: 'running' } },
      }),
    );
    expect(entry?.kind).toBe('tool');
    expect(entry?.actor).toBe('maicraft_execute');
  });
});

describe('framework_delegate 专用卡面解析', () => {
  /** 完整的 framework_delegate 入参/结果样本（形状对照线上 46 条样本） */
  function delegateArgs(extra: Record<string, unknown> = {}): Record<string, unknown> {
    return {
      agent: 'minecraft',
      instruction: '去村庄找村民，用 3 个绿宝石换 5 个面包，回来放进箱子里',
      when_busy: 'replace',
      ...extra,
    };
  }
  function delegateResult(extra: Record<string, unknown> = {}): Record<string, unknown> {
    return {
      accepted: true,
      task_id: 'deleg_abc123',
      executor: 'minecraft',
      queued: false,
      own_words: '我知道了，我这就去村庄用绿宝石换面包回来存箱子',
      ...extra,
    };
  }

  it('主路径：取目标 Agent/指令/复述/受理态', () => {
    const s = frameworkDelegateSummary(delegateArgs(), delegateResult());
    expect(s.targetAgent).toBe('minecraft');
    expect(s.instruction).toBe('去村庄找村民，用 3 个绿宝石换 5 个面包，回来放进箱子里');
    expect(s.ownWords).toBe('我知道了，我这就去村庄用绿宝石换面包回来存箱子');
    expect(s.accepted).toBe(true);
    expect(s.queued).toBe(false);
    expect(s.queuePosition).toBeNull();
    expect(s.queueNote).toBe('');
  });

  it('queued：位次与排队说明都取到', () => {
    const s = frameworkDelegateSummary(
      delegateArgs(),
      delegateResult({
        queued: true,
        queue_position: 1,
        note: '目标手上有事，这件已排进待办（待办2，第 1 位）',
      }),
    );
    expect(s.queued).toBe(true);
    expect(s.queuePosition).toBe(1);
    expect(s.queueNote).toBe('目标手上有事，这件已排进待办（待办2，第 1 位）');
  });

  it('own_words 与指令字面相同时留空（卡面不渲染复述行）', () => {
    const same = '去村庄换面包';
    const s = frameworkDelegateSummary(
      delegateArgs({ instruction: same }),
      delegateResult({ own_words: same }),
    );
    expect(s.ownWords).toBe('');
  });

  it('结果包一层 data 时穿透读取；字段缺失时各留空', () => {
    const wrapped = frameworkDelegateSummary(delegateArgs(), {
      ok: true,
      data: delegateResult({ accepted: true, own_words: '' }),
    });
    expect(wrapped.accepted).toBe(true);
    const empty = frameworkDelegateSummary(null, null);
    expect(empty).toEqual({
      targetAgent: '',
      instruction: '',
      ownWords: '',
      accepted: false,
      queued: false,
      queuePosition: null,
      queueNote: '',
    });
  });

  it('frameworkDelegateInstruction：取 args.instruction，非对象/缺失返回空串', () => {
    expect(frameworkDelegateInstruction(delegateArgs())).toBe(
      '去村庄找村民，用 3 个绿宝石换 5 个面包，回来放进箱子里',
    );
    expect(frameworkDelegateInstruction(null)).toBe('');
    expect(frameworkDelegateInstruction({ agent: 'minecraft' })).toBe('');
  });

  it('isFrameworkDelegateEntry：仅 tool + framework_delegate 命中', () => {
    const tool = makeEntry({
      id: '1',
      kind: 'tool',
      tsMs: 1000,
      actor: 'framework_delegate',
      text: '',
    });
    const other = makeEntry({
      id: '2',
      kind: 'tool',
      tsMs: 1000,
      actor: 'maicraft_execute',
      text: '',
    });
    const speech = makeEntry({
      id: '3',
      kind: 'speech',
      tsMs: 1000,
      actor: 'framework_delegate',
      text: '',
    });
    expect(isFrameworkDelegateEntry(tool)).toBe(true);
    expect(isFrameworkDelegateEntry(other)).toBe(false);
    expect(isFrameworkDelegateEntry(speech)).toBe(false);
  });

  it('isFrameworkDelegateCard：失败卡退回通用错误路径', () => {
    const failed = makeEntry({
      id: '1',
      kind: 'tool',
      tsMs: 1000,
      actor: 'framework_delegate',
      text: '',
      failed: true,
    });
    expect(isFrameworkDelegateCard(failed)).toBe(false);
  });
});
