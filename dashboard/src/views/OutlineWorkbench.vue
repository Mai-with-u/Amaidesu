<template>
  <div class="outline-workbench">
    <header class="page-header">
      <div class="header-left">
        <h1 class="page-title">直播大纲工作台</h1>
        <p class="page-subtitle">运行态监控 · 环节推进 · AI 发言追踪</p>
      </div>
    </header>

    <el-tabs v-model="activeTab" class="workbench-tabs">
      <!-- ===================== 运行 Tab ===================== -->
      <el-tab-pane label="运行" name="run">
        <div class="tab-body">
          <!-- ① 状态栏 -->
          <section class="card status-card">
            <div class="status-row">
              <span :class="['status-pill', `status-pill--${statusKind}`]">
                <span class="dot" />
                {{ statusLabel }}
              </span>
              <el-tag v-if="state?.is_paused" type="warning" size="small" effect="dark">
                已暂停
              </el-tag>
              <el-tooltip
                v-if="state?.manually_overridden"
                content="手动覆盖中，自动推进已暂停一次"
                placement="top"
              >
                <el-tag type="danger" size="small" effect="dark">
                  <el-icon class="tag-icon"><WarningFilled /></el-icon>
                  手动覆盖
                </el-tag>
              </el-tooltip>
              <div class="outline-meta">
                <span class="outline-title">{{ state?.outline_title || '未加载大纲' }}</span>
                <code v-if="state?.outline_id" class="outline-id">{{ state.outline_id }}</code>
              </div>
              <span class="row-spacer" />
              <el-button size="small" :loading="refreshing" @click="handleManualRefresh">
                <el-icon><Refresh /></el-icon>
                刷新
              </el-button>
            </div>
          </section>

          <el-alert
            v-if="loadError"
            type="warning"
            :title="loadError"
            show-icon
            :closable="false"
          />

          <!-- 空态：未加载大纲 -->
          <section v-if="showEmptyState" class="card empty-card">
            <el-empty description="尚未加载大纲，输入 TOML 路径后即可开始">
              <div class="empty-load">
                <el-input
                  v-model="loadPath"
                  placeholder="data/outlines/live.toml"
                  clearable
                  style="width: 320px"
                  @keydown.enter="handleLoadOutline"
                />
                <el-button type="primary" :loading="loadingOutline" @click="handleLoadOutline">
                  <el-icon><FolderOpened /></el-icon>
                  加载大纲
                </el-button>
              </div>
            </el-empty>
          </section>

          <template v-else>
            <!-- ② 当前环节 -->
            <section class="card current-card">
              <div class="card-head">
                <el-icon class="card-icon"><Aim /></el-icon>
                <span class="card-title">当前环节</span>
                <el-tag v-if="currentSegment" size="small" effect="plain">
                  {{ currentSegment.id }}
                </el-tag>
                <el-tag
                  v-if="currentSegment"
                  size="small"
                  :type="currentSegment.expanded ? 'success' : 'info'"
                  effect="plain"
                >
                  {{ currentSegment.expanded ? '已扩展' : '未扩展' }}
                </el-tag>
                <el-tag
                  v-if="currentSegment?.needs_expansion"
                  size="small"
                  type="warning"
                  effect="plain"
                >
                  待扩展
                </el-tag>
                <span class="row-spacer" />
                <span v-if="currentSegment" class="time-pill">
                  <span class="time-label">剩余</span>
                  <span class="time-value">{{ formatDuration(currentSegment.remaining_ms) }}</span>
                </span>
              </div>

              <div v-if="currentSegment" class="card-body">
                <h2 class="current-title">{{ currentSegment.title }}</h2>
                <el-progress
                  :percentage="currentPercent"
                  :stroke-width="10"
                  :status="state?.is_paused ? 'warning' : 'success'"
                />
                <div class="metric-row">
                  <span class="metric">
                    <span class="metric-label">已用</span>
                    <span class="metric-value">{{
                      formatDuration(currentSegment.elapsed_ms)
                    }}</span>
                  </span>
                  <span class="metric">
                    <span class="metric-label">剩余</span>
                    <span class="metric-value">{{
                      formatDuration(currentSegment.remaining_ms)
                    }}</span>
                  </span>
                  <span class="metric">
                    <span class="metric-label">计划时长</span>
                    <span class="metric-value">{{
                      formatDuration(currentSegment.duration_ms)
                    }}</span>
                  </span>
                </div>

                <p v-if="currentDetail?.task_description" class="task-desc">
                  {{ currentDetail.task_description }}
                </p>

                <div v-if="currentKeyPoints.length" class="key-points">
                  <el-tag
                    v-for="(point, idx) in currentKeyPoints"
                    :key="`kp-${idx}`"
                    size="small"
                    type="primary"
                    effect="plain"
                  >
                    {{ point }}
                  </el-tag>
                </div>

                <div v-if="currentBranches.length" class="branch-list">
                  <div class="branch-head">可选分支</div>
                  <div v-for="branch in currentBranches" :key="branch.branch_id" class="branch-row">
                    <el-tag size="small" type="warning" effect="plain">
                      {{ branch.branch_id }}
                    </el-tag>
                    <span class="branch-desc">{{ branch.description }}</span>
                    <el-icon class="branch-arrow"><Right /></el-icon>
                    <span class="branch-target">{{ segmentTitle(branch.target_segment_id) }}</span>
                  </div>
                </div>
              </div>
              <div v-else class="card-body">
                <p class="placeholder">当前没有进行中的环节（全部完成或尚未启动）。</p>
              </div>

              <div v-if="state?.next_segment" class="next-hint">
                <el-icon><DArrowRight /></el-icon>
                <span class="next-label">下一环节</span>
                <span class="next-title">{{ state.next_segment.title }}</span>
                <el-tag size="small" effect="plain">{{ state.next_segment.id }}</el-tag>
              </div>
            </section>

            <!-- ③ AI 视角 -->
            <section class="card ai-card">
              <el-collapse v-model="aiPanels">
                <el-collapse-item name="ai">
                  <template #title>
                    <span class="collapse-title">
                      <el-icon class="card-icon"><View /></el-icon>
                      <span class="card-title">AI 视角</span>
                      <el-tag size="small" type="info" effect="plain">当前环节扩展内容</el-tag>
                    </span>
                  </template>
                  <div v-if="expandedData" class="ai-body">
                    <div class="ai-row">
                      <span class="ai-label">开场白</span>
                      <p class="ai-value">{{ expandedData.opening_line || '（空）' }}</p>
                    </div>
                    <div class="ai-row">
                      <span class="ai-label">话题引导</span>
                      <p class="ai-value">{{ expandedData.topic_guidance || '（空）' }}</p>
                    </div>
                    <div class="ai-row">
                      <span class="ai-label">讨论要点</span>
                      <ul v-if="expandedData.talking_points.length" class="ai-points">
                        <li v-for="(point, idx) in expandedData.talking_points" :key="`tp-${idx}`">
                          {{ point }}
                        </li>
                      </ul>
                      <p v-else class="ai-value">（空）</p>
                    </div>
                  </div>
                  <p v-else class="placeholder">该环节尚未生成扩展内容</p>
                </el-collapse-item>
              </el-collapse>
            </section>

            <!-- ④ 实时发言流 -->
            <section class="card speech-card">
              <div class="card-head">
                <el-icon class="card-icon"><Microphone /></el-icon>
                <span class="card-title">实时发言流</span>
                <el-tag size="small" type="info" effect="plain">{{ speechItems.length }} 条</el-tag>
                <span class="row-spacer" />
                <span class="card-hint">最新 {{ MAX_SPEECH_ITEMS }} 条 · 最新在下</span>
              </div>
              <div ref="speechListRef" class="speech-list">
                <div v-if="!speechItems.length" class="list-empty">暂无 AI 发言</div>
                <article v-for="item in speechItems" :key="item.key" class="speech-item">
                  <div class="speech-meta">
                    <span class="speech-time">{{ formatClock(item.atMs) }}</span>
                    <el-tag
                      :type="TRIGGER_META[item.trigger].tagType"
                      :effect="TRIGGER_META[item.trigger].effect"
                      size="small"
                    >
                      {{ TRIGGER_META[item.trigger].label }}
                    </el-tag>
                    <el-tag v-if="item.segmentId" size="small" type="info" effect="plain">
                      {{ segmentTitle(item.segmentId) }}
                    </el-tag>
                  </div>
                  <p class="speech-text">{{ item.speech }}</p>
                </article>
              </div>
            </section>

            <!-- ⑤ 推进时间线 -->
            <section class="card timeline-card">
              <div class="card-head">
                <el-icon class="card-icon"><Sort /></el-icon>
                <span class="card-title">推进时间线</span>
                <el-tag size="small" type="info" effect="plain">
                  {{ timelineItems.length }} 次推进
                </el-tag>
              </div>
              <div class="card-body">
                <div v-if="!timelineItems.length" class="list-empty">暂无推进记录</div>
                <el-timeline v-else>
                  <el-timeline-item
                    v-for="item in timelineItems"
                    :key="item.key"
                    :icon="item.meta.icon"
                    :type="item.meta.type"
                    :timestamp="formatClock(item.at_ms)"
                    placement="top"
                  >
                    <div class="timeline-row">
                      <span class="timeline-reason">{{ item.meta.label }}</span>
                      <span class="timeline-title">{{ item.title || item.segment_id }}</span>
                      <span v-if="item.stayed_ms !== null" class="timeline-stayed">
                        上一段停留 {{ formatDuration(item.stayed_ms) }}
                      </span>
                    </div>
                  </el-timeline-item>
                </el-timeline>
              </div>
            </section>

            <!-- ⑥ 控制区 -->
            <section class="card control-card">
              <div class="card-head">
                <el-icon class="card-icon"><Operation /></el-icon>
                <span class="card-title">手动控制</span>
              </div>
              <div class="control-row">
                <el-button
                  v-if="!state?.is_paused"
                  type="warning"
                  :disabled="!canControl"
                  :loading="controlBusy === 'pause'"
                  @click="sendControl('pause')"
                >
                  <el-icon><VideoPause /></el-icon>
                  暂停
                </el-button>
                <el-button
                  v-else
                  type="success"
                  :disabled="!canControl"
                  :loading="controlBusy === 'resume'"
                  @click="sendControl('resume')"
                >
                  <el-icon><VideoPlay /></el-icon>
                  继续
                </el-button>
                <el-button
                  type="primary"
                  plain
                  :disabled="!canControl"
                  :loading="controlBusy === 'skip'"
                  @click="sendControl('skip')"
                >
                  <el-icon><DArrowRight /></el-icon>
                  跳过
                </el-button>
                <el-button
                  :disabled="!canControl"
                  :loading="controlBusy === 'rewind'"
                  @click="sendControl('rewind')"
                >
                  <el-icon><DArrowLeft /></el-icon>
                  回退
                </el-button>
                <el-select
                  v-model="jumpTargetId"
                  placeholder="选择跳转目标"
                  clearable
                  filterable
                  :disabled="!canControl || !segmentOptions.length"
                  style="width: 220px"
                >
                  <el-option
                    v-for="option in segmentOptions"
                    :key="option.value"
                    :label="option.label"
                    :value="option.value"
                  />
                </el-select>
                <el-button
                  type="primary"
                  :disabled="!canControl || !jumpTargetId"
                  :loading="controlBusy === 'jump'"
                  @click="sendControl('jump')"
                >
                  <el-icon><Position /></el-icon>
                  跳转
                </el-button>
                <span class="row-spacer" />
                <el-button @click="reloadDialogVisible = true">
                  <el-icon><FolderOpened /></el-icon>
                  重新加载大纲
                </el-button>
              </div>
            </section>

            <!-- ⑦ 整场进度 -->
            <section class="card live-card">
              <div class="card-head">
                <el-icon class="card-icon"><TrendCharts /></el-icon>
                <span class="card-title">整场进度</span>
                <span class="row-spacer" />
                <span class="segment-count">
                  <strong>{{ state?.completed_count ?? 0 }}</strong>
                  <span class="count-divider">/</span>
                  <span>{{ state?.total_count ?? 0 }}</span>
                  <span class="count-key">环节</span>
                </span>
              </div>
              <div class="card-body">
                <el-progress :percentage="livePercent" :stroke-width="16" />
                <div class="metric-row">
                  <span class="metric">
                    <span class="metric-label">已进行</span>
                    <span class="metric-value">{{
                      formatDuration(state?.elapsed_live_ms ?? null)
                    }}</span>
                  </span>
                  <span class="metric">
                    <span class="metric-label">预计总时长</span>
                    <span class="metric-value">{{
                      formatDuration(state?.total_planned_ms ?? null)
                    }}</span>
                  </span>
                  <span class="metric metric--accent">
                    <span class="metric-label">完成环节</span>
                    <span class="metric-value">
                      {{ state?.completed_count ?? 0 }} / {{ state?.total_count ?? 0 }}
                    </span>
                  </span>
                </div>
              </div>
            </section>
          </template>
        </div>
      </el-tab-pane>

      <!-- ===================== 编辑 Tab ===================== -->
      <el-tab-pane label="编辑" name="edit">
        <div class="tab-body">
          <OutlineEditorPanel @reloaded="handleOutlineReloaded" />
        </div>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="reloadDialogVisible" title="重新加载大纲" width="480px">
      <el-input
        v-model="loadPath"
        placeholder="data/outlines/live.toml"
        clearable
        @keydown.enter="handleLoadOutline"
      />
      <p class="dialog-hint">重新加载会重置运行状态，从大纲第一个环节重新开始。</p>
      <template #footer>
        <el-button @click="reloadDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="loadingOutline" @click="handleLoadOutline">
          确认加载
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue';
import type { Component } from 'vue';
import { storeToRefs } from 'pinia';
import { ElMessage } from 'element-plus';
import axios from 'axios';
import {
  Aim,
  Clock,
  Connection,
  DArrowLeft,
  DArrowRight,
  FolderOpened,
  MagicStick,
  Microphone,
  Operation,
  Pointer,
  Position,
  Refresh,
  Right,
  Sort,
  Timer,
  TrendCharts,
  VideoPause,
  VideoPlay,
  View,
  WarningFilled,
} from '@element-plus/icons-vue';
import { outlineApi } from '@/api';
import { useEventsStore } from '@/stores';
import OutlineEditorPanel from '@/components/outline/OutlineEditorPanel.vue';
import type {
  ExpandedSegmentData,
  OutlineControlAction,
  OutlineSegmentData,
  OutlineStateSnapshot,
  OutlineTransition,
} from '@/types';

type TagType = 'primary' | 'success' | 'info' | 'warning' | 'danger';
type TagEffect = 'dark' | 'light' | 'plain';
type StatusKind = 'running' | 'paused' | 'inactive' | 'completed' | 'unloaded';
type SpeechTrigger = 'outline' | 'proactive' | 'danmaku' | 'other';

interface SpeechItem {
  key: string;
  atMs: number;
  speech: string;
  trigger: SpeechTrigger;
  segmentId: string | null;
}

interface TriggerMeta {
  label: string;
  tagType: TagType;
  effect: TagEffect;
}

interface TransitionMeta {
  label: string;
  icon: Component;
  type: TagType;
}

const MAX_SPEECH_ITEMS = 20;
const speechListRef = ref<{ scrollTop: number; scrollHeight: number } | null>(null);
const POLL_INTERVAL_MS = 1000;

const STATUS_LABELS: Record<StatusKind, string> = {
  running: '运行中',
  paused: '已暂停',
  inactive: '未激活',
  completed: '已完成',
  unloaded: '未加载',
};

const CONTROL_LABELS: Record<OutlineControlAction, string> = {
  pause: '暂停',
  resume: '继续',
  skip: '跳过',
  rewind: '回退',
  jump: '跳转',
};

const TRIGGER_META: Record<SpeechTrigger, TriggerMeta> = {
  outline: { label: '大纲驱动', tagType: 'primary', effect: 'dark' },
  proactive: { label: '主动发言', tagType: 'primary', effect: 'plain' },
  danmaku: { label: '弹幕回复', tagType: 'info', effect: 'plain' },
  other: { label: '其他', tagType: 'info', effect: 'plain' },
};

const TRANSITION_META: Record<string, TransitionMeta> = {
  start: { label: '开播', icon: VideoPlay, type: 'success' },
  'outline:time': { label: '时长到', icon: Timer, type: 'primary' },
  'outline:assessment': { label: 'AI 评估', icon: MagicStick, type: 'primary' },
  'outline:branch': { label: '分支', icon: Connection, type: 'warning' },
  'outline:extend': { label: '延长', icon: Clock, type: 'warning' },
  'manual:skip': { label: '手动跳过', icon: DArrowRight, type: 'danger' },
  'manual:jump': { label: '手动跳转', icon: Position, type: 'danger' },
  'manual:rewind': { label: '手动回退', icon: DArrowLeft, type: 'danger' },
};

const { events } = storeToRefs(useEventsStore());

const activeTab = ref<'run' | 'edit'>('run');
const state = ref<OutlineStateSnapshot | null>(null);
const segments = ref<OutlineSegmentData[]>([]);
const transitions = ref<OutlineTransition[]>([]);
const loadPath = ref('data/outlines/live.toml');
const loadError = ref<string | null>(null);
const refreshing = ref(false);
const loadingOutline = ref(false);
const controlBusy = ref<OutlineControlAction | ''>('');
const jumpTargetId = ref('');
const reloadDialogVisible = ref(false);
const aiPanels = ref<string[]>(['ai']);

const segmentMap = computed(() => new Map(segments.value.map(segment => [segment.id, segment])));

const currentSegment = computed(() => state.value?.current_segment ?? null);

const currentDetail = computed<OutlineSegmentData | null>(() => {
  const id = currentSegment.value?.id;
  return id ? (segmentMap.value.get(id) ?? null) : null;
});

const currentKeyPoints = computed(() => currentDetail.value?.key_points ?? []);
const currentBranches = computed(() => currentDetail.value?.branches ?? []);
const expandedData = computed<ExpandedSegmentData | null>(
  () => currentDetail.value?.expanded ?? null,
);

const statusKind = computed<StatusKind>(() => {
  const snapshot = state.value;
  if (!snapshot) return 'inactive';
  if (snapshot.is_paused) return 'paused';
  if (snapshot.status === 'running') return 'running';
  if (snapshot.status === 'completed') return 'completed';
  if (snapshot.status === 'unloaded') return 'unloaded';
  return 'inactive';
});

const statusLabel = computed(() => (state.value ? STATUS_LABELS[statusKind.value] : '未连接'));

const showEmptyState = computed(() => {
  const snapshot = state.value;
  if (!snapshot) return true;
  if (snapshot.status === 'unloaded') return true;
  return snapshot.total_count === 0;
});

const canControl = computed(() => (state.value?.total_count ?? 0) > 0);

const currentPercent = computed(() => {
  const segment = currentSegment.value;
  if (!segment || segment.duration_ms <= 0) return 0;
  return clampPercent((segment.elapsed_ms / segment.duration_ms) * 100);
});

const livePercent = computed(() => clampPercent(state.value?.progress_percent ?? 0));

const segmentOptions = computed(() =>
  segments.value.map(segment => ({
    value: segment.id,
    label: `${segment.id} · ${segment.title}`,
  })),
);

const speechItems = computed<SpeechItem[]>(() => {
  const collected: SpeechItem[] = [];
  for (const event of events.value) {
    if (event.type !== 'decision.intent') continue;
    const intentData = asRecord(event.data.intent_data);
    if (!intentData) continue;
    const speech = asText(intentData.speech);
    if (!speech) continue;
    const metadata = asRecord(intentData.metadata);
    const triggerReason = asText(metadata?.trigger_reason);
    const sourceMessageId = asText(metadata?.source_message_id);
    collected.push({
      key: event.id,
      atMs: asTimestampMs(metadata?.decision_time_ms) ?? event.timestamp * 1000,
      speech,
      trigger: classifyTrigger(triggerReason, sourceMessageId),
      segmentId: asText(metadata?.outline_segment_id) ?? null,
    });
  }
  return collected.slice(-MAX_SPEECH_ITEMS);
});

const timelineItems = computed(() =>
  [...transitions.value].reverse().map(transition => ({
    ...transition,
    key: `${transition.segment_id}-${transition.at_ms}`,
    meta: transitionMeta(transition.reason),
  })),
);

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : null;
}

function asText(value: unknown): string | undefined {
  return typeof value === 'string' && value.length > 0 ? value : undefined;
}

function asTimestampMs(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : undefined;
}

/** trigger_reason / outline_segment_id 是后端新增字段，旧事件缺失时按 source_message_id 降级 */
function classifyTrigger(
  triggerReason: string | undefined,
  sourceMessageId: string | undefined,
): SpeechTrigger {
  if (triggerReason?.startsWith('proactive:outline')) return 'outline';
  if (triggerReason?.startsWith('proactive:')) return 'proactive';
  if (sourceMessageId && sourceMessageId !== 'proactive') return 'danmaku';
  return 'other';
}

function transitionMeta(reason: string): TransitionMeta {
  return TRANSITION_META[reason] ?? { label: reason || '未知', icon: Pointer, type: 'info' };
}

function segmentTitle(segmentId: string): string {
  return segmentMap.value.get(segmentId)?.title ?? segmentId;
}

function clampPercent(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.round(Math.max(0, Math.min(100, value)) * 10) / 10;
}

function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—';
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) return `${hours}h${minutes.toString().padStart(2, '0')}m`;
  if (minutes > 0) return `${minutes}m${seconds.toString().padStart(2, '0')}s`;
  return `${seconds}s`;
}

function formatClock(ms: number): string {
  if (!ms) return '--:--:--';
  return new Date(ms).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function describeApiError(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const detail = (error.response?.data as { detail?: unknown } | undefined)?.detail;
    return asText(detail) ?? error.message;
  }
  return error instanceof Error ? error.message : '未知错误';
}

async function fetchState(): Promise<void> {
  try {
    const res = await outlineApi.getState();
    state.value = res.data;
    loadError.value = null;
  } catch (error: unknown) {
    state.value = null;
    const status = axios.isAxiosError(error) ? error.response?.status : undefined;
    if (status === 404) {
      loadError.value = '决策器未加载（decision_manager 未注入），无法读取大纲状态';
    } else if (status === 501) {
      loadError.value = '当前 Decider 不支持大纲（需要 AmaidesuDecider 等 outline_* 实现）';
    } else {
      loadError.value = `读取大纲状态失败：${describeApiError(error)}`;
    }
  }
}

async function fetchSegments(): Promise<void> {
  try {
    const res = await outlineApi.getSegments();
    segments.value = res.data.loaded ? res.data.segments : [];
  } catch {
    segments.value = [];
  }
}

async function fetchTransitions(): Promise<void> {
  try {
    const res = await outlineApi.getTransitions();
    transitions.value = res.data.loaded ? res.data.transitions : [];
  } catch {
    transitions.value = [];
  }
}

async function refreshRuntime(): Promise<void> {
  await Promise.all([fetchState(), fetchTransitions()]);
}

async function handleManualRefresh(): Promise<void> {
  refreshing.value = true;
  try {
    await Promise.all([fetchState(), fetchSegments(), fetchTransitions()]);
  } finally {
    refreshing.value = false;
  }
}

async function sendControl(action: OutlineControlAction): Promise<void> {
  if (action === 'jump' && !jumpTargetId.value) {
    ElMessage.warning('请先选择跳转目标环节');
    return;
  }
  controlBusy.value = action;
  try {
    await outlineApi.control(action, action === 'jump' ? jumpTargetId.value : undefined);
    ElMessage.success(`${CONTROL_LABELS[action]}指令已发送`);
    await refreshRuntime();
  } catch (error: unknown) {
    ElMessage.error(`${CONTROL_LABELS[action]}失败：${describeApiError(error)}`);
  } finally {
    controlBusy.value = '';
  }
}

async function handleLoadOutline(): Promise<void> {
  const path = loadPath.value.trim();
  if (!path) {
    ElMessage.warning('请输入大纲文件路径');
    return;
  }
  loadingOutline.value = true;
  try {
    const res = await outlineApi.loadOutline(path);
    ElMessage.success(`大纲已加载：${res.data.path}`);
    reloadDialogVisible.value = false;
    await Promise.all([fetchState(), fetchSegments(), fetchTransitions()]);
  } catch (error: unknown) {
    ElMessage.error(`加载失败：${describeApiError(error)}`);
  } finally {
    loadingOutline.value = false;
  }
}

/** 编辑 Tab 保存并重载后，刷新运行数据（state + segments + transitions） */
async function handleOutlineReloaded(): Promise<void> {
  await Promise.all([fetchState(), fetchSegments(), fetchTransitions()]);
}

let pollTimer: ReturnType<typeof setInterval> | null = null;

function startPolling(): void {
  if (pollTimer !== null) return;
  pollTimer = setInterval(() => {
    void refreshRuntime();
  }, POLL_INTERVAL_MS);
}

function stopPolling(): void {
  if (pollTimer !== null) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

watch(activeTab, tab => {
  if (tab === 'run') {
    startPolling();
    void refreshRuntime();
  } else {
    stopPolling();
  }
});

/** 新发言到达时滚动到列表底部（聊天日志模式） */
watch(
  () => speechItems.value.length,
  async () => {
    await nextTick();
    const el = speechListRef.value;
    if (el) el.scrollTop = el.scrollHeight;
  },
);

onMounted(() => {
  void handleManualRefresh();
  startPolling();
});

onUnmounted(stopPolling);
</script>

<style scoped>
.outline-workbench {
  display: flex;
  flex-direction: column;
  max-width: 1180px;
  margin: 0 auto;
}

/* ===== 头部 ===== */
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: var(--spacing-sm);
}
.header-left {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-xs);
}
.page-title {
  font-size: 24px;
  font-weight: 600;
  margin: 0;
  color: var(--text-primary);
}
.page-subtitle {
  font-size: 13px;
  color: var(--text-secondary);
  margin: 2px 0 0 0;
}

.tab-body {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
  padding-bottom: var(--spacing-lg);
}

/* ===== 通用卡片 ===== */
.card {
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
  padding: var(--spacing-md) var(--spacing-lg);
  box-shadow: var(--shadow-sm);
}
.card-head {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  font-size: 12px;
  color: var(--text-secondary);
  margin-bottom: var(--spacing-sm);
}
.card-icon {
  font-size: 15px;
  color: var(--color-primary);
}
.card-title {
  font-weight: 600;
  font-size: 13px;
  color: var(--text-primary);
}
.card-hint {
  font-size: 11px;
  color: var(--text-placeholder);
}
.card-body {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
}
.row-spacer {
  flex: 1;
}
.tag-icon {
  margin-right: 3px;
  vertical-align: -1px;
}
.placeholder {
  margin: 0;
  font-size: 13px;
  color: var(--text-secondary);
  font-style: italic;
}
.list-empty {
  padding: var(--spacing-lg) 0;
  text-align: center;
  font-size: 13px;
  color: var(--text-placeholder);
}

/* ===== ① 状态栏 ===== */
.status-card {
  padding: var(--spacing-sm) var(--spacing-lg);
}
.status-row {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  flex-wrap: wrap;
}
.status-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 12px;
  font-size: 12px;
  font-weight: 600;
  border-radius: 999px;
  background: var(--bg-hover);
  color: var(--text-regular);
  border: 1px solid var(--border-color-light);
}
.status-pill .dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--text-placeholder);
}
.status-pill--running {
  color: var(--color-success);
  border-color: rgba(103, 194, 58, 0.4);
  background: var(--color-success-bg);
}
.status-pill--running .dot {
  background: var(--color-success);
  box-shadow: 0 0 0 3px rgba(103, 194, 58, 0.18);
}
.status-pill--paused {
  color: var(--color-warning);
  border-color: rgba(230, 162, 60, 0.4);
  background: var(--color-warning-bg);
}
.status-pill--paused .dot {
  background: var(--color-warning);
}
.status-pill--completed {
  color: var(--color-primary);
  border-color: rgba(64, 158, 255, 0.4);
  background: var(--color-input-bg);
}
.status-pill--completed .dot {
  background: var(--color-primary);
}
.status-pill--inactive .dot,
.status-pill--unloaded .dot {
  background: var(--text-placeholder);
}
.outline-meta {
  display: flex;
  align-items: baseline;
  gap: var(--spacing-sm);
  min-width: 0;
}
.outline-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.outline-id {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-secondary);
  background: var(--bg-hover);
  padding: 1px 6px;
  border-radius: var(--radius-sm);
}

/* ===== 空态 ===== */
.empty-card {
  padding: var(--spacing-lg);
}
.empty-load {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  flex-wrap: wrap;
  justify-content: center;
}

/* ===== ② 当前环节 ===== */
.current-card {
  border-left: 3px solid var(--color-primary);
}
.current-title {
  font-size: 22px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
}
.time-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 10px;
  border-radius: 999px;
  background: var(--bg-hover);
  font-size: 12px;
}
.time-label {
  color: var(--text-secondary);
}
.time-value {
  font-family: var(--font-mono);
  font-weight: 700;
  color: var(--color-primary);
}
.metric-row {
  display: flex;
  gap: var(--spacing-lg);
  flex-wrap: wrap;
}
.metric {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.metric-label {
  font-size: 11px;
  color: var(--text-secondary);
  letter-spacing: 0.4px;
}
.metric-value {
  font-size: 15px;
  font-weight: 600;
  font-family: var(--font-mono);
  color: var(--text-primary);
}
.metric--accent .metric-value {
  color: var(--color-primary);
}
.task-desc {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--text-regular);
  padding: var(--spacing-sm) var(--spacing-md);
  background: var(--bg-hover);
  border-radius: var(--radius-md);
}
.key-points {
  display: flex;
  gap: var(--spacing-xs);
  flex-wrap: wrap;
}
.branch-list {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-xs);
  padding-top: var(--spacing-xs);
  border-top: 1px dashed var(--border-color-light);
}
.branch-head {
  font-size: 11px;
  color: var(--text-secondary);
  letter-spacing: 0.4px;
}
.branch-row {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  font-size: 13px;
}
.branch-desc {
  color: var(--text-regular);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.branch-arrow {
  color: var(--text-placeholder);
  font-size: 12px;
}
.branch-target {
  color: var(--color-primary);
  font-weight: 500;
}
.next-hint {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  margin-top: var(--spacing-sm);
  padding-top: var(--spacing-sm);
  border-top: 1px dashed var(--border-color-light);
  font-size: 13px;
  color: var(--text-secondary);
}
.next-label {
  font-size: 11px;
  letter-spacing: 0.4px;
}
.next-title {
  color: var(--text-primary);
  font-weight: 500;
}

/* ===== ③ AI 视角 ===== */
.ai-card {
  padding-top: 0;
  padding-bottom: 0;
}
.ai-card :deep(.el-collapse),
.ai-card :deep(.el-collapse-item__header),
.ai-card :deep(.el-collapse-item__wrap) {
  border: none;
  background: transparent;
}
.collapse-title {
  display: inline-flex;
  align-items: center;
  gap: var(--spacing-sm);
}
.ai-body {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
  padding-bottom: var(--spacing-sm);
}
.ai-row {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: var(--spacing-sm) var(--spacing-md);
  background: var(--bg-hover);
  border-radius: var(--radius-md);
  border-left: 2px solid var(--color-decision);
}
.ai-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--color-decision);
  letter-spacing: 0.4px;
}
.ai-value {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--text-regular);
}
.ai-points {
  margin: 0;
  padding-left: var(--spacing-md);
  font-size: 13px;
  line-height: 1.8;
  color: var(--text-regular);
}

/* ===== ④ 实时发言流 ===== */
.speech-card {
  border-left: 3px solid var(--color-decision);
}
.speech-list {
  height: 320px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: var(--spacing-xs);
  padding-right: var(--spacing-xs);
}
.speech-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: var(--spacing-sm);
  background: var(--bg-hover);
  border-radius: var(--radius-md);
}
.speech-meta {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  white-space: nowrap;
  overflow: hidden;
}
.speech-time {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-secondary);
  flex-shrink: 0;
}
.speech-meta .el-tag {
  flex-shrink: 0;
}
.speech-text {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-primary);
  word-break: break-word;
}
.speech-list::-webkit-scrollbar {
  width: 6px;
}
.speech-list::-webkit-scrollbar-thumb {
  background: var(--border-color-dark);
  border-radius: 3px;
}
.speech-list::-webkit-scrollbar-track {
  background: transparent;
}

/* ===== ⑤ 推进时间线 ===== */
.timeline-card :deep(.el-timeline) {
  padding-left: 2px;
}
.timeline-row {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  flex-wrap: wrap;
  font-size: 13px;
}
.timeline-reason {
  font-weight: 600;
  color: var(--text-primary);
}
.timeline-title {
  color: var(--text-regular);
}
.timeline-stayed {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-secondary);
}

/* ===== ⑥ 控制区 ===== */
.control-card {
  background: var(--bg-elevated);
}
.control-row {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  flex-wrap: wrap;
}

/* ===== ⑦ 整场进度 ===== */
.live-card {
  background: linear-gradient(135deg, var(--bg-card) 0%, var(--bg-elevated) 100%);
}
.segment-count {
  display: inline-flex;
  align-items: baseline;
  gap: 4px;
  font-size: 12px;
  color: var(--text-secondary);
  font-family: var(--font-mono);
}
.segment-count strong {
  font-size: 16px;
  color: var(--color-primary);
}
.count-divider {
  color: var(--text-placeholder);
}
.count-key {
  margin-left: 4px;
}

.dialog-hint {
  margin: var(--spacing-sm) 0 0 0;
  font-size: 12px;
  color: var(--text-secondary);
}

@media (max-width: 768px) {
  .metric-row {
    gap: var(--spacing-md);
  }
  .control-row {
    flex-direction: column;
    align-items: stretch;
  }
  .control-row :deep(.el-select) {
    width: 100% !important;
  }
}
</style>
