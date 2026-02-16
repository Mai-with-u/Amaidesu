import { defineStore } from 'pinia';
import { ref } from 'vue';
import { systemApi } from '@/api';
import type { SystemStatusResponse } from '@/types';

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

  return {
    status,
    loading,
    error,
    fetchStatus,
  };
});
