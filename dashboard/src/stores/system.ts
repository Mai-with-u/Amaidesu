import { defineStore } from 'pinia';
import { ref } from 'vue';
import { systemApi } from '@/api';
import type { SystemStatusResponse } from '@/types';
import { useComponentsStore } from './components';

/**
 * 系统状态 store（v2.0）
 *
 * `/api/v1/system/status` 返回 running/uptime/version/python_version + groups.{collectors,agents,tools}
 * + event_bus.total_events。旧 input/decision/output 三阶段字段已删除，前端读 groups。
 */
export const useSystemStore = defineStore('system', () => {
  const status = ref<SystemStatusResponse | null>(null);
  const loading = ref(false);
  const error = ref<string | null>(null);

  async function fetchStatus() {
    loading.value = true;
    error.value = null;
    try {
      const response = await systemApi.getStatus();
      status.value = response.data;
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch status';
    } finally {
      loading.value = false;
    }
  }

  // 引用计数式轮询管理：多个组件可共享同一 setInterval，最后一个使用者离开时才真正停止
  let _pollInterval: ReturnType<typeof setInterval> | null = null;
  let _pollCount = 0;
  let _pollIntervalMs = 1000;

  async function _pollOnce() {
    await fetchStatus();
    const componentsStore = useComponentsStore();
    await componentsStore.fetchComponents();
  }

  function startPolling(intervalMs: number = 1000) {
    _pollCount++;
    if (_pollInterval === null) {
      _pollIntervalMs = intervalMs;
      // 首次启动立即调一次
      void _pollOnce();
      _pollInterval = setInterval(() => {
        void _pollOnce();
      }, _pollIntervalMs);
    }
  }

  function stopPolling() {
    _pollCount = Math.max(0, _pollCount - 1);
    if (_pollCount === 0 && _pollInterval !== null) {
      clearInterval(_pollInterval);
      _pollInterval = null;
    }
  }

  return {
    status,
    loading,
    error,
    fetchStatus,
    startPolling,
    stopPolling,
  };
});
