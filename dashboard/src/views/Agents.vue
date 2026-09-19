<template>
  <div class="agents-shell">
    <!-- 主网格：左 240px Agent 列表 + 右 flex-1 详情 -->
    <div class="agents-page">
      <!-- LEFT：Agent 列表（narrow, 240px） -->
      <aside class="md-list-panel" aria-label="Agent 列表">
        <header class="md-list-header">
          <div class="md-list-header-main">
            <h2 class="md-list-title">Agent</h2>
            <span class="md-list-count">
              <span class="md-list-count-running">{{ startedCount }}</span>
              <span class="md-list-count-unit">运行</span>
              <span class="md-list-count-divider">·</span>
              <span class="md-list-count-total">共 {{ totalCount }}</span>
            </span>
          </div>
          <div class="md-batch-actions">
            <el-button
              size="small"
              type="success"
              plain
              class="batch-btn"
              :loading="batchLoading === 'start'"
              :disabled="totalCount === 0 || startedCount === totalCount"
              title="启动全部 Agent"
              @click="runBatch('start')"
            >
              全部启动
            </el-button>
            <el-button
              size="small"
              type="danger"
              plain
              class="batch-btn"
              :loading="batchLoading === 'stop'"
              :disabled="totalCount === 0 || startedCount === 0"
              title="停止全部 Agent"
              @click="runBatch('stop')"
            >
              全部停止
            </el-button>
          </div>
        </header>

        <div v-if="totalCount === 0 && !loading" class="md-list-empty">
          <el-empty :image-size="64" description="暂无 Agent" />
        </div>
        <ul v-else class="md-list" role="listbox">
          <li
            v-for="a in agentsList"
            :key="a.name"
            class="md-row"
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
            <span class="md-status-dot" aria-hidden="true" />
            <span class="md-row-name" :title="a.name">{{ a.name }}</span>
            <el-tag
              v-if="a.is_started"
              size="small"
              type="success"
              effect="plain"
              class="md-running-tag"
            >
              运行
            </el-tag>
          </li>
        </ul>
      </aside>

      <!-- RIGHT：详情 + 运行轨迹（flex-1, the star） -->
      <main class="md-detail-panel" aria-label="Agent 详情">
        <template v-if="selected">
          <!-- 详情头：名称 + 状态 + 操作 -->
          <header class="md-detail-header">
            <div class="md-detail-title-block">
              <div class="md-detail-title-row">
                <h1 class="md-detail-name">{{ selected.name }}</h1>
                <el-tag
                  size="default"
                  :type="selected.is_started ? 'success' : selected.is_enabled ? 'warning' : 'info'"
                  effect="dark"
                  class="md-status-tag"
                >
                  {{ statusLabel(selected) }}
                </el-tag>
                <span class="md-type-chip">类型：Agent</span>
              </div>
              <p class="md-detail-description">
                {{ selected.description || '（暂无描述）' }}
              </p>
            </div>
            <div class="md-detail-actions">
              <!-- 启动/停止互斥：同一时刻只渲染可用的一项 -->
              <el-button
                v-if="!selected.is_started"
                type="primary"
                size="default"
                :loading="actionLoading[`${selected.name}-start`]"
                @click="handleControl('start')"
              >
                启动
              </el-button>
              <el-button
                v-else
                size="default"
                :loading="actionLoading[`${selected.name}-stop`]"
                @click="handleControl('stop')"
              >
                停止
              </el-button>
              <!-- 暂停/恢复互斥：同理只渲染可用的一项 -->
              <el-button
                v-if="selectedState !== 'paused'"
                size="default"
                plain
                :disabled="!agentStateOf(selected.name)"
                :loading="controlLoading[`${selected.name}-pause`]"
                @click="handleAgentControl('pause')"
              >
                暂停
              </el-button>
              <el-button
                v-else
                size="default"
                type="warning"
                plain
                :loading="controlLoading[`${selected.name}-resume`]"
                @click="handleAgentControl('resume')"
              >
                恢复
              </el-button>
              <!-- 重启/关机为低频高风险动作，收进下拉（点击后仍有确认框） -->
              <el-dropdown class="more-actions" trigger="click" @command="onMoreCommand">
                <el-button size="default" plain :loading="moreActionsLoading">
                  更多
                  <el-icon class="el-icon--right"><arrow-down /></el-icon>
                </el-button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="restart">重启…</el-dropdown-item>
                    <el-dropdown-item command="shutdown" divided>关机…</el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </div>
          </header>

          <!-- 元信息条：状态 / 心跳 / 重启 / 最近决策（启停与存活由标题 tag 与心跳新鲜度表达） -->
          <div class="md-details-strip" aria-label="状态摘要">
            <div class="md-stat-chip">
              <span class="md-chip-label">状态</span>
              <span class="md-chip-value mono">{{ selectedState }}</span>
            </div>
            <div class="md-stat-chip">
              <span class="md-chip-label">心跳</span>
              <span
                class="md-chip-value mono"
                :class="heartbeatTone"
                title="距上次心跳的时间；超时由守护线程判定失活"
              >
                {{ heartbeatLabel }}
              </span>
            </div>
            <div class="md-stat-chip">
              <span class="md-chip-label">重启</span>
              <span class="md-chip-value mono">{{ selectedInfo?.restart_count ?? '—' }}</span>
            </div>
            <div class="md-stat-chip md-stat-chip--accent">
              <span class="md-chip-label">最近决策</span>
              <span class="md-chip-value mono">{{ latestDecisionLabel }}</span>
            </div>
            <el-button
              size="small"
              text
              aria-label="刷新状态"
              title="刷新状态"
              :loading="stateRefreshing"
              @click="refreshAgentStates()"
            >
              <el-icon><refresh /></el-icon>
            </el-button>
          </div>

          <!-- 运行轨迹 -->
          <section class="md-stream-panel" aria-label="运行轨迹">
            <header class="stream-header">
              <div class="stream-header-row stream-header-row--main">
                <div class="md-stream-title-block">
                  <span class="md-stream-pulse" aria-hidden="true" />
                  <h3 class="md-stream-title">运行轨迹</h3>
                  <el-tooltip
                    content="轨迹由三族事件构成：决策（planner.*）/ 流程（rundown.changed）/ 工具（tool.result.*）；单 Agent 归因精确，多 Agent 并行时按时间近似"
                    placement="top"
                  >
                    <el-tag size="small" type="info" effect="plain" class="md-stream-count">
                      {{ displayedEntries.length }} / {{ STREAM_CAP }}
                    </el-tag>
                  </el-tooltip>
                </div>
                <div class="md-stream-controls">
                  <el-button
                    size="small"
                    :type="paused ? 'primary' : 'default'"
                    @click="togglePause"
                  >
                    {{ paused ? '继续' : '暂停' }}
                  </el-button>
                  <el-button size="small" :disabled="streamItems.length === 0" @click="clearStream">
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

            <div ref="streamScrollRef" class="md-stream-scroll">
              <div v-if="displayedEntries.length === 0" class="md-stream-empty">
                <span class="md-stream-empty-icon" aria-hidden="true">∅</span>
                <p>
                  {{
                    streamItems.length === 0
                      ? '暂无轨迹——等待 Agent 活动（planner/rundown/tool.result）'
                      : '当前过滤下无匹配条目'
                  }}
                </p>
              </div>
              <ul v-else class="md-stream-list">
                <li
                  v-for="item in displayedEntries"
                  :key="item.id"
                  class="md-stream-item"
                  :class="{ 'is-failed': item.failed }"
                >
                  <span
                    class="stage-badge"
                    :class="[`stage-badge--${item.stage}`, { 'is-failed': item.failed }]"
                    aria-hidden="true"
                  >
                    {{ stageLabel(item.stage) }}
                  </span>
                  <span class="md-stream-item-type mono">{{ item.eventType }}</span>
                  <span class="md-stream-item-content" :class="{ 'is-failed': item.failed }">
                    {{ item.summary }}
                  </span>
                  <span class="md-stream-item-time mono">
                    {{ relativeTime(item.timestampMs) }}
                  </span>
                </li>
              </ul>
            </div>
          </section>
        </template>

        <div v-else class="md-detail-empty">
          <el-empty description="从左侧选择一个 Agent 查看详情与运行轨迹" />
        </div>
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * Agents 页面 —— Master-Detail 布局：左 240px Agent 列表 + 右详情三段
 * （详情头 / 状态摘要条 / 运行轨迹）。
 *
 * 运行轨迹按三事件族合并（planner.* / rundown.changed / tool.result.*），
 * 每条带阶段 badge（决策/流程/工具）与失败标记（tool.result 失败标红）。
 * 事件负载暂无 agent 身份字段：单 Agent 场景归因精确，多 Agent 并行时
 * 按时间近似；消除近似需后端在事件负载中增加 agent-identity 字段。
 *
 * 主从通用逻辑（选中保持 / 控制 / 批量 / 事件流缓冲）见 useComponentMasterDetail。
 */
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue';
import { ElMessage } from 'element-plus';
import { confirmAction } from '@/utils/confirmAction';
import { ArrowDown, Refresh } from '@element-plus/icons-vue';
import { storeToRefs } from 'pinia';
import { useComponentsStore, useEventsStore } from '@/stores';
import { agentsApi } from '@/api';
import { useNowTick } from '@/composables/useNowTick';
import { useScrollFollow } from '@/composables/useScrollFollow';
import {
  STREAM_CAP,
  useComponentMasterDetail,
  type ComponentEvent,
  type ComponentStreamItem,
} from '@/composables/useComponentMasterDetail';
import type { AgentControlActionType, AgentInfo, WebSocketMessage } from '@/types';
import { summarizeEvent } from '@/utils/eventSummary';
import { relativeTime as relativeTimeLabel } from '@/utils/liveFeed';
import { formatDurationShort } from '@/utils/format';
import '@/styles/component-master-detail.css';

// Store + 基础状态

const componentsStore = useComponentsStore();
const eventsStore = useEventsStore();
const { agentsList, loading } = storeToRefs(componentsStore);
const { events } = storeToRefs(eventsStore);

const totalCount = computed(() => agentsList.value.length);
const startedCount = computed(() => agentsList.value.filter(a => a.is_started).length);

// 主从通用逻辑：选中保持 / 启停控制 / 批量 / 状态文案 / 事件流缓冲
const {
  selectedName,
  selected,
  select,
  controlPending: actionLoading,
  batchLoading,
  handleControl,
  runBatch,
  statusLabel,
  paused,
  streamItems,
  togglePause,
  clearStream,
} = useComponentMasterDetail({
  list: agentsList,
  domain: 'agents',
  noun: 'Agent',
  events,
  mapEvent: mapStreamEvent,
  afterControl: () => void refreshAgentStates(),
});

// Agent 控制面（/api/v1/agents）：运行状态 + pause/resume/shutdown

// 运行状态名册：name → AgentInfo（进页面拉一次，此后轮询 + 操作后刷新）
const agentStates = ref<Record<string, AgentInfo>>({});
const stateRefreshing = ref(false);

// 后台轮询静默失败（不打扰用户），手动刷新失败才弹错
const STATE_POLL_INTERVAL_MS = 8000;
let statePollTimer: ReturnType<typeof setInterval> | null = null;

async function refreshAgentStates(silent = false): Promise<void> {
  if (!silent) stateRefreshing.value = true;
  try {
    const res = await agentsApi.listAgents();
    const next: Record<string, AgentInfo> = {};
    for (const a of res.data.agents) next[a.name] = a;
    agentStates.value = next;
  } catch (error) {
    if (silent) {
      console.warn('Agent 状态轮询失败（等待下轮重试）:', error);
    } else {
      ElMessage.error(extractAgentError(error, '获取 Agent 状态失败'));
    }
  } finally {
    if (!silent) stateRefreshing.value = false;
  }
}

function agentStateOf(name: string): AgentInfo | null {
  return agentStates.value[name] ?? null;
}

const selectedInfo = computed<AgentInfo | null>(() =>
  selectedName.value ? agentStateOf(selectedName.value) : null,
);

const selectedState = computed<string>(() => selectedInfo.value?.state ?? '—');

// 相对时间的时钟源：共享 tick 驱动心跳/轨迹/最近决策的时间自动更新
const nowMs = useNowTick();

// heartbeat_ms 是 Unix epoch 毫秒时刻（非时长）→ 折算"距上次心跳多久"
const heartbeatAgeSec = computed<number | null>(() => {
  const info = selectedInfo.value;
  if (!info || !info.heartbeat_ms) return null;
  return Math.max(0, Math.floor((nowMs.value - info.heartbeat_ms) / 1000));
});

const heartbeatLabel = computed<string>(() => {
  if (heartbeatAgeSec.value === null) return '—';
  return formatDurationShort(heartbeatAgeSec.value);
});

// 新鲜度着色：失活（守护判定）红色；失活与否未知但明显滞后（>60s，默认心跳间隔 10s 的 6 倍）黄色
const heartbeatTone = computed<string>(() => {
  const info = selectedInfo.value;
  if (!info || heartbeatAgeSec.value === null) return '';
  if (!info.is_alive) return 'md-chip-dead';
  return heartbeatAgeSec.value > 60 ? 'md-chip-stale' : 'md-chip-ok';
});

// 从 axios 错误中提取后端中文 detail（400 风险说明 / 404 / 500 均为中文）
function extractAgentError(error: unknown, fallback: string): string {
  if (error && typeof error === 'object' && 'response' in error) {
    const data = (error as { response?: { data?: { detail?: unknown } } }).response?.data;
    if (data && typeof data.detail === 'string') return data.detail;
  }
  return error instanceof Error && error.message ? error.message : fallback;
}

// pause/resume/shutdown 走框架级控制端点；shutdown 为高风险动作，确认后才携带 confirm: true
const controlLoading = reactive<Record<string, boolean>>({});

async function handleAgentControl(action: AgentControlActionType): Promise<void> {
  const name = selectedName.value;
  if (!name) return;
  const riskHints: Partial<Record<AgentControlActionType, string>> = {
    shutdown: '停机后该 Agent 不再响应（需重新启用才能恢复）',
  };
  const hint = riskHints[action];
  if (hint) {
    const ok = await confirmAction(`确认对「${name}」执行关机？${hint}`, '高风险操作确认', {
      confirmButtonText: '确认关机',
    });
    if (!ok) return;
  }
  const key = `${name}-${action}`;
  controlLoading[key] = true;
  try {
    const res = await agentsApi.controlAgent(name, action, hint ? true : undefined);
    ElMessage.success(res.data.message);
  } catch (error) {
    ElMessage.error(extractAgentError(error, '操作失败'));
  } finally {
    controlLoading[key] = false;
    await refreshAgentStates();
  }
}

// 既有重启语义（组件控制端点）不动，仅补确认框
async function handleRestartWithConfirm(): Promise<void> {
  const name = selectedName.value;
  if (!name) return;
  const ok = await confirmAction(`确认重启「${name}」？将停止当前实例并重新构造启动`, '重启确认', {
    confirmButtonText: '确认重启',
  });
  if (!ok) return;
  await handleControl('restart');
}

// "更多"下拉：重启/关机均带确认框，此处只做分发
function onMoreCommand(command: string): void {
  if (command === 'restart') {
    void handleRestartWithConfirm();
  } else if (command === 'shutdown') {
    void handleAgentControl('shutdown');
  }
}

const moreActionsLoading = computed<boolean>(() => {
  const name = selectedName.value;
  if (!name) return false;
  return Boolean(actionLoading[`${name}-restart`] || controlLoading[`${name}-shutdown`]);
});

// "最近决策"指标：planner.* 最新事件的相对时间

const latestDecisionLabel = computed<string>(() => {
  // events store 按 timestamp_ms 升序；末条即最新。逆序找第一条 planner.*。
  const all = events.value;
  for (let i = all.length - 1; i >= 0; i--) {
    if (all[i].type.startsWith('planner.')) {
      return relativeTime(all[i].timestamp_ms);
    }
  }
  return '—';
});

// 运行轨迹：三族合并 + 阶段 badge + 失败标记

type StageKind = 'planner' | 'rundown' | 'tool';
type FilterKind = 'all' | StageKind;

const filterOptions: { value: FilterKind; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'planner', label: '决策' },
  { value: 'rundown', label: '流程' },
  { value: 'tool', label: '工具' },
];

const stageLabels: Record<StageKind, string> = {
  planner: '决策',
  rundown: '流程',
  tool: '工具',
};

function stageLabel(stage: StageKind): string {
  return stageLabels[stage];
}

function getStage(type: string): StageKind | null {
  if (type.startsWith('planner.')) return 'planner';
  if (type === 'rundown.changed') return 'rundown';
  if (type.startsWith('tool.result.')) return 'tool';
  return null;
}

function isToolFailed(data: WebSocketMessage['data']): boolean {
  const status = String((data as Record<string, unknown>).status ?? '').toLowerCase();
  return status === 'failed' || status === 'failure' || status === 'error';
}

interface AgentStreamItem extends ComponentStreamItem {
  stage: StageKind;
  failed: boolean;
}

function mapStreamEvent(e: ComponentEvent): AgentStreamItem | null {
  const stage = getStage(e.type);
  if (!stage) return null;
  return {
    id: e.id,
    eventType: e.type,
    stage,
    summary: summarizeEvent(e.type, e.data),
    timestampMs: e.timestamp_ms,
    failed: stage === 'tool' && isToolFailed(e.data),
  };
}

const activeFilter = ref<FilterKind>('all');

// 视图层：按 activeFilter 过滤；保持时间升序展示（新条目在末尾）。
const displayedEntries = computed<AgentStreamItem[]>(() => {
  if (activeFilter.value === 'all') return streamItems.value;
  return streamItems.value.filter(e => e.stage === activeFilter.value);
});

// 自动滚动：新条目追加时滚到底部，除非用户已向上滚动

const { scrollRef: streamScrollRef } = useScrollFollow(displayedEntries);

// 工具：相对时间

function relativeTime(timestampMs: number): string {
  return relativeTimeLabel(nowMs.value, timestampMs);
}

// 生命周期

onMounted(() => {
  componentsStore.fetchComponents();
  void refreshAgentStates();
  // 状态轮询：心跳/存活/状态随时间自动保鲜
  statePollTimer = setInterval(() => void refreshAgentStates(true), STATE_POLL_INTERVAL_MS);
});

onUnmounted(() => {
  if (statePollTimer) clearInterval(statePollTimer);
});
</script>

<style scoped>
/* 页面级：强调色注入 + 网格布局；主从通用样式见 styles/component-master-detail.css */
.agents-shell {
  --md-accent: var(--color-agent);
  --md-accent-bg: var(--color-agent-bg);
  --md-pulse-ring: rgba(139, 92, 246, 0.5);

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

.more-actions {
  flex-shrink: 0;
}

/* 运行轨迹头部：主行 + 过滤行两段布局（本页特有） */
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

.filter-label {
  font-size: 11px;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  margin-right: 2px;
}

.filter-chip {
  font-size: 12px;
}

/* 流条目网格与阶段 badge（本页特有） */
.md-stream-item {
  grid-template-columns: 36px 156px minmax(0, 1fr) auto;
}

.stage-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
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

.stage-badge--rundown {
  color: var(--color-rundown);
  background: var(--color-rundown-bg);
  border-color: var(--color-rundown);
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

.md-stream-item-type {
  color: var(--text-secondary);
}

.md-stream-item-content.is-failed {
  color: var(--color-danger);
  font-weight: 500;
}

/* 响应式 */
@media (max-width: 1023px) {
  .agents-page {
    grid-template-columns: 200px minmax(0, 1fr);
  }

  .md-detail-name {
    font-size: 20px;
  }

  .md-stream-item {
    grid-template-columns: 32px 100px minmax(0, 1fr) auto;
  }

  .stage-badge {
    width: 30px;
    font-size: 10px;
  }
}

@media (max-width: 768px) {
  .agents-page {
    grid-template-columns: 1fr;
    min-height: 480px;
  }

  .md-stream-item {
    grid-template-columns: 28px minmax(0, 1fr) auto;
  }

  .stage-badge {
    width: 28px;
    font-size: 10px;
    letter-spacing: 0;
  }
}
</style>
