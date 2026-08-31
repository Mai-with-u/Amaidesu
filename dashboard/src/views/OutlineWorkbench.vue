<template>
  <div class="agenda-workbench">
    <!-- 顶部：标题 + 副标题 + 刷新 -->
    <header class="page-header">
      <div class="header-left">
        <h1 class="page-title">Agenda 工作台</h1>
        <p class="page-subtitle">节目单实时状态 · 手动控制 · 推进历史</p>
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

    <!-- 不可用态：后端 agenda 未启用 -->
    <template v-else-if="state && !state.available">
      <el-alert
        :title="state.message ?? '节目单管理通道未启用'"
        type="warning"
        :closable="false"
        show-icon
        class="state-block"
      >
        <p class="hint-line">
          请到「设置」页开启 <code>agents.streamer.agenda_enabled</code> 后重启 Amaidesu 生效。
        </p>
        <el-button size="small" type="primary" @click="refresh">重试</el-button>
      </el-alert>
    </template>

    <!-- 未加载态：status ∈ {inactive, unloaded} -->
    <template v-else-if="isNotLoaded">
      <section class="load-card">
        <h3 class="load-title">加载节目单</h3>
        <p class="load-desc">从指定路径加载节目单文件并立即启动播出。</p>
        <div class="load-row">
          <el-input
            v-model="agendaPathInput"
            placeholder="节目单文件路径（如 data/agendas/demo.yaml）"
            clearable
            class="load-input"
            @keyup.enter="handleStart"
          />
          <el-button
            type="primary"
            :icon="VideoPlay"
            :loading="actionLoading === 'start'"
            @click="handleStart"
          >
            加载并开始
          </el-button>
        </div>
        <p v-if="state?.config.agenda_path" class="load-default-hint">
          当前配置默认路径：<code>{{ state.config.agenda_path }}</code>
          <el-link
            v-if="state.config.agenda_path !== agendaPathInput"
            type="primary"
            @click="agendaPathInput = state.config.agenda_path"
          >
            使用此路径
          </el-link>
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
          <el-table-column label="时长" width="100">
            <template #default="{ row }">
              <span class="mono">{{ formatDuration(row.duration_ms) }}</span>
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

    <!-- 运行态：status ∈ {loading, running, completed} -->
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
              v-if="snapshot.is_paused"
              type="warning"
              effect="plain"
              size="small"
              class="paused-tag"
            >
              已暂停
            </el-tag>
            <el-tag
              v-if="snapshot.manually_overridden"
              type="warning"
              effect="plain"
              size="small"
              class="override-tag"
            >
              手动模式
            </el-tag>
          </div>
          <div class="total-sub">
            节目单 ID：<span class="mono">{{ snapshot.agenda_id ?? '—' }}</span>
          </div>
        </article>

        <article class="total-card total-title">
          <div class="total-label">节目单标题</div>
          <div class="total-value title-value">{{ snapshot.agenda_title ?? '—' }}</div>
          <div class="total-sub">
            进度 {{ snapshot.completed_count }} / {{ snapshot.total_count || '?' }} 个环节
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
            <span>{{ formatDuration(elapsedLiveMs) }}</span>
            <span class="progress-sep">/</span>
            <span>{{ formatDuration(snapshot.total_planned_ms) }}</span>
            <span class="progress-percent mono">{{ progressPercent.toFixed(1) }}%</span>
          </div>
          <div class="total-sub">
            自动启动：<span :class="state?.config.agenda_auto_start ? 'flag-yes' : 'flag-no'">
              {{ state?.config.agenda_auto_start ? '已开启' : '已关闭' }}
            </span>
          </div>
        </article>
      </section>

      <!-- 2. 当前环节大卡 -->
      <section
        v-if="snapshot.current_segment"
        class="current-card"
        :class="{ 'is-paused': snapshot.is_paused }"
      >
        <div class="current-head">
          <span class="current-eyebrow">当前环节</span>
          <h2 class="current-title" :title="snapshot.current_segment.title">
            {{ snapshot.current_segment.title }}
          </h2>
          <el-tag
            v-if="snapshot.current_segment.needs_expansion && !snapshot.current_segment.expanded"
            type="warning"
            effect="plain"
            size="small"
            class="expansion-tag"
          >
            扩展内容生成中
          </el-tag>
          <span class="grow" />
          <el-button
            v-if="canUnload"
            size="small"
            plain
            :icon="Delete"
            :loading="actionLoading === 'unload'"
            class="unload-btn"
            @click="confirmUnload"
          >
            卸载节目单
          </el-button>
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
              formatDuration(snapshot.current_segment.duration_ms)
            }}</span>
          </div>
        </div>

        <div class="current-actions">
          <el-button
            :type="snapshot.is_paused ? 'primary' : 'default'"
            :icon="snapshot.is_paused ? VideoPlay : VideoPause"
            :loading="actionLoading === (snapshot.is_paused ? 'resume' : 'pause')"
            @click="togglePause"
          >
            {{ snapshot.is_paused ? '继续' : '暂停' }}
          </el-button>
          <el-button :icon="ArrowRightBold" :loading="actionLoading === 'skip'" @click="handleSkip">
            跳过本环节
          </el-button>
          <el-button
            :icon="RefreshLeft"
            :loading="actionLoading === 'rewind'"
            plain
            @click="handleRewind"
          >
            回到上一环节
          </el-button>
        </div>

        <div v-if="snapshot.next_segment" class="next-line">
          <span class="next-eyebrow">下一环节</span>
          <span class="next-title">{{ snapshot.next_segment.title }}</span>
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
          <el-table-column label="时长" width="100">
            <template #default="{ row }">
              <span class="mono">{{ formatDuration(row.duration_ms) }}</span>
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
          <el-table-column v-if="showInsertedBy" label="来源" width="80" align="center">
            <template #default="{ row }">
              <span class="mono source-cell">{{
                row.inserted_by === 'human' ? '人工' : 'AI'
              }}</span>
            </template>
          </el-table-column>
          <el-table-column v-if="showStartsAt" label="计划开始" width="120" align="center">
            <template #default="{ row }">
              <span class="mono">{{ formatStartsAt(row.starts_at_ms) }}</span>
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
            :key="`${entry.timestamp_ms}-${idx}`"
            :timestamp="formatTime(entry.timestamp_ms)"
            :type="historyDotType(entry)"
            placement="top"
            class="history-item"
          >
            <div class="history-row">
              <span class="history-event mono">{{ entry.event }}</span>
              <span class="history-segment">{{ segmentTitleOf(entry.segment_id) }}</span>
              <span v-if="entry.reason" class="history-reason">· {{ entry.reason }}</span>
            </div>
          </el-timeline-item>
        </el-timeline>
      </section>
    </template>

    <!-- 兜底：available=true 但 snapshot 缺失（如已 unloaded 中间态） -->
    <template v-else>
      <el-alert
        title="节目单快照不可用"
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
            <dt>时长</dt>
            <dd class="mono">{{ formatDuration(activeSegment.duration_ms) }}</dd>
            <template v-if="activeSegment.min_duration_ms != null">
              <dt>最小时长</dt>
              <dd class="mono">{{ formatDuration(activeSegment.min_duration_ms) }}</dd>
            </template>
            <dt>分支数</dt>
            <dd class="mono">{{ activeSegment.branch_count }}</dd>
            <template v-if="activeSegment.inserted_by">
              <dt>来源</dt>
              <dd>{{ activeSegment.inserted_by === 'human' ? '人工编排' : 'AI 插入' }}</dd>
            </template>
          </dl>
        </section>

        <section class="drawer-section">
          <h4 class="drawer-h">扩展内容</h4>
          <div v-if="activeExpanded" class="expanded-content">
            <div class="expanded-block">
              <span class="expanded-label">开场白</span>
              <p class="expanded-text">{{ activeExpanded.opening_line }}</p>
            </div>
            <div class="expanded-block">
              <span class="expanded-label">话题引导</span>
              <p class="expanded-text">{{ activeExpanded.topic_guidance }}</p>
            </div>
            <div class="expanded-block">
              <span class="expanded-label">讨论要点</span>
              <ul v-if="activeExpanded.talking_points.length > 0" class="talking-points">
                <li v-for="(p, i) in activeExpanded.talking_points" :key="i" class="talking-point">
                  <span class="talking-bullet" aria-hidden="true">▸</span>
                  <span>{{ p }}</span>
                </li>
              </ul>
              <p v-else class="drawer-muted">未提供讨论要点</p>
            </div>
          </div>
          <p v-else class="drawer-muted">尚未生成</p>
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
                :loading="actionLoading === 'jump'"
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
 * Agenda 工作台 —— 节目单实时状态 + 手动控制
 *
 * 数据来源：
 * - REST 轮询：GET /api/v1/agenda/state（300ms 防抖 + WS 触发）
 * - WebSocket：agenda.update / planner.checkpoint（onMessage 过滤，触发重拉）
 * - 本地 1s setInterval：仅用于重算当前环节的 elapsed/remaining 倒计时显示
 *
 * 三态布局：
 * 1. 不可用（available=false）：alert 引导去 Settings 开启
 * 2. 未加载（status=inactive|unloaded）：窄卡输入路径 + start + 环节预览
 * 3. 运行中（status=loading|running|completed）：KPI 行 + 当前环节卡 + 环节表 + 历史时间线
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import {
  ArrowRightBold,
  Delete,
  Position,
  Refresh,
  RefreshLeft,
  VideoPause,
  VideoPlay,
} from '@element-plus/icons-vue';
import { agendaApi } from '@/api';
import { wsClient } from '@/api/websocket';
import type {
  AgendaControlAction,
  AgendaControlResponse,
  AgendaCurrentSegmentView,
  AgendaExpandedContent,
  AgendaSegmentView,
  AgendaSnapshot,
  AgendaStateResponse,
  AgendaTransitionEntry,
  WebSocketMessage,
} from '@/types';

// ============================================================
// 响应式状态
// ============================================================

const state = ref<AgendaStateResponse | null>(null);
const initialLoading = ref(true);
const loadingState = ref(false);
const loadError = ref<string | null>(null);
const actionLoading = ref<AgendaControlAction | null>(null);
const agendaPathInput = ref('');

// 本地 1s tick：仅重算当前环节 elapsed/remaining 展示
const nowTickMs = ref(Date.now());

// ============================================================
// 抽屉
// ============================================================

const drawerOpen = ref(false);
const activeSegment = ref<AgendaSegmentView | null>(null);

const drawerTitle = computed(() =>
  activeSegment.value ? `环节详情 · ${activeSegment.value.title}` : '环节详情',
);

const activeExpanded = computed<AgendaExpandedContent | null>(() => {
  const seg = activeSegment.value;
  if (!seg || !state.value) return null;
  return state.value.expanded[seg.id] ?? null;
});

const canJumpFromDrawer = computed(() => {
  // 仅运行中可跳转（loading/running），completed/unloaded 不可
  const s = snapshot.value?.status;
  return s === 'loading' || s === 'running';
});

function openDrawer(row: AgendaSegmentView) {
  activeSegment.value = row;
  drawerOpen.value = true;
}

// ============================================================
// 派生状态
// ============================================================

const snapshot = computed<AgendaSnapshot | null>(() => state.value?.snapshot ?? null);

const isNotLoaded = computed(() => {
  const s = snapshot.value?.status;
  return s === 'inactive' || s === 'unloaded';
});

const canUnload = computed(() => {
  const s = snapshot.value?.status;
  return s === 'running' || s === 'completed';
});

const showInsertedBy = computed(() =>
  (state.value?.segments ?? []).some(seg => seg.inserted_by !== undefined),
);

const showStartsAt = computed(() =>
  (state.value?.segments ?? []).some(seg => seg.starts_at_ms != null && seg.starts_at_ms > 0),
);

const statusLabel = computed(() => {
  const s = snapshot.value;
  if (!s) return '—';
  if (s.is_paused && s.status === 'running') return '已暂停';
  switch (s.status) {
    case 'inactive':
      return '未激活';
    case 'loading':
      return '加载中';
    case 'running':
      return '进行中';
    case 'completed':
      return '已完成';
    case 'unloaded':
      return '已卸载';
    default:
      return s.status;
  }
});

const statusTagType = computed<'success' | 'warning' | 'info' | 'primary' | 'danger'>(() => {
  const s = snapshot.value;
  if (!s) return 'info';
  if (s.is_paused && s.status === 'running') return 'warning';
  switch (s.status) {
    case 'running':
      return 'success';
    case 'loading':
      return 'primary';
    case 'completed':
      return 'info';
    case 'unloaded':
      return 'info';
    case 'inactive':
    default:
      return 'info';
  }
});

const progressPercent = computed(() => {
  const p = snapshot.value?.progress_percent;
  if (p == null || Number.isNaN(p)) return 0;
  return Math.max(0, Math.min(100, p));
});

const progressColor = computed(() => {
  if (snapshot.value?.manually_overridden) return 'var(--color-warning)';
  if (snapshot.value?.status === 'completed') return 'var(--color-info)';
  return 'var(--color-agenda)';
});

const elapsedLiveMs = computed(() => snapshot.value?.elapsed_live_ms ?? 0);

const currentSegment = computed<AgendaCurrentSegmentView | null>(
  () => snapshot.value?.current_segment ?? null,
);

const tickElapsedMs = computed(() => {
  const seg = currentSegment.value;
  if (!seg) return 0;
  // 后端 elapsed_ms 是快照时刻的累计；is_paused 时不递增
  if (snapshot.value?.is_paused) return Math.max(0, seg.elapsed_ms);
  const drift = nowTickMs.value - snapshotBaselineMs.value;
  return Math.max(0, Math.min(seg.duration_ms, seg.elapsed_ms + drift));
});

const tickRemainingMs = computed(() => {
  const seg = currentSegment.value;
  if (!seg) return 0;
  return Math.max(0, seg.duration_ms - tickElapsedMs.value);
});

/** 快照基线时刻（用于本地 tick 漂移计算） */
const snapshotBaselineMs = ref(Date.now());

/** 推进历史：仅展示最近 20 条，按时间倒序 */
const historyEntries = computed<AgendaTransitionEntry[]>(() => {
  const list = state.value?.transitions ?? [];
  return [...list].sort((a, b) => b.timestamp_ms - a.timestamp_ms).slice(0, 20);
});

// ============================================================
// 段状态 / 来源 / 时间格式化
// ============================================================

function segmentStatusOf(seg: AgendaSegmentView): 'done' | 'current' | 'pending' {
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

function segmentStatusLabel(seg: AgendaSegmentView): string {
  const s = segmentStatusOf(seg);
  if (s === 'done') return '已完成';
  if (s === 'current') return '进行中';
  return '待开始';
}

function segmentStatusTagType(seg: AgendaSegmentView): 'success' | 'warning' | 'info' {
  const s = segmentStatusOf(seg);
  if (s === 'done') return 'success';
  if (s === 'current') return 'warning';
  return 'info';
}

function segmentStatusTagEffect(seg: AgendaSegmentView): 'plain' | 'dark' {
  return segmentStatusOf(seg) === 'current' ? 'dark' : 'plain';
}

function segmentTitleOf(id: string): string {
  const seg = (state.value?.segments ?? []).find(s => s.id === id);
  return seg?.title ?? id;
}

function rowClassName({ row }: { row: AgendaSegmentView }): string {
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

function formatStartsAt(tsMs: number | null | undefined): string {
  if (tsMs == null || tsMs <= 0) return '—';
  const d = new Date(tsMs);
  return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false });
}

function historyDotType(
  entry: AgendaTransitionEntry,
): 'primary' | 'success' | 'warning' | 'danger' | 'info' {
  const ev = entry.event.toLowerCase();
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
    const res = await agendaApi.getState();
    state.value = res.data;
    // 初始化输入路径（首次加载时）
    if (!agendaPathInput.value && res.data.config.agenda_path) {
      agendaPathInput.value = res.data.config.agenda_path;
    }
    // 记录本次拉取的基线时刻，用于本地 tick 漂移
    snapshotBaselineMs.value = Date.now();
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : '无法加载节目单状态';
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
  action: AgendaControlAction,
  extra: { segment_id?: string; path?: string } = {},
): Promise<AgendaControlResponse['snapshot'] | null> {
  if (actionLoading.value) return null;
  actionLoading.value = action;
  try {
    const res = await agendaApi.control({ action, ...extra });
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

function handleStart(): void {
  const path = agendaPathInput.value.trim();
  if (!path) {
    ElMessage.warning('请填写节目单文件路径');
    return;
  }
  void performControl('start', { path });
}

function togglePause(): void {
  const s = snapshot.value;
  if (!s) return;
  void performControl(s.is_paused ? 'resume' : 'pause');
}

function handleSkip(): void {
  void performControl('skip');
}

function handleRewind(): void {
  void performControl('rewind');
}

function confirmUnload(): void {
  void performControl('unload');
}

function handleJump(seg: AgendaSegmentView): void {
  void performControl('jump', { segment_id: seg.id });
}

// ============================================================
// WS 订阅 + 防抖重拉
// ============================================================

let reloadTimer: ReturnType<typeof setTimeout> | null = null;
let tickTimer: ReturnType<typeof setInterval> | null = null;
let wsActive = false;

function onWsMessage(msg: WebSocketMessage): void {
  if (!wsActive) return;
  if (msg.type !== 'agenda.update' && msg.type !== 'planner.checkpoint') return;
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
