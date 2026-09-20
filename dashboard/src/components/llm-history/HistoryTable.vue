<template>
  <section class="table-section">
    <el-table v-loading="loading" :data="historyData" stripe style="width: 100%">
      <el-table-column prop="timestamp" label="时间" width="170">
        <template #default="{ row }">
          <span class="mono">{{ formatDateTime(row.timestamp_ms) }}</span>
        </template>
      </el-table-column>

      <el-table-column prop="client_type" label="客户端" width="110">
        <template #default="{ row }">
          <el-tag size="small" effect="plain" :type="getClientTypeTag(row.client_type)">
            {{ getClientTypeLabel(row.client_type) }}
          </el-tag>
        </template>
      </el-table-column>

      <el-table-column prop="model_name" label="模型" min-width="150" show-overflow-tooltip>
        <template #default="{ row }">
          <span class="model-name">{{ row.model_name }}</span>
        </template>
      </el-table-column>

      <el-table-column label="Prompt" min-width="180">
        <template #default="{ row }">
          <div class="truncate-text" :title="row.prompt_preview">
            {{ row.prompt_preview || '-' }}
          </div>
        </template>
      </el-table-column>

      <el-table-column label="Response" min-width="180">
        <template #default="{ row }">
          <div class="truncate-text" :title="row.response_preview || row.error || '-'">
            {{ row.response_preview || row.error || '-' }}
          </div>
        </template>
      </el-table-column>

      <el-table-column label="Tokens" width="100" align="right">
        <template #default="{ row }">
          <span v-if="row.usage" class="token-info">
            <span class="token-prompt">{{ row.usage.prompt_tokens }}</span>
            <span class="token-sep">/</span>
            <span class="token-completion">{{ row.usage.completion_tokens }}</span>
          </span>
          <span v-else class="text-muted">-</span>
        </template>
      </el-table-column>

      <el-table-column prop="cost" label="费用" width="90" align="right">
        <template #default="{ row }">
          <span class="cost">{{ formatCost(row.cost) }}</span>
        </template>
      </el-table-column>

      <el-table-column prop="latency_ms" label="延迟" width="90" align="right">
        <template #default="{ row }">
          <span :class="['latency', getLatencyClass(row.latency_ms)]">
            {{ formatLatency(row.latency_ms) }}
          </span>
        </template>
      </el-table-column>

      <el-table-column label="缓存" width="80" align="right">
        <template #default="{ row }">
          <el-tooltip
            v-if="cacheRate(row) !== null"
            :content="`命中 ${row.cache_hit_tokens.toLocaleString()} / 未中 ${row.cache_miss_tokens.toLocaleString()} tokens`"
            placement="top"
          >
            <span class="cache-rate">{{ cacheRateText(row) }}</span>
          </el-tooltip>
          <span v-else class="text-muted" title="上游未上报缓存用量">—</span>
        </template>
      </el-table-column>

      <el-table-column prop="success" label="状态" width="80" align="center">
        <template #default="{ row }">
          <el-tag :type="row.success ? 'success' : 'danger'" size="small" effect="plain">
            {{ row.success ? '成功' : '失败' }}
          </el-tag>
        </template>
      </el-table-column>

      <el-table-column label="操作" width="100" fixed="right">
        <template #default="{ row }">
          <el-button type="primary" link size="small" @click="handleShowDetail(row)">
            详情
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 分页 -->
    <div class="pagination-wrapper">
      <el-pagination
        :current-page="queryParams.page"
        :page-size="queryParams.page_size"
        :page-sizes="[10, 20, 50, 100]"
        :total="totalRecords"
        layout="total, sizes, prev, pager, next, jumper"
        @size-change="handleSizeChange"
        @current-change="handleCurrentChange"
      />
    </div>
  </section>
</template>

<script setup lang="ts">
import type { LLMRequestHistorySummary, LLMHistoryQueryParams } from '@/types';
import {
  formatDateTime,
  formatLatency,
  getClientTypeLabel,
  formatCost,
  getLatencyClass,
  getClientTypeTag,
} from '@/utils/format';

interface Props {
  loading: boolean;
  historyData: LLMRequestHistorySummary[];
  totalRecords: number;
  queryParams: LLMHistoryQueryParams;
}

interface Emits {
  (e: 'update:queryParams', value: LLMHistoryQueryParams): void;
  (e: 'show-detail', row: LLMRequestHistorySummary): void;
  (e: 'page-change'): void;
}

const props = defineProps<Props>();
const emit = defineEmits<Emits>();

function handleShowDetail(row: LLMRequestHistorySummary) {
  emit('show-detail', row);
}

/** 逐条缓存命中率（0-100）；hit+miss 均为 0 表示上游未上报，返回 null */
function cacheRate(row: LLMRequestHistorySummary): number | null {
  const total = row.cache_hit_tokens + row.cache_miss_tokens;
  if (total <= 0) return null;
  return (row.cache_hit_tokens / total) * 100;
}

function cacheRateText(row: LLMRequestHistorySummary): string {
  const rate = cacheRate(row);
  return rate === null ? '—' : `${rate.toFixed(1)}%`;
}

function handleSizeChange(size: number) {
  emit('update:queryParams', { ...props.queryParams, page_size: size, page: 1 });
  emit('page-change');
}

function handleCurrentChange(page: number) {
  emit('update:queryParams', { ...props.queryParams, page });
  emit('page-change');
}
</script>

<style scoped>
.table-section {
  background: var(--bg-card);
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-color-light);
  padding: var(--spacing-md);
}

.mono {
  font-size: 12px;
}

.cache-rate {
  font-variant-numeric: tabular-nums;
  font-size: 12px;
}
.model-name {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-primary);
}

.truncate-text {
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  color: var(--text-secondary);
}

.token-info {
  font-family: var(--font-mono);
  font-size: 12px;
}

.token-prompt {
  color: var(--color-primary);
}

.token-sep {
  color: var(--text-placeholder);
  margin: 0 2px;
}

.token-completion {
  color: var(--color-success);
}

.cost {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-primary);
}

.latency {
  font-family: var(--font-mono);
  font-size: 12px;
}

.latency.fast {
  color: var(--color-success);
}

.latency.normal {
  color: var(--color-warning);
}

.latency.slow {
  color: var(--color-danger);
}

.text-muted {
  color: var(--text-placeholder);
}

.pagination-wrapper {
  display: flex;
  justify-content: flex-end;
  margin-top: var(--spacing-md);
  padding-top: var(--spacing-md);
  border-top: 1px solid var(--border-color-light);
}
</style>
