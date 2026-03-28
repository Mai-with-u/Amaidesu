import { defineStore } from 'pinia';
import { shallowRef } from 'vue';
import { useWebSocketStore } from './websocket';
import type { WebSocketMessage } from '@/types';

export const useEventsStore = defineStore('events', () => {
  const events = shallowRef<WebSocketMessage[]>([]);
  const maxEvents = 100;

  function handleEvent(message: WebSocketMessage) {
    const newEvents = [...events.value, message];
    if (newEvents.length > maxEvents) {
      newEvents.splice(0, newEvents.length - maxEvents);
    }
    events.value = newEvents;
  }

  function connect() {
    const wsStore = useWebSocketStore();

    // 订阅 WebSocket 消息
    wsStore.subscribe(handleEvent);

    // 启动统一连接
    wsStore.connect();
  }

  function disconnect() {
    const wsStore = useWebSocketStore();
    wsStore.unsubscribe(handleEvent);
  }

  function clearEvents() {
    events.value = [];
  }

  return {
    events,
    connect,
    disconnect,
    clearEvents,
  };
});
