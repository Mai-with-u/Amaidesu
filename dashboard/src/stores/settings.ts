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

  // 动作
  async function fetchSchema() {
    loading.value = true;
    error.value = null;

    try {
      const response = await api.get<ConfigSchemaResponse>('/config/schema');
      schema.value = response.data;

      // 初始化当前值（嵌套结构，递归包含子字段，与 getFieldValue 的点分遍历匹配）
      const values: Record<string, unknown> = {};
      for (const group of schema.value.groups) {
        flattenFields(group.fields, values);
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
      // 收集所有 PATCH 结果：HTTP 状态恒为 200，必须逐条 inspect 响应体的 success
      const results: ConfigUpdateResponse[] = [];

      for (const change of pendingChanges.value) {
        const request: ConfigUpdateRequest = {
          key: change.key,
          value: change.newValue,
        };

        const response = await api.patch<ConfigUpdateResponse>('/config', request);
        results.push(response.data);
      }

      // 全成功：把每条变更落到 originalValues 并清空 pending；任一失败则保持 dirty 状态
      const failedResults = results.filter(r => !r.success);
      if (failedResults.length === 0) {
        for (const change of pendingChanges.value) {
          setNestedValue(originalValues.value, change.key, change.newValue);
        }
        pendingChanges.value = [];

        // requires_restart 由后端在每次成功响应里告知，全部成功后取任意一条为 true 即生效
        const requiresRestartFlag = results.some(r => r.requires_restart);
        return {
          success: true,
          message: results[results.length - 1]?.message || '配置已保存',
          requires_restart: requiresRestartFlag,
        };
      }

      // 失败聚合：取首条失败消息 + 失败计数；保留后端中文文案
      const firstFailure = failedResults[0];
      const failureMessage =
        failedResults.length === 1
          ? firstFailure.message
          : `${firstFailure.message}（另有 ${failedResults.length - 1} 项失败）`;
      return {
        success: false,
        message: failureMessage,
        requires_restart: false,
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
  // 递归展开 fields（含 children），用嵌套结构存入 values
  function flattenFields(
    fields: import('@/types/settings').ConfigFieldSchema[],
    values: Record<string, unknown>,
  ) {
    for (const field of fields) {
      setNestedValue(values, field.key, field.value ?? field.default);
      if (field.children && field.children.length > 0) {
        flattenFields(field.children, values);
      }
    }
  }

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
    // 动作
    fetchSchema,
    saveChanges,
    restartService,
    discardChanges,
    updateCurrentValues,
    updatePendingChanges,
  };
});
