/**
 * 直播控制台的 Agent 备忘录/待办面板数据源：主播备忘录 + minecraft 工作文档
 * （todo / 工作笔记 / 近期上报）的混合视图。
 *
 * 数据通道（对齐 useAgentTasks 的混合先例）：挂载时各拉一次 REST 快照；
 * 此后由事件触发定向刷新——主播侧听 `streamer.notebook.changed`（写入/清空
 * 即广播全文，但面板统一走快照重拉，不直接消费 payload）；minecraft 侧听
 * `tool.result.*` 中 tool_name 为 minecraft_notebook / minecraft_todo /
 * minecraft_report 的条目。事件可能漏接（页面开在写入前、WS 断连），另有
 * 30 秒级轮询兜底追平。快照端点是纯内存读取，失败（后端未装配 503 /
 * Agent 未运行 404）属常态，静默不打扰。
 *
 * keep-alive 生命周期：消费方多在全局 keep-alive 内、scope 永不 dispose——
 * 轮询按激活态启停（onActivated/onDeactivated），onScopeDispose 兜底清理。
 */
import { onActivated, onDeactivated, onScopeDispose, ref, watch } from 'vue';
import { useEventsStore } from '@/stores';
import { agentsApi } from '@/api';
import type { MemoTodoItem } from '@/types';

/** 静默轮询周期：备忘录写入频率低，30 秒级兜底足够 */
export const MEMO_POLL_INTERVAL_MS = 30000;

/** 触发 minecraft 侧刷新的工具结果（tool.result 事件 payload 的 tool_name） */
export const MINECRAFT_MEMO_TOOL_NAMES = new Set([
  'minecraft_notebook',
  'minecraft_todo',
  'minecraft_report',
]);

/** minecraft 工作文档三元组（todos/reports 拉取失败时为 null，面板显示占位） */
export interface MinecraftMemo {
  notebook: string;
  todos: MemoTodoItem[] | null;
  reports: Array<Record<string, string>> | null;
}

const EMPTY_MINECRAFT_MEMO: MinecraftMemo = { notebook: '', todos: null, reports: null };

/** WebSocketMessage 最小形状（events store 条目；测试假体同形即可） */
interface StoreEvent {
  type: string;
  data?: { tool_name?: string } | null;
}

/** 事件计数变化检测：events store 只增不删（滑动窗口裁剪头部除外），按计数触发 */
function countEvents(events: StoreEvent[], match: (e: StoreEvent) => boolean): number {
  return events.reduce((count, e) => (match(e) ? count + 1 : count), 0);
}

export function useAgentMemo() {
  const streamerNotebook = ref('');
  const minecraftMemo = ref<MinecraftMemo>({ ...EMPTY_MINECRAFT_MEMO });

  async function refreshStreamers(): Promise<void> {
    try {
      const res = await agentsApi.getAgentMemo('streamer');
      streamerNotebook.value = res.data.notebook;
    } catch {
      // 后端未装配（503）/ Agent 未运行（404）属常态：保留旧值，静默不打扰
    }
  }

  async function refreshMinecraft(): Promise<void> {
    try {
      const res = await agentsApi.getAgentMemo('minecraft');
      minecraftMemo.value = {
        notebook: res.data.notebook,
        todos: res.data.todos ?? null,
        reports: res.data.reports ?? null,
      };
    } catch {
      // 同上：静默保留旧值
    }
  }

  async function refresh(): Promise<void> {
    await Promise.all([refreshStreamers(), refreshMinecraft()]);
  }

  // 挂载即拉一次快照：后开的面板也能立刻看到当前文档
  void refresh();

  // 事件触发定向刷新（WS 全量订阅已入 events store；这里按计数增量触发）
  const eventsStore = useEventsStore();
  let lastNotebookCount = 0;
  let lastToolResultCount = 0;
  watch(
    () => eventsStore.events,
    events => {
      const notebookCount = countEvents(
        events as StoreEvent[],
        e => e.type === 'streamer.notebook.changed',
      );
      if (notebookCount !== lastNotebookCount) {
        lastNotebookCount = notebookCount;
        void refreshStreamers();
      }
      const toolCount = countEvents(
        events as StoreEvent[],
        e =>
          e.type.startsWith('tool.result.') &&
          MINECRAFT_MEMO_TOOL_NAMES.has(e.data?.tool_name ?? ''),
      );
      if (toolCount !== lastToolResultCount) {
        lastToolResultCount = toolCount;
        void refreshMinecraft();
      }
    },
  );

  // 轮询兜底：事件只在写入时刻发射，漏接一次账面就停在旧快照。
  // 消费方多在全局 keep-alive 内——激活态启停 + 销毁兜底
  let pollTimer: ReturnType<typeof setInterval> | undefined;
  const startPolling = (): void => {
    if (pollTimer !== undefined) return;
    pollTimer = setInterval(() => void refresh(), MEMO_POLL_INTERVAL_MS);
  };
  const stopPolling = (): void => {
    if (pollTimer === undefined) return;
    clearInterval(pollTimer);
    pollTimer = undefined;
  };
  startPolling();
  onActivated(startPolling);
  onDeactivated(stopPolling);
  onScopeDispose(stopPolling);

  return { streamerNotebook, minecraftMemo, refresh };
}
