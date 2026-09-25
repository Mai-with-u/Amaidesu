<template>
  <div class="llm-usage">
    <!-- 页面标题 -->
    <header class="page-header">
      <div class="header-left">
        <h1 class="page-title">LLM 用量统计</h1>
        <p class="page-subtitle">监控大语言模型调用和费用</p>
      </div>
      <div class="header-actions">
        <el-button type="primary" plain :loading="loading" @click="fetchData">
          <el-icon><Refresh /></el-icon>
          刷新数据
        </el-button>
      </div>
    </header>

    <!-- 统计卡片 -->
    <section class="stats-cards">
      <div class="stat-card cost">
        <div class="stat-icon">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="12" cy="12" r="10" />
            <path d="M12 6v12M9 9h6M9 15h6" />
          </svg>
        </div>
        <div class="stat-content">
          <span class="stat-label">总费用</span>
          <span class="stat-value">¥{{ summary?.total_cost.toFixed(4) || '0.0000' }}</span>
        </div>
      </div>

      <div class="stat-card tokens">
        <div class="stat-icon">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
            <polyline points="14 2 14 8 20 8" />
            <line x1="16" y1="13" x2="8" y2="13" />
            <line x1="16" y1="17" x2="8" y2="17" />
            <polyline points="10 9 9 9 8 9" />
          </svg>
        </div>
        <div class="stat-content">
          <span class="stat-label">总 Token</span>
          <span class="stat-value">{{ formatNumber(summary?.total_tokens || 0) }}</span>
        </div>
      </div>

      <div class="stat-card calls">
        <div class="stat-icon">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
          </svg>
        </div>
        <div class="stat-content">
          <span class="stat-label">调用次数</span>
          <span class="stat-value">{{ formatNumber(summary?.total_calls || 0) }}</span>
        </div>
      </div>

      <div class="stat-card models">
        <div class="stat-icon">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <rect x="3" y="3" width="7" height="7" />
            <rect x="14" y="3" width="7" height="7" />
            <rect x="14" y="14" width="7" height="7" />
            <rect x="3" y="14" width="7" height="7" />
          </svg>
        </div>
        <div class="stat-content">
          <span class="stat-label">使用模型</span>
          <span class="stat-value">{{ summary?.model_count || 0 }}</span>
        </div>
      </div>

      <div class="stat-card rate">
        <div class="stat-icon">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
            <polyline points="22 4 12 14.01 9 11.01" />
          </svg>
        </div>
        <div class="stat-content">
          <span class="stat-label">
            缓存命中率
            <el-tooltip
              content="命中 / (命中 + 未命中)；「未上报」表示上游从未上报缓存用量"
              placement="top"
            >
              <el-icon class="cache-help"><QuestionFilled /></el-icon>
            </el-tooltip>
          </span>
          <span class="stat-value">{{ hitRateText }}</span>
          <span class="stat-sub" :title="cacheSubFullText">{{ cacheSubText }}</span>
        </div>
      </div>
    </section>

    <!-- 用量趋势图表 -->
    <section v-loading="loading" class="charts-section">
      <div class="section-header">
        <h2 class="section-title">用量趋势</h2>
        <el-radio-group v-model="rangeDays" size="small" @change="onRangeChange">
          <el-radio-button v-for="option in RANGE_OPTIONS" :key="option" :value="option">
            近 {{ option }} 天
          </el-radio-button>
        </el-radio-group>
      </div>

      <div class="chart-grid">
        <div class="chart-card">
          <h3 class="chart-title">每日费用与调用次数</h3>
          <TrendChart
            :labels="trendLabels"
            :bars="callBarSeries"
            :line="costLineSeries"
            :line-tick-format="costYuan"
            :tooltips="costTooltips"
            empty-text="暂无调用记录"
          />
        </div>

        <div class="chart-card">
          <h3 class="chart-title">每日 Token 构成</h3>
          <TrendChart
            :labels="trendLabels"
            :bars="tokenBarSeries"
            :left-tick-format="compactNumber"
            :tooltips="tokenTooltips"
            empty-text="暂无调用记录"
          />
        </div>

        <div class="chart-card">
          <h3 class="chart-title">
            每日缓存命中率
            <el-tooltip
              content="命中率 = 命中 / (命中 + 未命中)；断线表示当日无缓存用量上报"
              placement="top"
            >
              <el-icon class="cache-help"><QuestionFilled /></el-icon>
            </el-tooltip>
          </h3>
          <TrendChart
            :labels="trendLabels"
            :line="rateLineSeries"
            :line-max="100"
            :line-tick-format="percentTick"
            :tooltips="rateTooltips"
            empty-text="暂无缓存用量上报"
          />
        </div>

        <div class="chart-card">
          <h3 class="chart-title">模型费用占比</h3>
          <ModelCostDonut :items="donutItems" total-label="总费用" :format-value="costYuan" />
        </div>
      </div>
    </section>

    <!-- 模型用量表格 -->
    <section class="usage-table-section">
      <div class="section-header">
        <h2 class="section-title">模型用量详情</h2>
        <span class="model-count">{{ Object.keys(usageData).length }} 个模型</span>
      </div>

      <el-table
        v-loading="loading"
        :data="tableData"
        stripe
        style="width: 100%"
        :header-cell-style="{ background: 'var(--bg-hover)', color: 'var(--text-primary)' }"
      >
        <el-table-column prop="model_name" label="模型名称" min-width="200" fixed>
          <template #default="{ row }">
            <div
              class="model-name-cell model-name-link"
              title="查看该模型的调用历史"
              @click="goToModelHistory(row.model_name)"
            >
              <span class="model-icon">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M12 2L2 7l10 5 10-5-10-5z" />
                  <path d="M2 17l10 5 10-5" />
                  <path d="M2 12l10 5 10-5" />
                </svg>
              </span>
              <span class="model-name">{{ row.model_name }}</span>
            </div>
          </template>
        </el-table-column>

        <el-table-column prop="total_prompt_tokens" label="输入 Token" width="130" align="right">
          <template #default="{ row }">
            <span class="token-value prompt">{{ formatNumber(row.total_prompt_tokens) }}</span>
          </template>
        </el-table-column>

        <el-table-column label="上下文水位" width="180" align="left">
          <template #header>
            <span class="cache-header">
              上下文水位
              <el-tooltip
                content="最近一次调用的输入 token 占模型上下文窗口的比例；分母未配置时不展示"
                placement="top"
              >
                <el-icon class="cache-help"><QuestionFilled /></el-icon>
              </el-tooltip>
            </span>
          </template>
          <template #default="{ row }">
            <template v-if="row.context_window > 0 && row.last_call_prompt_tokens != null">
              <div class="water-level">
                <el-progress
                  :percentage="
                    Math.min(100, (row.last_call_prompt_tokens / row.context_window) * 100)
                  "
                  :stroke-width="10"
                  :format="() => ''"
                  :color="waterLevelColor(row.last_call_prompt_tokens / row.context_window)"
                  :show-text="false"
                />
                <span
                  class="water-level-text"
                  :class="{ warn: row.last_call_prompt_tokens / row.context_window > 0.8 }"
                  >{{ formatNumber(row.last_call_prompt_tokens) }} /
                  {{ formatNumber(row.context_window) }} ({{
                    ((row.last_call_prompt_tokens / row.context_window) * 100).toFixed(1)
                  }}%)</span
                >
              </div>
            </template>
            <span v-else class="water-level-empty">—</span>
          </template>
        </el-table-column>

        <el-table-column
          prop="total_completion_tokens"
          label="输出 Token"
          width="130"
          align="right"
        >
          <template #default="{ row }">
            <span class="token-value completion">{{
              formatNumber(row.total_completion_tokens)
            }}</span>
          </template>
        </el-table-column>

        <el-table-column prop="total_tokens" label="总 Token" width="130" align="right">
          <template #default="{ row }">
            <span class="token-value total">{{ formatNumber(row.total_tokens) }}</span>
          </template>
        </el-table-column>

        <el-table-column prop="total_calls" label="调用次数" width="110" align="right">
          <template #default="{ row }">
            <span class="calls-value">{{ formatNumber(row.total_calls) }}</span>
          </template>
        </el-table-column>

        <el-table-column width="140" align="right">
          <template #header>
            <span class="cache-header">
              缓存命中
              <el-tooltip
                content="来自上游上报的缓存用量；0 可能代表「未上报」而非真实零命中"
                placement="top"
              >
                <el-icon class="cache-help"><QuestionFilled /></el-icon>
              </el-tooltip>
            </span>
          </template>
          <template #default="{ row }">
            <span class="cache-value">{{ formatNumber(row.cache_hit_tokens) }}</span>
          </template>
        </el-table-column>

        <el-table-column prop="cache_miss_tokens" label="缓存未命中" width="130" align="right">
          <template #default="{ row }">
            <span class="cache-value">{{ formatNumber(row.cache_miss_tokens) }}</span>
          </template>
        </el-table-column>

        <el-table-column width="120" align="right">
          <template #header>
            <span class="cache-header">
              命中率
              <el-tooltip
                content="命中 / (命中 + 未命中)；「未上报」表示上游从未上报缓存用量"
                placement="top"
              >
                <el-icon class="cache-help"><QuestionFilled /></el-icon>
              </el-tooltip>
            </span>
          </template>
          <template #default="{ row }">
            <span v-if="row.cache_hit_rate == null" class="cache-value">未上报</span>
            <span v-else class="hit-rate-value">{{ (row.cache_hit_rate * 100).toFixed(1) }}%</span>
          </template>
        </el-table-column>

        <el-table-column prop="total_cost" label="费用" width="110" align="right">
          <template #default="{ row }">
            <span class="cost-value">¥{{ row.total_cost.toFixed(4) }}</span>
          </template>
        </el-table-column>

        <el-table-column prop="first_call_time" label="首次调用" width="180">
          <template #default="{ row }">
            <span class="time-value">{{ formatTime(row.first_call_time) }}</span>
          </template>
        </el-table-column>

        <el-table-column prop="last_call_time" label="最后调用" width="180">
          <template #default="{ row }">
            <span class="time-value">{{ formatTime(row.last_call_time) }}</span>
          </template>
        </el-table-column>
      </el-table>

      <div v-if="!loading && tableData.length === 0" class="empty-state">
        <el-icon :size="48"><Document /></el-icon>
        <p>暂无 LLM 调用记录</p>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue';
import { useRouter } from 'vue-router';
import { Refresh, Document, QuestionFilled } from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';
import { llmApi } from '@/api';
import type { LLMUsageStats, LLMUsageSummary, LLMUsageTrendsResponse } from '@/types';
import TrendChart from '@/components/llm/TrendChart.vue';
import ModelCostDonut from '@/components/llm/ModelCostDonut.vue';
import { compactNumber, costYuan } from '@/utils/chartFormat';

// 趋势图时间窗选项（天）
const RANGE_OPTIONS = [7, 30, 90];

const router = useRouter();

const loading = ref(false);
const usageData = ref<Record<string, LLMUsageStats>>({});
const summary = ref<LLMUsageSummary | null>(null);
const trends = ref<LLMUsageTrendsResponse | null>(null);
const rangeDays = ref(30);

// 将对象转换为数组用于表格显示
const tableData = computed(() => {
  return Object.values(usageData.value).sort((a, b) => b.total_cost - a.total_cost);
});

// 总体缓存命中率：null 表示上游从未上报缓存用量（≠ 真实零命中）
const hitRateText = computed(() => {
  const rate = summary.value?.cache_hit_rate;
  return rate === null || rate === undefined ? '未上报' : `${(rate * 100).toFixed(1)}%`;
});

// 卡片副行用紧凑数字保证五卡布局下不截断；精确值经 title 悬停查看
const cacheSubText = computed(() => {
  if (!summary.value) return '-';
  return `命中 ${compactNumber(summary.value.cache_hit_tokens)} · 未命中 ${compactNumber(
    summary.value.cache_miss_tokens,
  )}`;
});

const cacheSubFullText = computed(() => {
  if (!summary.value) return '';
  return `命中 ${formatNumber(summary.value.cache_hit_tokens)} · 未命中 ${formatNumber(
    summary.value.cache_miss_tokens,
  )}`;
});

// 趋势图表派生数据（后端已补零对齐连续时间轴）
const trendPoints = computed(() => trends.value?.points ?? []);

const trendLabels = computed(() => trendPoints.value.map(point => point.date.slice(5))); // MM-DD

const callBarSeries = computed(() => [
  { name: '调用次数', color: '#8b5cf6', values: trendPoints.value.map(point => point.total_calls) },
]);

const costLineSeries = computed(() => ({
  name: '费用',
  color: '#10b981',
  values: trendPoints.value.map(point => point.cost),
}));

const costTooltips = computed(() =>
  trendPoints.value.map(
    point =>
      `${point.date}\n调用 ${formatNumber(point.total_calls)} 次\n费用 ¥${point.cost.toFixed(4)}`,
  ),
);

const tokenBarSeries = computed(() => [
  {
    name: '输入 Token',
    color: '#3b82f6',
    values: trendPoints.value.map(point => point.prompt_tokens),
  },
  {
    name: '输出 Token',
    color: '#8b5cf6',
    values: trendPoints.value.map(point => point.completion_tokens),
  },
]);

const tokenTooltips = computed(() =>
  trendPoints.value.map(
    point =>
      `${point.date}\n输入 ${formatNumber(point.prompt_tokens)}\n输出 ${formatNumber(
        point.completion_tokens,
      )}`,
  ),
);

// 命中率折到 0-100 展示；null（无上报）断线
const rateLineSeries = computed(() => ({
  name: '缓存命中率',
  color: '#10b981',
  values: trendPoints.value.map(point =>
    point.cache_hit_rate === null ? null : point.cache_hit_rate * 100,
  ),
}));

const rateTooltips = computed(() =>
  trendPoints.value.map(point => {
    if (point.cache_hit_rate === null) {
      return `${point.date}\n无缓存用量上报`;
    }
    return `${point.date}\n命中率 ${(point.cache_hit_rate * 100).toFixed(1)}%\n命中 ${formatNumber(
      point.cache_hit_tokens,
    )} · 未命中 ${formatNumber(point.cache_miss_tokens)}`;
  }),
);

const percentTick = (value: number): string => `${Math.round(value)}%`;

const donutItems = computed(() =>
  tableData.value.map(model => ({ name: model.model_name, value: model.total_cost })),
);

// 跳转 LLM 历史页并按模型预置筛选（历史页读取 ?model_name=）
function goToModelHistory(modelName: string): void {
  void router.push({ path: '/llm/history', query: { model_name: modelName } });
}

// 格式化数字（添加千分位分隔符）
function formatNumber(num: number): string {
  return num.toLocaleString();
}

// 上下文水位色阶：≤80% 蓝色（安全），>80% 警告橙（即将溢出）
function waterLevelColor(ratio: number): string {
  return ratio > 0.8 ? '#f59e0b' : '#3b82f6';
}

// 格式化时间戳
function formatTime(timestamp: number | null): string {
  if (!timestamp) return '-';
  const date = new Date(timestamp); // 时间戳是毫秒级
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

// 获取数据
async function fetchTrends(): Promise<LLMUsageTrendsResponse> {
  const response = await llmApi.getUsageTrends(rangeDays.value);
  return response.data;
}

async function fetchData() {
  loading.value = true;
  try {
    const [usageResponse, summaryResponse, trendsData] = await Promise.all([
      llmApi.getUsage(),
      llmApi.getUsageSummary(),
      fetchTrends(),
    ]);

    usageData.value = usageResponse.data;
    summary.value = summaryResponse.data;
    trends.value = trendsData;
  } catch (error) {
    console.error('Failed to fetch LLM usage data:', error);
    ElMessage.error('获取 LLM 用量数据失败');
  } finally {
    loading.value = false;
  }
}

// 切换时间窗只重拉趋势接口（汇总/明细与窗口无关）
async function onRangeChange() {
  try {
    trends.value = await fetchTrends();
  } catch (error) {
    console.error('Failed to fetch LLM usage trends:', error);
    ElMessage.error('获取 LLM 用量趋势失败');
  }
}

onMounted(() => {
  fetchData();
});
</script>

<style scoped>
.llm-usage {
  max-width: 1400px;
  margin: 0 auto;
}

/* 页面标题 */
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: var(--spacing-lg);
}

.header-left {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-xs);
}

.page-title {
  font-size: 24px;
  font-weight: 700;
  color: var(--text-primary);
  margin: 0;
}

.page-subtitle {
  font-size: 14px;
  color: var(--text-secondary);
  margin: 0;
}

.header-actions {
  flex-shrink: 0;
}

/* 统计卡片 */
.stats-cards {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: var(--spacing-md);
  margin-bottom: var(--spacing-lg);
}

.stat-card {
  background: var(--bg-card);
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-color-light);
  padding: var(--spacing-lg);
  display: flex;
  align-items: center;
  gap: var(--spacing-md);
  box-shadow: var(--shadow-sm);
  transition: all var(--transition-normal);
  position: relative;
  overflow: hidden;
}

.stat-card::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  width: 4px;
  height: 100%;
  transition: background var(--transition-normal);
}

.stat-card:hover {
  box-shadow: var(--shadow-md);
  transform: translateY(-2px);
}

.stat-card.cost::before {
  background: linear-gradient(180deg, #10b981, #059669);
}

.stat-card.tokens::before {
  background: linear-gradient(180deg, #3b82f6, #2563eb);
}

.stat-card.calls::before {
  background: linear-gradient(180deg, #8b5cf6, #7c3aed);
}

.stat-card.models::before {
  background: linear-gradient(180deg, #f59e0b, #d97706);
}

.stat-card.rate::before {
  background: linear-gradient(180deg, #06b6d4, #0891b2);
}

.stat-icon {
  width: 48px;
  height: 48px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-md);
  flex-shrink: 0;
}

.stat-icon svg {
  width: 24px;
  height: 24px;
}

.stat-card.cost .stat-icon {
  background: rgba(16, 185, 129, 0.1);
  color: #10b981;
}

.stat-card.tokens .stat-icon {
  background: rgba(59, 130, 246, 0.1);
  color: #3b82f6;
}

.stat-card.calls .stat-icon {
  background: rgba(139, 92, 246, 0.1);
  color: #8b5cf6;
}

.stat-card.models .stat-icon {
  background: rgba(245, 158, 11, 0.1);
  color: #f59e0b;
}

.stat-card.rate .stat-icon {
  background: rgba(6, 182, 212, 0.1);
  color: #06b6d4;
}

.stat-content {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.stat-label {
  font-size: 12px;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.stat-value {
  font-size: 24px;
  font-weight: 700;
  color: var(--text-primary);
  font-family: var(--font-mono);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.stat-sub {
  font-size: 11px;
  color: var(--text-secondary);
  font-family: var(--font-mono);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 用量趋势图表 */
.charts-section {
  background: var(--bg-card);
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-color-light);
  padding: var(--spacing-lg);
  box-shadow: var(--shadow-sm);
  margin-bottom: var(--spacing-lg);
}

.chart-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: var(--spacing-lg);
}

.chart-card {
  min-width: 0;
  padding: var(--spacing-md);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-md);
  background: var(--bg-card);
}

.chart-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0 0 var(--spacing-md);
  display: flex;
  align-items: center;
  gap: 4px;
}

.hit-rate-value {
  font-family: var(--font-mono);
  font-weight: 600;
  color: #06b6d4;
}

/* 用量表格 */
.usage-table-section {
  background: var(--bg-card);
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-color-light);
  padding: var(--spacing-lg);
  box-shadow: var(--shadow-sm);
}

.section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: var(--spacing-md);
}

.section-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
}

.model-count {
  font-size: 12px;
  color: var(--text-secondary);
  background: var(--bg-hover);
  padding: 4px 12px;
  border-radius: var(--radius-full);
}

/* 表格样式 */
.model-name-cell {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
}

.model-icon {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--bg-hover);
  border-radius: var(--radius-sm);
  flex-shrink: 0;
}

.model-icon svg {
  width: 18px;
  height: 18px;
  color: var(--color-primary);
}

.model-name {
  font-weight: 500;
  color: var(--text-primary);
  font-family: var(--font-mono);
  font-size: 13px;
}

/* 模型名可点击：跳转历史页并预置筛选 */
.model-name-link {
  cursor: pointer;
  border-radius: var(--radius-sm);
  transition: background var(--transition-normal);
}

.model-name-link:hover .model-name {
  color: var(--color-primary);
  text-decoration: underline;
}

.token-value {
  font-family: var(--font-mono);
  font-weight: 500;
}

.token-value.prompt {
  color: #3b82f6;
}

.token-value.completion {
  color: #8b5cf6;
}

.token-value.total {
  color: var(--text-primary);
  font-weight: 600;
}

.calls-value {
  font-family: var(--font-mono);
  color: var(--text-primary);
}

/* 缓存列：表头说明图标与数值样式 */
.cache-header {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.cache-help {
  font-size: 13px;
  color: var(--text-secondary);
  cursor: help;
}

.cache-value {
  font-family: var(--font-mono);
  color: var(--text-secondary);
}

/* 上下文水位：进度条 + 分子/分母百分比；>80% 警示色 */
.water-level {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.water-level-text {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.water-level-text.warn {
  color: #f59e0b;
  font-weight: 600;
}

.water-level-empty {
  font-family: var(--font-mono);
  color: var(--text-secondary);
}

.cost-value {
  font-family: var(--font-mono);
  font-weight: 600;
  color: #10b981;
}

.time-value {
  font-size: 12px;
  font-family: var(--font-mono);
  color: var(--text-secondary);
}

/* 空状态 */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: var(--spacing-xl) * 2;
  color: var(--text-secondary);
}

.empty-state p {
  margin-top: var(--spacing-md);
  font-size: 14px;
}

/* 响应式 */
@media (max-width: 1400px) {
  .stats-cards {
    grid-template-columns: repeat(3, 1fr);
  }
}

@media (max-width: 1200px) {
  .stats-cards {
    grid-template-columns: repeat(2, 1fr);
  }

  .chart-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 768px) {
  .page-header {
    flex-direction: column;
    gap: var(--spacing-md);
  }

  .stats-cards {
    grid-template-columns: 1fr;
  }

  .stat-card {
    padding: var(--spacing-md);
  }

  .stat-value {
    font-size: 20px;
  }

  .chart-grid {
    grid-template-columns: 1fr;
  }
}
</style>
