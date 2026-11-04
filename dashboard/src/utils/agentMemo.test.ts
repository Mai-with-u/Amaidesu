// agentMemo.ts 的测试：事件流里哪些条目算"文档变了"
//
// 覆盖：三工具计数、非 tool.result 事件不计、其它工具不计、缺 tool_name 安全跳过、
// 空事件流为 0（面板据计数变化触发定向刷新）。

import { describe, expect, it } from 'vitest';

import { MEMO_TOOL_NAMES, countMemoToolResults } from './agentMemo';

function evt(type: string, toolName?: string) {
  return { type, data: toolName === undefined ? null : { tool_name: toolName } };
}

describe('countMemoToolResults', () => {
  it('三种 minecraft 记忆工具各计一次', () => {
    expect(MEMO_TOOL_NAMES.size).toBe(3);
    const events = [
      evt('tool.result.minecraft_placeholder', 'minecraft_notebook'),
      evt('tool.result.minecraft_placeholder', 'minecraft_todo'),
      evt('tool.result.minecraft_placeholder', 'minecraft_report'),
    ];
    expect(countMemoToolResults(events)).toBe(3);
  });

  it('其它工具与其它工具结果族不计入', () => {
    expect(
      countMemoToolResults([
        evt('tool.result.x', 'streamer_reply'),
        evt('tool.result.x', 'minecraft_build'),
        evt('tool.health.x', 'minecraft_notebook'),
        evt('streamer.speech', 'minecraft_notebook'),
      ]),
    ).toBe(0);
  });

  it('缺 tool_name 或 data 为空的条目安全跳过', () => {
    expect(
      countMemoToolResults([evt('tool.result.x'), { type: 'tool.result.y', data: null }]),
    ).toBe(0);
  });

  it('只数增量：多次同名工具累加，空事件流为 0', () => {
    const events = [evt('tool.result.a', 'minecraft_todo'), evt('tool.result.b', 'minecraft_todo')];
    expect(countMemoToolResults(events)).toBe(2);
    expect(countMemoToolResults([])).toBe(0);
  });
});
