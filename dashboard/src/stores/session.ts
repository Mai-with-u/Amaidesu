/**
 * 会话调试状态管理（v2 会话视图）
 *
 * 数据轴 = v2 对话闭环的三类观测点：
 * - 观众消息：room.message（WS 统一类型，payload 为扁平 RoomMessagePayload）
 * - 主播发言：streamer.speech（Planner → Replyer 产出）
 * - 决策/编排/工具：planner.checkpoint / agenda.update / tool.result.*（过程行）
 */

import { defineStore } from 'pinia';
import { ref } from 'vue';
import { useWebSocketStore } from './websocket';
import { debugApi } from '@/api';
import type {
  DebugSessionEvent,
  EventRecord,
  RoomMessageEventData,
  StreamerSpeechEventData,
  WebSocketMessage,
} from '@/types';

const MAX_EVENTS = 200;

/** 是否为会话页关注的决策/编排/工具事件 */
function isSystemEventType(type: string): boolean {
  return type.startsWith('planner') || type === 'rundown.changed' || type.startsWith('tool.result');
}

/** 统一解析：WS 消息与 events.history 历史共用；非会话关注类型返回 null */
function toSessionEvent(
  id: string,
  type: string,
  timestamp: number,
  data: Record<string, unknown>,
): DebugSessionEvent | null {
  if (type === 'room.message') {
    return {
      id,
      type,
      timestamp,
      kind: 'message',
      message: data as unknown as RoomMessageEventData,
      data,
    };
  }
  if (type === 'streamer.speech') {
    return {
      id,
      type,
      timestamp,
      kind: 'speech',
      speech: data as unknown as StreamerSpeechEventData,
      data,
    };
  }
  if (isSystemEventType(type)) {
    return {
      id,
      type,
      timestamp,
      kind: 'system',
      data,
    };
  }
  return null;
}

/** 合并历史与当前事件：按 id 去重、时间升序、限长 */
function mergeEvents(
  history: DebugSessionEvent[],
  current: DebugSessionEvent[],
): DebugSessionEvent[] {
  const byId = new Map<string, DebugSessionEvent>();
  for (const event of [...current, ...history]) {
    byId.set(event.id, event);
  }
  return [...byId.values()].sort((a, b) => a.timestamp - b.timestamp).slice(-MAX_EVENTS);
}

export const useSessionStore = defineStore('session', () => {
  const events = ref<DebugSessionEvent[]>([]);
  const sending = ref(false);

  function handleEvent(message: WebSocketMessage): void {
    // 后端推送的历史：与当前事件合并（幂等）
    if (message.type === 'events.history') {
      const history = ((message.data.events as EventRecord[]) ?? [])
        .map(record => toSessionEvent(record.id, record.type, record.timestamp, record.data))
        .filter((e): e is DebugSessionEvent => e !== null);
      events.value = mergeEvents(history, events.value);
      return;
    }

    const eventId =
      message.id ?? `${message.type}-${message.timestamp}-${crypto.randomUUID().slice(0, 6)}`;
    if (events.value.some(e => e.id === eventId)) return;

    const event = toSessionEvent(eventId, message.type, message.timestamp, message.data);
    if (!event) return;
    events.value.push(event);
    trimEvents();
  }

  function trimEvents() {
    while (events.value.length > MAX_EVENTS) {
      events.value.shift();
    }
  }

  const wsStore = useWebSocketStore();
  wsStore.subscribe(handleEvent);

  async function sendNormalizedMessage(
    text: string,
    source: string = 'dashboard',
    data_type: string = 'text',
    importance: number = 1,
  ) {
    sending.value = true;
    try {
      await debugApi.injectMessage({ text, source, data_type, importance });
    } finally {
      sending.value = false;
    }
  }

  function clearEvents() {
    events.value = [];
  }

  return {
    events,
    sending,
    sendNormalizedMessage,
    clearEvents,
  };
});
