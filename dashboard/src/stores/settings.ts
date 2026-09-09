/**
 * Settings 状态管理
 */

import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import type {
  ConfigSchemaResponse,
  ConfigGroupSchema,
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
      // 批量端点原子语义：要么全部成功落盘，要么任一校验失败整批回退（后端保证）。
      // 前端只 inspect 响应体的 success，HTTP 状态恒为 200。
      const response = await api.post<{
        success: boolean;
        message: string;
        requires_restart?: boolean;
        results?: { key: string; success: boolean }[];
        errors?: { key: string; message: string }[];
      }>('/config/batch', {
        changes: pendingChanges.value.map(c => ({ key: c.key, value: c.newValue })),
      });
      const data = response.data;

      if (data.success) {
        for (const change of pendingChanges.value) {
          setNestedValue(originalValues.value, change.key, change.newValue);
        }
        pendingChanges.value = [];
        return {
          success: true,
          message: data.message || '配置已保存',
          requires_restart: !!data.requires_restart,
        };
      }

      // 失败：保留 pendingChanges 不清空，让用户继续编辑；后端已保证磁盘零写入
      return {
        success: false,
        message: data.message || '保存配置失败',
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
