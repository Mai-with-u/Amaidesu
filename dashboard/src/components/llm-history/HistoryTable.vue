<template>
  <section class="table-section">
    <el-table
      v-loading="loading"
      :data="historyData"
      stripe
      style="width: 100%"
      :default-sort="{ prop: 'timestamp', order: 'descending' }"
      @sort-change="handleSortChange"
    >
      <el-table-column prop="timestamp" label="时间" width="170" sortable custom>
        <template #default="{ row }">
          <span class="mono">{{ formatDateTime(row.timestamp) }}</span>
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
          <div class="truncate-text" :title="getPromptPreview(row)">
            {{ getPromptPreview(row) }}
          </div>
        </template>
      </el-table-column>

      <el-table-column label="Response" min-width="180">
        <template #default="{ row }">
          <div class="truncate-text" :title="row.response_content || row.error || '-'">
            {{ truncateText(row.response_content || row.error || '-', 50) }}
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

      <el-table-column prop="cost" label="费用" width="90" align="right" sortable custom>
        <template #default="{ row }">
          <span class="cost">{{ formatCost(row.cost) }}</span>
        </template>
      </el-table-column>

      <el-table-column prop="latency_ms" label="延迟" width="90" align="right" sortable custom>
        <template #default="{ row }">
          <span :class="['latency', getLatencyClass(row.latency_ms)]">
            {{ formatLatency(row.latency_ms) }}
          </span>
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
import type { LLMRequestHistory, LLMHistoryQueryParams } from '@/types';

interface Props {
  loading: boolean;
  historyData: LLMRequestHistory[];
  totalRecords: number;
  queryParams: LLMHistoryQueryParams;
}

interface Emits {
  (e: 'update:queryParams', value: LLMHistoryQueryParams): void;
  (e: 'show-detail', row: LLMRequestHistory): void;
  (e: 'page-change'): void;
}

const props = defineProps<Props>();
const emit = defineEmits<Emits>();

// 格式化日期时间
function formatDateTime(timestamp: number): string {
  const date = new Date(timestamp);
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

// 格式化延迟
function formatLatency(ms: number): string {
  if (ms < 1000) {
    return `${ms}ms`;
  }
  return `${(ms / 1000).toFixed(2)}s`;
}

// 格式化费用
function formatCost(cost: number): string {
  return `¥${cost.toFixed(6)}`;
}

// 获取延迟样式类
function getLatencyClass(ms: number): string {
  if (ms < 1000) return 'fast';
  if (ms < 5000) return 'normal';
  return 'slow';
}

// 获取客户端类型标签
function getClientTypeTag(type: string): string {
  const typeMap: Record<string, string> = {
    llm: 'primary',
    llm_fast: 'success',
    vlm: 'warning',
    llm_local: 'info',
  };
  return typeMap[type] || 'info';
}

// 获取客户端类型标签文字
function getClientTypeLabel(type: string): string {
  const labelMap: Record<string, string> = {
    llm: 'LLM',
    llm_fast: 'Fast',
    vlm: 'VLM',
    llm_local: 'Local',
  };
  return labelMap[type] || type;
}

// 截断文本
function truncateText(text: string, maxLength: number): string {
  if (!text) return '-';
  if (text.length <= maxLength) return text;
  return text.substring(0, maxLength) + '...';
}

// 获取 Prompt 预览
function getPromptPreview(row: LLMRequestHistory): string {
  const params = row.request_params;
  if (!params) return '-';

  // 尝试从不同字段获取 prompt
  const messages = params.messages as Array<{ content?: string }> | undefined;
  if (messages && Array.isArray(messages)) {
    // 获取最后一条用户消息
    const lastUserMessage = [...messages].reverse().find((m) => typeof m.content === 'string');
    if (lastUserMessage?.content) {
      return truncateText(lastUserMessage.content, 50);
    }
  }

  // 尝试直接获取 prompt 字段
  const prompt = params.prompt as string | undefined;
  if (prompt) {
    return truncateText(prompt, 50);
  }

  return '-';
}

function handleSortChange({ prop, order }: { prop: string; order: string | null }) {
  console.log('Sort change:', prop, order);
}

function handleShowDetail(row: LLMRequestHistory) {
  emit('show-detail', row);
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
  font-family: var(--font-mono);
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
