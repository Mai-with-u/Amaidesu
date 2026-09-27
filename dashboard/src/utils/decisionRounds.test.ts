// decisionRounds.ts 决策轮归组纯函数的测试
//
// 覆盖：decision 锚成卡与降序、同轮工具挂载（含工具先于决策到达的真实时序）、
// 异轮工具排除、全沉默卡、字段映射（planner_raw / llm_request_id / 耗时）、
// 空输入与字段缺失退化。

import { describe, expect, it } from 'vitest';

import { groupDecisionRounds, type DecisionEvent } from '@/utils/decisionRounds';

let seq = 0;

/** 构造事件（events store 入口形状：WS 消息 + 去重 id） */
function evt(type: string, data: Record<string, unknown>, tsMs = 1000): DecisionEvent {
  seq += 1;
  return { id: `e${seq}`, type, timestamp_ms: tsMs, data };
}

/** 决策事件最小载荷（完整字段以 src/modules/events/payloads/planner.py 为准） */
function decisionData(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    round_id: 'rnd_1_1',
    trigger_reason: 'buffer_flush',
    proactive: false,
    batch: [],
    should_reply: false,
    confidence: 0.0,
    planner_raw: '',
    planner_duration_ms: 0,
    reply_duration_ms: 0,
    total_duration_ms: 0,
    ...overrides,
  };
}

describe('groupDecisionRounds', () => {
  it('单条 decision 成一张卡', () => {
    const cards = groupDecisionRounds([evt('planner.decision', decisionData(), 1000)]);
    expect(cards).toHaveLength(1);
    expect(cards[0].roundId).toBe('rnd_1_1');
    expect(cards[0].shouldReply).toBe(false);
  });

  it('多轮输出按时间降序（乱序输入也归位）', () => {
    const events = [
      evt('planner.decision', decisionData({ round_id: 'rnd_1_1' }), 1000),
      evt('planner.decision', decisionData({ round_id: 'rnd_1_3' }), 3000),
      evt('planner.decision', decisionData({ round_id: 'rnd_1_2' }), 2000),
    ];
    const cards = groupDecisionRounds(events);
    expect(cards.map(c => c.roundId)).toEqual(['rnd_1_3', 'rnd_1_2', 'rnd_1_1']);
  });

  it('全沉默轮：卡含 silentReason 无 speech', () => {
    const cards = groupDecisionRounds([
      evt(
        'planner.decision',
        decisionData({
          should_reply: false,
          silent_reason: 'low_confidence',
          confidence: 0.42,
        }),
      ),
    ]);
    expect(cards[0].silentReason).toBe('low_confidence');
    expect(cards[0].confidence).toBe(0.42);
    expect(cards[0].speech).toBeUndefined();
  });

  it('同轮工具挂到卡上；真实时序（工具先于轮末 decision）不漏挂', () => {
    const events = [
      evt(
        'tool.result.speak',
        {
          round_id: 'rnd_1_1',
          tool_name: 'look_at_screen',
          caller_source: 'planner-react',
          status: 'success',
          arguments: { monitor_index: 1 },
          result: { ok: true },
          error_message: '',
        },
        1500,
      ),
      evt(
        'tool.result.speak',
        {
          round_id: 'rnd_1_1',
          tool_name: 'speak',
          caller_source: 'planner-react',
          status: 'error',
          arguments: { text: '你好' },
          result: {},
          error_message: 'TTS 不可用',
        },
        1600,
      ),
      evt(
        'planner.decision',
        decisionData({
          round_id: 'rnd_1_1',
          should_reply: true,
          speech: '你好呀',
          emotion: 'happy',
        }),
        2000,
      ),
    ];
    const cards = groupDecisionRounds(events);
    expect(cards).toHaveLength(1);
    expect(cards[0].tools).toHaveLength(2);
    expect(cards[0].tools.map(t => t.name)).toEqual(['look_at_screen', 'speak']);
    expect(cards[0].tools[0].failed).toBe(false);
    expect(cards[0].tools[1].failed).toBe(true);
    expect(cards[0].tools[1].error).toBe('TTS 不可用');
    expect(cards[0].tools[0].argsSummary).toContain('monitor_index');
    expect(cards[0].speech).toBe('你好呀');
    expect(cards[0].emotion).toBe('happy');
  });

  it('异轮工具与无锚工具不出现在任何卡上', () => {
    const events = [
      evt('planner.decision', decisionData({ round_id: 'rnd_1_1' }), 1000),
      evt(
        'tool.result.speak',
        { round_id: 'rnd_other', tool_name: 'speak', status: 'success' },
        1100,
      ),
      evt('tool.result.speak', { tool_name: 'speak', status: 'success' }, 1200),
    ];
    const cards = groupDecisionRounds(events);
    expect(cards).toHaveLength(1);
    expect(cards[0].tools).toHaveLength(0);
  });

  it('minecraft 的工具调用没有 planner.decision 锚，天然不成卡', () => {
    const cards = groupDecisionRounds([
      evt(
        'tool.result.goto',
        {
          round_id: 'rnd_mc_9',
          tool_name: 'goto',
          caller_source: 'minecraft-react',
          status: 'success',
        },
        1000,
      ),
    ]);
    expect(cards).toEqual([]);
  });

  it('发言轮字段映射：批消息 / 触发原因 / planner_raw / llm_request_id', () => {
    const cards = groupDecisionRounds([
      evt(
        'planner.decision',
        decisionData({
          should_reply: true,
          trigger_reason: 'danmaku',
          batch: [{ message_id: 'm1', user_id: 'u1', user_name: '小明', text: '在吗' }],
          planner_raw: '{"action":"reply"}',
          llm_request_id: 'req_123',
          planner_duration_ms: 1200,
          reply_duration_ms: 800,
          total_duration_ms: 2100,
        }),
      ),
    ]);
    expect(cards[0].triggerReason).toBe('danmaku');
    expect(cards[0].batch).toEqual([{ sender: '小明', content: '在吗', type: 'danmaku' }]);
    expect(cards[0].plannerRaw).toBe('{"action":"reply"}');
    expect(cards[0].llmRequestId).toBe('req_123');
    expect(cards[0].durations).toEqual({ plannerMs: 1200, replyMs: 800, totalMs: 2100 });
  });

  it('主动发言轮带 proactive 标记', () => {
    const cards = groupDecisionRounds([
      evt('planner.decision', decisionData({ proactive: true, trigger_reason: 'proactive:cold' })),
    ]);
    expect(cards[0].proactive).toBe(true);
  });

  it('失败轮：error 字段映射到卡', () => {
    const cards = groupDecisionRounds([
      evt('planner.decision', decisionData({ error: 'planner_failed' })),
    ]);
    expect(cards[0].error).toBe('planner_failed');
  });

  it('空输入输出空', () => {
    expect(groupDecisionRounds([])).toEqual([]);
  });

  it('字段缺失不抛错，缺 round_id 的锚用事件 id 兜底成卡', () => {
    const cards = groupDecisionRounds([
      evt('planner.decision', {}, 1000),
      evt('planner.decision', {}, 2000),
    ]);
    expect(cards).toHaveLength(2);
    expect(cards[0].durations).toBeUndefined();
    expect(cards[0].plannerRaw).toBeUndefined();
    expect(cards[0].llmRequestId).toBeUndefined();
  });
});
