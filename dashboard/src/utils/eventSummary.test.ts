// summarizeEvent 各业务族摘要规则的测试
//
// 规则核心：按 WS 广播 type 分族抽取一行摘要，各字段有独立截断上限；
// 空字段回退链的终点是事件 type 本身。

import { describe, expect, it } from 'vitest';

import { summarizeEvent } from './eventSummary';

describe('room.message', () => {
  it('昵称用户 + 内容', () => {
    expect(summarizeEvent('room.message', { user: { name: 'Alice' }, content: '你好' })).toBe(
      'danmaku · Alice: 你好',
    );
  });

  it('message_type 判别消息种类', () => {
    expect(
      summarizeEvent('room.message', {
        message_type: 'gift',
        user: { name: 'Bob' },
        content: '送出小星星',
      }),
    ).toBe('gift · Bob: 送出小星星');
  });

  it('无昵称时回退 #id', () => {
    expect(summarizeEvent('room.message', { user: { id: 'u1' }, content: 'hi' })).toBe(
      'danmaku · #u1: hi',
    );
  });

  it('user 缺失回退匿名', () => {
    expect(summarizeEvent('room.message', { content: 'hi' })).toBe('danmaku · 匿名: hi');
  });

  it('内容缺失时只到用户名为止', () => {
    expect(summarizeEvent('room.message', { user: { name: 'Alice' } })).toBe('danmaku · Alice');
  });

  it('内容超过 24 字截断补省略号', () => {
    const content = 'x'.repeat(30);
    const text = summarizeEvent('room.message', { user: { name: 'Alice' }, content });
    expect(text).toBe(`danmaku · Alice: ${'x'.repeat(24)}…`);
  });
});

describe('streamer.speech', () => {
  it('有文本输出"发言:"前缀（32 字上限）', () => {
    expect(summarizeEvent('streamer.speech', { text: '大家好呀' })).toBe('发言: 大家好呀');
  });

  it('无文本回退事件 type', () => {
    expect(summarizeEvent('streamer.speech', {})).toBe('streamer.speech');
  });
});

describe('tool.result.*', () => {
  it('工具名 · 状态 · 错误尾', () => {
    expect(
      summarizeEvent('tool.result.speak', {
        tool_name: 'speak',
        status: 'error',
        error_message: 'TTS 超时',
      }),
    ).toBe('speak · error · TTS 超时');
  });

  it('无错误只到状态', () => {
    expect(summarizeEvent('tool.result.speak', { tool_name: 'speak', status: 'ok' })).toBe(
      'speak · ok',
    );
  });

  it('tool_name 缺失时从事件 type 抠名字，status 缺失显示占位', () => {
    expect(summarizeEvent('tool.result.look_up', {})).toBe('look_up · —');
  });
});

describe('planner.*', () => {
  it('decision 失败态', () => {
    expect(summarizeEvent('planner.decision', { error: 'LLM 超时' })).toBe('决策失败 · LLM 超时');
  });

  it('decision 低置信压制', () => {
    expect(
      summarizeEvent('planner.decision', { should_reply: false, silent_reason: 'low_confidence' }),
    ).toBe('低置信压制，本轮沉默');
  });

  it('decision 普通沉默', () => {
    expect(summarizeEvent('planner.decision', { should_reply: false })).toBe('本轮不回应');
  });

  it('decision 回应带主题与置信度', () => {
    expect(
      summarizeEvent('planner.decision', {
        should_reply: true,
        topic_summary: '聊聊新皮肤',
        confidence: 0.85,
      }),
    ).toBe('回应: 聊聊新皮肤（85%）');
  });

  it('置信度为 0 不展示百分比', () => {
    expect(
      summarizeEvent('planner.decision', {
        should_reply: true,
        topic_summary: '聊聊',
        confidence: 0,
      }),
    ).toBe('回应: 聊聊');
  });

  it('verdict 输出裁决前缀', () => {
    expect(summarizeEvent('planner.verdict', { topic_summary: '回个招呼', confidence: 0.5 })).toBe(
      '裁决: 回个招呼（50%）',
    );
  });
});

describe('rundown.changed', () => {
  it('index 达到 total 判流程单完成', () => {
    expect(summarizeEvent('rundown.changed', { index: 3, total: 3 })).toBe('流程单完成');
  });

  it('未完成输出切换目标', () => {
    expect(
      summarizeEvent('rundown.changed', { segment_title: '闲聊环节', index: 1, total: 3 }),
    ).toBe('切换到 闲聊环节');
  });

  it('无信息回退事件 type', () => {
    expect(summarizeEvent('rundown.changed', {})).toBe('rundown.changed');
  });
});

describe('game.*', () => {
  it('message + scene 拼接', () => {
    expect(summarizeEvent('game.report', { message: '已下井', scene: '矿井' })).toBe(
      '已下井 @ 矿井',
    );
  });

  it('无 message 回退事件 type', () => {
    expect(summarizeEvent('game.report', {})).toBe('game.report');
  });
});

describe('live.*', () => {
  it('platform · session 双拼', () => {
    expect(summarizeEvent('live.started', { platform: 'bilibili', live_session_id: 's-123' })).toBe(
      'bilibili · s-123',
    );
  });

  it('单字段与双缺失回退', () => {
    expect(summarizeEvent('live.started', { platform: 'bilibili' })).toBe('bilibili');
    expect(summarizeEvent('live.started', {})).toBe('live.started');
  });
});

describe('core.*', () => {
  it('message 优先，其次 event 字段', () => {
    expect(summarizeEvent('core.started', { message: '核心就绪' })).toBe('核心就绪');
    expect(summarizeEvent('core.started', { event: 'core.started' })).toBe('core.started');
  });

  it('双缺失回退事件 type', () => {
    expect(summarizeEvent('core.started', {})).toBe('core.started');
  });
});

describe('未知类型与空载荷', () => {
  it('未匹配任何族的 type 原样返回', () => {
    expect(summarizeEvent('system.some_event', { foo: 1 })).toBe('system.some_event');
  });

  it('data 为 null/undefined 不抛错', () => {
    expect(summarizeEvent('unknown.type', null)).toBe('unknown.type');
    expect(summarizeEvent('unknown.type', undefined)).toBe('unknown.type');
  });
});
