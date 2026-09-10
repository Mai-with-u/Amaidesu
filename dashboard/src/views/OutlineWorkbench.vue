<template>
  <div class="agenda-workbench">
    <!-- 顶部：标题 + 副标题 + 刷新 -->
    <header class="page-header">
      <div class="header-left">
        <h1 class="page-title">流程单工作台</h1>
        <p class="page-subtitle">流程单实时状态 · 手动控制 · 推进历史</p>
      </div>
      <div class="header-actions">
        <el-button :icon="Refresh" :loading="loadingState" @click="refresh"> 刷新 </el-button>
      </div>
    </header>

    <!-- 骨架屏：初次加载 -->
    <div v-if="initialLoading" class="state-block">
      <el-skeleton :rows="4" animated />
    </div>

    <!-- 错误态：拉取失败 -->
    <el-alert
      v-else-if="loadError"
      :title="loadError"
      type="error"
      :closable="false"
      show-icon
      class="state-block"
    >
      <el-button size="small" type="primary" @click="refresh">重试</el-button>
    </el-alert>

    <!-- 不可用态：后端流程单未加载 -->
    <template v-else-if="state && !state.available">
      <el-alert
        :title="state.message ?? '流程单未加载'"
        type="warning"
        :closable="false"
        show-icon
        class="state-block"
      >
        <p class="hint-line">
          请在设置页配置 <code>agents.streamer.rundown_id</code>；未配置时主播 Agent
          会自动使用内置默认流程单。
        </p>
        <el-button size="small" type="primary" @click="refresh">重试</el-button>
      </el-alert>
    </template>

    <!-- 未加载态：status ∈ {inactive, unloaded} -->
    <template v-else-if="isNotLoaded">
      <section class="load-card">
        <h3 class="load-title">流程单未启动</h3>
        <p class="load-desc">
          未配置 rundown_id 时，主播 Agent 会自动使用内置默认流程单（初次直播·自我介绍）； Agent
          启动后本页将展示实时进度。可在下方环节预览查看流程单内容。
        </p>
      </section>

      <!-- 仍渲染环节清单（如有），用于预览 -->
      <section v-if="state && state.segments.length > 0" class="segments-section">
        <header class="section-bar">
          <h3 class="section-title">环节预览</h3>
          <span class="section-meta">共 {{ state.segments.length }} 个环节</span>
        </header>
        <el-table
          :data="state.segments"
          stripe
          size="default"
          class="segments-table"
          @row-click="openDrawer"
        >
          <el-table-column label="#" type="index" width="56" align="center" />
          <el-table-column label="环节名" min-width="200">
            <template #default="{ row }">{{ row.title }}</template>
          </el-table-column>
          <el-table-column label="预期时长" width="110">
            <template #default="{ row }">
              <span class="mono">{{ formatDuration(row.expected_ms) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="100" align="center">
            <template #default>
              <el-tag size="small" type="info" effect="plain">待开始</el-tag>
            </template>
          </el-table-column>
        </el-table>
      </section>
    </template>

    <!-- 运行态：status ∈ {running, paused, done} -->
    <template v-else-if="snapshot">
      <!-- 1. 总览 KPI 行 -->
      <section class="totals-row">
        <article class="total-card total-status">
          <div class="total-label">状态</div>
          <div class="total-value status-value">
            <el-tag :type="statusTagType" effect="dark" size="large" class="status-tag">
              {{ statusLabel }}
            </el-tag>
            <el-tag
              v-if="snapshot.paused"
              type="warning"
              effect="plain"
              size="small"
              class="paused-tag"
            >
              已暂停
            </el-tag>
          </div>
          <div class="total-sub">
            流程单 ID：<span class="mono">{{ snapshot.rundown_id ?? '—' }}</span>
          </div>
        </article>

        <article class="total-card total-title">
          <div class="total-label">流程单标题</div>
          <div class="total-value title-value">{{ snapshot.title ?? '—' }}</div>
          <div class="total-sub">
            进度 {{ snapshot.index }} / {{ snapshot.total || '?' }} 个环节
          </div>
        </article>

        <article class="total-card total-progress">
          <div class="total-label">整场进度</div>
          <el-progress
            :percentage="progressPercent"
            :stroke-width="14"
            :show-text="false"
            :color="progressColor"
            class="progress-bar"
          />
          <div class="progress-text mono">
            <span class="progress-percent mono">{{ progressPercent.toFixed(1) }}%</span>
          </div>
        </article>
      </section>

      <!-- 2. 当前环节大卡 -->
      <section
        v-if="snapshot.current"
        class="current-card"
        :class="{ 'is-paused': snapshot.paused }"
      >
        <div class="current-head">
          <span class="current-eyebrow">当前环节</span>
          <h2 class="current-title" :title="snapshot.current.title">
            {{ snapshot.current.title }}
          </h2>
          <span class="grow" />
        </div>

        <div class="current-times">
          <div class="time-block">
            <span class="time-label">已播</span>
            <span class="time-value mono">{{ formatDuration(tickElapsedMs) }}</span>
          </div>
          <div class="time-sep" aria-hidden="true">/</div>
          <div class="time-block">
            <span class="time-label">剩余</span>
            <span class="time-value mono">{{ formatDuration(tickRemainingMs) }}</span>
          </div>
          <div class="time-block time-block--total">
            <span class="time-label">总时长</span>
            <span class="time-value mono time-value--sub">{{
              formatDuration(snapshot.current.expected_ms)
            }}</span>
          </div>
        </div>

        <div class="current-actions">
          <el-button
            :type="snapshot.paused ? 'primary' : 'default'"
            :icon="VideoPause"
            :loading="actionLoading === (snapshot.paused ? 'resume' : 'pause')"
            @click="togglePause"
          >
            {{ snapshot.paused ? '继续' : '暂停' }}
          </el-button>
          <el-button :icon="ArrowRightBold" :loading="actionLoading === 'next'" @click="handleNext">
            切到下一环节
          </el-button>
        </div>

        <div v-if="nextSegment" class="next-line">
          <span class="next-eyebrow">下一环节</span>
          <span class="next-title">{{ nextSegment.title }}</span>
          <span class="next-arrow" aria-hidden="true">→</span>
        </div>
      </section>

      <el-alert
        v-else
        title="等待第一个环节开始"
        type="info"
        :closable="false"
        show-icon
        class="state-block"
      />

      <!-- 3. 环节清单 -->
      <section class="segments-section">
        <header class="section-bar">
          <h3 class="section-title">环节清单</h3>
          <span class="section-meta">共 {{ state?.segments.length ?? 0 }} 个环节</span>
        </header>
        <el-table
          :data="state?.segments ?? []"
          stripe
          size="default"
          class="segments-table"
          :row-class-name="rowClassName"
          @row-click="openDrawer"
        >
          <el-table-column label="#" type="index" width="56" align="center">
            <template #default="{ $index }">
              <span class="order-cell mono">{{ $index + 1 }}</span>
            </template>
          </el-table-column>
          <el-table-column label="环节名" min-width="220">
            <template #default="{ row }">
              <span class="segment-label">{{ row.title }}</span>
            </template>
          </el-table-column>
          <el-table-column label="预期时长" width="110">
            <template #default="{ row }">
              <span class="mono">{{ formatDuration(row.expected_ms) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="100" align="center">
            <template #default="{ row }">
              <el-tag
                :type="segmentStatusTagType(row)"
                :effect="segmentStatusTagEffect(row)"
                size="small"
              >
                {{ segmentStatusLabel(row) }}
              </el-tag>
            </template>
          </el-table-column>
        </el-table>
      </section>

      <!-- 4. 推进历史 -->
      <section class="history-section">
        <header class="section-bar">
          <h3 class="section-title">推进历史</h3>
          <span class="section-meta">最近 {{ historyEntries.length }} 条</span>
        </header>
        <div v-if="historyEntries.length === 0" class="history-empty">
          <el-empty description="暂无推进记录" :image-size="80" />
        </div>
        <el-timeline v-else class="history-timeline">
          <el-timeline-item
            v-for="(entry, idx) in historyEntries"
            :key="`${entry.at_ms}-${idx}`"
            :timestamp="formatTime(entry.at_ms)"
            :type="historyDotType(entry)"
            placement="top"
            class="history-item"
          >
            <div class="history-row">
              <span class="history-event mono">{{ entry.action }}</span>
              <span class="history-segment">{{ segmentTitleOf(entry.segment_id) }}</span>
              <span class="history-reason"
                >·
                {{ entry.by === 'human' ? '手动' : entry.by === 'system' ? '系统' : 'Agent' }}</span
              >
            </div>
          </el-timeline-item>
        </el-timeline>
      </section>
    </template>

    <!-- 兜底：available=true 但 snapshot 缺失（如已 unloaded 中间态） -->
    <template v-else>
      <el-alert
        title="流程单快照不可用"
        type="info"
        :closable="false"
        show-icon
        class="state-block"
      />
    </template>

    <!-- 环节详情抽屉 -->
    <el-drawer
      v-model="drawerOpen"
      direction="rtl"
      size="420px"
      :with-header="true"
      :title="drawerTitle"
      class="segment-drawer"
    >
      <div v-if="activeSegment" class="drawer-body">
        <section class="drawer-section">
          <h4 class="drawer-h">环节名</h4>
          <p class="drawer-text">{{ activeSegment.title }}</p>
        </section>

        <section class="drawer-section">
          <h4 class="drawer-h">任务说明</h4>
          <p class="drawer-text">{{ activeSegment.task_description || '（未提供）' }}</p>
        </section>

        <section class="drawer-section">
          <h4 class="drawer-h">关键要点</h4>
          <ul v-if="activeSegment.key_points.length > 0" class="key-points">
            <li v-for="(point, i) in activeSegment.key_points" :key="i" class="key-point">
              <span class="key-point-bullet" aria-hidden="true">·</span>
              <span>{{ point }}</span>
            </li>
          </ul>
          <p v-else class="drawer-muted">未设置关键要点</p>
        </section>

        <section class="drawer-section">
          <h4 class="drawer-h">元信息</h4>
          <dl class="meta-grid">
            <dt>预期时长</dt>
            <dd class="mono">{{ formatDuration(activeSegment.expected_ms) }}</dd>
            <template v-if="activeSegment.min_duration_ms != null">
              <dt>最短停留</dt>
              <dd class="mono">{{ formatDuration(activeSegment.min_duration_ms) }}</dd>
            </template>
            <template v-if="activeSegment.notes">
              <dt>备注</dt>
              <dd>{{ activeSegment.notes }}</dd>
            </template>
          </dl>
        </section>

        <div class="drawer-footer">
          <el-popconfirm
            title="确定跳转到该环节？当前环节会被跳过。"
            confirm-button-text="跳转"
            cancel-button-text="取消"
            @confirm="handleJump(activeSegment)"
          >
            <template #reference>
              <el-button
                type="primary"
                :icon="Position"
                :disabled="!canJumpFromDrawer"
                :loading="actionLoading === 'goto'"
              >
                跳到此环节
              </el-button>
            </template>
          </el-popconfirm>
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
/**
 * 流程单编排页 —— 流程单实时状态 + 手动控制
 *
 * 数据来源：
 * - REST 轮询：GET /api/v1/agenda/state（300ms 防抖 + WS 触发）
 * - WebSocket：rundown.changed（onMessage 过滤，触发重拉）
 * - 本地 1s setInterval：仅用于重算当前环节的 elapsed/remaining 倒计时显示
 *
 * 三态布局：
 * 1. 不可用（available=false）：主播 Agent 未启动
 * 2. 未加载（status=idle）：等待主播 Agent 启动 + 环节预览
 * 3. 运行中（status=running|paused|done）：KPI 行 + 当前环节卡 + 环节表 + 历史时间线
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import { ArrowRightBold, Position, Refresh, VideoPause } from '@element-plus/icons-vue';
import { rundownApi } from '@/api';
import { wsClient } from '@/api/websocket';
import type {
  RundownControlAction,
  RundownControlResponse,
  RundownCurrentSegment,
  RundownSegmentView,
  RundownSnapshot,
  RundownStateResponse,
  RundownTransitionEntry,
  WebSocketMessage,
} from '@/types';

// ============================================================
// 响应式状态
// ============================================================

const state = ref<RundownStateResponse | null>(null);
const initialLoading = ref(true);
const loadingState = ref(false);
const loadError = ref<string | null>(null);
const actionLoading = ref<RundownControlAction | null>(null);

// 本地 1s tick：仅重算当前环节 elapsed/remaining 展示
const nowTickMs = ref(Date.now());

// ============================================================
// 抽屉
// ============================================================

const drawerOpen = ref(false);
const activeSegment = ref<RundownSegmentView | null>(null);

const drawerTitle = computed(() =>
  activeSegment.value ? `环节详情 · ${activeSegment.value.title}` : '环节详情',
);

const canJumpFromDrawer = computed(() => snapshot.value?.status === 'running');

function openDrawer(row: RundownSegmentView) {
  activeSegment.value = row;
  drawerOpen.value = true;
}

// ============================================================
// 派生状态
// ============================================================

const snapshot = computed<RundownSnapshot | null>(() => state.value?.snapshot ?? null);

const isNotLoaded = computed(() => snapshot.value?.status === 'idle');

const statusLabel = computed(() => {
  const s = snapshot.value;
  if (!s) return '—';
  switch (s.status) {
    case 'running':
      return '进行中';
    case 'paused':
      return '已暂停';
    case 'done':
      return '已完成';
    default:
      return '未启动';
  }
});

const statusTagType = computed<'success' | 'warning' | 'info' | 'primary' | 'danger'>(() => {
  const s = snapshot.value;
  if (!s) return 'info';
  if (s.status === 'paused') return 'warning';
  if (s.status === 'running') return 'success';
  return 'info';
});

const progressPercent = computed(() => {
  const p = snapshot.value?.progress_percent;
  if (p == null || Number.isNaN(p)) return 0;
  return Math.max(0, Math.min(100, p));
});

const progressColor = computed(() => {
  if (snapshot.value?.status === 'done') return 'var(--color-info)';
  return 'var(--color-agenda)';
});

const currentSegment = computed<RundownCurrentSegment | null>(
  () => snapshot.value?.current ?? null,
);

const tickElapsedMs = computed(() => {
  const seg = currentSegment.value;
  if (!seg) return 0;
  // 后端 elapsed_ms 是快照时刻的累计；paused 时不递增
  if (snapshot.value?.paused) return Math.max(0, seg.elapsed_ms);
  const drift = nowTickMs.value - snapshotBaselineMs.value;
  return Math.max(0, Math.min(seg.expected_ms, seg.elapsed_ms + drift));
});

const tickRemainingMs = computed(() => {
  const seg = currentSegment.value;
  if (!seg) return 0;
  return Math.max(0, seg.expected_ms - tickElapsedMs.value);
});

/** 快照基线时刻（用于本地 tick 漂移计算） */
const snapshotBaselineMs = ref(Date.now());

/** 变更历史：仅展示最近 20 条，按时间倒序 */
const historyEntries = computed<RundownTransitionEntry[]>(() => {
  const list = state.value?.transitions ?? [];
  return [...list].sort((a, b) => b.at_ms - a.at_ms).slice(0, 20);
});

/** 下一环节预览（无下一环节/已结束时为 null） */
const nextSegment = computed<RundownSegmentView | null>(() => {
  const s = snapshot.value;
  if (!s || s.status === 'done') return null;
  return state.value?.segments[s.index + 1] ?? null;
});

// ============================================================
// 段状态 / 来源 / 时间格式化
// ============================================================

function segmentStatusOf(seg: RundownSegmentView): 'done' | 'current' | 'pending' {
  const cur = currentSegment.value;
  if (cur && cur.id === seg.id) return 'current';
  // 简化：用 currentSegment.id 之前的视作 done，索引比较作为兜底
  const segments = state.value?.segments ?? [];
  const idx = segments.findIndex(s => s.id === seg.id);
  if (idx === -1) return 'pending';
  const curIdx = segments.findIndex(s => s.id === cur?.id);
  if (curIdx >= 0 && idx < curIdx) return 'done';
  return 'pending';
}

function segmentStatusLabel(seg: RundownSegmentView): string {
  const s = segmentStatusOf(seg);
  if (s === 'done') return '已完成';
  if (s === 'current') return '进行中';
  return '待开始';
}

function segmentStatusTagType(seg: RundownSegmentView): 'success' | 'warning' | 'info' {
  const s = segmentStatusOf(seg);
  if (s === 'done') return 'success';
  if (s === 'current') return 'warning';
  return 'info';
}

function segmentStatusTagEffect(seg: RundownSegmentView): 'plain' | 'dark' {
  return segmentStatusOf(seg) === 'current' ? 'dark' : 'plain';
}

function segmentTitleOf(id: string): string {
  const seg = (state.value?.segments ?? []).find(s => s.id === id);
  return seg?.title ?? id;
}

function rowClassName({ row }: { row: RundownSegmentView }): string {
  return segmentStatusOf(row) === 'current' ? 'is-current-row' : '';
}

function formatDuration(ms: number | null | undefined): string {
  if (ms == null || Number.isNaN(ms) || ms < 0) return '—';
  const totalSec = Math.floor(ms / 1000);
  const hh = Math.floor(totalSec / 3600);
  const mm = Math.floor((totalSec % 3600) / 60);
  const ss = totalSec % 60;
  const pad = (n: number) => String(n).padStart(2, '0');
  return hh > 0 ? `${pad(hh)}:${pad(mm)}:${pad(ss)}` : `${pad(mm)}:${pad(ss)}`;
}

function formatTime(tsMs: number): string {
  if (!tsMs) return '—';
  const d = new Date(tsMs);
  return d.toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
}

function historyDotType(
  entry: RundownTransitionEntry,
): 'primary' | 'success' | 'warning' | 'danger' | 'info' {
  const ev = entry.action.toLowerCase();
  if (ev.includes('fail') || ev.includes('error')) return 'danger';
  if (ev.includes('skip') || ev.includes('pause') || ev.includes('override')) return 'warning';
  if (ev.includes('done') || ev.includes('complete') || ev.includes('finish')) return 'success';
  if (ev.includes('start') || ev.includes('begin') || ev.includes('load')) return 'primary';
  return 'info';
}

// ============================================================
// 数据加载
// ============================================================

async function fetchState(opts: { silent?: boolean } = {}): Promise<void> {
  if (!opts.silent) loadingState.value = true;
  loadError.value = null;
  try {
    const res = await rundownApi.getState();
    state.value = res.data;
    // 记录本次拉取的基线时刻，用于本地 tick 漂移
    snapshotBaselineMs.value = Date.now();
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : '无法加载流程单状态';
    state.value = null;
  } finally {
    initialLoading.value = false;
    loadingState.value = false;
  }
}

function refresh(): void {
  void fetchState();
}

// ============================================================
// 控制操作
// ============================================================

async function performControl(
  action: RundownControlAction,
  extra: { segment_id?: string } = {},
): Promise<RundownControlResponse['snapshot'] | null> {
  if (actionLoading.value) return null;
  actionLoading.value = action;
  try {
    const res = await rundownApi.control({ action, ...extra });
    const data = res.data;
    if (!data.success) {
      ElMessage.error(data.message || '操作失败');
      return null;
    }
    ElMessage.success(data.message || '操作成功');
    // 用响应内嵌的 snapshot 立即刷新（避免等 WS 抖动）
    if (data.snapshot && state.value) {
      state.value = { ...state.value, snapshot: data.snapshot };
      snapshotBaselineMs.value = Date.now();
    } else {
      // 控制后无 snapshot，回拉完整 state
      await fetchState({ silent: true });
    }
    return data.snapshot;
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '操作失败');
    return null;
  } finally {
    actionLoading.value = null;
  }
}

function togglePause(): void {
  const s = snapshot.value;
  if (!s) return;
  void performControl(s.paused ? 'resume' : 'pause');
}

function handleNext(): void {
  void performControl('next');
}

function handleJump(seg: RundownSegmentView): void {
  void performControl('goto', { segment_id: seg.id });
}

// ============================================================
// WS 订阅 + 防抖重拉
// ============================================================

let reloadTimer: ReturnType<typeof setTimeout> | null = null;
let tickTimer: ReturnType<typeof setInterval> | null = null;
let wsActive = false;

function onWsMessage(msg: WebSocketMessage): void {
  if (!wsActive) return;
  if (msg.type !== 'rundown.changed') return;
  // 300ms 防抖：避免事件风暴期间反复拉取
  if (reloadTimer) clearTimeout(reloadTimer);
  reloadTimer = setTimeout(() => {
    if (!wsActive) return;
    void fetchState({ silent: true });
  }, 300);
}

function startWs(): void {
  wsActive = true;
  wsClient.onMessage(onWsMessage);
  if (tickTimer) clearInterval(tickTimer);
  tickTimer = setInterval(() => {
    nowTickMs.value = Date.now();
  }, 1000);
}

function stopWs(): void {
  wsActive = false;
  if (reloadTimer) {
    clearTimeout(reloadTimer);
    reloadTimer = null;
  }
  if (tickTimer) {
    clearInterval(tickTimer);
    tickTimer = null;
  }
}

// ============================================================
// 生命周期
// ============================================================

onMounted(() => {
  startWs();
  void fetchState();
});

onBeforeUnmount(() => {
  stopWs();
});

// 状态切换时同步基线：snapshot 改变（如切换环节）时重置本地 tick
watch(
  () => currentSegment.value?.id,
  () => {
    snapshotBaselineMs.value = Date.now();
  },
);
</script>

<style scoped>
.agenda-workbench {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
  max-width: 1600px;
  margin: 0 auto;
}

/* ============================================================ */
/* 顶部                                                          */
/* ============================================================ */

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
  min-width: 0;
}

.page-title {
  font-size: 24px;
  font-weight: 700;
  margin: 0;
  color: var(--text-primary);
}

.page-subtitle {
  font-size: 13px;
  color: var(--text-secondary);
  margin: 0;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: var(--spacing-xs);
  flex-shrink: 0;
}

/* ============================================================ */
/* 通用：状态块 / 骨架 / 错误 / 不可用                             */
/* ============================================================ */

.state-block {
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
  padding: var(--spacing-lg);
}

.hint-line {
  margin: var(--spacing-xs) 0 var(--spacing-sm);
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.7;
}

.hint-line code {
  font-family: var(--font-mono);
  background: var(--bg-hover);
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  font-size: 11px;
  color: var(--text-regular);
}

/* ============================================================ */
/* 未加载态：窄卡                                                  */
/* ============================================================ */

.load-card {
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
  padding: var(--spacing-lg);
  max-width: 720px;
  margin: 0 auto;
  width: 100%;
}

.load-title {
  margin: 0 0 var(--spacing-xs);
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}

.load-desc {
  margin: 0 0 var(--spacing-md);
  font-size: 13px;
  color: var(--text-secondary);
  line-height: 1.6;
}

.load-row {
  display: flex;
  gap: var(--spacing-sm);
  align-items: center;
}

.load-input {
  flex: 1;
  min-width: 0;
}

.load-default-hint {
  margin: var(--spacing-sm) 0 0;
  font-size: 11px;
  color: var(--text-placeholder);
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  flex-wrap: wrap;
}

.load-default-hint code {
  font-family: var(--font-mono);
  background: var(--bg-hover);
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  color: var(--text-regular);
}

/* ============================================================ */
/* 总览 KPI 行（沿用 Tools.vue 的 total-card 风格）                */
/* ============================================================ */

.totals-row {
  display: grid;
  grid-template-columns: 1fr 1.4fr 1.6fr;
  gap: var(--spacing-md);
}

.total-card {
  background: var(--bg-card);
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-color-light);
  padding: var(--spacing-md);
  display: flex;
  flex-direction: column;
  gap: 4px;
  position: relative;
  overflow: hidden;
}

.total-card::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: var(--color-agenda);
}

.total-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.total-value {
  font-size: 20px;
  font-weight: 700;
  color: var(--text-primary);
  font-family: var(--font-mono);
  line-height: 1.3;
  margin-top: 2px;
}

.total-sub {
  font-size: 11px;
  color: var(--text-placeholder);
  margin-top: 2px;
}

/* ---- status 卡：tag 主导 ---- */
.total-status .status-value {
  display: flex;
  align-items: center;
  gap: var(--spacing-xs);
  flex-wrap: wrap;
}

.total-status .status-tag {
  font-size: 13px;
  font-weight: 600;
  padding: 0 12px;
  height: 26px;
  line-height: 24px;
}

.total-status .paused-tag,
.total-status .override-tag {
  font-size: 10px;
  font-weight: 600;
}

/* ---- title 卡 ---- */
.total-title .title-value {
  font-family: var(--font-family);
  font-size: 18px;
  font-weight: 700;
  color: var(--text-primary);
  word-break: break-word;
}

/* ---- progress 卡 ---- */
.total-progress .progress-bar {
  margin: 4px 0 6px;
}

.total-progress :deep(.el-progress-bar__outer) {
  background: var(--bg-hover);
}

.total-progress .progress-text {
  display: flex;
  align-items: baseline;
  gap: 4px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

.total-progress .progress-sep {
  color: var(--text-placeholder);
  font-weight: 400;
}

.total-progress .progress-percent {
  margin-left: auto;
  font-size: 12px;
  color: var(--color-agenda);
  font-weight: 700;
}

.flag-yes {
  color: var(--color-success);
  font-weight: 600;
}

.flag-no {
  color: var(--text-placeholder);
}

/* ============================================================ */
/* 当前环节大卡                                                  */
/* ============================================================ */

.current-card {
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-left: 3px solid var(--color-agenda);
  border-radius: var(--radius-lg);
  padding: var(--spacing-lg);
  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
  position: relative;
  overflow: hidden;
}

.current-card.is-paused {
  border-left-color: var(--color-warning);
  background: var(--color-warning-bg);
}

.current-head {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  flex-wrap: wrap;
}

.current-eyebrow {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 1.6px;
  text-transform: uppercase;
  color: var(--color-agenda);
  flex-shrink: 0;
}

.current-card.is-paused .current-eyebrow {
  color: var(--color-warning);
}

.current-title {
  margin: 0;
  font-size: 22px;
  font-weight: 700;
  color: var(--text-primary);
  word-break: break-word;
}

.expansion-tag {
  flex-shrink: 0;
}

.unload-btn {
  flex-shrink: 0;
}

.grow {
  flex: 1;
  min-width: 0;
}

.current-times {
  display: flex;
  align-items: baseline;
  gap: var(--spacing-md);
  flex-wrap: wrap;
}

.time-block {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.time-label {
  font-size: 10px;
  font-weight: 600;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.time-value {
  font-size: 32px;
  font-weight: 700;
  color: var(--text-primary);
  line-height: 1.1;
  font-variant-numeric: tabular-nums;
}

.time-value--sub {
  font-size: 18px;
  font-weight: 600;
  color: var(--text-secondary);
}

.time-block--total .time-value {
  color: var(--text-secondary);
}

.time-sep {
  font-size: 24px;
  color: var(--text-placeholder);
  font-weight: 300;
}

.current-actions {
  display: flex;
  gap: var(--spacing-sm);
  flex-wrap: wrap;
}

.next-line {
  display: flex;
  align-items: center;
  gap: var(--spacing-xs);
  padding-top: var(--spacing-sm);
  border-top: 1px dashed var(--border-color-light);
  font-size: 12px;
}

.next-eyebrow {
  color: var(--text-placeholder);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 1.2px;
  text-transform: uppercase;
}

.next-title {
  color: var(--text-regular);
  font-weight: 500;
}

.next-arrow {
  color: var(--color-agenda);
  font-weight: 700;
  margin-left: auto;
}

/* ============================================================ */
/* 区段通用（segments / history）                                */
/* ============================================================ */

.section-bar {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--spacing-sm);
  margin-bottom: var(--spacing-sm);
}

.section-title {
  margin: 0;
  font-size: 14px;
  font-weight: 700;
  letter-spacing: 0.04em;
  color: var(--text-primary);
  text-transform: uppercase;
}

.section-meta {
  font-size: 11px;
  color: var(--text-placeholder);
  font-family: var(--font-mono);
}

.segments-section,
.history-section {
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
  padding: var(--spacing-md);
}

/* ============================================================ */
/* 环节表格                                                      */
/* ============================================================ */

.segments-table {
  cursor: pointer;
}

.segments-table :deep(tr.is-current-row) {
  background: var(--color-agenda-bg) !important;
}

.segments-table :deep(tr.is-current-row td) {
  font-weight: 600;
}

.order-cell {
  color: var(--color-agenda);
  font-weight: 600;
}

.segment-label {
  font-weight: 500;
}

.source-cell {
  font-size: 11px;
  color: var(--text-secondary);
}

/* ============================================================ */
/* 推进历史时间线                                                  */
/* ============================================================ */

.history-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--spacing-md) 0;
}

.history-timeline {
  padding: var(--spacing-xs) 0;
}

.history-item {
  font-size: 12.5px;
}

.history-row {
  display: flex;
  align-items: baseline;
  gap: var(--spacing-xs);
  flex-wrap: wrap;
}

.history-event {
  font-size: 11px;
  font-weight: 700;
  color: var(--color-agenda);
  background: var(--color-agenda-bg);
  padding: 1px 8px;
  border-radius: var(--radius-sm);
  letter-spacing: 0.5px;
  flex-shrink: 0;
}

.history-segment {
  color: var(--text-primary);
  font-weight: 500;
  word-break: break-word;
}

.history-reason {
  color: var(--text-secondary);
  font-size: 11.5px;
}

/* ============================================================ */
/* 抽屉                                                          */
/* ============================================================ */

.drawer-body {
  padding: 0 var(--spacing-md) var(--spacing-md);
  display: flex;
  flex-direction: column;
}

.drawer-section {
  margin-bottom: var(--spacing-lg);
}

.drawer-h {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin: 0 0 var(--spacing-xs);
  padding-bottom: var(--spacing-xs);
  border-bottom: 1px solid var(--border-color-light);
}

.drawer-text {
  font-size: 13px;
  color: var(--text-regular);
  margin: 0;
  line-height: 1.7;
}

.drawer-muted {
  font-size: 12px;
  color: var(--text-placeholder);
  margin: 0;
  font-style: italic;
}

.key-points,
.talking-points {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.key-point,
.talking-point {
  display: flex;
  gap: 6px;
  font-size: 12.5px;
  color: var(--text-regular);
  line-height: 1.6;
}

.key-point-bullet {
  color: var(--color-agenda);
  font-weight: 700;
}

.talking-bullet {
  color: var(--color-agenda);
  flex-shrink: 0;
}

.meta-grid {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 4px var(--spacing-sm);
  margin: 0;
  font-size: 12px;
}

.meta-grid dt {
  color: var(--text-placeholder);
}

.meta-grid dd {
  margin: 0;
  color: var(--text-regular);
}

.expanded-content {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
  background: var(--color-agenda-bg);
  padding: var(--spacing-sm) var(--spacing-md);
  border-radius: var(--radius-md);
  border-left: 2px solid var(--color-agenda);
}

.expanded-block {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.expanded-label {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.8px;
  color: var(--color-agenda);
  text-transform: uppercase;
}

.expanded-text {
  font-size: 13px;
  color: var(--text-primary);
  margin: 0;
  line-height: 1.6;
  white-space: pre-wrap;
}

.drawer-footer {
  display: flex;
  justify-content: flex-end;
  padding-top: var(--spacing-sm);
  border-top: 1px solid var(--border-color-light);
  margin-top: auto;
}

/* ============================================================ */
/* 响应式                                                        */
/* ============================================================ */

@media (max-width: 1100px) {
  .totals-row {
    grid-template-columns: 1fr 1fr;
  }
  .total-progress {
    grid-column: 1 / -1;
  }
}

@media (max-width: 768px) {
  .totals-row {
    grid-template-columns: 1fr;
  }
  .current-title {
    font-size: 18px;
  }
  .time-value {
    font-size: 26px;
  }
  .current-times {
    gap: var(--spacing-sm);
  }
  .current-actions {
    width: 100%;
  }
  .current-actions :deep(.el-button) {
    flex: 1;
  }
  .segments-section,
  .history-section {
    overflow-x: auto;
  }
  .segments-table {
    min-width: 640px;
  }
  .page-header {
    flex-direction: column;
    align-items: stretch;
  }
}
</style>
