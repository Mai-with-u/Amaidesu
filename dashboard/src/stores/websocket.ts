import { defineStore } from 'pinia';
import { ref } from 'vue';
import { wsClient } from '@/api/websocket';
import type { WebSocketMessage } from '@/types';

type MessageHandler = (msg: WebSocketMessage) => void;

// 模块级别的消息处理器列表
const messageHandlers: MessageHandler[] = [];

export const useWebSocketStore = defineStore('websocket', () => {
  const isConnected = ref(false);

  function connect() {
    if (isConnected.value) return;
    if (wsClient.isConnected()) {
      isConnected.value = true;
      return;
    }

    wsClient.onConnect(() => {
      isConnected.value = true;
      // 订阅所有事件，由各 handler 自行过滤
      wsClient.subscribe(['*']);
    });

    wsClient.onDisconnect(() => {
      isConnected.value = false;
    });

    wsClient.onMessage((msg) => {
      messageHandlers.forEach((handler) => handler(msg));
    });

    wsClient.connect().catch(console.error);
  }

  function subscribe(handler: MessageHandler) {
    if (!messageHandlers.includes(handler)) {
      messageHandlers.push(handler);
    }
  }

  function unsubscribe(handler: MessageHandler) {
    const index = messageHandlers.indexOf(handler);
    if (index > -1) {
      messageHandlers.splice(index, 1);
    }
  }

  function disconnect() {
    wsClient.disconnect();
    isConnected.value = false;
  }

  return {
    isConnected,
    connect,
    disconnect,
    subscribe,
    unsubscribe,
  };
});
