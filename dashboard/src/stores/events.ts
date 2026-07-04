import { defineStore } from 'pinia';
import { shallowRef } from 'vue';
import { useWebSocketStore } from './websocket';
import type { WebSocketMessage } from '@/types';

export const useEventsStore = defineStore('events', () => {
  const events = shallowRef<WebSocketMessage[]>([]);
  const maxEvents = 500;

  function handleMessage(message: WebSocketMessage) {
    // 后端初始历史消息：替换整个列表
    if (message.type === 'events.history') {
      events.value = (message.data.events as WebSocketMessage[]) ?? [];
      return;
    }
    // 常规实时事件：追加并限长
    const newEvents = [...events.value, message];
    if (newEvents.length > maxEvents) {
      newEvents.splice(0, newEvents.length - maxEvents);
    }
    events.value = newEvents;
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
