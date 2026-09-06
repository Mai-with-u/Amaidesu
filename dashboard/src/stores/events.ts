import { defineStore } from 'pinia';
import { ref, shallowRef } from 'vue';
import { useWebSocketStore } from './websocket';
import { eventsApi } from '@/api';
import type { WebSocketMessage } from '@/types';

const MAX_EVENTS = 1000;
/** 游标持久化键：记录最后收到的事件 id，刷新/重连后按此补缺口 */
const CURSOR_KEY = 'amaidesu-events-cursor';

/** 事件面板条目：在 WebSocketMessage 基础上附加去重 id */
interface LoggedEvent extends WebSocketMessage {
  id: string;
}

/** 合并历史与当前条目：按 id 去重（历史优先）、时间升序、限长保尾 */
function mergeEvents(history: LoggedEvent[], current: LoggedEvent[]): LoggedEvent[] {
  const byId = new Map<string, LoggedEvent>();
  for (const event of [...current, ...history]) {
    byId.set(event.id, event);
  }
  return [...byId.values()].sort((a, b) => a.timestamp - b.timestamp).slice(-MAX_EVENTS);
}

function loadCursor(): string {
  try {
    return localStorage.getItem(CURSOR_KEY) ?? '';
  } catch {
    return '';
  }
}

function saveCursor(id: string): void {
  try {
    localStorage.setItem(CURSOR_KEY, id);
  } catch {
    /* 存储不可用时游标退化为会话内有效 */
  }
}

export const useEventsStore = defineStore('events', () => {
  const events = shallowRef<LoggedEvent[]>([]);
  /** 断线/刷新续传游标（最后收到的事件 id） */
  const cursor = ref<string>(loadCursor());
  const backfillDone = ref(false);

  function advanceCursor(id?: string): void {
    if (!id) return;
    cursor.value = id;
    saveCursor(id);
  }

  function handleMessage(message: WebSocketMessage) {
    // 后端初始历史：与当前条目合并（幂等，任意到达顺序，避免替换吞掉已到达的实时事件）
    if (message.type === 'events.history') {
      const history = (message.data.events as LoggedEvent[]) ?? [];
      events.value = mergeEvents(history, events.value);
      return;
    }
    // 常规实时事件：附加去重 id 后合并（限长）；后端已带 id 时直接使用（与历史同源）
    const id = message.id ?? `live-${crypto.randomUUID()}`;
    events.value = mergeEvents([], [...events.value, { ...message, id }]);
    if (message.id) advanceCursor(message.id);
  }

  useWebSocketStore().subscribe(handleMessage);

  /**
   * 游标回填：刷新/重连后按游标拉取事件缺口（REST），与 WS 历史合并去重。
   * 无游标（首次访问）时跳过——WS 连接时的 events.history 已覆盖最近窗口。
   */
  async function backfill(): Promise<void> {
    if (backfillDone.value) return;
    backfillDone.value = true;
    if (!cursor.value) return;
    try {
      const response = await eventsApi.list({ since_id: cursor.value, limit: MAX_EVENTS });
      const gap = (response.data.events ?? []) as LoggedEvent[];
      if (gap.length > 0) {
        events.value = mergeEvents(gap, events.value);
        advanceCursor(gap[gap.length - 1]?.id);
      }
    } catch {
      // 回填失败静默：WS events.history 兜底，不阻断页面
    }
  }

  function clearEvents() {
    events.value = [];
  }

  // 启动即回填（fire-and-forget）：有游标则补缺口，无游标由 WS events.history 兜底
  void backfill();

  return {
    events,
    cursor,
    clearEvents,
    backfill,
  };
});
