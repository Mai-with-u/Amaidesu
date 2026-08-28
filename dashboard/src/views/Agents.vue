<template>
  <div class="agents-shell">
    <!-- ============================================================== -->
    <!-- 主网格：左 240px Agent 列表 + 右 flex-1 详情                       -->
    <!-- ============================================================== -->
    <div class="agents-page">
      <!-- ============================================================ -->
      <!-- LEFT：Agent 列表（narrow, 240px）                              -->
      <!-- ============================================================ -->
      <aside class="list-panel" aria-label="Agent 列表">
        <header class="list-header">
          <div class="list-header-main">
            <h2 class="list-title">Agent</h2>
            <span class="list-count">
              <span class="list-count-running">{{ startedCount }}</span>
              <span class="list-count-divider">/</span>
              <span class="list-count-total">{{ totalCount }}</span>
            </span>
          </div>
          <div class="batch-actions">
            <el-button
              size="small"
              type="success"
              plain
              class="batch-btn"
              :loading="batchLoading === 'start'"
              :disabled="totalCount === 0 || startedCount === totalCount"
              title="启动全部"
              @click="runBatch('start')"
            >
              启动
            </el-button>
            <el-button
              size="small"
              type="danger"
              plain
              class="batch-btn"
              :loading="batchLoading === 'stop'"
              :disabled="totalCount === 0 || startedCount === 0"
              title="停止全部"
              @click="runBatch('stop')"
            >
              停止
            </el-button>
          </div>
        </header>

        <div v-if="totalCount === 0 && !loading" class="list-empty">
          <el-empty :image-size="64" description="暂无 Agent" />
        </div>
        <ul v-else class="agent-list" role="listbox">
          <li
            v-for="a in agentsList"
            :key="a.name"
            class="agent-row"
            :class="{
              'is-selected': a.name === selectedName,
              'is-running': a.is_started,
              'is-stopped': !a.is_started && a.is_enabled,
              'is-disabled': !a.is_enabled,
            }"
            role="option"
            :aria-selected="a.name === selectedName"
            @click="select(a.name)"
          >
            <span class="status-dot" aria-hidden="true" />
            <span class="agent-name" :title="a.name">{{ a.name }}</span>
            <el-tag
              v-if="a.is_started"
              size="small"
              type="success"
              effect="plain"
              class="running-tag"
            >
              运行
            </el-tag>
          </li>
        </ul>
      </aside>

      <!-- ============================================================ -->
      <!-- RIGHT：详情 + 运行轨迹（flex-1, the star）                       -->
      <!-- ============================================================ -->
      <main class="detail-panel" aria-label="Agent 详情">
        <template v-if="selectedAgent">
          <!-- 1. 详情头：名称 + 状态 + 操作 -->
          <header class="detail-header">
            <div class="detail-title-block">
              <div class="detail-title-row">
                <h1 class="detail-name">{{ selectedAgent.name }}</h1>
                <el-tag
                  size="default"
                  :type="
                    selectedAgent.is_started
                      ? 'success'
                      : selectedAgent.is_enabled
                        ? 'warning'
                        : 'info'
                  "
                  effect="dark"
                  class="status-tag"
                >
                  {{ statusLabel(selectedAgent) }}
                </el-tag>
                <span class="type-chip">类型：Agent</span>
              </div>
              <p class="detail-description">
                {{ selectedAgent.description || '（暂无描述）' }}
              </p>
            </div>
            <div class="detail-actions">
              <el-button
                type="primary"
                size="default"
                :disabled="selectedAgent.is_started"
                :loading="actionLoading[`${selectedAgent.name}-start`]"
                @click="handleControl('start')"
              >
                启动
              </el-button>
              <el-button
                size="default"
                :disabled="!selectedAgent.is_started"
                :loading="actionLoading[`${selectedAgent.name}-stop`]"
                @click="handleControl('stop')"
              >
                停止
              </el-button>
              <el-button
                type="warning"
                size="default"
                plain
                :loading="actionLoading[`${selectedAgent.name}-restart`]"
                @click="handleControl('restart')"
              >
                重启
              </el-button>
            </div>
          </header>

          <!-- 2. 元信息条：已启用 / 运行中 / 最近决策 -->
          <div class="details-strip" aria-label="状态摘要">
            <div class="stat-chip">
              <span class="chip-label">已启用</span>
              <span class="chip-value" :class="selectedAgent.is_enabled ? 'chip-yes' : 'chip-no'">
                {{ selectedAgent.is_enabled ? '是' : '否' }}
              </span>
            </div>
            <div class="stat-chip">
              <span class="chip-label">运行中</span>
              <span class="chip-value" :class="selectedAgent.is_started ? 'chip-yes' : 'chip-no'">
                {{ selectedAgent.is_started ? '是' : '否' }}
              </span>
            </div>
            <div class="stat-chip stat-chip--accent">
              <span class="chip-label">最近决策</span>
              <span class="chip-value mono">{{ latestDecisionLabel }}</span>
            </div>
          </div>

          <!-- 3. 运行轨迹：THE MAIN SPACE -->
          <section class="stream-panel" aria-label="运行轨迹">
            <header class="stream-header">
              <div class="stream-header-row stream-header-row--main">
                <div class="stream-title-block">
                  <span class="stream-pulse" aria-hidden="true" />
                  <h3 class="stream-title">运行轨迹</h3>
                  <span class="stream-subtitle">
                    · 按事件族归属（单 Agent 场景精确，多 Agent 并行时按时间近似）
                  </span>
                  <el-tag size="small" type="info" effect="plain" class="stream-count">
                    {{ displayedEntries.length }} / {{ STREAM_CAP }}
                  </el-tag>
                </div>
                <div class="stream-controls">
                  <el-button
                    size="small"
                    :type="paused ? 'primary' : 'default'"
                    @click="togglePause"
                  >
                    {{ paused ? '继续' : '暂停' }}
                  </el-button>
                  <el-button
                    size="small"
                    :disabled="streamBuffer.length === 0"
                    @click="clearStream"
                  >
                    清空
                  </el-button>
                </div>
              </div>
              <div class="stream-header-row stream-header-row--filter">
                <span class="filter-label">阶段：</span>
                <el-check-tag
                  v-for="f in filterOptions"
                  :key="f.value"
                  :checked="activeFilter === f.value"
                  class="filter-chip"
                  @change="activeFilter = f.value"
                >
                  {{ f.label }}
                </el-check-tag>
              </div>
            </header>

            <p class="stream-note">
              三族合并：planner.*（想）/ agenda.*（程）/ tool.result.*（做）； tool.result
              失败时显示红色 badge 与错误摘要。
            </p>

            <div ref="streamScrollRef" class="stream-scroll">
              <div v-if="displayedEntries.length === 0" class="stream-empty">
                <span class="stream-empty-icon" aria-hidden="true">∅</span>
                <p>
                  {{
                    streamBuffer.length === 0
                      ? '暂无轨迹——等待 Agent 活动（planner/agenda/tool.result）'
                      : '当前过滤下无匹配条目'
                  }}
                </p>
              </div>
              <ul v-else class="stream-list">
                <li
                  v-for="item in displayedEntries"
                  :key="item.id"
                  class="stream-item"
                  :class="{ 'is-failed': item.failed }"
                >
                  <span
                    class="stage-badge"
                    :class="[`stage-badge--${item.stage}`, { 'is-failed': item.failed }]"
                    aria-hidden="true"
                  >
                    {{ stageLabel(item.stage) }}
                  </span>
                  <span class="stream-item-type mono">{{ item.eventType }}</span>
                  <span class="stream-item-content" :class="{ 'is-failed': item.failed }">
                    {{ item.summary }}
                  </span>
                  <span class="stream-item-time mono">
                    {{ relativeTime(item.timestamp) }}
                  </span>
                </li>
              </ul>
            </div>
          </section>
        </template>

        <div v-else class="detail-empty">
          <el-empty description="从左侧选择一个 Agent 查看详情与运行轨迹" />
        </div>
      </main>
    </div>

    <!-- 4. 规划中笔记（页面底部，muted） -->
    <div class="page-notes" aria-label="规划中提示">
      <p class="page-note">Agent 内部指标（缓冲/置信度/回复延迟）需后端暴露——规划中</p>
      <p class="page-note">声明工具清单与工具审计需后端只读端点——规划中</p>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * Agents 页面 —— Master-Detail 版（与 Collectors.vue 镜像）
 *
 * 改动要点（vs 旧版）：
 * - 旧版：counts header + 批量按钮 + 卡片网格 + 侧栏决策脉搏。
 * - 新版：左 240px Agent 列表 + 右详情三段（详情头 / 元信息条 / 运行轨迹）。
 *   旧版侧栏 feed 升格为页面主角（flex-1）；批量按钮下放到左列表头。
 *
 * 数据流归因：
 * - 运行轨迹按三事件族（planner.* / agenda.* / tool.result.*）合并，
 *   对每个 entry 标 stage badge（想/程/做）+ 失败标记（tool.result 红色）。
 * - 单 Agent 场景下三族前缀与目标 Agent 强相关（planner/agenda 由 Agent 触发，
 *   tool.result 由 Agent 调用）；多 Agent 并行时按时间近似归属（见 subtitle）。
 * - 后端后续票：事件负载增加 agent-identity 字段可消除近似归因。
 */
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import { storeToRefs } from 'pinia';
import { useComponentsStore, useEventsStore } from '@/stores';
import type { ComponentControlAction, ComponentSummary, WebSocketMessage } from '@/types';
import { summarizeEvent } from '@/utils/eventSummary';

// ============================================================
// Store + 基础状态
// ============================================================

const componentsStore = useComponentsStore();
const eventsStore = useEventsStore();
const { agentsList, loading } = storeToRefs(componentsStore);
const { events } = storeToRefs(eventsStore);

const STREAM_CAP = 100;

const totalCount = computed(() => agentsList.value.length);
const startedCount = computed(() => agentsList.value.filter(a => a.is_started).length);

// ----- 选中状态：默认首个 RUNNING，否则首个 enabled，否则首个 -----
const selectedName = ref<string | null>(null);

function pickDefault(): string | null {
  const list = agentsList.value;
  if (list.length === 0) return null;
  const running = list.find(a => a.is_started);
  if (running) return running.name;
  const enabled = list.find(a => a.is_enabled);
  if (enabled) return enabled.name;
  return list[0].name;
}

watch(
  agentsList,
  list => {
    if (list.length === 0) {
      selectedName.value = null;
      return;
    }
    // 选中项仍存在 → 保持
    if (selectedName.value && list.some(a => a.name === selectedName.value)) return;
    // 否则重选默认
    selectedName.value = pickDefault();
  },
  { immediate: true },
);

const selectedAgent = computed<ComponentSummary | null>(
  () => agentsList.value.find(a => a.name === selectedName.value) ?? null,
);

function select(name: string): void {
  selectedName.value = name;
}

// ----- 启停状态：按 (name, action) 维度跟踪 loading -----
const actionLoading = reactive<Record<string, boolean>>({});
const batchLoading = ref<'start' | 'stop' | null>(null);

async function handleControl(action: ComponentControlAction): Promise<void> {
  const name = selectedName.value;
  if (!name) return;
  const key = `${name}-${action}`;
  actionLoading[key] = true;
  try {
    const result = await componentsStore.controlComponent('agents', name, action);
    ElMessage.success(result.message);
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '操作失败');
  } finally {
    actionLoading[key] = false;
  }
}

async function runBatch(action: 'start' | 'stop'): Promise<void> {
  batchLoading.value = action;
  try {
    const { succeeded, failed, messages } = await componentsStore.batchControl('agents', action);
    if (succeeded === 0 && failed === 0) {
      ElMessage.info(action === 'start' ? '所有 Agent 已在运行中' : '所有 Agent 已停止');
    } else if (failed === 0) {
      ElMessage.success(`已${action === 'start' ? '启动' : '停止'} ${succeeded} 个 Agent`);
    } else {
      const failureHints = messages
        .filter(m => m.includes('失败') || m.includes('未启动') || m.includes('已停止'))
        .slice(0, 2)
        .join('；');
      ElMessage.warning(
        `${action === 'start' ? '启动' : '停止'}完成：${succeeded} 成功, ${failed} 失败${failureHints ? `（${failureHints}）` : ''}`,
      );
    }
  } catch (error) {
    ElMessage.error('批量操作失败');
    console.error('Batch control error:', error);
  } finally {
    batchLoading.value = null;
  }
}

function statusLabel(a: ComponentSummary): string {
  if (a.is_started) return '运行中';
  if (a.is_enabled) return '已停止';
  return '未启用';
}

// ============================================================
// "最近决策"指标：planner.* 最新事件的相对时间
// ============================================================

const latestDecisionLabel = computed<string>(() => {
  // events store 按 timestamp 升序；末条即最新。逆序找第一条 planner.*。
  const all = events.value;
  for (let i = all.length - 1; i >= 0; i--) {
    if (all[i].type.startsWith('planner.')) {
      return relativeTime(all[i].timestamp);
    }
  }
  return '—';
});

// ============================================================
// 运行轨迹：三族合并 + 阶段 badge + 失败标记
// ============================================================

type StageKind = 'planner' | 'agenda' | 'tool';
type FilterKind = 'all' | StageKind;

const filterOptions: { value: FilterKind; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'planner', label: '想' },
  { value: 'agenda', label: '程' },
  { value: 'tool', label: '做' },
];

const stageLabels: Record<StageKind, string> = {
  planner: '想',
  agenda: '程',
  tool: '做',
};

function stageLabel(stage: StageKind): string {
  return stageLabels[stage];
}

function getStage(type: string): StageKind | null {
  if (type.startsWith('planner.')) return 'planner';
  if (type.startsWith('agenda.')) return 'agenda';
  if (type.startsWith('tool.result.')) return 'tool';
  return null;
}

function isToolFailed(data: WebSocketMessage['data']): boolean {
  const status = String((data as Record<string, unknown>).status ?? '').toLowerCase();
  return status === 'failed' || status === 'failure' || status === 'error';
}

interface StreamItem {
  id: string;
  eventType: string;
  stage: StageKind;
  summary: string;
  timestamp: number;
  failed: boolean;
}

// 暂停时不再向 UI 追加（counter 也不前进——按需求"暂停=停渲染"）
// 但 store 仍持续接收（不消费 = 不丢消息）。
const paused = ref(false);

// "last shown" 缓冲：累计 view-ready 流条目；最多 STREAM_CAP；超出从头丢。
const streamBuffer = ref<StreamItem[]>([]);

// 从 events store → 按三族过滤 → 本地缓冲
watch(
  [events, paused],
  ([evts, isPaused]) => {
    if (isPaused) return;
    const slice = evts.slice(-STREAM_CAP * 2);
    const fresh: StreamItem[] = [];
    for (const e of slice) {
      const stage = getStage(e.type);
      if (!stage) continue;
      fresh.push({
        id: e.id,
        eventType: e.type,
        stage,
        summary: summarizeEvent(e.type, e.data),
        timestamp: e.timestamp,
        failed: stage === 'tool' && isToolFailed(e.data),
      });
    }
    streamBuffer.value = fresh.slice(-STREAM_CAP);
  },
  { immediate: true },
);

const activeFilter = ref<FilterKind>('all');

// 视图层：按 activeFilter 过滤；保持时间升序展示（新条目在末尾）。
const displayedEntries = computed<StreamItem[]>(() => {
  if (activeFilter.value === 'all') return streamBuffer.value;
  return streamBuffer.value.filter(e => e.stage === activeFilter.value);
});

function togglePause(): void {
  paused.value = !paused.value;
}

function clearStream(): void {
  streamBuffer.value = [];
}

// ============================================================
// 自动滚动：新条目追加时滚到底部，除非用户已向上滚动
// ============================================================

const streamScrollRef = ref<HTMLElement | null>(null);
// 距底 < 32px 视为"在底部"
const SCROLL_BOTTOM_THRESHOLD_PX = 32;

function isAtBottom(el: HTMLElement): boolean {
  return el.scrollHeight - el.scrollTop - el.clientHeight <= SCROLL_BOTTOM_THRESHOLD_PX;
}

watch(displayedEntries, async () => {
  await nextTick();
  const el = streamScrollRef.value;
  if (!el) return;
  // 用户滚到底 → 跟到底；用户向上滚动则不强制。
  if (isAtBottom(el)) {
    el.scrollTop = el.scrollHeight;
  }
});

// ============================================================
// 工具：相对时间
// ============================================================

function relativeTime(timestampMs: number): string {
  // 后端事件 timestamp 是 Unix 秒（参见 utils/eventSummary.ts 注释）
  const nowSec = Date.now() / 1000;
  const tsSec = timestampMs > 1e12 ? timestampMs / 1000 : timestampMs;
  const diffSec = Math.max(0, Math.floor(nowSec - tsSec));
  if (diffSec < 5) return '刚刚';
  if (diffSec < 60) return `${diffSec}s 前`;
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m 前`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h 前`;
  return `${Math.floor(diffSec / 86400)}d 前`;
}

// ============================================================
// 生命周期
// ============================================================

onMounted(() => {
  componentsStore.fetchComponents();
});
</script>

<style scoped>
/* ============================================================ */
/* 页面布局：flex shell 包裹 grid（左 240 + 右 1）              */
/* ============================================================ */
.agents-shell {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
  height: calc(100vh - var(--header-height) - 2 * var(--spacing-lg));
  min-height: 640px;
}

.agents-page {
  display: grid;
  grid-template-columns: 240px minmax(0, 1fr);
  gap: var(--spacing-md);
  flex: 1;
  min-height: 0;
}

/* ============================================================ */
/* LEFT：列表                                                    */
/* ============================================================ */
.list-panel {
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.list-header {
  padding: var(--spacing-md);
  border-bottom: 1px solid var(--border-color-light);
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
  background: var(--bg-card);
  flex-shrink: 0;
}

.list-header-main {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--spacing-sm);
}

.list-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
  letter-spacing: 0.02em;
  text-transform: uppercase;
}

.list-count {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-secondary);
  display: inline-flex;
  align-items: baseline;
  gap: 2px;
}

.list-count-running {
  color: var(--color-agent);
  font-weight: 600;
  font-size: 13px;
}

.list-count-divider {
  color: var(--text-placeholder);
  margin: 0 1px;
}

.list-count-total {
  color: var(--text-regular);
}

.batch-actions {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
}

.batch-actions :deep(.el-button) {
  margin-left: 0;
  width: 100%;
}

.list-empty {
  padding: var(--spacing-lg) var(--spacing-sm);
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}

.agent-list {
  list-style: none;
  margin: 0;
  padding: var(--spacing-xs);
  overflow-y: auto;
  flex: 1;
}

.agent-list::-webkit-scrollbar {
  width: 6px;
}
.agent-list::-webkit-scrollbar-thumb {
  background: var(--border-color-dark);
  border-radius: 3px;
}

.agent-row {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  padding: var(--spacing-sm) var(--spacing-md);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition:
    background var(--transition-fast),
    transform var(--transition-fast);
  margin-bottom: 2px;
  user-select: none;
}

.agent-row:hover {
  background: var(--bg-hover);
}

.agent-row.is-selected {
  background: var(--color-agent-bg);
  box-shadow: inset 3px 0 0 0 var(--color-agent);
}

.agent-row.is-selected .agent-name {
  color: var(--text-primary);
  font-weight: 600;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
  border: 1.5px solid var(--text-placeholder);
  background: transparent;
  transition: background var(--transition-normal);
}

.agent-row.is-running .status-dot {
  background: var(--color-success);
  border-color: var(--color-success);
  box-shadow: 0 0 0 0 var(--color-success);
  animation: pulse-running 2s ease-in-out infinite;
}

.agent-row.is-stopped .status-dot {
  background: var(--color-info);
  border-color: var(--color-info);
}

.agent-row.is-disabled .status-dot {
  border-style: dashed;
  border-color: var(--text-placeholder);
  background: transparent;
}

@keyframes pulse-running {
  0%,
  100% {
    box-shadow: 0 0 0 0 rgba(103, 194, 58, 0.4);
  }
  50% {
    box-shadow: 0 0 0 4px rgba(103, 194, 58, 0);
  }
}

.agent-name {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  color: var(--text-regular);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--font-mono);
}

.running-tag {
  flex-shrink: 0;
  font-size: 10px;
  height: 18px;
  padding: 0 6px;
  line-height: 16px;
}

/* ============================================================ */
/* RIGHT：详情面板                                              */
/* ============================================================ */
.detail-panel {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
  min-width: 0;
  overflow: hidden;
}

/* ----- 1. 详情头 ----- */
.detail-header {
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
  padding: var(--spacing-md) var(--spacing-lg);
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: var(--spacing-md);
  flex-shrink: 0;
}

.detail-title-block {
  flex: 1;
  min-width: 0;
}

.detail-title-row {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  flex-wrap: wrap;
}

.detail-name {
  font-size: 24px;
  font-weight: 700;
  color: var(--text-primary);
  margin: 0;
  font-family: var(--font-mono);
  letter-spacing: -0.01em;
}

.status-tag {
  font-weight: 500;
}

.type-chip {
  font-size: 11px;
  color: var(--text-secondary);
  padding: 2px 8px;
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-sm);
  background: var(--bg-page);
}

.detail-description {
  font-size: 13px;
  color: var(--text-regular);
  margin: var(--spacing-sm) 0 0;
  line-height: 1.6;
  max-width: 720px;
}

.detail-actions {
  display: flex;
  gap: var(--spacing-sm);
  flex-shrink: 0;
}

/* ----- 2. 元信息条 ----- */
.details-strip {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  flex-wrap: wrap;
  flex-shrink: 0;
}

.stat-chip {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  padding: 6px 12px;
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-md);
  font-size: 12px;
}

.stat-chip--accent {
  background: var(--color-agent-bg);
  border-color: transparent;
}

.chip-label {
  color: var(--text-secondary);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.chip-value {
  color: var(--text-primary);
  font-weight: 600;
}

.chip-yes {
  color: var(--color-success);
}

.chip-no {
  color: var(--text-placeholder);
}

/* ----- 3. 运行轨迹：主角 ----- */
.stream-panel {
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.stream-header {
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
}

.stream-header-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--spacing-sm) var(--spacing-lg);
  gap: var(--spacing-md);
}

.stream-header-row--main {
  border-bottom: 1px solid var(--border-color-light);
}

.stream-header-row--filter {
  justify-content: flex-start;
  background: var(--bg-page);
  padding-top: var(--spacing-xs);
  padding-bottom: var(--spacing-xs);
  flex-wrap: wrap;
  gap: var(--spacing-sm);
}

.stream-title-block {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  min-width: 0;
  flex: 1;
}

.stream-pulse {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--color-agent);
  box-shadow: 0 0 0 0 var(--color-agent);
  animation: pulse-stream 2.5s ease-in-out infinite;
  flex-shrink: 0;
}

@keyframes pulse-stream {
  0%,
  100% {
    box-shadow: 0 0 0 0 rgba(139, 92, 246, 0.5);
  }
  50% {
    box-shadow: 0 0 0 6px rgba(139, 92, 246, 0);
  }
}

.stream-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
  flex-shrink: 0;
}

.stream-subtitle {
  font-size: 11px;
  color: var(--text-placeholder);
  font-style: italic;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.stream-count {
  flex-shrink: 0;
  font-family: var(--font-mono);
}

.stream-controls {
  display: flex;
  gap: 6px;
  flex-shrink: 0;
}

.stream-filter {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.filter-label {
  font-size: 11px;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  margin-right: 2px;
}

.filter-chip {
  font-family: var(--font-mono);
}

.stream-note {
  font-size: 11px;
  color: var(--text-placeholder);
  margin: 0;
  padding: var(--spacing-xs) var(--spacing-lg);
  background: var(--bg-page);
  border-bottom: 1px solid var(--border-color-light);
  line-height: 1.5;
}

.stream-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: var(--spacing-xs) 0;
}

.stream-scroll::-webkit-scrollbar {
  width: 8px;
}

.stream-scroll::-webkit-scrollbar-thumb {
  background: var(--border-color-dark);
  border-radius: 4px;
}

.stream-empty {
  height: 100%;
  min-height: 200px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--spacing-md);
  color: var(--text-placeholder);
  font-size: 13px;
  text-align: center;
  padding: var(--spacing-lg);
}

.stream-empty p {
  margin: 0;
  max-width: 360px;
  line-height: 1.6;
}

.stream-empty-icon {
  font-size: 36px;
  color: var(--border-color-dark);
  font-family: var(--font-mono);
}

.stream-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.stream-item {
  display: grid;
  grid-template-columns: 36px 156px minmax(0, 1fr) auto;
  align-items: center;
  gap: var(--spacing-sm);
  padding: 7px var(--spacing-lg);
  font-size: 12.5px;
  border-bottom: 1px solid var(--border-color-light);
  transition: background var(--transition-fast);
  animation: fade-in 0.2s ease-out;
}

.stream-item:hover {
  background: var(--bg-hover);
}

@keyframes fade-in {
  from {
    opacity: 0;
    transform: translateY(-2px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* 阶段 badge */
.stage-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 22px;
  border-radius: var(--radius-sm);
  font-size: 11px;
  font-weight: 700;
  font-family: var(--font-mono);
  border: 1px solid transparent;
  flex-shrink: 0;
  letter-spacing: 0.04em;
}

.stage-badge--planner {
  color: var(--color-agent);
  background: var(--color-agent-bg);
  border-color: var(--color-agent);
}

.stage-badge--agenda {
  color: var(--color-agenda);
  background: var(--color-agenda-bg);
  border-color: var(--color-agenda);
}

.stage-badge--tool {
  color: var(--color-tool);
  background: var(--color-tool-bg);
  border-color: var(--color-tool);
}

.stage-badge.is-failed {
  color: var(--color-danger);
  background: var(--color-danger-bg);
  border-color: var(--color-danger);
}

.stream-item-type {
  color: var(--text-secondary);
  font-size: 11px;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.stream-item-content {
  color: var(--text-regular);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

.stream-item-content.is-failed {
  color: var(--color-danger);
  font-weight: 500;
}

.stream-item-time {
  color: var(--text-placeholder);
  font-size: 11px;
  white-space: nowrap;
}

/* ============================================================ */
/* Empty 详情                                                    */
/* ============================================================ */
.detail-empty {
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* ============================================================ */
/* 规划中笔记（页面底部）                                         */
/* ============================================================ */
.page-notes {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: var(--spacing-sm) var(--spacing-md);
  border-top: 1px dashed var(--border-color-light);
  flex-shrink: 0;
}

.page-note {
  margin: 0;
  font-size: 11px;
  color: var(--text-placeholder);
  font-style: italic;
  line-height: 1.5;
}

/* ============================================================ */
/* Responsive                                                   */
/* ============================================================ */
@media (max-width: 1023px) {
  .agents-page {
    grid-template-columns: 200px minmax(0, 1fr);
  }

  .detail-name {
    font-size: 20px;
  }

  .stream-item {
    grid-template-columns: 32px 100px minmax(0, 1fr) auto;
  }
}

@media (max-width: 768px) {
  .agents-page {
    grid-template-columns: 1fr;
    min-height: 480px;
  }

  .list-panel {
    max-height: 280px;
  }

  .detail-header {
    flex-direction: column;
    align-items: stretch;
  }

  .detail-actions {
    flex-wrap: wrap;
  }

  .stream-item {
    grid-template-columns: 28px minmax(0, 1fr) auto;
    row-gap: 2px;
  }

  .stream-item-type {
    grid-column: 2;
    grid-row: 1;
  }

  .stream-item-content {
    grid-column: 2;
    grid-row: 2;
  }

  .stream-item-time {
    grid-column: 3;
    grid-row: 1 / span 2;
    align-self: center;
  }
}
</style>
