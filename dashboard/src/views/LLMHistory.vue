<template>
  <div class="llm-history">
    <!-- 页面标题 -->
    <header class="page-header">
      <div class="header-left">
        <h1 class="page-title">LLM 请求历史</h1>
        <p class="page-subtitle">查看和分析 LLM 请求记录</p>
      </div>
      <div class="header-actions">
        <el-tooltip
          content="缓存命中来自上游上报，0 可能代表「未上报」而非真实零命中"
          placement="bottom"
        >
          <el-tag type="warning" effect="plain" size="small">
            缓存命中 {{ formatCacheTokens(statistics?.cache_hit_tokens ?? 0) }}
          </el-tag>
        </el-tooltip>
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
import { useRoute } from 'vue-router';
import { ElMessage } from 'element-plus';
import { llmApi } from '@/api';
import type {
  LLMRequestHistory,
  LLMRequestHistorySummary,
  LLMHistoryQueryParams,
  LLMHistoryStatistics,
} from '@/types';
import HistoryFilter from '@/components/llm-history/HistoryFilter.vue';
import HistoryTable from '@/components/llm-history/HistoryTable.vue';
import HistoryDetail from '@/components/llm-history/HistoryDetail.vue';

// 状态
const loading = ref(false);
const historyData = ref<LLMRequestHistorySummary[]>([]);
const totalRecords = ref(0);
const detailVisible = ref(false);
const currentDetail = ref<LLMRequestHistory | null>(null);
const availableModels = ref<string[]>([]);
// 统计视图数据：页头展示当前时间窗内的缓存命中总量（随筛选联动）
const statistics = ref<LLMHistoryStatistics | null>(null);

function formatCacheTokens(num: number): string {
  return num.toLocaleString();
}

async function fetchStatistics() {
  try {
    const params =
      queryParams.start_time !== undefined || queryParams.end_time !== undefined
        ? { start_time: queryParams.start_time, end_time: queryParams.end_time }
        : undefined;
    const response = await llmApi.getStatistics(params);
    statistics.value = response.data;
  } catch {
    // 统计仅是辅助信息，拉取失败不打断历史列表主流程
    statistics.value = null;
  }
}
const dateRange = ref<[number, number] | null>(null);

const route = useRoute();

// 查询参数
const queryParams = reactive<LLMHistoryQueryParams>({
  page: 1,
  page_size: 20,
  model_name: undefined,
  profile_name: undefined,
  start_time: undefined,
  end_time: undefined,
  success_only: undefined,
});

// 直达筛选：外部页面（如 LLM 用量表）经 ?model_name= 跳入时预置模型筛选，
// 须在 setup 期（首拉之前）同步写入，避免初始化请求与筛选变更请求并发
const queryModelName = String(route.query.model_name ?? '').trim();
if (queryModelName) {
  queryParams.model_name = queryModelName;
}

// 获取历史数据
async function fetchHistory() {
  loading.value = true;
  try {
    // 处理日期范围（时间选择器即毫秒，后端按毫秒比较，无需换算）
    if (dateRange.value && dateRange.value.length === 2) {
      queryParams.start_time = dateRange.value[0];
      queryParams.end_time = dateRange.value[1];
    } else {
      queryParams.start_time = undefined;
      queryParams.end_time = undefined;
    }

    const response = await llmApi.getHistory(queryParams);
    historyData.value = response.data.items;
    totalRecords.value = response.data.total;

    // 历史列表与统计共用筛选时间窗，列表刷新后同步刷新统计
    void fetchStatistics();

    // 提取本页出现过的模型（兜底）：候选全量来自 /history/models，接口不可用时仍可筛当前页
    const models = new Set<string>(availableModels.value);
    response.data.items.forEach(item => {
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
  queryParams.profile_name = undefined;
  queryParams.start_time = undefined;
  queryParams.end_time = undefined;
  queryParams.success_only = undefined;
  queryParams.page = 1;
  dateRange.value = null;
  fetchHistory();
}

// 显示详情（行只带摘要，完整内容按 request_id 拉取）
async function showDetail(row: LLMRequestHistorySummary) {
  try {
    const response = await llmApi.getRequestById(row.request_id);
    currentDetail.value = response.data;
    detailVisible.value = true;
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '获取详情失败');
  }
}

// 直达详情：外部页面（如直播控制台决策卡）经 ?request_id= 跳入时自动打开
async function openFromQuery() {
  const requestId = String(route.query.request_id ?? '').trim();
  if (!requestId) return;
  try {
    const response = await llmApi.getRequestById(requestId);
    if (response.data) {
      currentDetail.value = response.data;
      detailVisible.value = true;
    } else {
      ElMessage.warning(`未找到请求: ${requestId}`);
    }
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '获取详情失败');
  }
}

// 模型筛选候选：全量来自后端 distinct 查询（与当前页内容无关），失败静默由页内提取兜底
async function fetchAvailableModels() {
  try {
    const response = await llmApi.getHistoryModels();
    availableModels.value = response.data;
  } catch {
    // 候选拉取失败不打断主流程：fetchHistory 的页内提取会补齐当前页出现过的模型
  }
}

onMounted(() => {
  void fetchAvailableModels();
  void openFromQuery();
});

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
    () => queryParams.profile_name,
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
