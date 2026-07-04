import { defineStore } from 'pinia';
import { ref } from 'vue';
import { wsClient } from '@/api/websocket';
import type { WebSocketMessage } from '@/types';

type MessageHandler = (msg: WebSocketMessage) => void;

// 模块级别的消息处理器列表
const messageHandlers: MessageHandler[] = [];

let _initialized = false;

export const useWebSocketStore = defineStore('websocket', () => {
  const isConnected = ref(false);

  // 安全：即使多次调用，_initialized 短路 + wsClient 内部的 callbacks 去重（includes 检查）确保不会重复注册
  function init() {
    if (_initialized) return;

    wsClient.onConnect(() => {
      isConnected.value = true;
      // 订阅所有事件，由各 handler 自行过滤
      wsClient.subscribe(['*']);
    });

    wsClient.onDisconnect(() => {
      isConnected.value = false;
    });

    wsClient.onMessage(msg => {
      messageHandlers.forEach(handler => handler(msg));
    });

    _initialized = true;
  }

  function connect() {
    init(); // 注册与连接解耦：init() 幂等，多次 connect() 不会重复注册

    if (wsClient.isConnected()) {
      isConnected.value = true;
      return;
    }
    if (isConnected.value) return;

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
    init,
    connect,
    disconnect,
    subscribe,
    unsubscribe,
  };
});
