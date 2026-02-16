/**
 * Settings 状态管理
 */

import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import type {
  ConfigSchemaResponse,
  ConfigGroupSchema,
  ConfigUpdateRequest,
  ConfigUpdateResponse,
  PendingChange,
} from '@/types/settings';
import api from '@/api';

export const useSettingsStore = defineStore('settings', () => {
  // 状态
  const schema = ref<ConfigSchemaResponse | null>(null);
  const currentValues = ref<Record<string, unknown>>({});
  const originalValues = ref<Record<string, unknown>>({});
  const pendingChanges = ref<PendingChange[]>([]);
  const loading = ref(false);
  const saving = ref(false);
  const error = ref<string | null>(null);

  // 计算属性
  const groups = computed((): ConfigGroupSchema[] => {
    return schema.value?.groups ?? [];
  });

  const hasChanges = computed(() => {
    return pendingChanges.value.length > 0;
  });

  const changeCount = computed(() => {
    return pendingChanges.value.length;
  });

  const requiresRestart = computed(() => {
    return pendingChanges.value.some((change) => {
      // 检查是否有需要重启的配置变更
      const restartPrefixes = [
        'llm.',
        'llm_fast.',
        'vlm.',
        'llm_local.',
        'maicore.',
        'dashboard.',
        'http_server.',
        'logging.',
      ];
      return restartPrefixes.some((prefix) => change.key.startsWith(prefix));
    });
  });

  // 动作
  async function fetchSchema() {
    loading.value = true;
    error.value = null;

    try {
      const response = await api.get<ConfigSchemaResponse>('/config/schema');
      schema.value = response.data;

      // 初始化当前值
      const values: Record<string, unknown> = {};
      for (const group of schema.value.groups) {
        for (const field of group.fields) {
          values[field.key] = field.value ?? field.default;
        }
      }
      currentValues.value = values;
      originalValues.value = JSON.parse(JSON.stringify(values));
    } catch (e) {
      console.error('Failed to fetch config schema:', e);
      error.value = '获取配置 Schema 失败';
    } finally {
      loading.value = false;
    }
  }

  async function saveChanges(): Promise<ConfigUpdateResponse> {
    if (pendingChanges.value.length === 0) {
      return { success: true, message: '没有需要保存的变更' };
    }

    saving.value = true;
    error.value = null;

    try {
      // 逐个保存变更
      const results: ConfigUpdateResponse[] = [];
      let requiresRestartFlag = false;

      for (const change of pendingChanges.value) {
        const request: ConfigUpdateRequest = {
          key: change.key,
          value: change.newValue,
        };

        const response = await api.patch<ConfigUpdateResponse>('/config', request);
        results.push(response.data);

        if (response.data.requires_restart) {
          requiresRestartFlag = true;
        }
      }

      // 更新原始值
      for (const change of pendingChanges.value) {
        setNestedValue(originalValues.value, change.key, change.newValue);
      }

      // 清空待保存变更
      pendingChanges.value = [];

      return {
        success: results.every((r) => r.success),
        message: requiresRestartFlag ? '配置已保存，部分更改需要重启服务才能生效' : '配置已保存',
        requires_restart: requiresRestartFlag,
      };
    } catch (e) {
      console.error('Failed to save changes:', e);
      error.value = '保存配置失败';
      return { success: false, message: '保存配置失败' };
    } finally {
      saving.value = false;
    }
  }

  async function restartService(): Promise<ConfigUpdateResponse> {
    try {
      const response = await api.post<ConfigUpdateResponse>('/config/restart');
      return response.data;
    } catch (e) {
      console.error('Failed to restart service:', e);
      return { success: false, message: '重启服务失败' };
    }
  }

  function discardChanges() {
    // 重置当前值为原始值
    currentValues.value = JSON.parse(JSON.stringify(originalValues.value));
    pendingChanges.value = [];
  }

  function updateCurrentValues(values: Record<string, unknown>) {
    currentValues.value = values;
  }

  function updatePendingChanges(changes: PendingChange[]) {
    pendingChanges.value = changes;
  }

  // 辅助函数
  function setNestedValue(obj: Record<string, unknown>, key: string, value: unknown) {
    const keys = key.split('.');
    let current: Record<string, unknown> = obj;

    for (let i = 0; i < keys.length - 1; i++) {
      const k = keys[i];
      if (!current[k] || typeof current[k] !== 'object') {
        current[k] = {};
      }
      current = current[k] as Record<string, unknown>;
    }

    current[keys[keys.length - 1]] = value;
  }

  return {
    // 状态
    schema,
    currentValues,
    originalValues,
    pendingChanges,
    loading,
    saving,
    error,
    // 计算属性
    groups,
    hasChanges,
    changeCount,
    requiresRestart,
    // 动作
    fetchSchema,
    saveChanges,
    restartService,
    discardChanges,
    updateCurrentValues,
    updatePendingChanges,
  };
});
