import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import { useWebSocketStore } from './websocket';
import type { WebSocketMessage } from '@/types';

export interface LogEntry {
  timestamp: string;
  level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL';
  module: string;
  message: string;
}

export const useLogsStore = defineStore('logs', () => {
  const logs = ref<LogEntry[]>([]);
  const isPaused = ref(false);
  const maxLogs = 500;

  // 动态收集所有出现过的模块名
  const availableModules = computed(() => {
    const modules = new Set<string>();
    logs.value.forEach((log) => modules.add(log.module));
    return Array.from(modules).sort();
  });

  function handleLog(message: WebSocketMessage) {
    if (isPaused.value) return;
    // 只处理日志类型消息
    if (message.type === 'log.entry' && message.data) {
      const logEntry = message.data as unknown as LogEntry;
      logs.value.push(logEntry);
      if (logs.value.length > maxLogs) {
        logs.value.shift();
      }
    }
  }

  function connect() {
    const wsStore = useWebSocketStore();

    // 订阅 WebSocket 消息
    wsStore.subscribe(handleLog);

    // 启动统一连接
    wsStore.connect();
  }

  function disconnect() {
    const wsStore = useWebSocketStore();
    wsStore.unsubscribe(handleLog);
  }

  function clearLogs() {
    logs.value = [];
  }

  function togglePause() {
    isPaused.value = !isPaused.value;
  }

  return {
    logs,
    isPaused,
    availableModules,
    connect,
    disconnect,
    clearLogs,
    togglePause,
  };
});
