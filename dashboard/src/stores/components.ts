import { defineStore } from 'pinia';
import { ref } from 'vue';
import { componentApi } from '@/api';
import type { ComponentListResponse, ComponentControlAction } from '@/types';

export const useComponentsStore = defineStore('components', () => {
  const components = ref<ComponentListResponse | null>(null);
  const loading = ref(false);
  const error = ref<string | null>(null);

  async function fetchComponents() {
    loading.value = true;
    error.value = null;
    try {
      const response = await componentApi.getAll();
      components.value = response.data;
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch components';
    } finally {
      loading.value = false;
    }
  }

  async function controlComponent(phase: string, name: string, action: ComponentControlAction) {
    try {
      const response = await componentApi.control(phase, name, { action });
      await fetchComponents();
      return response.data;
    } catch (e) {
      throw e instanceof Error ? e : new Error('Failed to control component');
    }
  }

  return {
    components,
    loading,
    error,
    fetchComponents,
    controlComponent,
  };
});
