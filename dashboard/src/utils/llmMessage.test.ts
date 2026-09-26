// normalizeMessage 中立契约消息 → 展示形状的测试
//
// 契约形状：{role, parts, tool_calls, tool_call_id}；parts 片段为字符串或
// {type:"text",text} / {type:"image",image}；tool_calls 扁平 {id,name,arguments}。

import { describe, expect, it } from 'vitest';

import { normalizeMessage } from './llmMessage';

describe('正文提取（messageText）', () => {
  it('字符串片段与 text 片段按序拼接', () => {
    const msg = normalizeMessage({
      role: 'user',
      parts: ['第一行', { type: 'text', text: '第二行' }],
    });
    expect(msg.content).toBe('第一行\n第二行');
  });

  it('图像片段替换为占位符，避免 base64 灌入页面', () => {
    const msg = normalizeMessage({
      role: 'user',
      parts: [{ type: 'image', image: 'data:image/png;base64,AAAA' }],
    });
    expect(msg.content).toBe('[图片内容已省略]');
    expect(msg.content).not.toContain('base64');
  });

  it('无 text 的对象片段与其他类型片段跳过', () => {
    const msg = normalizeMessage({
      role: 'user',
      parts: [{ type: 'tool_use' }, { foo: 'bar' }, '正文'],
    });
    expect(msg.content).toBe('正文');
  });

  it('parts 缺失或非数组返回空正文', () => {
    expect(normalizeMessage({ role: 'user' }).content).toBe('');
    expect(normalizeMessage({ role: 'user', parts: 'oops' }).content).toBe('');
  });
});

describe('tool_calls 归一', () => {
  it('对象形态 arguments 序列化为 JSON 字符串', () => {
    const msg = normalizeMessage({
      role: 'assistant',
      tool_calls: [{ id: 'call-1', name: 'speak', arguments: { text: '你好' } }],
    });
    expect(msg.toolCalls).toHaveLength(1);
    expect(msg.toolCalls[0]).toMatchObject({ id: 'call-1', name: 'speak' });
    expect(JSON.parse(msg.toolCalls[0].arguments)).toEqual({ text: '你好' });
  });

  it('字符串形态 arguments 原样保留', () => {
    const msg = normalizeMessage({
      role: 'assistant',
      tool_calls: [{ id: 'call-1', name: 'speak', arguments: '{"text":"你好"}' }],
    });
    expect(msg.toolCalls[0].arguments).toBe('{"text":"你好"}');
  });

  it('id 缺失 → undefined；name 缺失 → unknown；arguments 缺失 → "{}"', () => {
    const msg = normalizeMessage({ role: 'assistant', tool_calls: [{}] });
    expect(msg.toolCalls[0]).toEqual({ id: undefined, name: 'unknown', arguments: '{}' });
  });
});

describe('退化输入', () => {
  it('非对象输入退化为空正文 unknown 角色', () => {
    expect(normalizeMessage(null)).toEqual({
      role: 'unknown',
      content: '',
      toolCallId: undefined,
      toolCalls: [],
    });
    expect(normalizeMessage(42)).toEqual({
      role: 'unknown',
      content: '',
      toolCallId: undefined,
      toolCalls: [],
    });
  });

  it('role 非字符串回退 unknown', () => {
    expect(normalizeMessage({ role: 3 }).role).toBe('unknown');
  });

  it('tool_call_id 透传', () => {
    const msg = normalizeMessage({ role: 'tool', tool_call_id: 'call-9', parts: ['结果'] });
    expect(msg.toolCallId).toBe('call-9');
  });

  it('tool_call_id 非字符串回退 undefined', () => {
    expect(normalizeMessage({ role: 'tool', tool_call_id: 7 }).toolCallId).toBeUndefined();
  });
});
