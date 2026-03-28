import { defineStore } from 'pinia';
import { ref, computed, shallowRef } from 'vue';
import { useWebSocketStore } from './websocket';
import type { WebSocketMessage } from '@/types';

export interface LogEntry {
  timestamp: string;
  level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL';
  module: string;
  message: string;
}

export const useLogsStore = defineStore('logs', () => {
  const logs = shallowRef<LogEntry[]>([]);
  const isPaused = ref(false);
  const maxLogs = 500;

  let cachedModules: string[] = [];
  let logsCacheVersion = 0;
  let modulesCacheVersion = -1;

  const availableModules = computed(() => {
    if (modulesCacheVersion !== logsCacheVersion) {
      const modules = new Set<string>();
      logs.value.forEach(log => modules.add(log.module));
      cachedModules = Array.from(modules).sort();
      modulesCacheVersion = logsCacheVersion;
    }
    return cachedModules;
  });

  function handleLog(message: WebSocketMessage) {
    if (isPaused.value) return;
    if (message.type === 'log.entry' && message.data) {
      const logEntry = message.data as unknown as LogEntry;
      const newLogs = [...logs.value, logEntry];
      if (newLogs.length > maxLogs) {
        newLogs.splice(0, newLogs.length - maxLogs);
      }
      logsCacheVersion++;
      logs.value = newLogs;
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
