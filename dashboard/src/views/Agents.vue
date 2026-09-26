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

          <!-- 任务区：委派任务卡（进行中在上，已完结折叠可查，仅本次运行内）+
               干预输入条（递话默认 / 委派派活；Tab 切模式、Enter 发送） -->
          <section class="md-tasks-panel" aria-label="任务区">
            <header class="md-tasks-header">
              <div class="md-stream-title-block">
                <span class="md-stream-pulse" aria-hidden="true" />
                <h3 class="md-stream-title">任务</h3>
                <el-tag size="small" type="info" effect="plain" class="md-stream-count">
                  {{ runningTasks.length }} 进行中
                </el-tag>
              </div>
              <el-button
                size="small"
                text
                aria-label="刷新任务"
                title="刷新任务"
                :loading="tasksRefreshing"
                @click="refreshTasks()"
              >
                <el-icon><refresh /></el-icon>
              </el-button>
            </header>
            <div class="md-tasks-scroll">
              <div
                v-if="runningTasks.length === 0 && finishedTasks.length === 0"
                class="md-tasks-empty"
              >
                <p>暂无任务——可在下方输入条委派新工作，或递话提醒正在执行的她</p>
              </div>
              <template v-else>
                <ul class="md-task-list">
                  <li
                    v-for="task in runningTasks"
                    :key="task.task_id"
                    class="md-task-card"
                    :class="{ 'is-waiting': task.status === 'waiting_for_decision' }"
                  >
                    <div class="md-task-head">
                      <span class="md-task-status" :class="`is-${task.status}`">
                        {{ taskStatusText(task.status) }}
                      </span>
                      <span class="md-task-id mono" :title="task.task_id">{{ task.task_id }}</span>
                      <span class="md-task-flow mono"
                        >{{ task.initiator }} → {{ task.executor }}</span
                      >
                      <el-button
                        size="small"
                        type="danger"
                        plain
                        class="md-task-cancel"
                        @click="cancelTask(task)"
                      >
                        取消
                      </el-button>
                    </div>
                    <p class="md-task-instruction">{{ task.instruction || '（无指令摘要）' }}</p>
                    <span class="md-task-time mono">{{ relativeTime(task.updated_at_ms) }}</span>
                  </li>
                </ul>
                <el-collapse v-if="finishedTasks.length > 0" class="md-task-finished">
                  <el-collapse-item :title="`已完结（${finishedTasks.length}，仅本次运行内）`">
                    <ul class="md-task-list">
                      <li
                        v-for="task in finishedTasks"
                        :key="task.task_id"
                        class="md-task-card is-finished"
                      >
                        <div class="md-task-head">
                          <span class="md-task-status" :class="`is-${task.status}`">
                            {{ taskStatusText(task.status) }}
                          </span>
                          <span class="md-task-id mono" :title="task.task_id">{{
                            task.task_id
                          }}</span>
                          <span class="md-task-flow mono"
                            >{{ task.initiator }} → {{ task.executor }}</span
                          >
                        </div>
                        <p class="md-task-instruction">{{ task.summary || task.instruction }}</p>
                        <span class="md-task-time mono">{{
                          relativeTime(task.updated_at_ms)
                        }}</span>
                      </li>
                    </ul>
                  </el-collapse-item>
                </el-collapse>
              </template>
            </div>
            <div class="md-task-input">
              <InterventionInput
                ref="agentSendBarRef"
                :modes="AGENT_SEND_MODES"
                :sending="agentSending"
                @send="onAgentSend"
              />
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
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import { confirmAction } from '@/utils/confirmAction';
import { ArrowDown, Refresh } from '@element-plus/icons-vue';
import { storeToRefs } from 'pinia';
import { useComponentsStore, useEventsStore } from '@/stores';
import { agentsApi, tasksApi } from '@/api';
import InterventionInput from '@/components/dashboard/InterventionInput.vue';
import { useNowTick } from '@/composables/useNowTick';
import { useScrollFollow } from '@/composables/useScrollFollow';
import {
  STREAM_CAP,
  useComponentMasterDetail,
  type ComponentEvent,
  type ComponentStreamItem,
} from '@/composables/useComponentMasterDetail';
import type {
  AgentControlActionType,
  AgentInfo,
  TaskCard,
  TaskSnapshotResponse,
  WebSocketMessage,
} from '@/types';
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

// 任务区：委派任务卡（进行中账本快照 + 已完结事件聚合，按执行 Agent 过滤）
// 与干预输入条（递话默认 / 委派派活）。

const TASK_STATUS_TEXT: Record<string, string> = {
  accepted: '已受理',
  running: '进行中',
  waiting_for_decision: '待定夺',
  succeeded: '已完成',
  failed: '失败',
  cancelled: '已取消',
  timeout: '已超时',
};

function taskStatusText(status: string): string {
  return TASK_STATUS_TEXT[status] ?? status;
}

const taskSnapshot = ref<TaskSnapshotResponse>({ running: [], finished: [] });
const tasksRefreshing = ref(false);

async function refreshTasks(silent = false): Promise<void> {
  if (!silent) tasksRefreshing.value = true;
  try {
    const res = await tasksApi.listTasks();
    taskSnapshot.value = res.data;
  } catch (error) {
    // 已完结聚合仅运行内成立、后端未装配任务基建（503）皆属常态——静默轮询不打扰
    if (!silent) ElMessage.error(extractAgentError(error, '获取任务快照失败'));
  } finally {
    if (!silent) tasksRefreshing.value = false;
  }
}

const runningTasks = computed<TaskCard[]>(() =>
  taskSnapshot.value.running.filter(task => task.executor === selectedName.value),
);
const finishedTasks = computed<TaskCard[]>(() =>
  taskSnapshot.value.finished.filter(task => task.executor === selectedName.value),
);

// task.changed 实时刷新（WS 全量订阅已入 events store；这里只数增量触发拉取）
watch(
  () => events.value.reduce((count, e) => (e.type === 'task.changed' ? count + 1 : count), 0),
  () => void refreshTasks(true),
);

async function cancelTask(task: TaskCard): Promise<void> {
  const name = selectedName.value;
  if (!name) return;
  const ok = await confirmAction(
    `确认取消任务 ${task.task_id}？将强制清账并通知该 Agent 停手`,
    '取消任务确认',
    {
      confirmButtonText: '确认取消',
    },
  );
  if (!ok) return;
  try {
    await agentsApi.cancelAgentTask(name, task.task_id);
    ElMessage.success('任务已取消');
  } catch (error) {
    ElMessage.error(extractAgentError(error, '取消失败'));
  }
  await refreshTasks(true);
}

// 干预输入条：递话（默认）/ 委派两模式；输入交互在共享组件，传输归本页

const AGENT_SEND_MODES = [
  {
    key: 'prompt',
    label: '递话',
    desc: '纯文本留言（插话/提醒）：不派新任务——任务执行中下一步吸收，挂起中被唤醒',
    placeholder: '给该 Agent 的留言（不派新任务）',
  },
  {
    key: 'delegate',
    label: '委派',
    desc: '派一项新工作：登记任务账本并送达目标，受理回执任务号，任务卡在此可见',
    placeholder: '工作指令（自然语言：目标与约束，不规定步骤）',
  },
];

const agentSendBarRef = ref<InstanceType<typeof InterventionInput> | null>(null);
const agentSending = ref(false);

async function onAgentSend(modeKey: string, text: string): Promise<void> {
  const name = selectedName.value;
  if (!name || agentSending.value) return;
  if (!text) {
    ElMessage.warning(modeKey === 'delegate' ? '请填写工作指令' : '请填写留言内容');
    return;
  }
  agentSending.value = true;
  try {
    if (modeKey === 'delegate') {
      const res = await agentsApi.delegateAgent(name, text);
      ElMessage.success(`已受理（任务号 ${res.data.task_id}）`);
      await refreshTasks(true);
    } else {
      await agentsApi.promptAgent(name, text);
      ElMessage.success('已递话——执行中的任务下一步会吸收，挂起中的会被唤醒');
    }
    await agentSendBarRef.value?.settle();
  } catch (error) {
    ElMessage.error(extractAgentError(error, modeKey === 'delegate' ? '委派失败' : '递话失败'));
  } finally {
    agentSending.value = false;
  }
}

// 生命周期

onMounted(() => {
  componentsStore.fetchComponents();
  void refreshAgentStates();
  void refreshTasks();
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

/* 任务区：任务卡列表 + 底部干预输入条（进行中在上，已完结折叠可查） */
.md-tasks-panel {
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-md);
  background: var(--bg-card);
  max-height: 380px;
}

.md-tasks-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--spacing-sm) var(--spacing-lg);
  border-bottom: 1px solid var(--border-color-light);
  flex-shrink: 0;
}

.md-tasks-scroll {
  overflow-y: auto;
  padding: var(--spacing-sm) var(--spacing-lg);
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
  min-height: 0;
}

.md-tasks-empty {
  color: var(--text-secondary);
  font-size: 12px;
  text-align: center;
  padding: var(--spacing-sm) 0;
}

.md-task-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
}

.md-task-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-md);
  padding: 8px 12px;
  background: var(--bg-page);
}

.md-task-card.is-waiting {
  border-color: var(--el-color-warning);
}

.md-task-card.is-finished {
  opacity: 0.75;
}

.md-task-head {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  min-width: 0;
}

.md-task-status {
  font-size: 11px;
  font-weight: 700;
  padding: 1px 8px;
  border-radius: 999px;
  flex-shrink: 0;
  background: var(--color-agent-bg);
  color: var(--color-agent);
}

.md-task-status.is-waiting_for_decision {
  background: var(--el-color-warning-light-9);
  color: var(--el-color-warning);
}

.md-task-status.is-succeeded {
  background: var(--el-color-success-light-9);
  color: var(--el-color-success);
}

.md-task-status.is-failed,
.md-task-status.is-cancelled,
.md-task-status.is-timeout {
  background: var(--el-color-danger-light-9);
  color: var(--el-color-danger);
}

.md-task-id {
  font-size: 11px;
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.md-task-flow {
  font-size: 11px;
  color: var(--text-secondary);
  flex-shrink: 0;
}

.md-task-cancel {
  margin-left: auto;
  flex-shrink: 0;
}

.md-task-instruction {
  margin: 0;
  font-size: 13px;
  color: var(--text-primary);
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.md-task-time {
  font-size: 11px;
  color: var(--text-tertiary, var(--text-secondary));
}

.md-task-finished {
  border: none;
}

.md-task-finished :deep(.el-collapse-item__header) {
  font-size: 12px;
  color: var(--text-secondary);
  height: 32px;
  background: transparent;
  border-bottom: none;
}

.md-task-finished :deep(.el-collapse-item__wrap) {
  background: transparent;
  border-bottom: none;
}

.md-task-input {
  padding: var(--spacing-sm) var(--spacing-lg);
  border-top: 1px solid var(--border-color-light);
  flex-shrink: 0;
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
