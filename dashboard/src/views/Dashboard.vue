<template>
  <div class="dashboard">
    <header class="page-header">
      <div class="header-left">
        <h1 class="page-title">运行总览</h1>
        <p class="page-subtitle">组件运行状态 · 事件吞吐 · LLM 用量 · Agenda 节目单</p>
      </div>
      <div class="header-actions">
        <el-tag :type="status?.running ? 'success' : 'danger'" effect="plain" size="small">
          {{ status?.running ? '运行中' : '已停止' }}
        </el-tag>
        <el-button type="primary" plain @click="$router.push('/eventlog')">
          <el-icon><Document /></el-icon>
          事件流
        </el-button>
      </div>
    </header>

    <!-- KPI 顶栏（全局指标；各组件域数字只出现在下方组卡片，不在此重复） -->
    <section class="kpi-row">
      <div class="kpi-card kpi-eventbus">
        <div class="kpi-label">EventBus 总吞吐</div>
        <div class="kpi-value mono">{{ formatCount(status?.event_bus?.total_events ?? 0) }}</div>
        <div class="kpi-sub">累计事件数</div>
      </div>

      <div class="kpi-card kpi-uptime">
        <div class="kpi-label">运行时长</div>
        <div class="kpi-value mono">{{ formatUptime(status?.uptime_seconds ?? 0) }}</div>
        <div class="kpi-sub">本次启动以来</div>
      </div>

      <div class="kpi-card kpi-llm">
        <div class="kpi-label">LLM 今日成本</div>
        <div class="kpi-value mono">{{ llmCostText }}</div>
        <div class="kpi-sub">{{ llmCostSub }}</div>
      </div>

      <div class="kpi-card kpi-agenda">
        <div class="kpi-label">当前 Agenda</div>
        <div class="kpi-value mono">—</div>
        <div class="kpi-sub">节目单管理通道规划中</div>
      </div>
    </section>

    <!-- 组件分组卡片 -->
    <section class="group-cards">
      <article
        v-for="card in groupCards"
        :key="card.key"
        class="group-card"
        :class="`group-card--${card.key}`"
      >
        <div class="card-header">
          <div class="card-icon" :class="`card-icon--${card.key}`">
            <component :is="card.icon" />
          </div>
          <div class="card-title-area">
            <div class="card-title-row">
              <h3 class="card-title">{{ card.title }}</h3>
              <span class="health-badge" :class="healthClass(card.key)">
                {{ healthLabel(card.key) }}
              </span>
            </div>
            <span class="card-subtitle">{{ card.subtitle }}</span>
          </div>
        </div>

        <div class="card-stats">
          <div class="stat">
            <span class="stat-value mono">{{ card.started }}</span>
            <span class="stat-label">{{ card.statPrimary }}</span>
          </div>
          <div class="stat-divider" />
          <div class="stat">
            <span class="stat-value mono">{{ card.total }}</span>
            <span class="stat-label">{{ card.statSecondary }}</span>
          </div>
        </div>

        <div class="card-footer">
          <div class="footer-actions">
            <el-button
              v-if="card.supportsRuntimeStop"
              size="small"
              type="success"
              plain
              :loading="batchLoading[card.key] === 'start'"
              :disabled="card.started === card.total"
              @click="batchControl(card.key, 'start')"
            >
              启动全部
            </el-button>
            <el-button
              v-if="card.supportsRuntimeStop"
              size="small"
              type="danger"
              plain
              :loading="batchLoading[card.key] === 'stop'"
              :disabled="card.started === 0"
              @click="batchControl(card.key, 'stop')"
            >
              停止全部
            </el-button>
            <span v-else class="tool-hint">工具组为被动调用，无运行时启停</span>
            <span class="footer-spacer" />
            <el-button text size="small" type="primary" @click="goGroupPage(card.key)">
              查看组件 →
            </el-button>
          </div>
        </div>
      </article>
    </section>

    <!-- 系统元信息 -->
    <section class="system-info-section">
      <div class="section-header">
        <h2 class="section-title">运行时元信息</h2>
      </div>
      <div class="info-grid">
        <div class="info-card">
          <span class="info-label">版本</span>
          <span class="info-value mono">{{ status?.version || '-' }}</span>
        </div>
        <div class="info-card">
          <span class="info-label">Python</span>
          <span class="info-value mono">{{ status?.python_version || '-' }}</span>
        </div>
        <div class="info-card">
          <span class="info-label">小部件入口</span>
          <span class="info-value mono">{{ baseUrl }}/danmaku · /subtitle</span>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, h, ref } from 'vue';
import { useRouter } from 'vue-router';
import { Document } from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';
import { storeToRefs } from 'pinia';
import { useSystemStore, useComponentsStore } from '@/stores';
import { capabilitiesApi, llmApi } from '@/api';
import type { LLMUsageSummary } from '@/types';

const systemStore = useSystemStore();
const componentsStore = useComponentsStore();
const router = useRouter();

const { status } = storeToRefs(systemStore);

/** 能力条目总数（ToolRegistry 运行时 spec 数，与工具目录页同源） */
const toolCapabilityCount = ref<number | null>(null);

// ====== KPI / 卡片数据 ======

interface GroupCard {
  key: 'collectors' | 'agents' | 'tools';
  title: string;
  subtitle: string;
  started: number;
  total: number;
  statPrimary: string;
  statSecondary: string;
  icon: Record<string, unknown>;
  supportsRuntimeStop: boolean;
}

const CollectorIcon = {
  render() {
    return h(
      'svg',
      { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2' },
      [
        h('path', { d: 'M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4' }),
        h('polyline', { points: '7,10 12,15 17,10' }),
        h('line', { x1: '12', y1: '15', x2: '12', y2: '3' }),
      ],
    );
  },
};

const AgentIcon = {
  render() {
    return h(
      'svg',
      { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2' },
      [
        h('rect', { x: '4', y: '4', width: '16', height: '16', rx: '2', ry: '2' }),
        h('rect', { x: '9', y: '9', width: '6', height: '6' }),
        h('line', { x1: '9', y1: '1', x2: '9', y2: '4' }),
        h('line', { x1: '15', y1: '1', x2: '15', y2: '4' }),
        h('line', { x1: '9', y1: '20', x2: '9', y2: '23' }),
        h('line', { x1: '15', y1: '20', x2: '15', y2: '23' }),
      ],
    );
  },
};

const ToolIcon = {
  render() {
    return h(
      'svg',
      { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2' },
      [
        h('path', {
          d: 'M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z',
        }),
      ],
    );
  },
};

function startedCount(group: 'collectors' | 'agents' | 'tools'): number {
  const list = componentsStore.components?.[group];
  if (!list || list.length === 0) return 0;
  return list.filter(c => c.is_started).length;
}

function totalCount(group: 'collectors' | 'agents' | 'tools'): number {
  return componentsStore.components?.[group]?.length ?? 0;
}

const groupCards = computed<GroupCard[]>(() => {
  const cards: GroupCard[] = [
    {
      key: 'collectors',
      title: '采集器',
      subtitle: '数据入口（v2 room.message.*）',
      started: startedCount('collectors'),
      total: totalCount('collectors'),
      statPrimary: '运行中',
      statSecondary: '已配置',
      icon: CollectorIcon,
      supportsRuntimeStop: true,
    },
    {
      key: 'agents',
      title: 'Agent',
      subtitle: '决策主体（v2 planner.*）',
      started: startedCount('agents'),
      total: totalCount('agents'),
      statPrimary: '运行中',
      statSecondary: '已配置',
      icon: AgentIcon,
      supportsRuntimeStop: true,
    },
    {
      key: 'tools',
      title: '工具',
      subtitle: '能力契约（v2 tool.result.*）',
      started: totalCount('tools'),
      total: toolCapabilityCount.value ?? totalCount('tools'),
      statPrimary: '提供方',
      statSecondary: '能力条目',
      icon: ToolIcon,
      supportsRuntimeStop: false,
    },
  ];
  return cards;
});

function healthClass(group: 'collectors' | 'agents' | 'tools'): string {
  const card = groupCards.value.find(c => c.key === group);
  if (!card) return 'status-disabled';
  if (card.total === 0) return 'status-disabled';
  if (card.key === 'tools') return 'status-healthy';
  if (card.started === card.total) return 'status-healthy';
  if (card.started === 0) return 'status-stopped';
  return 'status-warning';
}

function healthLabel(group: 'collectors' | 'agents' | 'tools'): string {
  const cls = healthClass(group);
  if (cls === 'status-disabled') return '未配置';
  if (cls === 'status-stopped') return '已停止';
  if (cls === 'status-warning') return '部分运行';
  return '正常运行';
}

// ====== 批量启停（采集器 / Agent）======

const batchLoading = ref<Record<'collectors' | 'agents' | 'tools', 'start' | 'stop' | null>>({
  collectors: null,
  agents: null,
  tools: null,
});

async function batchControl(group: 'collectors' | 'agents' | 'tools', action: 'start' | 'stop') {
  if (group === 'tools') {
    ElMessage.info('工具组无运行时启停语义');
    return;
  }
  batchLoading.value[group] = action;
  try {
    const { succeeded, failed } = await componentsStore.batchControl(group, action);
    if (succeeded === 0 && failed === 0) {
      ElMessage.info(action === 'start' ? '所有组件已在运行中' : '所有组件已停止');
    } else if (failed === 0) {
      ElMessage.success(`已${action === 'start' ? '启动' : '停止'} ${succeeded} 个组件`);
    } else {
      ElMessage.warning(
        `${action === 'start' ? '启动' : '停止'}完成：${succeeded} 成功, ${failed} 失败`,
      );
    }
    await systemStore.fetchStatus();
  } catch (error) {
    ElMessage.error('批量操作失败');
    console.error('Batch control error:', error);
  } finally {
    batchLoading.value[group] = null;
  }
}

// ====== LLM 今日成本 ======

const llmSummary = ref<LLMUsageSummary | null>(null);
const llmSummaryError = ref<string | null>(null);

async function fetchLLMSummary() {
  try {
    const response = await llmApi.getUsageSummary();
    llmSummary.value = response.data;
    llmSummaryError.value = null;
  } catch (e) {
    llmSummaryError.value = e instanceof Error ? e.message : '获取 LLM 用量失败';
    llmSummary.value = null;
  }
}

const llmCostText = computed(() => {
  if (!llmSummary.value) return llmSummaryError.value ? '—' : '...';
  const summary = llmSummary.value as unknown as { today_cost?: number; today_tokens?: number };
  if (typeof summary.today_cost === 'number') {
    return `$${summary.today_cost.toFixed(2)}`;
  }
  if (typeof summary.today_tokens === 'number') {
    return `${formatCount(summary.today_tokens)} tok`;
  }
  return '—';
});

const llmCostSub = computed(() => {
  if (!llmSummary.value) return llmSummaryError.value ?? '加载中';
  return '今日累计';
});

// ====== 工具函数 ======

function formatUptime(seconds: number): string {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = Math.floor(seconds % 60);
  if (hours > 0) return `${hours}h ${minutes}m ${secs}s`;
  if (minutes > 0) return `${minutes}m ${secs}s`;
  return `${secs}s`;
}

function formatCount(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(2)}M`;
}

const baseUrl = computed(() => window.location.origin);

function goGroupPage(group: 'collectors' | 'agents' | 'tools') {
  router.push({ path: `/${group}` });
}

// ====== 生命周期 ======

onMounted(async () => {
  await systemStore.fetchStatus();
  await componentsStore.fetchComponents();
  systemStore.startPolling(1000);
  void fetchLLMSummary();
  try {
    const res = await capabilitiesApi.list();
    toolCapabilityCount.value = res.data.actions.length;
  } catch {
    toolCapabilityCount.value = null; // 失败时回退显示提供方数
  }
});

onUnmounted(() => {
  systemStore.stopPolling();
});
</script>

<style scoped>
.dashboard {
  max-width: 1400px;
  margin: 0 auto;
}

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

.header-actions {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  flex-shrink: 0;
}

/* ===== KPI ===== */

.kpi-row {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: var(--spacing-md);
  margin-bottom: var(--spacing-lg);
}

.kpi-card {
  background: var(--bg-card);
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-color-light);
  padding: var(--spacing-md) var(--spacing-md);
  display: flex;
  flex-direction: column;
  gap: 4px;
  position: relative;
  overflow: hidden;
}

.kpi-card::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: var(--border-color);
}

.kpi-uptime::before {
  background: var(--color-live);
}
.kpi-eventbus::before {
  background: var(--color-primary);
}
.kpi-llm::before {
  background: var(--color-warning);
}
.kpi-agenda::before {
  background: var(--color-agenda);
}

.kpi-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.kpi-value {
  font-size: 22px;
  font-weight: 700;
  color: var(--text-primary);
  display: flex;
  align-items: baseline;
  gap: 4px;
}

.kpi-divider {
  color: var(--text-placeholder);
  font-weight: 400;
  font-size: 16px;
}

.kpi-total {
  color: var(--text-secondary);
  font-size: 16px;
  font-weight: 500;
}

.kpi-sub {
  font-size: 11px;
  color: var(--text-placeholder);
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

/* ===== 三组卡片 ===== */

.group-cards {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--spacing-md);
  margin-bottom: var(--spacing-lg);
}

.group-card {
  background: var(--bg-card);
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-color-light);
  padding: var(--spacing-lg);
  box-shadow: var(--shadow-sm);
  transition: all var(--transition-normal);
  position: relative;
  overflow: hidden;
}

.group-card::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: var(--border-color);
}

.group-card--collectors::before {
  background: var(--color-collector);
}

.group-card--agents::before {
  background: var(--color-agent);
}

.group-card--tools::before {
  background: var(--color-tool);
}

.group-card:hover {
  box-shadow: var(--shadow-md);
}

.card-header {
  display: flex;
  align-items: center;
  gap: var(--spacing-md);
  margin-bottom: var(--spacing-md);
}

.card-icon {
  width: 44px;
  height: 44px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-md);
  flex-shrink: 0;
}

.card-icon :deep(svg) {
  width: 22px;
  height: 22px;
}

.card-icon--collectors {
  background-color: var(--color-collector-bg);
  color: var(--color-collector);
}

.card-icon--agents {
  background-color: var(--color-agent-bg);
  color: var(--color-agent);
}

.card-icon--tools {
  background-color: var(--color-tool-bg);
  color: var(--color-tool);
}

.card-title-area {
  flex: 1;
  min-width: 0;
}

.card-title-row {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
}

.card-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
}

.card-subtitle {
  font-size: 12px;
  color: var(--text-secondary);
}

.health-badge {
  font-size: 10px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: var(--radius-sm);
}

.health-badge.status-healthy {
  background-color: var(--color-success-light);
  color: var(--color-success);
}
.health-badge.status-warning {
  background-color: var(--color-warning-light);
  color: var(--color-warning);
}
.health-badge.status-stopped {
  background-color: var(--color-danger-light);
  color: var(--color-danger);
}
.health-badge.status-disabled {
  background-color: var(--bg-hover);
  color: var(--text-secondary);
}

.card-stats {
  display: flex;
  align-items: center;
  gap: var(--spacing-md);
  margin-bottom: var(--spacing-md);
}

.stat {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.stat-value {
  font-size: 22px;
  font-weight: 700;
  color: var(--text-primary);
}

.stat-label {
  font-size: 11px;
  color: var(--text-secondary);
  text-transform: uppercase;
}

.stat-divider {
  width: 1px;
  height: 28px;
  background-color: var(--border-color-light);
}

.card-footer {
  display: flex;
  align-items: center;
}

.footer-actions {
  display: flex;
  align-items: center;
  gap: var(--spacing-xs);
  flex-wrap: wrap;
}

.footer-spacer {
  flex: 1;
}

.tool-hint {
  font-size: 12px;
  color: var(--text-placeholder);
  font-style: italic;
}

/* ===== 系统信息 ===== */

.system-info-section {
  margin-bottom: var(--spacing-lg);
}

.info-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: var(--spacing-md);
}

.info-card {
  background: var(--bg-card);
  border-radius: var(--radius-md);
  border: 1px solid var(--border-color-light);
  padding: var(--spacing-md);
  display: flex;
  flex-direction: column;
  gap: var(--spacing-xs);
}

.info-label {
  font-size: 11px;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.info-value {
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
}

.mono {
  font-family: var(--font-mono);
}

/* ===== 响应式 ===== */

@media (max-width: 1200px) {
  .kpi-row {
    grid-template-columns: repeat(3, 1fr);
  }
  .group-cards {
    grid-template-columns: 1fr;
  }
  .info-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}

@media (max-width: 768px) {
  .page-header {
    flex-direction: column;
    gap: var(--spacing-md);
  }
  .kpi-row {
    grid-template-columns: repeat(2, 1fr);
  }
  .info-grid {
    grid-template-columns: 1fr;
  }
}
</style>
