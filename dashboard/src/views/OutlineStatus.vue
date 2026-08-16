<template>
  <div class="outline-status">
    <!-- ===== 页面头部 ===== -->
    <header class="page-header">
      <div class="header-left">
        <h1 class="page-title">直播大纲</h1>
        <p class="page-subtitle">实时追踪直播环节进度 · 手动控制推进</p>
      </div>
      <div class="header-actions">
        <span :class="['status-pill', `status-pill--${statusKind}`]">
          <span class="dot" />
          {{ statusLabel }}
        </span>
        <el-tag v-if="state?.is_paused" type="warning" size="small" effect="dark">已暂停</el-tag>
        <el-tag v-if="state?.expanded_ready" type="success" size="small" effect="plain"
          >已扩展</el-tag
        >
        <el-button size="small" :loading="loading" @click="fetchState">
          <el-icon><Refresh /></el-icon>
          刷新
        </el-button>
      </div>
    </header>

    <!-- ===== 加载 / 错误提示 ===== -->
    <el-alert
      v-if="loadError"
      type="warning"
      :title="loadError"
      show-icon
      :closable="false"
      class="load-error"
    />

    <!-- ===== 空状态：未加载大纲 ===== -->
    <section v-if="showEmptyState" class="empty-state">
      <el-icon class="empty-icon"><DocumentRemove /></el-icon>
      <h2 class="empty-title">未加载大纲</h2>
      <p class="empty-desc">
        请在下方输入大纲 TOML 文件路径并点击"加载大纲"。例如：<code>data/outlines/live.toml</code>
      </p>
      <div class="empty-load">
        <el-input
          v-model="loadPath"
          placeholder="data/outlines/live.toml"
          size="default"
          clearable
          style="width: 360px"
          @keydown.enter="loadOutline"
        />
        <el-button type="primary" :loading="loadingOutline" @click="loadOutline">
          <el-icon><FolderOpened /></el-icon>
          加载大纲
        </el-button>
      </div>
    </section>

    <!-- ===== 主体内容：有大纲时 ===== -->
    <template v-else>
      <!-- 整场进度卡片 -->
      <section class="card live-progress-card">
        <div class="card-head">
          <span class="card-icon">🎬</span>
          <span class="card-title">整场进度</span>
          <span class="bubble-spacer" />
          <span class="progress-count">
            <strong>{{ state?.completed_count ?? 0 }}</strong>
            <span class="divider">/</span>
            <span>{{ state?.total_count ?? 0 }}</span>
            <span class="progress-key">环节</span>
          </span>
        </div>
        <div class="card-body">
          <el-progress :percentage="livePercent" :stroke-width="14" :status="liveProgressStatus" />
          <div class="progress-detail">
            <span class="detail-item">
              <span class="detail-label">已进行</span>
              <span class="detail-value">{{ formatDuration(liveElapsedMs) }}</span>
            </span>
            <span class="detail-item">
              <span class="detail-label">预计总时长</span>
              <span class="detail-value">{{ formatDuration(liveTotalMs) }}</span>
            </span>
            <span class="detail-item detail-item--accent">
              <span class="detail-label">完成度</span>
              <span class="detail-value">{{ livePercent.toFixed(1) }}%</span>
            </span>
          </div>
        </div>
      </section>

      <!-- 当前环节卡片 -->
      <section v-if="state?.current_segment" class="card current-card">
        <div class="card-head">
          <span class="card-icon">🎯</span>
          <span class="card-title">当前环节</span>
          <el-tag size="small" effect="plain">{{ state.current_segment.id }}</el-tag>
          <span class="bubble-spacer" />
          <span class="time-pill">
            <span class="time-label">剩余</span>
            <span class="time-value">{{ formatDuration(currentRemainingMs) }}</span>
          </span>
        </div>
        <div class="card-body">
          <h2 class="current-title">{{ state.current_segment.title }}</h2>
          <el-progress
            :percentage="currentPercent"
            :stroke-width="10"
            :status="state.is_paused ? 'warning' : 'success'"
          />
          <div class="time-detail">
            <span class="time-item">
              <span class="time-item-label">已用</span>
              <span class="time-item-value">{{ formatDuration(currentElapsedMs) }}</span>
            </span>
            <span class="time-item">
              <span class="time-item-label">计划时长</span>
              <span class="time-item-value">{{ formatDuration(currentDurationMs) }}</span>
            </span>
          </div>
        </div>
      </section>
      <section v-else class="card current-card current-card--none">
        <div class="card-head">
          <span class="card-icon">🎯</span>
          <span class="card-title">当前环节</span>
        </div>
        <div class="card-body">
          <p class="no-segment">大纲中暂无进行中的环节（全部完成或未启动）。</p>
        </div>
      </section>

      <!-- 控制按钮组 -->
      <section class="card control-card">
        <div class="card-head">
          <span class="card-icon">⚙</span>
          <span class="card-title">手动控制</span>
        </div>
        <div class="card-body control-body">
          <div class="control-row">
            <el-button
              :type="state?.is_paused ? 'success' : 'warning'"
              :disabled="!canControl"
              :loading="controlBusy === 'pause'"
              @click="sendControl('pause')"
              v-show="!state?.is_paused"
            >
              <el-icon><VideoPause /></el-icon>
              暂停
            </el-button>
            <el-button
              type="success"
              :disabled="!canControl"
              :loading="controlBusy === 'resume'"
              @click="sendControl('resume')"
              v-show="state?.is_paused"
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
              跳过（下一环节）
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
              placeholder="选择目标环节跳转"
              clearable
              filterable
              :disabled="!canControl || !segmentsOptions.length"
              style="width: 220px"
              size="default"
            >
              <el-option
                v-for="opt in segmentsOptions"
                :key="opt.value"
                :label="opt.label"
                :value="opt.value"
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
          </div>
        </div>
      </section>

      <!-- 下一环节预览 -->
      <section v-if="state?.next_segment" class="card next-card">
        <div class="card-head">
          <span class="card-icon">⏭</span>
          <span class="card-title">下一环节</span>
          <el-tag size="small" effect="plain">{{ state.next_segment.id }}</el-tag>
        </div>
        <div class="card-body">
          <p class="next-title">{{ state.next_segment.title }}</p>
        </div>
      </section>

      <!-- 已完成环节列表 -->
      <section v-if="completedSegments.length > 0" class="card completed-card">
        <div class="card-head">
          <span class="card-icon">✅</span>
          <span class="card-title">已完成环节 ({{ completedSegments.length }})</span>
        </div>
        <div class="card-body">
          <ol class="completed-list">
            <li v-for="seg in completedSegments" :key="seg.id" class="completed-item">
              <span class="completed-tag">{{ seg.id }}</span>
              <span class="completed-title">{{ seg.title }}</span>
              <span class="completed-duration">{{ formatDuration(seg.duration_ms) }}</span>
            </li>
          </ol>
        </div>
      </section>

      <!-- 加载 / 重新加载大纲 -->
      <section class="card load-card">
        <div class="card-head">
          <span class="card-icon">📁</span>
          <span class="card-title">大纲文件</span>
        </div>
        <div class="card-body load-body">
          <el-input
            v-model="loadPath"
            placeholder="data/outlines/live.toml"
            size="default"
            clearable
            style="flex: 1; min-width: 240px"
            @keydown.enter="loadOutline"
          />
          <el-button type="primary" :loading="loadingOutline" @click="loadOutline">
            <el-icon><FolderOpened /></el-icon>
            加载大纲
          </el-button>
          <el-button :icon="Refresh" :loading="loading" @click="fetchState">刷新状态</el-button>
        </div>
      </section>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { ElMessage } from 'element-plus';
import axios from 'axios';
import {
  Refresh,
  FolderOpened,
  VideoPause,
  VideoPlay,
  DArrowRight,
  DArrowLeft,
  Position,
  DocumentRemove,
} from '@element-plus/icons-vue';

// ====== 类型定义 ======
interface SegmentSnapshot {
  id: string;
  title: string;
  task_description?: string;
  duration_ms: number;
  min_duration_ms?: number | null;
  key_points?: string[];
  branches?: Array<{ branch_id: string; description: string; target_segment_id: string }>;
}

interface SegmentsResponse {
  loaded: boolean;
  outline_id?: string;
  title?: string;
  fallback_segment_id?: string | null;
  path?: string | null;
  segments: SegmentSnapshot[];
}

interface LiveProgress {
  elapsed_ms: number;
  total_ms: number;
  percent: number;
}

interface OutlineState {
  status: 'running' | 'paused' | 'inactive' | string;
  current_segment: {
    id: string;
    title: string;
    elapsed_ms: number;
    duration_ms: number;
    remaining_ms: number;
  } | null;
  next_segment: { id: string; title: string } | null;
  completed_count: number;
  total_count: number;
  is_paused: boolean;
  expanded_ready: boolean;
  live_progress: LiveProgress;
}

type ControlAction = 'skip' | 'pause' | 'resume' | 'rewind' | 'jump';

// ====== 响应式数据 ======
const state = ref<OutlineState | null>(null);
const segments = ref<SegmentSnapshot[]>([]);
const loadPath = ref('data/outlines/live.toml');
const loading = ref(false);
const loadingOutline = ref(false);
const controlBusy = ref<ControlAction | ''>('');
const loadError = ref<string | null>(null);
const jumpTargetId = ref<string>('');

let pollTimer: ReturnType<typeof setInterval> | null = null;

// ====== 计算属性 ======

/** 是否处于空状态：未加载大纲 / 没有任何环节 / 401/501 等不可用 */
const showEmptyState = computed(() => {
  if (loadError.value && !state.value) return true;
  if (!state.value) return true;
  return state.value.total_count === 0 && state.value.status === 'inactive';
});

/** 状态枚举对应的视觉分类 */
const statusKind = computed<'running' | 'paused' | 'inactive'>(() => {
  if (!state.value) return 'inactive';
  if (state.value.is_paused) return 'paused';
  if (state.value.status === 'running') return 'running';
  return 'inactive';
});

const statusLabel = computed(() => {
  if (!state.value) return '未连接';
  if (state.value.is_paused) return '已暂停';
  if (state.value.status === 'running') return '运行中';
  if (state.value.status === 'paused') return '已暂停';
  return '未激活';
});

const livePercent = computed(() => {
  const p = state.value?.live_progress?.percent ?? 0;
  return Math.max(0, Math.min(100, Number(p) || 0));
});

const liveElapsedMs = computed(() => state.value?.live_progress?.elapsed_ms ?? 0);
const liveTotalMs = computed(() => state.value?.live_progress?.total_ms ?? 0);

const currentElapsedMs = computed(() => state.value?.current_segment?.elapsed_ms ?? 0);
const currentDurationMs = computed(() => state.value?.current_segment?.duration_ms ?? 0);
const currentRemainingMs = computed(() => state.value?.current_segment?.remaining_ms ?? 0);

const currentPercent = computed(() => {
  const total = currentDurationMs.value;
  if (total <= 0) return 0;
  return Math.max(0, Math.min(100, (currentElapsedMs.value / total) * 100));
});

const liveProgressStatus = computed<'' | 'success' | 'warning' | 'exception'>(() => {
  if (!state.value) return '';
  if (state.value.is_paused) return 'warning';
  if (livePercent.value >= 100) return 'exception';
  if (livePercent.value > 0) return 'success';
  return '';
});

const canControl = computed(() => {
  if (!state.value) return false;
  return state.value.total_count > 0;
});

const segmentsOptions = computed(() => {
  return segments.value.map(s => ({
    value: s.id,
    label: `${s.id} · ${s.title}`,
  }));
});

/** 粗略的"已完成环节"列表：基于 current_segment 之前的环节
 *  后端未直接返回 completed_segments 列表，所以这里取 segments 中 index
 *  小于 current_segment 索引的部分作为"已完成"。如果无法定位（current_segment 缺失），
 *  则用 completed_count 截取前 N 段。 */
const completedSegments = computed(() => {
  if (segments.value.length === 0) return [];
  const currentId = state.value?.current_segment?.id;
  if (currentId) {
    const idx = segments.value.findIndex(s => s.id === currentId);
    if (idx >= 0) return segments.value.slice(0, idx);
  }
  const count = state.value?.completed_count ?? 0;
  return segments.value.slice(0, Math.max(0, Math.min(count, segments.value.length)));
});

// ====== 工具函数 ======
function formatDuration(ms: number): string {
  const totalSec = Math.max(0, Math.floor((ms || 0) / 1000));
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  if (h > 0) return `${h}h${m.toString().padStart(2, '0')}m`;
  if (m > 0) return `${m}m${s.toString().padStart(2, '0')}s`;
  return `${s}s`;
}

// ====== API 调用 ======
async function fetchState(): Promise<void> {
  loading.value = true;
  loadError.value = null;
  try {
    const res = await axios.get<OutlineState>('/api/v1/outline/state');
    state.value = res.data;
  } catch (e: unknown) {
    const status = axios.isAxiosError(e) ? e.response?.status : undefined;
    if (status === 404) {
      loadError.value = '决策器未加载（decision_manager 未注入），无法读取大纲状态';
      state.value = null;
    } else if (status === 501) {
      loadError.value = '当前未启用支持大纲的 Decider（需要 AmaidesuDecider 等 outline_* 实现）';
      state.value = null;
    } else if (axios.isAxiosError(e)) {
      loadError.value = `读取大纲状态失败：${e.message}`;
    } else {
      loadError.value = '读取大纲状态失败：未知错误';
    }
  } finally {
    loading.value = false;
  }
}

async function fetchSegments(): Promise<void> {
  try {
    const res = await axios.get<SegmentsResponse>('/api/v1/outline/segments');
    if (res.data?.loaded) {
      segments.value = res.data.segments ?? [];
    } else {
      segments.value = [];
    }
  } catch {
    // 静默失败：segments 用于跳转下拉与已完成列表，state 已能展示核心信息
    segments.value = [];
  }
}

async function sendControl(action: ControlAction): Promise<void> {
  controlBusy.value = action;
  try {
    const body: { action: ControlAction; segment_id?: string } = { action };
    if (action === 'jump') {
      if (!jumpTargetId.value) {
        ElMessage.warning('请选择目标环节');
        controlBusy.value = '';
        return;
      }
      body.segment_id = jumpTargetId.value;
    }
    await axios.post('/api/v1/outline/control', body);
    ElMessage.success(actionLabel(action) + '指令已发送');
    // 立即拉一次新状态，避免等下一轮轮询
    await fetchState();
  } catch (e: unknown) {
    const msg =
      axios.isAxiosError(e) && e.response?.data?.detail
        ? String(e.response.data.detail)
        : e instanceof Error
          ? e.message
          : '控制指令失败';
    ElMessage.error(`${actionLabel(action)}失败：${msg}`);
  } finally {
    controlBusy.value = '';
  }
}

async function loadOutline(): Promise<void> {
  const path = loadPath.value.trim();
  if (!path) {
    ElMessage.warning('请输入大纲文件路径');
    return;
  }
  loadingOutline.value = true;
  try {
    const res = await axios.post<{ status: string; path: string; outline_id?: string }>(
      '/api/v1/outline/load',
      { path },
    );
    ElMessage.success(`大纲已加载：${res.data.path}`);
    await Promise.all([fetchState(), fetchSegments()]);
  } catch (e: unknown) {
    const msg =
      axios.isAxiosError(e) && e.response?.data?.detail
        ? String(e.response.data.detail)
        : e instanceof Error
          ? e.message
          : '加载大纲失败';
    ElMessage.error(`加载失败：${msg}`);
  } finally {
    loadingOutline.value = false;
  }
}

function actionLabel(action: ControlAction): string {
  switch (action) {
    case 'pause':
      return '暂停';
    case 'resume':
      return '继续';
    case 'skip':
      return '跳过';
    case 'rewind':
      return '回退';
    case 'jump':
      return '跳转';
  }
}

// ====== 生命周期 ======
onMounted(async () => {
  await Promise.all([fetchState(), fetchSegments()]);
  // 每 1 秒轮询一次，保持进度条平滑更新
  pollTimer = setInterval(() => {
    void fetchState();
  }, 1000);
});

onUnmounted(() => {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
});
</script>

<style scoped>
.outline-status {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
  max-width: 1100px;
  margin: 0 auto;
}

/* ===== 头部 ===== */
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
.header-actions {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  flex-wrap: wrap;
}

/* 状态徽标 */
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
.status-pill--running .dot {
  background: var(--color-success);
  box-shadow: 0 0 0 2px rgba(103, 194, 58, 0.18);
}
.status-pill--running {
  color: var(--color-success);
  border-color: rgba(103, 194, 58, 0.4);
  background: rgba(103, 194, 58, 0.06);
}
.status-pill--paused .dot {
  background: var(--color-warning);
}
.status-pill--paused {
  color: var(--color-warning);
  border-color: rgba(230, 162, 60, 0.4);
  background: rgba(230, 162, 60, 0.06);
}
.status-pill--inactive .dot {
  background: var(--text-placeholder);
}

.load-error {
  margin-bottom: var(--spacing-sm);
}

/* ===== 空状态 ===== */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--spacing-sm);
  padding: var(--spacing-xl) var(--spacing-lg);
  background: var(--bg-card);
  border: 1px dashed var(--border-color-light);
  border-radius: var(--radius-lg);
  color: var(--text-secondary);
  text-align: center;
}
.empty-icon {
  font-size: 56px;
  color: var(--text-placeholder);
}
.empty-title {
  font-size: 18px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
}
.empty-desc {
  font-size: 13px;
  color: var(--text-secondary);
  margin: 0;
  max-width: 520px;
  line-height: 1.6;
}
.empty-desc code {
  font-family: var(--font-mono);
  background: var(--bg-hover);
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 12px;
  color: var(--color-primary);
}
.empty-load {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  margin-top: var(--spacing-md);
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
  font-size: 14px;
}
.card-title {
  font-weight: 600;
  font-size: 13px;
  color: var(--text-primary);
}
.bubble-spacer {
  flex: 1;
}
.card-body {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
}

/* 整场进度 */
.live-progress-card {
  background: linear-gradient(135deg, var(--bg-card) 0%, var(--bg-elevated) 100%);
}
.progress-count {
  display: inline-flex;
  align-items: baseline;
  gap: 4px;
  font-size: 12px;
  color: var(--text-secondary);
  font-family: var(--font-mono);
}
.progress-count strong {
  font-size: 16px;
  color: var(--color-primary);
}
.progress-count .divider {
  color: var(--text-placeholder);
}
.progress-count .progress-key {
  margin-left: 4px;
  color: var(--text-secondary);
}
.progress-detail {
  display: flex;
  gap: var(--spacing-lg);
  flex-wrap: wrap;
  margin-top: var(--spacing-xs);
}
.detail-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.detail-label {
  font-size: 11px;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.4px;
}
.detail-value {
  font-size: 16px;
  font-weight: 600;
  font-family: var(--font-mono);
  color: var(--text-primary);
}
.detail-item--accent .detail-value {
  color: var(--color-primary);
}

/* 当前环节 */
.current-card {
  border-left: 3px solid var(--color-primary);
}
.current-card--none {
  border-left-color: var(--border-color-light);
}
.current-title {
  font-size: 22px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0 0 var(--spacing-xs) 0;
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
.time-detail {
  display: flex;
  gap: var(--spacing-lg);
  flex-wrap: wrap;
}
.time-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.time-item-label {
  font-size: 11px;
  color: var(--text-secondary);
}
.time-item-value {
  font-size: 14px;
  font-weight: 500;
  font-family: var(--font-mono);
  color: var(--text-regular);
}
.no-segment {
  margin: 0;
  font-size: 13px;
  color: var(--text-secondary);
  font-style: italic;
}

/* 控制按钮 */
.control-card {
  background: var(--bg-elevated);
}
.control-body {
  padding-top: var(--spacing-xs);
}
.control-row {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  flex-wrap: wrap;
}

/* 下一环节预览 */
.next-card {
  border-left: 3px solid var(--color-warning);
}
.next-title {
  font-size: 15px;
  font-weight: 500;
  color: var(--text-primary);
  margin: 0;
}

/* 已完成列表 */
.completed-card {
  border-left: 3px solid var(--color-success);
}
.completed-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: var(--spacing-xs);
}
.completed-item {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  padding: 6px var(--spacing-sm);
  background: var(--bg-page);
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-color-light);
  font-size: 13px;
}
.completed-tag {
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 600;
  padding: 1px 8px;
  background: rgba(103, 194, 58, 0.12);
  color: var(--color-success);
  border-radius: 4px;
  flex-shrink: 0;
}
.completed-title {
  flex: 1;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.completed-duration {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-secondary);
  flex-shrink: 0;
}

/* 加载卡片 */
.load-card {
  border-top: 1px dashed var(--border-color-light);
}
.load-body {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  flex-wrap: wrap;
}

@media (max-width: 768px) {
  .progress-detail {
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
