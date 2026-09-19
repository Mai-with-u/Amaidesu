<template>
  <div class="rundown-workbench">
    <!-- 顶部：标题 + 副标题 + 刷新 -->
    <header class="page-header">
      <div class="header-left">
        <h1 class="page-title">流程单工作台</h1>
        <p class="page-subtitle">流程单实时状态 · 手动控制 · 推进历史</p>
      </div>
      <div class="header-actions">
        <el-button :icon="Refresh" :loading="loadingState" @click="refresh"> 刷新 </el-button>
        <el-button type="primary" :icon="Files" :loading="libraryLoading" @click="openLibrary">
          流程单库
        </el-button>
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
          <div class="section-tools">
            <span class="section-meta">共 {{ state.segments.length }} 个环节</span>
            <el-button size="small" :icon="EditPen" @click="startEditCurrent">编辑流程单</el-button>
          </div>
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
      <!-- 总览 KPI 行 -->
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

      <!-- 当前环节大卡 -->
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

      <!-- 环节清单 -->
      <section class="segments-section">
        <header class="section-bar">
          <h3 class="section-title">环节清单</h3>
          <div class="section-tools">
            <span class="section-meta">共 {{ state?.segments.length ?? 0 }} 个环节</span>
            <el-button size="small" :icon="EditPen" @click="startEditCurrent">编辑流程单</el-button>
          </div>
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

      <!-- 推进历史 -->
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

    <!-- 流程单库：列表 / 新建 / 复制 / 删除 / 设为当前 -->
    <el-dialog
      v-model="libraryOpen"
      title="流程单库"
      width="760px"
      append-to-body
      class="library-dialog"
    >
      <el-table :data="libraryItems" stripe size="default" class="library-table">
        <el-table-column label="标题" min-width="150">
          <template #default="{ row }">
            <span class="library-title">{{ row.title }}</span>
            <el-tag
              v-if="row.rundown_id === currentRundownId"
              size="small"
              type="success"
              effect="plain"
              class="current-flag"
            >
              当前
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="ID" min-width="170">
          <template #default="{ row }">
            <span class="mono library-id">{{ row.rundown_id }}</span>
          </template>
        </el-table-column>
        <el-table-column label="环节数" width="80" align="center">
          <template #default="{ row }">{{ row.segments.length }}</template>
        </el-table-column>
        <el-table-column label="总时长" width="100">
          <template #default="{ row }">
            <span class="mono">{{ formatDuration(totalExpectedMs(row)) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="250" fixed="right" align="center">
          <template #default="{ row }">
            <el-button link type="primary" @click.stop="startEdit(row)">编辑</el-button>
            <el-button
              link
              type="success"
              :disabled="row.rundown_id === currentRundownId"
              @click.stop="activateRundown(row)"
            >
              设为当前
            </el-button>
            <el-button link @click.stop="duplicateRundown(row)">复制</el-button>
            <el-button link type="danger" @click.stop="removeRundown(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <template #footer>
        <div class="library-footer">
          <span class="library-hint">
            未选单时主播 Agent 使用内置默认流程单（初次直播·自我介绍）。
          </span>
          <div>
            <el-button :icon="Plus" @click="startCreate">新建流程单</el-button>
            <el-button @click="libraryOpen = false">关闭</el-button>
          </div>
        </div>
      </template>
    </el-dialog>

    <!-- 流程单编辑器：标题 + 环节卡片排序增删 -->
    <el-dialog
      v-model="editorOpen"
      :title="editorOriginalId ? '编辑流程单' : '新建流程单'"
      width="680px"
      append-to-body
      class="editor-dialog"
      :close-on-click-modal="false"
    >
      <el-form label-width="84px" class="editor-form">
        <el-form-item label="流程单 ID">
          <el-input v-model="editorForm.rundown_id" placeholder="唯一标识，配置引用此 id" />
        </el-form-item>
        <el-form-item label="标题">
          <el-input v-model="editorForm.title" placeholder="流程单标题" />
        </el-form-item>
      </el-form>

      <div class="seg-editor-bar">
        <span class="section-title">环节（{{ editorForm.segments.length }}）</span>
        <el-button size="small" :icon="Plus" @click="openSegmentDialog(-1)">添加环节</el-button>
      </div>
      <div v-if="editorForm.segments.length === 0" class="seg-editor-empty">
        <el-empty description="至少需要一个环节" :image-size="60" />
      </div>
      <div v-else class="seg-editor-list">
        <div
          v-for="(seg, idx) in editorForm.segments"
          :key="`${seg.id}-${idx}`"
          class="seg-editor-card"
        >
          <span class="seg-order mono">{{ idx + 1 }}</span>
          <div class="seg-info">
            <div class="seg-name">{{ seg.title }}</div>
            <div class="seg-meta">
              <span class="mono">{{ seg.id }}</span>
              <span class="mono">{{ formatDuration(seg.expected_ms) }}</span>
            </div>
          </div>
          <div class="seg-actions">
            <el-button link :disabled="idx === 0" @click="moveSegment(idx, -1)">上移</el-button>
            <el-button
              link
              :disabled="idx === editorForm.segments.length - 1"
              @click="moveSegment(idx, 1)"
            >
              下移
            </el-button>
            <el-button link type="primary" @click="openSegmentDialog(idx)">编辑</el-button>
            <el-button
              link
              type="danger"
              :disabled="editorForm.segments.length <= 1"
              @click="removeSegment(idx)"
            >
              删除
            </el-button>
          </div>
        </div>
      </div>

      <template #footer>
        <el-button @click="editorOpen = false">取消</el-button>
        <el-button type="primary" :loading="editorSaving" @click="saveEditor">保存</el-button>
      </template>
    </el-dialog>

    <!-- 环节编辑（二级）：新增与编辑共用 -->
    <el-dialog
      v-model="segDialogOpen"
      :title="segEditingIndex >= 0 ? '编辑环节' : '添加环节'"
      width="540px"
      append-to-body
      class="seg-dialog"
      :close-on-click-modal="false"
    >
      <el-form label-width="90px">
        <el-form-item label="环节 ID">
          <el-input v-model="segForm.id" placeholder="环节唯一标识，跳转定位用" />
        </el-form-item>
        <el-form-item label="环节名">
          <el-input v-model="segForm.title" placeholder="环节标题" />
        </el-form-item>
        <el-form-item label="任务说明">
          <el-input
            v-model="segForm.task_description"
            type="textarea"
            :rows="3"
            placeholder="给主播 Agent 的目标指引，允许自由发挥"
          />
        </el-form-item>
        <el-form-item label="关键要点">
          <el-input
            v-model="segForm.keyPointsText"
            type="textarea"
            :rows="3"
            placeholder="每行一条；可留空"
          />
        </el-form-item>
        <el-form-item label="预期时长">
          <el-input-number
            v-model="segForm.expectedMinutes"
            :min="0.1"
            :step="0.5"
            :precision="1"
            controls-position="right"
          />
          <span class="minutes-unit">分钟</span>
        </el-form-item>
        <el-form-item label="最短停留">
          <el-input-number
            v-model="segForm.minMinutes"
            :min="0.1"
            :step="0.5"
            :precision="1"
            controls-position="right"
            placeholder="可选"
          />
          <span class="minutes-unit">分钟（可选；防御 Agent 抢跑）</span>
        </el-form-item>
        <el-form-item label="备注">
          <el-input
            v-model="segForm.notes"
            type="textarea"
            :rows="2"
            placeholder="导演直录内容（如参考开场白），直接注入上下文；可留空"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="segDialogOpen = false">取消</el-button>
        <el-button type="primary" @click="saveSegmentDialog">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
/**
 * 流程单编排页 —— 流程单实时状态 + 手动控制
 *
 * 数据来源：
 * - REST 轮询：GET /api/v1/rundown/state（300ms 防抖 + WS 触发）
 * - WebSocket：rundown.changed（onMessage 过滤，触发重拉）
 * - 本地 1s setInterval：仅用于重算当前环节的 elapsed/remaining 倒计时显示
 *
 * 三态布局：
 * 1. 不可用（available=false）：主播 Agent 未启动
 * 2. 未加载（status=idle）：等待主播 Agent 启动 + 环节预览
 * 3. 运行中（status=running|paused|done）：KPI 行 + 当前环节卡 + 环节表 + 历史时间线
 */
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import { confirmAction } from '@/utils/confirmAction';
import {
  ArrowRightBold,
  EditPen,
  Files,
  Plus,
  Position,
  Refresh,
  VideoPause,
} from '@element-plus/icons-vue';
import { rundownApi } from '@/api';
import { wsClient } from '@/api/websocket';
import { useNowTick } from '@/composables/useNowTick';
import type {
  RundownControlAction,
  RundownControlResponse,
  RundownCurrentSegment,
  RundownDefinition,
  RundownSegmentView,
  RundownSnapshot,
  RundownStateResponse,
  RundownTransitionEntry,
  WebSocketMessage,
} from '@/types';

// 响应式状态

const state = ref<RundownStateResponse | null>(null);
const initialLoading = ref(true);
const loadingState = ref(false);
const loadError = ref<string | null>(null);
const actionLoading = ref<RundownControlAction | null>(null);

// 本地 1s tick：仅重算当前环节 elapsed/remaining 展示
const nowTickMs = useNowTick();

// 抽屉

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

// 派生状态

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
  return 'var(--color-rundown)';
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

// 段状态 / 来源 / 时间格式化

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

// 数据加载

async function fetchState(opts: { silent?: boolean } = {}): Promise<void> {
  if (!opts.silent) loadingState.value = true;
  loadError.value = null;
  try {
    const res = await rundownApi.getState();
    state.value = res.data;
    // 本地 tick 从该基准起算已播时长，吸收取数耗时造成的漂移
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

// 控制操作

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

// 流程单库与编辑器
// 编辑保存（upsert）写入存储；保存的是直播运行中的那份流程单时，
// 后端写穿运行态（进度按环节 id 对齐），本页经既有 rundown.changed
// 防抖重拉机制自动刷新，无需额外订阅。

const libraryOpen = ref(false);
const libraryLoading = ref(false);
const libraryItems = ref<RundownDefinition[]>([]);

/** 配置当前指向的流程单 id；空串 = 使用内置默认流程单 */
const currentRundownId = computed(() => state.value?.config.rundown_id ?? '');

function totalExpectedMs(def: RundownDefinition): number {
  return def.segments.reduce((sum, seg) => sum + (seg.expected_ms || 0), 0);
}

async function fetchLibrary(): Promise<void> {
  libraryLoading.value = true;
  try {
    const res = await rundownApi.listRundowns();
    if (!res.data.success) {
      ElMessage.error(res.data.message || '流程单库加载失败');
      return;
    }
    libraryItems.value = res.data.rundowns;
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '流程单库加载失败');
  } finally {
    libraryLoading.value = false;
  }
}

async function openLibrary(): Promise<void> {
  await fetchLibrary();
  libraryOpen.value = true;
}

// ---- 编辑器 ----

const editorOpen = ref(false);
const editorSaving = ref(false);
/** 打开时的 rundown_id；空串 = 新建 */
const editorOriginalId = ref('');
const editorForm = reactive<{ rundown_id: string; title: string; segments: RundownSegmentView[] }>({
  rundown_id: '',
  title: '',
  segments: [],
});

function fillEditorForm(def: RundownDefinition): void {
  editorForm.rundown_id = def.rundown_id;
  editorForm.title = def.title;
  editorForm.segments = JSON.parse(JSON.stringify(def.segments)) as RundownSegmentView[];
}

async function startCreate(): Promise<void> {
  try {
    const res = await rundownApi.getTemplate();
    if (!res.data.success || !res.data.definition) {
      ElMessage.error(res.data.message || '模板加载失败');
      return;
    }
    fillEditorForm(res.data.definition);
    editorForm.rundown_id = `rundown_${Date.now() % 100_000}`;
    editorOriginalId.value = '';
    editorOpen.value = true;
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '模板加载失败');
  }
}

/** 编辑当前配置指向的流程单；未选单或指向不存在时从运行快照预填新建 */
async function startEditCurrent(): Promise<void> {
  await fetchLibrary();
  const found = currentRundownId.value
    ? libraryItems.value.find(r => r.rundown_id === currentRundownId.value)
    : null;
  if (found) {
    startEdit(found);
    return;
  }
  if (state.value && state.value.segments.length > 0) {
    // 运行中的是内置默认流程单（虚拟存在不写库）：从快照预填，保存即落库
    fillEditorForm({
      rundown_id: `rundown_${Date.now() % 100_000}`,
      title: snapshot.value?.title || '未命名流程单',
      segments: state.value.segments,
    });
    editorOriginalId.value = '';
    editorOpen.value = true;
    ElMessage.info('当前使用的是内置默认流程单；保存后将作为新流程单入库');
    return;
  }
  ElMessage.warning('流程单内容尚未加载，请先启动主播 Agent 或从流程单库选择');
}

function startEdit(def: RundownDefinition): void {
  fillEditorForm(def);
  editorOriginalId.value = def.rundown_id;
  editorOpen.value = true;
}

async function saveEditor(): Promise<void> {
  if (!editorForm.rundown_id.trim()) {
    ElMessage.warning('请填写流程单 ID');
    return;
  }
  if (!editorForm.title.trim()) {
    ElMessage.warning('请填写流程单标题');
    return;
  }
  if (editorForm.segments.length === 0) {
    ElMessage.warning('至少需要一个环节');
    return;
  }
  editorSaving.value = true;
  try {
    const res = await rundownApi.upsert({
      rundown_id: editorForm.rundown_id.trim(),
      title: editorForm.title.trim(),
      segments: editorForm.segments,
    });
    if (!res.data.success) {
      ElMessage.error(res.data.message || '保存失败');
      return;
    }
    ElMessage.success(res.data.message || '已保存');
    editorOpen.value = false;
    // 写穿后 rundown.changed 会触发防抖重拉；这里主动刷新保证非运行态也即时
    void fetchState({ silent: true });
    if (libraryOpen.value) await fetchLibrary();
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '保存失败');
  } finally {
    editorSaving.value = false;
  }
}

// ---- 环节卡片操作 ----

function moveSegment(idx: number, dir: -1 | 1): void {
  const target = idx + dir;
  if (target < 0 || target >= editorForm.segments.length) return;
  const segs = editorForm.segments;
  [segs[idx], segs[target]] = [segs[target], segs[idx]];
}

function removeSegment(idx: number): void {
  if (editorForm.segments.length <= 1) return;
  editorForm.segments.splice(idx, 1);
}

// ---- 环节二级编辑 ----

const segDialogOpen = ref(false);
const segEditingIndex = ref(-1);
const segForm = reactive({
  id: '',
  title: '',
  task_description: '',
  keyPointsText: '',
  expectedMinutes: 5,
  minMinutes: null as number | null,
  notes: '',
});

const MINUTES_MS = 60_000;

function openSegmentDialog(idx: number): void {
  segEditingIndex.value = idx;
  if (idx >= 0) {
    const seg = editorForm.segments[idx];
    segForm.id = seg.id;
    segForm.title = seg.title;
    segForm.task_description = seg.task_description;
    segForm.keyPointsText = seg.key_points.join('\n');
    segForm.expectedMinutes = Math.round((seg.expected_ms / MINUTES_MS) * 10) / 10;
    segForm.minMinutes =
      seg.min_duration_ms != null ? Math.round((seg.min_duration_ms / MINUTES_MS) * 10) / 10 : null;
    segForm.notes = seg.notes ?? '';
  } else {
    segForm.id = `segment_${editorForm.segments.length + 1}`;
    segForm.title = '';
    segForm.task_description = '';
    segForm.keyPointsText = '';
    segForm.expectedMinutes = 5;
    segForm.minMinutes = null;
    segForm.notes = '';
  }
  segDialogOpen.value = true;
}

function saveSegmentDialog(): void {
  if (!segForm.id.trim()) {
    ElMessage.warning('请填写环节 ID');
    return;
  }
  if (!segForm.title.trim()) {
    ElMessage.warning('请填写环节名');
    return;
  }
  const duplicate = editorForm.segments.some(
    (seg, i) => seg.id === segForm.id.trim() && i !== segEditingIndex.value,
  );
  if (duplicate) {
    ElMessage.warning(`环节 ID "${segForm.id.trim()}" 已存在`);
    return;
  }
  if (
    segForm.minMinutes != null &&
    segForm.expectedMinutes != null &&
    segForm.minMinutes > segForm.expectedMinutes
  ) {
    ElMessage.warning('最短停留不能大于预期时长');
    return;
  }

  const segment: RundownSegmentView = {
    id: segForm.id.trim(),
    title: segForm.title.trim(),
    task_description: segForm.task_description.trim(),
    key_points: segForm.keyPointsText
      .split('\n')
      .map(line => line.trim())
      .filter(Boolean),
    expected_ms: Math.max(1000, Math.round((segForm.expectedMinutes ?? 0.1) * MINUTES_MS)),
    min_duration_ms:
      segForm.minMinutes != null
        ? Math.max(1000, Math.round(segForm.minMinutes * MINUTES_MS))
        : null,
    notes: segForm.notes.trim() || null,
  };
  if (segEditingIndex.value >= 0) {
    editorForm.segments[segEditingIndex.value] = segment;
  } else {
    editorForm.segments.push(segment);
  }
  segDialogOpen.value = false;
}

// ---- 库操作：删除 / 复制 / 设为当前 ----

async function removeRundown(def: RundownDefinition): Promise<void> {
  const referenced = def.rundown_id === currentRundownId.value;
  const ok = await confirmAction(
    referenced
      ? `确定删除「${def.title}」？当前配置仍指向它，重启主播 Agent 后将回退内置默认流程单。`
      : `确定删除「${def.title}」？`,
    '删除流程单',
    { confirmButtonText: '删除' },
  );
  if (!ok) return;
  try {
    const res = await rundownApi.remove(def.rundown_id);
    if (!res.data.success) {
      ElMessage.error(res.data.message || '删除失败');
      return;
    }
    ElMessage.success(res.data.message || '已删除');
    await fetchLibrary();
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '删除失败');
  }
}

async function duplicateRundown(def: RundownDefinition): Promise<void> {
  try {
    const res = await rundownApi.duplicate(def.rundown_id);
    if (!res.data.success) {
      ElMessage.error(res.data.message || '复制失败');
      return;
    }
    ElMessage.success(`已复制为 ${res.data.rundown_id}`);
    await fetchLibrary();
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '复制失败');
  }
}

async function activateRundown(def: RundownDefinition): Promise<void> {
  try {
    const res = await rundownApi.activate(def.rundown_id);
    if (!res.data.success) {
      ElMessage.error(res.data.message || '设置失败');
      return;
    }
    ElMessage.success(res.data.message || '已设为当前');
    await fetchLibrary();
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '设置失败');
  }
}

// WS 订阅 + 防抖重拉

let reloadTimer: ReturnType<typeof setTimeout> | null = null;
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
}

function stopWs(): void {
  wsActive = false;
  if (reloadTimer) {
    clearTimeout(reloadTimer);
    reloadTimer = null;
  }
}

// 生命周期

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
.rundown-workbench {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
  max-width: 1600px;
  margin: 0 auto;
}

/* 顶部                                                          */

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

/* 通用：状态块 / 骨架 / 错误 / 不可用                             */

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

/* 未加载态：窄卡                                                  */

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
/* 总览 KPI 行（沿用 Tools.vue 的 total-card 风格）                */

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
  background: var(--color-rundown);
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
.total-progress .progress-percent {
  margin-left: auto;
  font-size: 12px;
  color: var(--color-rundown);
  font-weight: 700;
}
/* 当前环节大卡                                                  */

.current-card {
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-left: 3px solid var(--color-rundown);
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
  color: var(--color-rundown);
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
  color: var(--color-rundown);
  font-weight: 700;
  margin-left: auto;
}

/* 区段通用（segments / history）                                */

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

/* 环节表格                                                      */

.segments-table {
  cursor: pointer;
}

.segments-table :deep(tr.is-current-row) {
  background: var(--color-rundown-bg) !important;
}

.segments-table :deep(tr.is-current-row td) {
  font-weight: 600;
}

.order-cell {
  color: var(--color-rundown);
  font-weight: 600;
}

.segment-label {
  font-weight: 500;
}
/* 推进历史时间线                                                  */

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
  color: var(--color-rundown);
  background: var(--color-rundown-bg);
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

/* 抽屉                                                          */

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
.key-point,
.key-point-bullet {
  color: var(--color-rundown);
  font-weight: 700;
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
.drawer-footer {
  display: flex;
  justify-content: flex-end;
  padding-top: var(--spacing-sm);
  border-top: 1px solid var(--border-color-light);
  margin-top: auto;
}

/* 流程单库与编辑器                                                */

.section-tools {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
}

.library-table {
  cursor: default;
}

.library-title {
  font-weight: 500;
  margin-right: 6px;
}

.current-flag {
  margin-left: 4px;
}

.library-id {
  font-size: 12px;
  color: var(--text-secondary);
}

.library-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--spacing-md);
}

.library-hint {
  font-size: 12px;
  color: var(--text-placeholder);
}

.seg-editor-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: var(--spacing-sm) 0;
}

.seg-editor-empty {
  padding: var(--spacing-xs) 0;
}

.seg-editor-list {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-xs);
  max-height: 360px;
  overflow-y: auto;
}

.seg-editor-card {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  background: var(--bg-hover);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-md);
  padding: var(--spacing-xs) var(--spacing-sm);
}

.seg-order {
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: var(--color-rundown);
  color: #fff;
  font-size: 12px;
  font-weight: 700;
  flex-shrink: 0;
}

.seg-info {
  flex: 1;
  min-width: 0;
}

.seg-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  word-break: break-word;
}

.seg-meta {
  display: flex;
  gap: var(--spacing-sm);
  font-size: 11px;
  color: var(--text-placeholder);
}

.seg-actions {
  display: flex;
  flex-shrink: 0;
}

.minutes-unit {
  margin-left: var(--spacing-sm);
  font-size: 12px;
  color: var(--text-secondary);
}

/* 响应式                                                        */

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
