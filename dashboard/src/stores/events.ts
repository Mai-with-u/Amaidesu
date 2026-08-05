import { defineStore } from 'pinia';
import { shallowRef } from 'vue';
import { useWebSocketStore } from './websocket';
import type { WebSocketMessage } from '@/types';

const MAX_EVENTS = 500;

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

export const useEventsStore = defineStore('events', () => {
  const events = shallowRef<LoggedEvent[]>([]);

  function handleMessage(message: WebSocketMessage) {
    // 后端初始历史：与当前条目合并（幂等，任意到达顺序，避免替换吞掉已到达的实时事件）
    if (message.type === 'events.history') {
      const history: LoggedEvent[] = ((message.data.events as LoggedEvent[]) ?? []).map(e => ({
        ...e,
        id: `his-${e.id}`,
      }));
      events.value = mergeEvents(history, events.value);
      return;
    }
    // 常规实时事件：附加去重 id 后合并（限长）
    events.value = mergeEvents(
      [],
      [...events.value, { ...message, id: `live-${crypto.randomUUID()}` }],
    );
  }

  useWebSocketStore().subscribe(handleMessage);

  function clearEvents() {
    events.value = [];
  }

  return {
    events,
    clearEvents,
  };
});
