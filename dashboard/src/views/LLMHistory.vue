<template>
  <div class="llm-history">
    <!-- 页面标题 -->
    <header class="page-header">
      <div class="header-left">
        <h1 class="page-title">LLM 请求历史</h1>
        <p class="page-subtitle">查看和分析 LLM 请求记录</p>
      </div>
      <div class="header-actions">
        <!-- 紧凑统计条：statistics 按时间窗聚合，与表格筛选口径不同，故不放请求数 -->
        <div class="stats-strip">
          <div class="stat-item">
            <span class="stat-label">成功率</span>
            <span class="stat-value">{{ successRateText }}</span>
          </div>
          <div class="stat-item">
            <span class="stat-label">平均延迟</span>
            <span class="stat-value">{{ statsAvgLatencyText }}</span>
          </div>
          <div class="stat-item">
            <span class="stat-label">总 tokens</span>
            <span class="stat-value">{{ statsTokensText }}</span>
          </div>
          <div class="stat-item">
            <span class="stat-label">缓存命中率</span>
            <span class="stat-value">{{ cacheHitRateText }}</span>
          </div>
        </div>
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
    <HistoryDetail
      v-model:visible="detailVisible"
      :detail="currentDetail"
      :show-nav="currentRowExists"
      :has-prev="hasPrev"
      :has-next="hasNext"
      @prev="showPrevDetail"
      @next="showNextDetail"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ElMessage } from 'element-plus';
import { llmApi } from '@/api';
import type {
  LLMRequestHistory,
  LLMRequestHistorySummary,
  LLMHistoryQueryParams,
  LLMHistoryStatistics,
} from '@/types';
import { getApiErrorMessage } from '@/utils/apiError';
import { formatLatency, formatNumber, formatNumber as formatCacheTokens } from '@/utils/format';
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
// 统计视图数据：页头展示当前时间窗内的聚合指标（随筛选联动）
const statistics = ref<LLMHistoryStatistics | null>(null);

// 统计条文案：statistics 拉取失败（null）时各项降级为 "—"
const successRateText = computed<string>(() => {
  if (!statistics.value) return '—';
  return `${(statistics.value.success_rate * 100).toFixed(1)}%`;
});

const statsAvgLatencyText = computed<string>(() => {
  if (!statistics.value) return '—';
  return formatLatency(statistics.value.avg_latency_ms);
});

const statsTokensText = computed<string>(() => {
  if (!statistics.value) return '—';
  return formatNumber(statistics.value.total_tokens);
});

// 命中率为 null 表示窗口内上游未上报缓存用量（≠真实零命中），显示 "—"
const cacheHitRateText = computed<string>(() => {
  const rate = statistics.value?.cache_hit_rate;
  return rate === null || rate === undefined ? '—' : `${(rate * 100).toFixed(1)}%`;
});

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
const router = useRouter();

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

// 直达筛选：场次复盘页经 ?start_time=&end_time=（Unix 毫秒）跳入时预置时间窗，
// 同步到日期选择器（ui 显示与查询口径一致），同样在 setup 期首拉之前写入
const queryStartTime = Number(route.query.start_time ?? '');
const queryEndTime = Number(route.query.end_time ?? '');
if (
  Number.isFinite(queryStartTime) &&
  Number.isFinite(queryEndTime) &&
  queryEndTime > queryStartTime
) {
  dateRange.value = [queryStartTime, queryEndTime];
  queryParams.start_time = queryStartTime;
  queryParams.end_time = queryEndTime;
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
    ElMessage.error(getApiErrorMessage(error, '获取历史记录失败'));
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

// 详情加载统一入口：行点击、上一条/下一条都按 request_id 拉完整详情；
// 加载失败抛错给调用方，调用方决定提示方式且不改动当前详情
async function loadDetail(requestId: string): Promise<LLMRequestHistory> {
  const response = await llmApi.getRequestById(requestId);
  return response.data;
}

// 显示详情（行只带摘要，完整内容按 request_id 拉取）
async function showDetail(row: LLMRequestHistorySummary): Promise<void> {
  try {
    currentDetail.value = await loadDetail(row.request_id);
    detailVisible.value = true;
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '获取详情失败'));
  }
}

// 详情导航：以当前列表页 historyData 的顺序（时间倒序）为准。
// 经 ?request_id= 直达而该行不在当前列表数据里时，currentRowExists 为 false，两按钮隐藏
const currentRowIndex = computed<number>(() => {
  const id = currentDetail.value?.request_id;
  if (!id) return -1;
  return historyData.value.findIndex(item => item.request_id === id);
});

const currentRowExists = computed<boolean>(() => currentRowIndex.value >= 0);
const hasPrev = computed<boolean>(() => currentRowIndex.value > 0);
const hasNext = computed<boolean>(
  () => currentRowIndex.value >= 0 && currentRowIndex.value < historyData.value.length - 1,
);

async function switchDetail(offset: number): Promise<void> {
  const target = historyData.value[currentRowIndex.value + offset];
  if (!target) return;
  try {
    currentDetail.value = await loadDetail(target.request_id);
  } catch (error) {
    // 切换失败保持当前详情不变，仅提示
    ElMessage.error(getApiErrorMessage(error, '获取详情失败'));
  }
}

function showPrevDetail(): void {
  void switchDetail(-1);
}

function showNextDetail(): void {
  void switchDetail(1);
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
    ElMessage.error(getApiErrorMessage(error, '获取详情失败'));
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

// 筛选状态回写 URL：把 model_name 与时间窗写进 query（replace 不产生历史记录，
// 空值/默认态删键而非留空串），使筛选结果可直接分享与刷新恢复
function syncFilterToQuery(): void {
  const query: Record<string, string> = {};
  if (queryParams.model_name) {
    query.model_name = queryParams.model_name;
  }
  if (queryParams.start_time !== undefined && queryParams.end_time !== undefined) {
    query.start_time = String(queryParams.start_time);
    query.end_time = String(queryParams.end_time);
  }
  void router.replace({ query });
}

// 前进/后退恢复：query 变化时若与当前筛选状态一致（本页 replace 触发），跳过；
// 不一致则为用户导航，写回筛选状态——既有筛选 watcher 会自动重查并再回写（幂等）
watch(
  () => route.query,
  query => {
    const modelName = String(query.model_name ?? '').trim();
    const startTime = Number(query.start_time ?? '');
    const endTime = Number(query.end_time ?? '');
    const hasRange = Number.isFinite(startTime) && Number.isFinite(endTime) && endTime > startTime;
    const sameModel = modelName === (queryParams.model_name ?? '');
    const sameRange = hasRange
      ? queryParams.start_time === startTime && queryParams.end_time === endTime
      : queryParams.start_time === undefined && queryParams.end_time === undefined;
    if (sameModel && sameRange) return;
    queryParams.model_name = modelName || undefined;
    if (hasRange) {
      dateRange.value = [startTime, endTime];
      queryParams.start_time = startTime;
      queryParams.end_time = endTime;
    } else {
      dateRange.value = null;
      queryParams.start_time = undefined;
      queryParams.end_time = undefined;
    }
  },
);

// 监听筛选条件变化，自动触发搜索
watch(
  [
    () => queryParams.model_name,
    () => queryParams.profile_name,
    dateRange,
    () => queryParams.success_only,
  ],
  () => {
    syncFilterToQuery();
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

/* 紧凑统计条：纵排标签+数值的并排小单元 */
.stats-strip {
  display: flex;
  align-items: center;
  gap: var(--spacing-md);
  padding: 4px var(--spacing-md);
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-md);
}

.stat-item {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  line-height: 1.3;
}

.stat-label {
  font-size: 11px;
  color: var(--text-secondary);
}

.stat-value {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
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

  .stats-strip {
    width: 100%;
    justify-content: space-between;
  }
}
</style>
