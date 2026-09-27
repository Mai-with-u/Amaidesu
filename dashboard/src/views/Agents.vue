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
          <!-- 批量启停影响所有 Agent（直播中误点全部停止会直接停摆），先确认再执行 -->
          <div class="md-batch-actions">
            <el-button
              size="small"
              type="success"
              plain
              class="batch-btn"
              :loading="batchLoading === 'start'"
              :disabled="totalCount === 0 || startedCount === totalCount"
              title="启动全部 Agent"
              @click="handleBatch('start')"
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
              @click="handleBatch('stop')"
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

      <!-- RIGHT：详情（按 Agent 类型分视图） -->
      <main class="md-detail-panel" aria-label="Agent 详情">
        <template v-if="selected">
          <!-- 详情头：名称 + 状态 + 操作。档案页以查看为主：状态标签是头部
               最大权重元素，启停/暂停恢复为次要样式，高风险动作收进"更多" -->
          <header class="md-detail-header">
            <div class="md-detail-title-block">
              <div class="md-detail-title-row">
                <h1 class="md-detail-name">{{ selected.name }}</h1>
                <el-tag
                  size="large"
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
                size="small"
                plain
                :loading="actionLoading[`${selected.name}-start`]"
                @click="handleControl('start')"
              >
                启动
              </el-button>
              <el-button
                v-else
                size="small"
                plain
                :loading="actionLoading[`${selected.name}-stop`]"
                @click="handleControl('stop')"
              >
                停止
              </el-button>
              <!-- 暂停/恢复互斥：同理只渲染可用的一项 -->
              <el-button
                v-if="selectedState !== 'paused'"
                size="small"
                plain
                :disabled="!agentStateOf(selected.name)"
                :loading="controlLoading[`${selected.name}-pause`]"
                @click="handleAgentControl('pause')"
              >
                暂停
              </el-button>
              <el-button
                v-else
                size="small"
                type="warning"
                plain
                :loading="controlLoading[`${selected.name}-resume`]"
                @click="handleAgentControl('resume')"
              >
                恢复
              </el-button>
              <!-- 重启/关机为低频高风险动作，收进下拉（点击后仍有确认框） -->
              <el-dropdown class="more-actions" trigger="click" @command="onMoreCommand">
                <el-button size="small" plain :loading="moreActionsLoading">
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

          <!-- 详情主体按 Agent 类型分视图：主播 = 决策轮决定卡；
               minecraft = 活任务板（进行中可取消 + 已完结折叠）；其他 = 占位 -->
          <section
            v-if="selectedView === 'streamer'"
            ref="roundsPanelRef"
            class="md-rounds-panel"
            aria-label="决定记录"
          >
            <header class="md-tasks-header">
              <div class="md-stream-title-block">
                <span class="md-stream-pulse" aria-hidden="true" />
                <h3 class="md-stream-title">决定记录</h3>
                <el-tag size="small" type="info" effect="plain" class="md-stream-count">
                  {{ decisionCards.length }} 轮
                </el-tag>
              </div>
            </header>
            <div class="md-rounds-scroll">
              <div v-if="decisionCards.length === 0" class="md-tasks-empty">
                <p>暂无决定——等主播活动（planner.decision）</p>
              </div>
              <template v-else>
                <DecisionRoundCard
                  v-for="card in decisionCards"
                  :key="card.roundId || card.timestampMs"
                  :card="card"
                  :highlighted="card.roundId === highlightedRoundId"
                />
              </template>
            </div>
          </section>

          <section
            v-else-if="selectedView === 'minecraft'"
            class="md-tasks-panel"
            aria-label="任务区"
          >
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
          </section>

          <!-- 其他 Agent（adv 等）：暂不支持干预与详细查看 -->
          <div v-else class="md-detail-placeholder">
            <el-empty :image-size="80" description="该 Agent 暂不支持干预与详细查看" />
          </div>

          <!-- 干预输入条：模式集合随选中 Agent 分派（主播三模式 / minecraft 递话委派；
               其余 Agent 无收话能力不渲染）。发送成功提示语义不变：委派回执任务号
               并刷新任务板，递话提示已送达 -->
          <div v-if="sendModes.length > 0" class="md-input-dock">
            <InterventionInput
              ref="agentSendBarRef"
              :key="selectedName ?? ''"
              :modes="sendModes"
              :sending="interventionSending"
              @mode-change="onAgentModeChange"
              @send="onAgentSend"
            >
              <template #toolbar="{ onKeydown }">
                <el-input
                  v-if="selectedView === 'streamer' && agentActiveMode === 'danmaku'"
                  v-model="agentInjectNickname"
                  size="small"
                  class="md-input-nick"
                  placeholder="观众昵称（可选）"
                  @keydown="onKeydown"
                />
                <span
                  v-if="selectedView === 'streamer' && agentForcePending > 0"
                  class="md-input-status"
                >
                  <el-icon class="is-loading"><Loading /></el-icon>
                  主播正在想…
                </span>
              </template>
            </InterventionInput>
          </div>
        </template>

        <div v-else class="md-detail-empty">
          <el-empty description="从左侧选择一个 Agent 查看详情" />
        </div>
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * Agents 页面 —— 单 Agent 检视/管理页：左 240px Agent 列表 + 右详情。
 * 详情主体按 Agent 注册名分视图：
 * - 主播（streamer）：决策轮决定卡——每轮 planner.decision 一张卡，回答
 *   "她为什么这么做"（触发原因/批消息/说或不说/工具/发言/耗时/LLM 原文入口），
 *   由事件缓冲实时归组，新轮自动出现在顶部
 * - minecraft：活任务板——进行中可取消、已完结折叠，task.changed 实时刷新
 * - 其他（adv 等）：占位提示，该 Agent 暂不支持干预与详细查看
 *
 * 主从通用逻辑（选中保持 / 控制 / 批量 / 状态文案）见 useComponentMasterDetail；
 * 本页事件流缓冲仅作深链与最近决策指标的数据源，不再渲染流水面板。
 */
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import { confirmAction } from '@/utils/confirmAction';
import { ArrowDown, Loading, Refresh } from '@element-plus/icons-vue';
import { storeToRefs } from 'pinia';
import { useComponentsStore, useEventsStore } from '@/stores';
import { agentsApi, tasksApi } from '@/api';
import { getApiErrorMessage } from '@/utils/apiError';
import InterventionInput from '@/components/dashboard/InterventionInput.vue';
import DecisionRoundCard from '@/components/agents/DecisionRoundCard.vue';
import {
  MINECRAFT_AGENT_NAME,
  STREAMER_AGENT_NAME,
  useAgentIntervention,
} from '@/composables/useAgentIntervention';
import { useAgentDeepLink, type AgentDeepLink } from '@/composables/useAgentDeepLink';
import { useNowTick } from '@/composables/useNowTick';
import {
  useComponentMasterDetail,
  type ComponentStreamItem,
} from '@/composables/useComponentMasterDetail';
import { groupDecisionRounds } from '@/utils/decisionRounds';
import type { AgentControlActionType, AgentInfo, TaskCard, TaskSnapshotResponse } from '@/types';
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

// 主从通用逻辑：选中保持 / 启停控制 / 批量 / 状态文案。
// 本页不渲染事件流水，mapEvent 置空（共享逻辑保留，Collectors 页还在用）
const {
  selectedName,
  selected,
  select,
  controlPending: actionLoading,
  batchLoading,
  handleControl,
  runBatch,
  statusLabel,
} = useComponentMasterDetail<ComponentStreamItem>({
  list: agentsList,
  domain: 'agents',
  noun: 'Agent',
  events,
  mapEvent: (): null => null,
  afterControl: () => void refreshAgentStates(),
});

// 视图分派：按选中 Agent 的注册名分流

type AgentViewKind = 'streamer' | 'minecraft' | 'generic';

const selectedView = computed<AgentViewKind>(() => {
  if (selectedName.value === STREAMER_AGENT_NAME) return 'streamer';
  if (selectedName.value === MINECRAFT_AGENT_NAME) return 'minecraft';
  return 'generic';
});

// 决策轮决定卡（主播视图）：事件缓冲实时归组，输出按时间降序（新轮在前）
const decisionCards = computed(() => groupDecisionRounds(events.value));

/** 深链定位的轮次（任务 7 接管：/agents?agent=&round= 命中时置值并高亮） */
const highlightedRoundId = ref('');

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
      ElMessage.error(getApiErrorMessage(error, '获取 Agent 状态失败'));
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
    ElMessage.error(getApiErrorMessage(error, '操作失败'));
  } finally {
    controlLoading[key] = false;
    await refreshAgentStates();
  }
}

// 批量启停影响所有 Agent，先确认再执行（取消路径零请求）
async function handleBatch(action: 'start' | 'stop'): Promise<void> {
  const verb = action === 'start' ? '启动' : '停止';
  const ok = await confirmAction(
    `确认${verb}全部 ${totalCount.value} 个 Agent？将同时改变所有 Agent 的运行状态`,
    `批量${verb}确认`,
    { confirmButtonText: `确认${verb}` },
  );
  if (!ok) return;
  await runBatch(action);
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
    if (!silent) ElMessage.error(getApiErrorMessage(error, '获取任务快照失败'));
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
    ElMessage.error(getApiErrorMessage(error, '取消失败'));
  }
  await refreshTasks(true);
}

// 干预输入条：模式集合与传输按选中 Agent 分派——单一事实源在
// useAgentIntervention（主播三模式复用直播控制台同款通道，minecraft 递话/委派）
const agentSendBarRef = ref<InstanceType<typeof InterventionInput> | null>(null);
const {
  activeMode: agentActiveMode,
  injectNickname: agentInjectNickname,
  forcePending: agentForcePending,
  onModeChange: onAgentModeChange,
  modesForTarget,
  sendToTarget,
  sending: interventionSending,
} = useAgentIntervention({
  settle: async () => {
    await agentSendBarRef.value?.settle();
  },
  onDelegated: () => void refreshTasks(true),
});

const sendModes = computed(() => modesForTarget(selectedName.value ?? ''));

async function onAgentSend(modeKey: string, text: string): Promise<void> {
  const name = selectedName.value;
  if (!name) return;
  await sendToTarget(name, modeKey, text);
}

// Agent 切换经 :key 重挂输入条，内部模式回到首项——主播工具项状态同步复位
watch(selectedName, name => {
  if (name === STREAMER_AGENT_NAME) onAgentModeChange('danmaku');
});

// 深链接入：/agents?agent=<name>&round=<round_id> 选中对应 Agent 并滚动高亮该轮
// 决定卡。清单未就绪时选中会被默认选择覆盖，故挂起待清单到达后再消费；
// round 过期/已淘汰时静默跳过高亮，不报错
const { consumeDeepLink } = useAgentDeepLink();
const pendingDeepLink = ref<AgentDeepLink | null>(null);
const roundsPanelRef = ref<HTMLElement | null>(null);

watch(
  [agentsList, pendingDeepLink],
  ([list, link]) => {
    if (!link || list.length === 0) return;
    pendingDeepLink.value = null;
    if (!list.some(a => a.name === link.agent)) return;
    select(link.agent);
    if (link.round) void highlightRound(link.round);
  },
  { immediate: true },
);

/** 高亮定位目标轮：等卡渲染（事件回填异步）后滚到卡中央，约 3 秒撤掉高亮 */
async function highlightRound(roundId: string): Promise<void> {
  highlightedRoundId.value = roundId;
  await nextTick();
  for (let attempt = 0; attempt < 20; attempt++) {
    const el = roundsPanelRef.value?.querySelector(`[data-round-id="${CSS.escape(roundId)}"]`);
    if (el) {
      el.scrollIntoView({ block: 'center', behavior: 'smooth' });
      break;
    }
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  window.setTimeout(() => {
    if (highlightedRoundId.value === roundId) highlightedRoundId.value = '';
  }, 3000);
}

// 生命周期

onMounted(() => {
  componentsStore.fetchComponents();
  void refreshAgentStates();
  void refreshTasks();
  // 深链消费：读一次 query 并已由 replace 清参（刷新/前进后退不重复高亮）
  pendingDeepLink.value = consumeDeepLink();
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

/* 决定卡列表面板（主播视图）：容器与任务区同风格 */
.md-rounds-panel {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-md);
  background: var(--bg-card);
  overflow: hidden;
}

.md-rounds-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: var(--spacing-sm) var(--spacing-lg);
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
}

.md-rounds-scroll::-webkit-scrollbar {
  width: 8px;
}
.md-rounds-scroll::-webkit-scrollbar-thumb {
  background: var(--border-color-dark);
  border-radius: 4px;
}

/* 其他 Agent 占位视图 */
.md-detail-placeholder {
  flex: 1;
  display: grid;
  place-items: center;
  border: 1px dashed var(--border-color-light);
  border-radius: var(--radius-md);
  background: var(--bg-card);
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

/* 干预输入条底座：随视图贴在详情底部（主播/minecraft 有收话能力时渲染） */
.md-input-dock {
  flex-shrink: 0;
  padding: var(--spacing-sm) var(--spacing-lg);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-md);
  background: var(--bg-card);
}

/* 主播目标的工具项：观众昵称小输入框 + 强制回应在途 chip（与直播控制台同款） */
.md-input-nick {
  width: 132px;
  flex-shrink: 0;
}
.md-input-nick :deep(.el-input__wrapper) {
  border-radius: 999px;
  padding: 1px 10px;
  box-shadow: 0 0 0 1px var(--border-color-light) inset;
  background: var(--bg-card);
}
.md-input-nick :deep(.el-input__inner) {
  font-size: 11px;
  height: 20px;
}
.md-input-status {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
  padding: 1px 8px;
  border-radius: 999px;
  background: var(--color-agent-bg);
  color: var(--color-agent);
  font-size: 10px;
  font-weight: 600;
  white-space: nowrap;
}
.md-input-status .el-icon {
  font-size: 11px;
}

/* 响应式 */
@media (max-width: 1023px) {
  .agents-page {
    grid-template-columns: 200px minmax(0, 1fr);
  }

  .md-detail-name {
    font-size: 20px;
  }
}

@media (max-width: 768px) {
  .agents-page {
    grid-template-columns: 1fr;
    min-height: 480px;
  }
}
</style>
