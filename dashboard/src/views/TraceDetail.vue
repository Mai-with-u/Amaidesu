<template>
  <div class="trace-page">
    <!-- 页面标题 -->
    <header class="page-header">
      <div class="header-left">
        <div class="title-row">
          <el-button
            v-if="isDetailMode"
            aria-label="返回链路列表"
            :icon="ArrowLeft"
            circle
            plain
            @click="goBack"
          />
          <h1 class="page-title">{{ isDetailMode ? '消息链路追踪' : '链路追踪' }}</h1>
        </div>
        <p class="page-subtitle">
          {{
            isDetailMode ? '查看单条消息从输入到输出的完整处理链路' : '查看最近消息的全链路处理过程'
          }}
        </p>
      </div>
      <div class="header-actions">
        <el-tag v-if="isListMode" type="info" effect="plain" size="small">
          共 {{ total }} 条
        </el-tag>
        <el-button v-if="isListMode" :icon="Refresh" plain :loading="loading" @click="fetchList">
          刷新
        </el-button>
      </div>
    </header>

    <!-- 列表模式 -->
    <section v-if="isListMode" class="trace-list-section">
      <!-- 加载中：骨架屏 -->
      <div v-if="loading && traces.length === 0" class="skeleton-list">
        <el-skeleton v-for="i in 5" :key="i" :rows="2" animated class="skeleton-row" />
      </div>

      <!-- 错误 -->
      <el-result v-else-if="listError" icon="error" title="加载失败" :sub-title="listError">
        <template #extra>
          <el-button type="primary" @click="fetchList">重试</el-button>
        </template>
      </el-result>

      <!-- 空状态 -->
      <el-empty v-else-if="traces.length === 0" description="暂无链路数据，等待第一条消息..." />

      <!-- 列表表格 -->
      <div v-else class="trace-list">
        <div class="list-meta">
          <span class="auto-refresh">
            <el-icon><Loading /></el-icon>
            每 5 秒自动刷新
          </span>
        </div>
        <div
          v-for="item in traces"
          :key="item.message_id"
          class="trace-row"
          @click="openTrace(item.message_id)"
        >
          <span class="trace-id mono" :title="item.message_id">
            {{ item.message_id.slice(0, 8) }}
          </span>
          <span class="trace-source">
            <el-tag size="small" effect="plain" type="info">{{ firstMessageSource(item) }}</el-tag>
          </span>
          <span class="trace-text">{{ firstMessageText(item) }}</span>
          <span class="trace-time mono">{{ formatTime(firstMessageTimestamp(item)) }}</span>
          <span class="trace-elapsed mono">{{ formatLatency(item.total_elapsed_ms) }}</span>
          <span class="trace-arrow">
            <el-icon><ArrowRight /></el-icon>
          </span>
        </div>
      </div>
    </section>

    <!-- 详情模式 -->
    <section v-else class="trace-detail-section">
      <!-- 加载中：骨架屏 -->
      <div v-if="loading" class="skeleton-detail">
        <el-skeleton :rows="3" animated />
        <el-skeleton :rows="4" animated style="margin-top: 24px" />
        <el-skeleton :rows="3" animated style="margin-top: 24px" />
      </div>

      <!-- 错误（API 失败） -->
      <el-result
        v-else-if="detailError"
        :icon="notFound ? 'warning' : 'error'"
        :title="notFound ? '未找到该链路' : '加载失败'"
        :sub-title="detailError"
      >
        <template #extra>
          <el-button type="primary" @click="goBack">返回列表</el-button>
          <el-button v-if="!notFound" @click="fetchDetail">重试</el-button>
        </template>
      </el-result>

      <!-- 详情数据 -->
      <div v-else-if="trace" class="trace-timeline">
        <!-- 消息卡片（messages 段） -->
        <div class="timeline-node input">
          <div class="node-dot" />
          <el-card class="node-card" shadow="hover">
            <template #header>
              <div class="card-header">
                <span class="card-title">
                  <el-icon><ChatLineRound /></el-icon>
                  消息 — room.message
                </span>
                <el-tag size="small" effect="plain" type="primary">
                  {{ trace.segments.messages.length }} 条
                </el-tag>
              </div>
            </template>
            <template v-if="trace.segments.messages.length > 0">
              <div v-for="(m, i) in trace.segments.messages" :key="i" class="segment-block">
                <div class="segment-meta">
                  <span class="mono">{{ m.type }}</span>
                  <span class="mono">{{ formatDateTimeMs(m.timestamp_ms) }}</span>
                </div>
                <pre class="segment-data">{{ JSON.stringify(m.data, null, 2) }}</pre>
              </div>
            </template>
            <div v-else class="no-outputs">
              <span>暂无消息段事件</span>
            </div>
          </el-card>
        </div>

        <!-- 时间箭头 -->
        <div class="timeline-arrow">
          <span class="arrow-label mono">→ 决策段</span>
        </div>

        <!-- 决策卡片（planning 段） -->
        <div class="timeline-node decision">
          <div class="node-dot" />
          <el-card class="node-card" shadow="hover">
            <template #header>
              <div class="card-header">
                <span class="card-title">
                  <el-icon><MagicStick /></el-icon>
                  决策 — planner.checkpoint
                </span>
                <el-tag size="small" type="warning">
                  {{ trace.segments.planning.length }} 条
                </el-tag>
              </div>
            </template>
            <template v-if="trace.segments.planning.length > 0">
              <div v-for="(p, i) in trace.segments.planning" :key="i" class="segment-block">
                <div class="segment-meta">
                  <span class="mono">{{ p.type }}</span>
                  <span class="mono">{{ formatDateTimeMs(p.timestamp_ms) }}</span>
                </div>
                <pre class="segment-data">{{ JSON.stringify(p.data, null, 2) }}</pre>
              </div>
            </template>
            <div v-else class="no-outputs">
              <span>该链路暂无此段事件记录</span>
            </div>
          </el-card>
        </div>

        <!-- 时间箭头 -->
        <div class="timeline-arrow">
          <span class="arrow-label mono">→ 执行段</span>
        </div>

        <!-- 执行卡片（execution 段） -->
        <div class="timeline-node outputs">
          <div class="node-dot" />
          <el-card class="node-card" shadow="hover">
            <template #header>
              <div class="card-header">
                <span class="card-title">
                  <el-icon><Promotion /></el-icon>
                  执行 — tool.result.* / agenda.*
                </span>
                <el-tag size="small" type="success">
                  {{ trace.segments.execution.length }} 条
                </el-tag>
              </div>
            </template>
            <template v-if="trace.segments.execution.length > 0">
              <div v-for="(e, i) in trace.segments.execution" :key="i" class="segment-block">
                <div class="segment-meta">
                  <span class="mono">{{ e.type }}</span>
                  <span class="mono">{{ formatDateTimeMs(e.timestamp_ms) }}</span>
                </div>
                <pre class="segment-data">{{ JSON.stringify(e.data, null, 2) }}</pre>
              </div>
            </template>
            <div v-else class="no-outputs">
              <span>该链路暂无此段事件记录</span>
            </div>
          </el-card>
        </div>

        <!-- 汇总 -->
        <div class="timeline-summary">
          <el-card class="summary-card" shadow="never">
            <div class="summary-content">
              <span class="summary-label">消息 ID</span>
              <span class="summary-id mono" :title="trace.message_id">
                {{ trace.message_id }}
              </span>
              <span v-if="trace.simulated" class="summary-tag-simulated">模拟数据</span>
            </div>
          </el-card>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import {
  ArrowLeft,
  ArrowRight,
  Refresh,
  ChatLineRound,
  MagicStick,
  Promotion,
  Loading,
} from '@element-plus/icons-vue';
import { tracesApi } from '@/api';
import type { Trace, TraceSegmentEntry } from '@/types';

const route = useRoute();
const router = useRouter();

// ==================== 模式判断 ====================

const isDetailMode = computed(() => !!route.params.messageId);
const isListMode = computed(() => !route.params.messageId);
const messageId = computed(() => (route.params.messageId as string) || '');

// ==================== 列表模式状态 ====================

const traces = ref<Trace[]>([]);
const total = ref(0);
const loading = ref(false);
const listError = ref<string | null>(null);

// ==================== 详情模式状态 ====================

const trace = ref<Trace | null>(null);
const detailError = ref<string | null>(null);
const notFound = ref(false);

// ==================== 计算属性 ====================

// 旧 decision / outputs 字段已删除；segments 由 messages/planning/execution 三段组成

// ==================== 列表数据获取 ====================

async function fetchList() {
  loading.value = true;
  listError.value = null;
  try {
    const response = await tracesApi.list(50);
    traces.value = response.data.traces;
    total.value = response.data.total;
  } catch (error) {
    listError.value = error instanceof Error ? error.message : '获取链路列表失败';
  } finally {
    loading.value = false;
  }
}

// ==================== 详情数据获取 ====================

async function fetchDetail() {
  if (!messageId.value) return;
  loading.value = true;
  detailError.value = null;
  notFound.value = false;
  trace.value = null;
  try {
    const response = await tracesApi.get(messageId.value);
    if (!response.data.trace) {
      notFound.value = true;
      detailError.value = response.data.error || `未找到 message_id 为 ${messageId.value} 的链路`;
      trace.value = null;
    } else {
      trace.value = response.data.trace;
    }
  } catch (error) {
    detailError.value = error instanceof Error ? error.message : '获取链路详情失败';
  } finally {
    loading.value = false;
  }
}

// ==================== 交互方法 ====================

function openTrace(id: string) {
  router.push(`/traces/${id}`);
}

function goBack() {
  router.push('/traces');
}

// ==================== 格式化方法 ====================

function formatTime(tsMs: number): string {
  const date = new Date(tsMs);
  return date.toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function formatDateTimeMs(tsMs: number): string {
  const date = new Date(tsMs);
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function formatLatency(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || isNaN(ms)) return '—';
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

// ==================== 列表展示：从 segments.messages[0] 派生首条消息展示字段 ====================

interface NormalizedMessageView {
  source: string;
  text: string;
  timestamp_ms: number;
}

function extractNormalizedMessage(
  entry: TraceSegmentEntry | undefined,
): NormalizedMessageView | null {
  if (!entry) return null;
  const data = entry.data ?? {};
  const inner = (data.message as Record<string, unknown> | undefined) ?? {};
  return {
    source: (inner.source as string) ?? (data.source as string) ?? 'unknown',
    text: (inner.text as string) ?? '',
    timestamp_ms: entry.timestamp_ms ?? 0,
  };
}

function firstMessage(item: Trace): NormalizedMessageView | null {
  const first = item.segments?.messages?.[0];
  return extractNormalizedMessage(first);
}

function firstMessageSource(item: Trace): string {
  return firstMessage(item)?.source ?? 'unknown';
}

function firstMessageText(item: Trace): string {
  return firstMessage(item)?.text ?? '(无文本)';
}

function firstMessageTimestamp(item: Trace): number {
  return firstMessage(item)?.timestamp_ms ?? 0;
}

// ==================== 自动刷新（仅列表模式） ====================

let refreshTimer: ReturnType<typeof setInterval> | null = null;

function startAutoRefresh() {
  stopAutoRefresh();
  refreshTimer = setInterval(() => {
    if (!loading.value && isListMode.value) {
      fetchList();
    }
  }, 5000);
}

function stopAutoRefresh() {
  if (refreshTimer) {
    clearInterval(refreshTimer);
    refreshTimer = null;
  }
}

// ==================== 路由变化响应 ====================

watch(
  () => route.path,
  () => {
    if (isListMode.value) {
      fetchList();
      startAutoRefresh();
    } else {
      stopAutoRefresh();
      fetchDetail();
    }
  },
);

// ==================== 生命周期 ====================

onMounted(() => {
  if (isDetailMode.value) {
    fetchDetail();
    stopAutoRefresh();
  } else {
    fetchList();
    startAutoRefresh();
  }
});

onUnmounted(() => {
  stopAutoRefresh();
});
</script>

<style scoped>
.trace-page {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-lg);
  animation: fadeIn var(--transition-normal);
}

@keyframes fadeIn {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* 页面标题 */
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: var(--spacing-md);
}

.header-left {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-xs);
  flex: 1;
  min-width: 0;
}

.title-row {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
}

.header-actions {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  flex-shrink: 0;
}

/* ==================== 列表模式样式 ==================== */

.trace-list-section {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
}

.skeleton-list {
  background: var(--bg-card);
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-color-light);
  padding: var(--spacing-md);
}

.skeleton-row {
  margin-bottom: var(--spacing-md);
}

.skeleton-row:last-child {
  margin-bottom: 0;
}

.trace-list {
  background: var(--bg-card);
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-color-light);
  overflow: hidden;
}

.list-meta {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  padding: var(--spacing-sm) var(--spacing-md);
  border-bottom: 1px solid var(--border-color-light);
  background: var(--bg-elevated);
}

.auto-refresh {
  display: inline-flex;
  align-items: center;
  gap: var(--spacing-xs);
  font-size: 11px;
  color: var(--text-secondary);
}

.trace-row {
  display: grid;
  grid-template-columns: 80px 100px 1fr 80px 80px 24px;
  align-items: center;
  gap: var(--spacing-md);
  padding: var(--spacing-sm) var(--spacing-md);
  border-bottom: 1px solid var(--border-color-light);
  font-size: 13px;
  cursor: pointer;
  transition: background-color var(--transition-fast);
}

.trace-row:last-child {
  border-bottom: none;
}

.trace-row:hover {
  background-color: var(--bg-hover);
}

.trace-id {
  font-size: 12px;
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.trace-text {
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 500;
}

.trace-time,
.trace-elapsed {
  color: var(--text-secondary);
  font-size: 12px;
  text-align: right;
}

.trace-arrow {
  color: var(--text-placeholder);
  display: flex;
  justify-content: flex-end;
  transition: color var(--transition-fast);
}

.trace-row:hover .trace-arrow {
  color: var(--color-primary);
}

/* ==================== 详情模式 - 时间线样式 ==================== */

.trace-detail-section {
  position: relative;
}

.skeleton-detail {
  background: var(--bg-card);
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-color-light);
  padding: var(--spacing-lg);
}

.trace-timeline {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 0;
  max-width: 900px;
  margin: 0 auto;
  padding-left: 28px;
}

.trace-timeline::before {
  content: '';
  position: absolute;
  left: 8px;
  top: 12px;
  bottom: 12px;
  width: 2px;
  background: linear-gradient(
    to bottom,
    var(--color-collector) 0%,
    var(--color-collector) 33%,
    var(--color-agent) 33%,
    var(--color-agent) 66%,
    var(--color-tool) 66%,
    var(--color-tool) 100%
  );
}

.timeline-node {
  position: relative;
  margin-bottom: var(--spacing-md);
}

.node-dot {
  position: absolute;
  left: -28px;
  top: 18px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: var(--bg-card);
  border: 3px solid var(--color-primary);
  box-shadow: 0 0 0 4px var(--bg-app);
}

.timeline-node.input .node-dot {
  border-color: var(--color-collector);
}

.timeline-node.decision .node-dot {
  border-color: var(--color-agent);
}

.timeline-node.outputs .node-dot {
  border-color: var(--color-tool);
}

.node-card {
  border-radius: var(--radius-lg);
  transition:
    transform var(--transition-fast),
    box-shadow var(--transition-fast);
}

.node-card:hover {
  transform: translateY(-2px);
}

.timeline-node.input .node-card {
  border-left: 3px solid var(--color-collector);
}

.timeline-node.decision .node-card {
  border-left: 3px solid var(--color-agent);
}

.timeline-node.outputs .node-card {
  border-left: 3px solid var(--color-tool);
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--spacing-sm);
  flex-wrap: wrap;
}

.card-title {
  display: inline-flex;
  align-items: center;
  gap: var(--spacing-xs);
  font-weight: 600;
  font-size: 14px;
  color: var(--text-primary);
}

.header-tags {
  display: inline-flex;
  align-items: center;
  gap: var(--spacing-xs);
  flex-wrap: wrap;
}

/* 消息卡片内容 */
.message-content {
  font-size: 15px;
  line-height: 1.6;
  color: var(--text-primary);
  margin-bottom: var(--spacing-sm);
  word-break: break-word;
  white-space: pre-wrap;
}

.message-meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--spacing-md);
  font-size: 12px;
  color: var(--text-secondary);
}

.message-meta span {
  display: inline-flex;
  align-items: center;
  gap: var(--spacing-xs);
}

/* 决策卡片内容 */
.speech-bubble {
  background: var(--bg-elevated);
  border-radius: var(--radius-md);
  padding: var(--spacing-md);
  margin-bottom: var(--spacing-md);
  border-left: 3px solid var(--color-primary);
}

.bubble-label {
  display: inline-block;
  font-size: 11px;
  color: var(--text-secondary);
  margin-right: var(--spacing-sm);
  padding: 2px 8px;
  background: var(--bg-active);
  border-radius: var(--radius-sm);
  vertical-align: middle;
}

.bubble-text {
  font-size: 15px;
  line-height: 1.6;
  color: var(--text-primary);
  word-break: break-word;
  white-space: pre-wrap;
}

.meta-row {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  padding: var(--spacing-xs) 0;
  font-size: 13px;
  flex-wrap: wrap;
}

.meta-key {
  font-size: 12px;
  color: var(--text-secondary);
  min-width: 32px;
}

.action-name {
  font-size: 12px;
  color: var(--color-warning);
  background: var(--color-warning-light);
  padding: 2px 6px;
  border-radius: var(--radius-sm);
}

.action-params {
  font-size: 12px;
  color: var(--text-secondary);
  font-family: var(--font-mono);
}

/* 输出卡片内容 */
.no-outputs {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--spacing-sm);
  padding: var(--spacing-lg);
  color: var(--text-secondary);
  font-size: 13px;
}

.output-list {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
}

.output-row {
  display: flex;
  align-items: flex-start;
  gap: var(--spacing-md);
  padding: var(--spacing-sm) var(--spacing-md);
  background: var(--bg-elevated);
  border-radius: var(--radius-md);
  border: 1px solid var(--border-color-light);
  transition: border-color var(--transition-fast);
}

.output-row:hover {
  border-color: var(--color-tool);
}

.output-status {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: var(--color-success-bg);
  color: var(--color-success);
  margin-top: 2px;
}

.status-icon {
  font-size: 16px;
}

.output-body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--spacing-xs);
}

.output-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--spacing-sm);
}

.output-elapsed {
  font-size: 12px;
  color: var(--text-secondary);
}

.output-speech {
  font-size: 13px;
  color: var(--text-primary);
  word-break: break-word;
  white-space: pre-wrap;
}

.output-action {
  display: flex;
  align-items: center;
  gap: var(--spacing-xs);
  flex-wrap: wrap;
}

/* 时间箭头 */
.timeline-arrow {
  display: flex;
  justify-content: center;
  align-items: center;
  padding: var(--spacing-xs) 0;
  position: relative;
}

.arrow-label {
  font-size: 11px;
  color: var(--text-secondary);
  background: var(--bg-card);
  padding: 2px 10px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-color-light);
  position: relative;
  z-index: 1;
}

/* 总耗时卡片 */
.timeline-summary {
  margin-top: var(--spacing-md);
}

.segment-block {
  padding: var(--spacing-sm) 0;
  border-bottom: 1px dashed var(--border-color-light);
}
.segment-block:last-child {
  border-bottom: none;
}
.segment-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 11px;
  color: var(--text-secondary);
  margin-bottom: 4px;
}
.segment-data {
  margin: 0;
  padding: 8px 10px;
  background: var(--bg-elevated);
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-color-light);
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-regular);
  max-height: 220px;
  overflow: auto;
}
.summary-tag-simulated {
  margin-left: var(--spacing-sm);
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  background: var(--color-warning-light);
  color: var(--color-warning);
  font-size: 11px;
  font-weight: 500;
}

.summary-card {
  background: var(--bg-elevated);
  border-radius: var(--radius-md);
}

.summary-content {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  flex-wrap: wrap;
  font-size: 13px;
}

.summary-label {
  color: var(--text-secondary);
  font-size: 12px;
}

.summary-value {
  font-size: 18px;
  font-weight: 600;
  color: var(--color-primary);
}

.summary-divider {
  color: var(--text-placeholder);
  margin: 0 var(--spacing-xs);
}

.summary-id {
  font-size: 11px;
  color: var(--text-secondary);
  background: var(--bg-hover);
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  max-width: 280px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 通用工具类 */
.mono {
  font-family: var(--font-mono);
}

/* 响应式 */
@media (max-width: 768px) {
  .trace-row {
    grid-template-columns: 60px 1fr 60px 20px;
  }

  .trace-source,
  .trace-time {
    display: none;
  }

  .trace-timeline {
    padding-left: 24px;
  }

  .node-dot {
    left: -24px;
    width: 14px;
    height: 14px;
  }

  .trace-timeline::before {
    left: 5px;
  }

  .summary-content {
    font-size: 12px;
  }

  .summary-value {
    font-size: 16px;
  }
}
</style>
