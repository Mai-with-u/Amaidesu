import { defineStore } from 'pinia';
import { ref, watch } from 'vue';
import { useWebSocketStore } from './websocket';
import { debugApi } from '@/api';
import type {
  DebugSessionEvent,
  NormalizedMessageData,
  IntentEventData,
  WebSocketMessage,
  InjectIntentRequest,
} from '@/types';

const STORAGE_KEY = 'debug_session_events';
const MAX_EVENTS = 200;

function loadFromStorage(): DebugSessionEvent[] {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) {
      return JSON.parse(stored);
    }
  } catch (e) {
    console.warn('从 localStorage 加载调试事件失败:', e);
  }
  return [];
}

function saveToStorage(events: DebugSessionEvent[]) {
  try {
    const toSave = events.slice(-MAX_EVENTS);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(toSave));
  } catch (e) {
    console.warn('保存调试事件到 localStorage 失败:', e);
  }
}

export const useSessionStore = defineStore('session', () => {
  const events = ref<DebugSessionEvent[]>(loadFromStorage());
  const sending = ref(false);

  watch(events, newEvents => saveToStorage(newEvents), { deep: true });

  function handleEvent(message: WebSocketMessage) {
    if (message.type === 'message.received') {
      const data = message.data as Record<string, unknown>;
      const msg = data?.message as Record<string, unknown> | undefined;
      const msgId = `msg-${message.timestamp}-${crypto.randomUUID().slice(0, 6)}`;

      if (events.value.some(e => e.id === msgId)) return;

      events.value.push({
        id: msgId,
        type: 'message.received',
        timestamp: message.timestamp,
        message: msg
          ? ({
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
            } as NormalizedMessageData)
          : {
              text: (data?.text as string) || '',
              source: (data?.source as string) || 'unknown',
              data_type: 'text',
              importance: 0.5,
              timestamp_ms: message.timestamp,
            },
        source: (data?.source as string) || 'unknown',
      });
    } else if (message.type === 'decision.intent') {
      const data = message.data as Record<string, unknown>;
      const intentData = data?.intent_data as Record<string, unknown> | undefined;
      const intentId = `intent-${message.timestamp}-${crypto.randomUUID().slice(0, 6)}`;

      if (events.value.some(e => e.id === intentId)) return;

      events.value.push({
        id: intentId,
        type: 'decision.intent',
        timestamp: message.timestamp,
        intent: intentData
          ? ({
              speech: intentData.speech as string | undefined,
              emotion: intentData.emotion as IntentEventData['emotion'] | undefined,
              action: intentData.action as IntentEventData['action'] | undefined,
              metadata: (intentData.metadata as IntentEventData['metadata']) || {
                source_id: (data?.name as string) || 'unknown',
                decision_time_ms: message.timestamp,
              },
            } as IntentEventData)
          : {
              metadata: {
                source_id: (data?.name as string) || 'unknown',
                decision_time_ms: message.timestamp,
              },
            },
        deciderName: (data?.name as string) || '未知Decider',
      });
    } else if (message.type === 'output.render') {
      const data = message.data as Record<string, unknown>;
      const intentData = data?.intent_data as Record<string, unknown> | undefined;

      events.value.push({
        id: `output-${message.timestamp}-${crypto.randomUUID().slice(0, 6)}`,
        type: 'output.render',
        timestamp: message.timestamp,
        intent: intentData
          ? ({
              speech: intentData.speech as string | undefined,
              emotion: intentData.emotion as IntentEventData['emotion'] | undefined,
              action: intentData.action as IntentEventData['action'] | undefined,
              metadata: (intentData.metadata as IntentEventData['metadata']) || {
                source_id: (data?.name as string) || 'unknown',
                decision_time_ms: message.timestamp,
              },
            } as IntentEventData)
          : {
              metadata: {
                source_id: (data?.name as string) || 'unknown',
                decision_time_ms: message.timestamp,
              },
            },
        deciderName: 'Output',
      });
    }

    while (events.value.length > MAX_EVENTS) {
      events.value.shift();
    }
  }

  function connect() {
    const wsStore = useWebSocketStore();
    wsStore.subscribe(handleEvent);
    wsStore.connect();
  }

  function disconnect() {
    const wsStore = useWebSocketStore();
    wsStore.unsubscribe(handleEvent);
  }

  async function sendNormalizedMessage(text: string, source: string = 'dashboard') {
    sending.value = true;
    try {
      await debugApi.injectMessage({ text, source, importance: 1 });
    } finally {
      sending.value = false;
    }
  }

  async function sendIntent(text: string, responseText?: string) {
    sending.value = true;
    try {
      const request: InjectIntentRequest = {
        text,
        responseText,
        emotion: 'neutral',
        source: 'dashboard',
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
    connect,
    disconnect,
    sendNormalizedMessage,
    sendIntent,
    clearEvents,
  };
});
