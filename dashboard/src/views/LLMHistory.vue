<template>
  <div class="llm-history">
    <!-- 页面标题 -->
    <header class="page-header">
      <div class="header-left">
        <h1 class="page-title">LLM 请求历史</h1>
        <p class="page-subtitle">查看和分析 LLM 请求记录</p>
      </div>
      <div class="header-actions">
        <el-tag type="info" effect="plain" size="small"> 共 {{ totalRecords }} 条记录 </el-tag>
      </div>
    </header>

    <!-- 筛选工具栏 -->
    <HistoryFilter
      v-model:query-params="queryParams"
      v-model:date-range="dateRange"
      :available-models="availableModels"
      @reset="handleReset"
      @filter-change="debouncedFetchHistory"
    />

    <!-- 请求历史表格 -->
    <HistoryTable
      :loading="loading"
      :history-data="historyData"
      :total-records="totalRecords"
      :query-params="queryParams"
      @update:query-params="queryParams = $event"
      @show-detail="showDetail"
      @page-change="fetchHistory"
    />

    <!-- 请求详情弹窗 -->
    <HistoryDetail v-model:visible="detailVisible" :detail="currentDetail" />
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted, watch } from 'vue';
import { ElMessage } from 'element-plus';
import { llmApi } from '@/api';
import type { LLMRequestHistory, LLMHistoryQueryParams } from '@/types';
import HistoryFilter from '@/components/llm-history/HistoryFilter.vue';
import HistoryTable from '@/components/llm-history/HistoryTable.vue';
import HistoryDetail from '@/components/llm-history/HistoryDetail.vue';

// 状态
const loading = ref(false);
const historyData = ref<LLMRequestHistory[]>([]);
const totalRecords = ref(0);
const detailVisible = ref(false);
const currentDetail = ref<LLMRequestHistory | null>(null);
const availableModels = ref<string[]>([]);
const dateRange = ref<[number, number] | null>(null);

// 查询参数
const queryParams = reactive<LLMHistoryQueryParams>({
  page: 1,
  page_size: 20,
  model_name: undefined,
  client_type: undefined,
  start_time: undefined,
  end_time: undefined,
  success_only: undefined,
});

// 获取历史数据
async function fetchHistory() {
  loading.value = true;
  try {
    // 处理日期范围
    if (dateRange.value && dateRange.value.length === 2) {
      queryParams.start_time = Math.floor(dateRange.value[0] / 1000);
      queryParams.end_time = Math.floor(dateRange.value[1] / 1000);
    } else {
      queryParams.start_time = undefined;
      queryParams.end_time = undefined;
    }

    const response = await llmApi.getHistory(queryParams);
    historyData.value = response.data.items;
    totalRecords.value = response.data.total;

    // 提取可用模型列表
    const models = new Set<string>();
    response.data.items.forEach((item) => {
      if (item.model_name) {
        models.add(item.model_name);
      }
    });
    availableModels.value = Array.from(models);
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '获取历史记录失败');
  } finally {
    loading.value = false;
  }
}

// 重置
function handleReset() {
  queryParams.model_name = undefined;
  queryParams.client_type = undefined;
  queryParams.start_time = undefined;
  queryParams.end_time = undefined;
  queryParams.success_only = undefined;
  queryParams.page = 1;
  dateRange.value = null;
  fetchHistory();
}

// 显示详情
async function showDetail(row: LLMRequestHistory) {
  try {
    const response = await llmApi.getRequestById(row.request_id);
    currentDetail.value = response.data;
    detailVisible.value = true;
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '获取详情失败');
  }
}

// 防抖定时器
let debounceTimer: ReturnType<typeof setTimeout> | null = null;

// 防抖搜索
function debouncedFetchHistory() {
  if (debounceTimer) {
    clearTimeout(debounceTimer);
  }
  debounceTimer = setTimeout(() => {
    queryParams.page = 1;
    fetchHistory();
  }, 300);
}

// 监听筛选条件变化，自动触发搜索
watch(
  [
    () => queryParams.model_name,
    () => queryParams.client_type,
    dateRange,
    () => queryParams.success_only,
  ],
  () => {
    debouncedFetchHistory();
  },
);

// 初始化
onMounted(() => {
  fetchHistory();
});
</script>

<style scoped>
.llm-history {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
}

/* 页面标题 */
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
}

.header-left {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-xs);
}

.header-actions {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
}

@media (max-width: 768px) {
  .page-header {
    flex-direction: column;
    gap: var(--spacing-sm);
  }

  .header-actions {
    width: 100%;
    justify-content: flex-end;
  }
}
</style>
