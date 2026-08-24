/**
 * 调试会话状态管理（v2.0）
 *
 * 适配 v2 行为变化（参考 `.omo/evidence/w8-api-contract.txt`）：
 * - 后端不再发布 `decision.intent` / `output.render` 事件（v2 决策出口=工具调用，不再有 Intent）
 * - 所有消息事件统一为 `room.message.*` 一族（采集器归一化后）
 * - Planner/Replyer 发言通过 `agenda.*` / `tool.result.*` / `planner.checkpoint` 间接观测
 *
 * 兼容策略：同时接受 v2 新事件名与旧事件名，旧事件名仅在兼容旧后端时生效。
 */

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

// ===== v2 调试会话关注的事件类型 =====
// 主路：room.message（v2 默认）；其他观测事件来自 Agenda/Planner/Tool 通道
const SESSION_EVENT_TYPES = new Set<string>([
  'room.message',
  'agenda.speech',
  'tool.result',
  // 兼容旧后端
  'message.received',
  'decision.intent',
  'output.render',
]);

// ===== 解析纯函数（实时事件与 events.history 历史共用） =====

/**
 * v2 消息事件解析：兼容 `room.message.*` 与旧 `message.received`。
 * payload 结构：`data.message.message_id` 是消息唯一 ID。
 */
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
    message_id: msg.message_id as string | undefined,
    simulated: msg.simulated as boolean | undefined,
  };
}

/**
 * v2 决策/工具事件解析：v2 主要来源是 `agenda.speech` 与 `tool.result.*`，
 * payload 结构与旧 `decision.intent` 类似（speech/emotion/action/metadata）。
 */
function parseIntentEvent(
  data: Record<string, unknown>,
  fallbackTimestamp: number,
): IntentEventData {
  const intentData = data?.intent_data as Record<string, unknown> | undefined;
  const sourceName = (data?.name as string) || (data?.agent as string) || 'unknown';
  if (!intentData) {
    return {
      metadata: {
        source_id: sourceName,
        decision_time_ms: fallbackTimestamp,
      },
    };
  }
  const md = (intentData.metadata as Record<string, unknown> | undefined) ?? {};
  return {
    speech: intentData.speech as string | undefined,
    emotion: intentData.emotion as IntentEventData['emotion'] | undefined,
    action: intentData.action as IntentEventData['action'] | undefined,
    metadata: {
      source_id: (md.source_id as string) || sourceName,
      decision_time_ms: (md.decision_time_ms as number) ?? fallbackTimestamp,
      source_message_id: md.source_message_id as string | undefined,
    },
  };
}

/** 后端 EventRecord → DebugSessionEvent；非会话事件类型返回 null */
function eventRecordToSessionEvent(record: EventRecord): DebugSessionEvent | null {
  if (!SESSION_EVENT_TYPES.has(record.type)) return null;

  // v2 默认：room.message.*
  if (record.type.startsWith('room.message') || record.type === 'message.received') {
    return {
      id: record.id,
      type: record.type,
      timestamp: record.timestamp,
      message: parseMessageEvent(record.data, record.timestamp),
      source:
        (record.data?.source as string) ||
        ((record.data?.message as Record<string, unknown> | undefined)?.source as string) ||
        record.source ||
        'unknown',
    };
  }

  // v2 agenda / tool / 旧 decision.intent / output.render
  return {
    id: record.id,
    type: record.type,
    timestamp: record.timestamp,
    intent: parseIntentEvent(record.data, record.timestamp),
    deciderName:
      record.type === 'output.render' || record.type.startsWith('tool.result')
        ? 'Output/Tool'
        : (record.data?.name as string) || 'Decider',
  };
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
        .map(eventRecordToSessionEvent)
        .filter((e): e is DebugSessionEvent => e !== null);
      events.value = mergeEvents(history, events.value);
      return;
    }

    // v2: room.message.* 一律视为消息事件
    if (message.type.startsWith('room.message') || message.type === 'message.received') {
      const data = message.data as Record<string, unknown>;
      const eventId = message.id ?? `msg-${message.timestamp}-${crypto.randomUUID().slice(0, 6)}`;
      if (events.value.some(e => e.id === eventId)) return;
      events.value.push({
        id: eventId,
        type: message.type,
        timestamp: message.timestamp,
        message: parseMessageEvent(data, message.timestamp),
        source: (data?.source as string) || 'unknown',
      });
      trimEvents();
      return;
    }

    // v2: agenda.* / tool.result.* / 旧 decision.intent / output.render
    if (
      message.type.startsWith('agenda') ||
      message.type.startsWith('tool.result') ||
      message.type === 'decision.intent' ||
      message.type === 'output.render'
    ) {
      const data = message.data as Record<string, unknown>;
      const eventId =
        message.id ?? `intent-${message.timestamp}-${crypto.randomUUID().slice(0, 6)}`;
      if (events.value.some(e => e.id === eventId)) return;
      events.value.push({
        id: eventId,
        type: message.type,
        timestamp: message.timestamp,
        intent: parseIntentEvent(data, message.timestamp),
        deciderName:
          message.type === 'output.render' || message.type.startsWith('tool.result')
            ? 'Output/Tool'
            : (data?.name as string) || 'Decider',
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
