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
            <div class="model-name-cell">
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
import { Refresh, Document } from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';
import { llmApi } from '@/api';
import type { LLMUsageStats, LLMUsageSummary } from '@/types';

const loading = ref(false);
const usageData = ref<Record<string, LLMUsageStats>>({});
const summary = ref<LLMUsageSummary | null>(null);

// 将对象转换为数组用于表格显示
const tableData = computed(() => {
  return Object.values(usageData.value).sort((a, b) => b.total_cost - a.total_cost);
});

// 格式化数字（添加千分位分隔符）
function formatNumber(num: number): string {
  return num.toLocaleString();
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
async function fetchData() {
  loading.value = true;
  try {
    const [usageResponse, summaryResponse] = await Promise.all([
      llmApi.getUsage(),
      llmApi.getUsageSummary(),
    ]);

    usageData.value = usageResponse.data;
    summary.value = summaryResponse.data;
  } catch (error) {
    console.error('Failed to fetch LLM usage data:', error);
    ElMessage.error('获取 LLM 用量数据失败');
  } finally {
    loading.value = false;
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
  grid-template-columns: repeat(4, 1fr);
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
@media (max-width: 1200px) {
  .stats-cards {
    grid-template-columns: repeat(2, 1fr);
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
}
</style>
