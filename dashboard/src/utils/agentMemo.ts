// 游戏 Agent 备忘录面板的数据流纯逻辑：哪些工具结果会改动文档、事件流里累计
// 出现过几次。面板按计数增量触发定向刷新（漏接的事件由面板轮询兜底），
// 计数规则收在这里以便行为测试。

/** 会改动 minecraft 工作文档的工具（tool.result 事件 payload 的 tool_name） */
export const MEMO_TOOL_NAMES: ReadonlySet<string> = new Set([
  'minecraft_notebook',
  'minecraft_todo',
  'minecraft_report',
]);

/** WebSocket 事件条目的最小形状（只关心 type 与 data.tool_name） */
export interface MemoStoreEvent {
  type: string;
  data?: { tool_name?: string } | null;
}

/** 事件流里这些工具结果出现了几次（与上次计数比对，变化即重拉快照） */
export function countMemoToolResults(events: ReadonlyArray<MemoStoreEvent>): number {
  return events.reduce(
    (count, event) =>
      event.type.startsWith('tool.result.') && MEMO_TOOL_NAMES.has(event.data?.tool_name ?? '')
        ? count + 1
        : count,
    0,
  );
}
