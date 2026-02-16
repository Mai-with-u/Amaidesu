import { defineStore } from 'pinia';
import { ref } from 'vue';
import { providerApi } from '@/api';
import type { ProviderListResponse, ProviderControlAction } from '@/types';

export const useProvidersStore = defineStore('providers', () => {
  const providers = ref<ProviderListResponse | null>(null);
  const loading = ref(false);
  const error = ref<string | null>(null);

  async function fetchProviders() {
    loading.value = true;
    error.value = null;
    try {
      const response = await providerApi.getAll();
      providers.value = response.data;
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch providers';
    } finally {
      loading.value = false;
    }
  }

  async function controlProvider(domain: string, name: string, action: ProviderControlAction) {
    try {
      const response = await providerApi.control(domain, name, { action });
      // Refresh providers after control action
      await fetchProviders();
      return response.data;
    } catch (e) {
      throw e instanceof Error ? e : new Error('Failed to control provider');
    }
  }

  return {
    providers,
    loading,
    error,
    fetchProviders,
    controlProvider,
  };
});
