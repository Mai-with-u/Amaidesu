<template>
  <section class="filter-toolbar">
    <div class="filter-row">
      <div class="filter-item">
        <label class="filter-label">模型</label>
        <el-select
          :model-value="queryParams.model_name"
          placeholder="全部模型"
          clearable
          filterable
          style="width: 200px"
          @update:model-value="handleModelChange"
        >
          <el-option v-for="model in availableModels" :key="model" :label="model" :value="model" />
        </el-select>
      </div>

      <div class="filter-item">
        <label class="filter-label">客户端类型</label>
        <el-select
          :model-value="queryParams.client_type"
          placeholder="全部类型"
          clearable
          style="width: 160px"
          @update:model-value="handleClientTypeChange"
        >
          <el-option label="LLM" value="llm" />
          <el-option label="LLM Fast" value="llm_fast" />
          <el-option label="VLM" value="vlm" />
          <el-option label="LLM Local" value="llm_local" />
        </el-select>
      </div>

      <div class="filter-item">
        <label class="filter-label">时间范围</label>
        <el-date-picker
          :model-value="dateRange"
          type="daterange"
          range-separator="至"
          start-placeholder="开始日期"
          end-placeholder="结束日期"
          :shortcuts="dateShortcuts"
          value-format="x"
          style="width: 280px"
          @update:model-value="handleDateRangeChange"
        />
      </div>

      <div class="filter-item">
        <label class="filter-label">状态</label>
        <el-select
          :model-value="queryParams.success_only"
          placeholder="全部状态"
          clearable
          style="width: 120px"
          @update:model-value="handleSuccessOnlyChange"
        >
          <el-option label="仅成功" :value="true" />
          <el-option label="仅失败" :value="false" />
        </el-select>
      </div>
    </div>

    <div class="filter-actions">
      <el-button @click="handleReset">
        <el-icon><Refresh /></el-icon>
        重置
      </el-button>
    </div>
  </section>
</template>

<script setup lang="ts">
import { Refresh } from '@element-plus/icons-vue';
import type { LLMHistoryQueryParams } from '@/types';

interface Props {
  queryParams: LLMHistoryQueryParams;
  availableModels: string[];
  dateRange: [number, number] | null;
}

interface Emits {
  (e: 'update:queryParams', value: LLMHistoryQueryParams): void;
  (e: 'update:dateRange', value: [number, number] | null): void;
  (e: 'reset'): void;
  (e: 'filter-change'): void;
}

const props = defineProps<Props>();
const emit = defineEmits<Emits>();

// 日期快捷选项
const dateShortcuts = [
  {
    text: '今天',
    value: () => {
      const today = new Date();
      today.setHours(0, 0, 0, 0);
      return [today.getTime(), Date.now()];
    },
  },
  {
    text: '最近 7 天',
    value: () => {
      const end = new Date();
      const start = new Date();
      start.setTime(start.getTime() - 7 * 24 * 3600 * 1000);
      return [start.getTime(), end.getTime()];
    },
  },
  {
    text: '最近 30 天',
    value: () => {
      const end = new Date();
      const start = new Date();
      start.setTime(start.getTime() - 30 * 24 * 3600 * 1000);
      return [start.getTime(), end.getTime()];
    },
  },
];

function handleModelChange(value: string | undefined) {
  emit('update:queryParams', { ...props.queryParams, model_name: value, page: 1 });
  emit('filter-change');
}

function handleClientTypeChange(value: string | undefined) {
  emit('update:queryParams', { ...props.queryParams, client_type: value, page: 1 });
  emit('filter-change');
}

function handleDateRangeChange(value: [number, number] | null) {
  emit('update:dateRange', value);
  emit('filter-change');
}

function handleSuccessOnlyChange(value: boolean | undefined) {
  emit('update:queryParams', { ...props.queryParams, success_only: value, page: 1 });
  emit('filter-change');
}

function handleReset() {
  emit('reset');
}
</script>

<style scoped>
.filter-toolbar {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
  padding: var(--spacing-md);
  background: var(--bg-card);
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-color-light);
}

.filter-row {
  display: flex;
  flex-wrap: wrap;
  gap: var(--spacing-md);
}

.filter-item {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-xs);
}

.filter-label {
  font-size: 12px;
  color: var(--text-secondary);
  font-weight: 500;
}

.filter-actions {
  display: flex;
  gap: var(--spacing-sm);
  justify-content: flex-end;
}

@media (max-width: 1200px) {
  .filter-row {
    flex-direction: column;
  }

  .filter-item :deep(.el-select),
  .filter-item :deep(.el-date-editor) {
    width: 100% !important;
  }
}

@media (max-width: 768px) {
  .filter-actions {
    flex-direction: column;
  }

  .filter-actions .el-button {
    width: 100%;
  }
}
</style>
