import { defineStore } from 'pinia';
import { ref } from 'vue';
import { useWebSocketStore } from './websocket';
import type { WebSocketMessage } from '@/types';

export const useEventsStore = defineStore('events', () => {
  const events = ref<WebSocketMessage[]>([]);
  const maxEvents = 100;

  function handleEvent(message: WebSocketMessage) {
    // 添加到事件列表（保留最近的 N 个事件）
    events.value.push(message);
    if (events.value.length > maxEvents) {
      events.value.shift();
    }
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
