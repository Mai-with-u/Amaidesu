import { defineStore } from 'pinia';
import { ref } from 'vue';
import { useWebSocketStore } from './websocket';
import { debugApi } from '@/api';
import type {
  DebugSessionEvent,
  EventRecord,
  NormalizedMessageData,
  IntentEventData,
  WebSocketMessage,
  InjectIntentRequest,
} from '@/types';

const MAX_EVENTS = 200;

// 调试会话页展示的事件类型（与 DebugSessionEvent.type 联合类型一致）
const SESSION_EVENT_TYPES = new Set<string>([
  'message.received',
  'decision.intent',
  'output.render',
]);

// ===== 解析纯函数（实时事件与 events.history 历史共用，保证单一解析来源） =====

/** 从载荷中解析 NormalizedMessageData；无 message 字段时回退到顶层字段 */
function parseMessageEvent(
  data: Record<string, unknown>,
  fallbackTimestamp: number,
): NormalizedMessageData {
  const msg = data?.message as Record<string, unknown> | undefined;
  if (!msg) {
    return {
      text: (data?.text as string) || '',
      source: (data?.source as string) || 'unknown',
      data_type: 'text',
      importance: 0.5,
      timestamp_ms: fallbackTimestamp,
    };
  }
  return {
    text: (msg.text as string) || '',
    source: (msg.source as string) || (data?.source as string) || 'unknown',
    data_type: (msg.data_type as string) || 'text',
    importance: (msg.importance as number) ?? 0.5,
    timestamp_ms: (msg.timestamp_ms as number) ?? 0,
    user_id: msg.user_id as string | undefined,
    user_nickname: msg.user_nickname as string | undefined,
    platform: msg.platform as string | undefined,
    room_id: msg.room_id as string | undefined,
    raw: msg.raw as Record<string, unknown> | undefined,
  };
}

/** 从载荷中解析 IntentEventData；无 intent_data 时构造空元数据兜底 */
function parseIntentEvent(
  data: Record<string, unknown>,
  fallbackTimestamp: number,
): IntentEventData {
  const intentData = data?.intent_data as Record<string, unknown> | undefined;
  if (!intentData) {
    return {
      metadata: {
        source_id: (data?.name as string) || 'unknown',
        decision_time_ms: fallbackTimestamp,
      },
    };
  }
  return {
    speech: intentData.speech as string | undefined,
    emotion: intentData.emotion as IntentEventData['emotion'] | undefined,
    action: intentData.action as IntentEventData['action'] | undefined,
    metadata: (intentData.metadata as IntentEventData['metadata']) || {
      source_id: (data?.name as string) || 'unknown',
      decision_time_ms: fallbackTimestamp,
    },
  };
}

/** 将后端 EventRecord 转换为调试会话事件；非会话事件类型返回 null（过滤） */
function eventRecordToSessionEvent(record: EventRecord): DebugSessionEvent | null {
  if (!SESSION_EVENT_TYPES.has(record.type)) return null;

  if (record.type === 'message.received') {
    return {
      id: `his-${record.id}`,
      type: 'message.received',
      timestamp: record.timestamp,
      message: parseMessageEvent(record.data, record.timestamp),
      source: (record.data?.source as string) || record.source || 'unknown',
    };
  }

  // decision.intent / output.render：deciderName 与实时分支保持一致
  return {
    id: `his-${record.id}`,
    type: record.type as 'decision.intent' | 'output.render',
    timestamp: record.timestamp,
    intent: parseIntentEvent(record.data, record.timestamp),
    deciderName:
      record.type === 'output.render' ? 'Output' : (record.data?.name as string) || '未知Decider',
  };
}

/** 合并历史与当前事件：按 id 去重（历史优先）、时间升序、限长保尾 */
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

  function handleEvent(message: WebSocketMessage) {
    // 后端连接时推送的权威历史：与当前事件合并（幂等，任意到达顺序）
    if (message.type === 'events.history') {
      const history = ((message.data.events as EventRecord[]) ?? [])
        .map(eventRecordToSessionEvent)
        .filter((e): e is DebugSessionEvent => e !== null);
      events.value = mergeEvents(history, events.value);
      return;
    }

    if (message.type === 'message.received') {
      const data = message.data as Record<string, unknown>;
      const eventId = `msg-${message.timestamp}-${crypto.randomUUID().slice(0, 6)}`;

      if (events.value.some(e => e.id === eventId)) return;

      events.value.push({
        id: eventId,
        type: 'message.received',
        timestamp: message.timestamp,
        message: parseMessageEvent(data, message.timestamp),
        source: (data?.source as string) || 'unknown',
      });
      trimEvents();
    } else if (message.type === 'decision.intent') {
      const data = message.data as Record<string, unknown>;
      const eventId = `intent-${message.timestamp}-${crypto.randomUUID().slice(0, 6)}`;

      if (events.value.some(e => e.id === eventId)) return;

      events.value.push({
        id: eventId,
        type: 'decision.intent',
        timestamp: message.timestamp,
        intent: parseIntentEvent(data, message.timestamp),
        deciderName: (data?.name as string) || '未知Decider',
      });
      trimEvents();
    } else if (message.type === 'output.render') {
      const data = message.data as Record<string, unknown>;

      events.value.push({
        id: `output-${message.timestamp}-${crypto.randomUUID().slice(0, 6)}`,
        type: 'output.render',
        timestamp: message.timestamp,
        intent: parseIntentEvent(data, message.timestamp),
        deciderName: 'Output',
      });
      trimEvents();
    }
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

  async function sendIntent(
    text?: string,
    emotion: string = 'neutral',
    source: string = 'dashboard',
    responseText?: string,
    actions: Record<string, any>[] = [],
  ) {
    sending.value = true;
    try {
      const request: InjectIntentRequest = {
        text: text || undefined,
        responseText,
        emotion,
        source,
        actions,
      };
      await debugApi.injectIntent(request);
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
    sendIntent,
    clearEvents,
  };
});
