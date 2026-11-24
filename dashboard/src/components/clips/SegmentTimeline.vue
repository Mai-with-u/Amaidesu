<script setup lang="ts">
/**
 * 切片段时间轴：上方全场概览条 + 下方主轨（可放大到选中段邻域）。
 *
 * 交互（对齐视频编辑器惯例，不需要文字说明也能猜对）：
 * - 主轨单击空白 → 播放器跳到该时刻（抛出 seek）
 * - 主轨空白按下拖动 → 框选区间，松手新建一段（抛出 create；拖动不足 5 秒视为误触，按单击跳转处理）
 * - 主轨单击色块 → 选中（抛出 select；父组件同时联动预览）
 * - 拖动色块左右边缘 → 调整起止（pointer 事件，pointerup 才提交）
 * - 选中段时主轨自动放大到该段邻域（秒级精度拖边）；概览条点击移动放大窗口、
 *   「显示全场」退出放大；概览条未放大时点击 = 跳转播放
 *
 * 密度轨：主轨底部一层聊天密度直方图（弹幕/台词活跃度的视觉线索，帮助找
 * "值得切的时刻"与定位切点），数据由父组件取自密度端点。
 *
 * 播放头（playheadMs）实时标记播放位置，只画在当前视图范围内。
 *
 * 色块颜色按状态：suggested=AI 建议色 / confirmed=确认色 / done=完成色 /
 * failed=失败色 / cutting=进行色 / dismissed=置灰。
 */
import { computed, ref, watch } from 'vue';
import { formatRelClock } from '@/utils/clipsTime';
import type { ClipSegment } from '@/types/clips';

const props = defineProps<{
  segments: ClipSegment[];
  /** 场次起点（墙钟毫秒） */
  sessionStartMs: number;
  /** 场次终点（墙钟毫秒；进行中场次用最后活动时刻兜底） */
  sessionEndMs: number;
  selectedId: number | null;
  /** 当前播放位置（墙钟毫秒）；无预览时为 null，不渲染播放头 */
  playheadMs: number | null;
  /** 聊天密度（密度端点响应）；null = 尚未取到，不渲染密度轨 */
  density: { bucketMs: number; startedAtMs: number; counts: number[] } | null;
}>();

const emit = defineEmits<{
  (e: 'select', segmentId: number): void;
  (e: 'resize', segmentId: number, startMs: number, endMs: number): void;
  (e: 'create', startMs: number, endMs: number): void;
  (e: 'seek', startMs: number): void;
}>();

/** 按下后移动超过该距离才进入框选，否则松手按单击跳转处理 */
const DRAG_THRESHOLD_PX = 4;
/** 框选建段的最短时长；更短的拖动视为误触单击 */
const MIN_CREATE_SPAN_MS = 5 * 1000;
/** 选中段放大时两侧各留的最小上下文（毫秒） */
const ZOOM_CONTEXT_MS = 60 * 1000;
/** 放大窗口最短跨度（段比这还短时兜底，保证窗口里有可操作空间） */
const MIN_ZOOM_SPAN_MS = 2 * 60 * 1000;

const trackEl = ref<HTMLElement | null>(null);

// ---- 视图窗口：null = 全场；选中段时自动放大到段邻域 ----
const view = ref<{ startMs: number; endMs: number } | null>(null);

const viewStartMs = computed(() => view.value?.startMs ?? props.sessionStartMs);
const viewEndMs = computed(() => view.value?.endMs ?? props.sessionEndMs);
const viewDurationMs = computed(() => Math.max(1, viewEndMs.value - viewStartMs.value));

const sessionDurationMs = computed(() => Math.max(1, props.sessionEndMs - props.sessionStartMs));

watch(
  () => props.selectedId,
  id => {
    if (id === null) return;
    const segment = props.segments.find(s => s.id === id);
    if (!segment) return;
    const span = Math.max(segment.end_ms - segment.start_ms, MIN_ZOOM_SPAN_MS / 2);
    const context = Math.max(ZOOM_CONTEXT_MS, Math.floor(span * 0.5));
    const windowSpan = Math.max(span + context * 2, MIN_ZOOM_SPAN_MS);
    const startMs = Math.max(
      props.sessionStartMs,
      Math.min(props.sessionEndMs - windowSpan, segment.start_ms - context),
    );
    view.value = {
      startMs: Math.round(startMs),
      endMs: Math.round(Math.min(props.sessionEndMs, startMs + windowSpan)),
    };
  },
);

function zoomOut(): void {
  view.value = null;
}

function pctOf(wallMs: number, base: number, duration: number): number {
  return ((wallMs - base) / duration) * 100;
}

interface Block {
  segment: ClipSegment;
  leftPct: number;
  widthPct: number;
  statusClass: string;
  narrow: boolean;
}

/** 文字塞得下的最小宽度（百分比）；更窄的块隐藏文字，靠 tooltip 与列表看名字 */
const NARROW_PCT = 6;

const blocks = computed<Block[]>(() =>
  props.segments
    .filter(s => s.status !== 'dismissed')
    .map(segment => {
      const startRel = Math.max(0, segment.start_ms - viewStartMs.value);
      const endRel = Math.min(viewDurationMs.value, segment.end_ms - viewStartMs.value);
      const widthPct = (Math.max(0, endRel - startRel) / viewDurationMs.value) * 100;
      return {
        segment,
        leftPct: (startRel / viewDurationMs.value) * 100,
        widthPct,
        statusClass: `is-${segment.status}`,
        narrow: widthPct < NARROW_PCT,
      };
    }),
);

/** 概览条里的迷你色块（全场坐标） */
const overviewBlocks = computed(() =>
  props.segments
    .filter(s => s.status !== 'dismissed')
    .map(segment => {
      const startRel = Math.max(0, segment.start_ms - props.sessionStartMs);
      const endRel = Math.min(sessionDurationMs.value, segment.end_ms - props.sessionStartMs);
      return {
        segment,
        leftPct: (startRel / sessionDurationMs.value) * 100,
        widthPct: (Math.max(0, endRel - startRel) / sessionDurationMs.value) * 100,
        statusClass: `is-${segment.status}`,
      };
    }),
);

/** 概览条上的放大窗口矩形（全场坐标） */
const overviewWindow = computed(() => {
  if (!view.value) return null;
  return {
    leftPct: pctOf(view.value.startMs, props.sessionStartMs, sessionDurationMs.value),
    widthPct: ((view.value.endMs - view.value.startMs) / sessionDurationMs.value) * 100,
  };
});

/** 密度直方图（主轨坐标系；只渲染视图内非零桶） */
const densityBars = computed(() => {
  const density = props.density;
  if (!density) return [];
  const maxCount = Math.max(1, ...density.counts);
  const bars: Array<{ leftPct: number; widthPct: number; heightPct: number; count: number }> = [];
  for (let i = 0; i < density.counts.length; i++) {
    const count = density.counts[i];
    if (count === 0) continue;
    const startMs = density.startedAtMs + i * density.bucketMs;
    const endMs = startMs + density.bucketMs;
    if (endMs <= viewStartMs.value || startMs >= viewEndMs.value) continue;
    const clippedStart = Math.max(startMs, viewStartMs.value);
    const clippedEnd = Math.min(endMs, viewEndMs.value);
    bars.push({
      leftPct: pctOf(clippedStart, viewStartMs.value, viewDurationMs.value),
      widthPct: ((clippedEnd - clippedStart) / viewDurationMs.value) * 100,
      heightPct: (count / maxCount) * 100,
      count,
    });
  }
  return bars;
});

/** 播放头位置（当前视图坐标；视图外不渲染） */
const playhead = computed(() => {
  if (props.playheadMs === null) return null;
  if (props.playheadMs < viewStartMs.value || props.playheadMs > viewEndMs.value) return null;
  return pctOf(props.playheadMs, viewStartMs.value, viewDurationMs.value);
});

/** 刻度尺：从 5 分钟到 2 小时里选能让标签数 ≤ 8 的最大密度（按当前视图跨度） */
const ticks = computed(() => {
  const totalMin = viewDurationMs.value / 60000;
  const stepMin = [5, 10, 15, 30, 60, 120].find(step => totalMin / step <= 8) ?? 240;
  const count = Math.floor(totalMin / stepMin);
  return Array.from({ length: count + 1 }, (_, i) => {
    const relMs = i * stepMin * 60000;
    return { pct: (relMs / viewDurationMs.value) * 100, label: formatRelClock(relMs) };
  });
});

// ---- 指针状态（pointer 捕获期间非响应式值走普通变量）----
interface DragState {
  segmentId: number;
  edge: 'start' | 'end';
  baseStartMs: number;
  baseEndMs: number;
}

const dragging = ref<DragState | null>(null);
/** 拖动中的预览边界（毫秒，相对值由 blocks 内即时重算） */
const previewBounds = ref<{ id: number; startMs: number; endMs: number } | null>(null);

/** 空白处按下的待定起点；跨过拖动阈值前只是"待定单击" */
const press = ref<{ x: number; ms: number } | null>(null);
/** 框选中的区间（跨过拖动阈值后出现，松手提交建段或回退为单击） */
const draftRange = ref<{ startMs: number; endMs: number } | null>(null);

function pointerToMs(event: PointerEvent): number {
  const track = trackEl.value;
  if (!track) return viewStartMs.value;
  const rect = track.getBoundingClientRect();
  const ratio = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
  return viewStartMs.value + ratio * viewDurationMs.value;
}

function onEdgePointerDown(event: PointerEvent, segment: ClipSegment, edge: 'start' | 'end'): void {
  event.preventDefault();
  event.stopPropagation();
  (event.target as HTMLElement).setPointerCapture(event.pointerId);
  dragging.value = {
    segmentId: segment.id,
    edge,
    baseStartMs: segment.start_ms,
    baseEndMs: segment.end_ms,
  };
  previewBounds.value = { id: segment.id, startMs: segment.start_ms, endMs: segment.end_ms };
}

function onTrackPointerDown(event: PointerEvent): void {
  if (event.button !== 0) return;
  const track = trackEl.value;
  if (!track) return;
  track.setPointerCapture(event.pointerId);
  press.value = { x: event.clientX, ms: pointerToMs(event) };
  draftRange.value = null;
}

function onPointerMove(event: PointerEvent): void {
  if (dragging.value) {
    const state = dragging.value;
    const pointerMs = pointerToMs(event);
    const minSpanMs = 5 * 1000;
    if (state.edge === 'start') {
      const startMs = Math.min(pointerMs, state.baseEndMs - minSpanMs);
      previewBounds.value = { id: state.segmentId, startMs, endMs: state.baseEndMs };
    } else {
      const endMs = Math.max(pointerMs, state.baseStartMs + minSpanMs);
      previewBounds.value = { id: state.segmentId, startMs: state.baseStartMs, endMs };
    }
    return;
  }
  const origin = press.value;
  if (!origin) return;
  const pointerMs = pointerToMs(event);
  if (!draftRange.value && Math.abs(event.clientX - origin.x) < DRAG_THRESHOLD_PX) return;
  draftRange.value = {
    startMs: Math.min(origin.ms, pointerMs),
    endMs: Math.max(origin.ms, pointerMs),
  };
}

function onPointerUp(): void {
  if (dragging.value) {
    const state = dragging.value;
    const bounds = previewBounds.value;
    dragging.value = null;
    previewBounds.value = null;
    press.value = null;
    draftRange.value = null;
    if (state && bounds) {
      emit('resize', state.segmentId, Math.round(bounds.startMs), Math.round(bounds.endMs));
    }
    return;
  }
  const origin = press.value;
  const range = draftRange.value;
  press.value = null;
  draftRange.value = null;
  if (range && range.endMs - range.startMs >= MIN_CREATE_SPAN_MS) {
    emit('create', Math.round(range.startMs), Math.round(range.endMs));
    return;
  }
  // 无拖动或拖动太短：按单击跳转处理
  if (origin) emit('seek', Math.round(origin.ms));
}

function onPointerCancel(): void {
  // 拖动调界被系统打断视为未发生；框选/单击同样作废，不触发跳转
  dragging.value = null;
  previewBounds.value = null;
  press.value = null;
  draftRange.value = null;
}

/** 概览条点击：放大中=移动窗口到点击处；全场视图=跳转播放 */
function onOverviewClick(event: PointerEvent): void {
  if (event.button !== 0) return;
  const target = event.currentTarget as HTMLElement;
  const rect = target.getBoundingClientRect();
  const ratio = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
  const wallMs = props.sessionStartMs + ratio * sessionDurationMs.value;
  if (!view.value) {
    emit('seek', Math.round(wallMs));
    return;
  }
  const span = view.value.endMs - view.value.startMs;
  const startMs = Math.max(
    props.sessionStartMs,
    Math.min(props.sessionEndMs - span, wallMs - span / 2),
  );
  view.value = { startMs: Math.round(startMs), endMs: Math.round(startMs + span) };
}

function boundsOf(block: Block): { leftPct: number; widthPct: number } {
  if (previewBounds.value && previewBounds.value.id === block.segment.id) {
    const startRel = Math.max(0, previewBounds.value.startMs - viewStartMs.value);
    const endRel = Math.min(viewDurationMs.value, previewBounds.value.endMs - viewStartMs.value);
    return {
      leftPct: (startRel / viewDurationMs.value) * 100,
      widthPct: (Math.max(0, endRel - startRel) / viewDurationMs.value) * 100,
    };
  }
  return { leftPct: block.leftPct, widthPct: block.widthPct };
}

function draftStyle(): { left: string; width: string } {
  const range = draftRange.value;
  const startMs = range ? range.startMs : viewStartMs.value;
  const endMs = range ? range.endMs : viewStartMs.value;
  const startRel = Math.max(0, startMs - viewStartMs.value);
  const endRel = Math.min(viewDurationMs.value, endMs - viewStartMs.value);
  return {
    left: `${(startRel / viewDurationMs.value) * 100}%`,
    width: `${(Math.max(0, endRel - startRel) / viewDurationMs.value) * 100}%`,
  };
}
</script>

<template>
  <div class="clip-timeline">
    <div class="clip-overview-row">
      <div
        class="clip-overview"
        title="全场概览：放大中点击移动窗口；未放大点击跳转播放"
        @pointerdown="onOverviewClick"
      >
        <span
          v-for="block in overviewBlocks"
          :key="`ov-${block.segment.id}`"
          class="clip-overview-block"
          :class="block.statusClass"
          :style="{ left: `${block.leftPct}%`, width: `${block.widthPct}%` }"
        />
        <div
          v-if="overviewWindow"
          class="clip-overview-window"
          :style="{ left: `${overviewWindow.leftPct}%`, width: `${overviewWindow.widthPct}%` }"
        />
        <span v-if="!view" class="clip-overview-hint">全场概览</span>
      </div>
      <button v-if="view" class="clip-overview-reset" type="button" @click.stop="zoomOut">
        显示全场
      </button>
    </div>
    <div
      ref="trackEl"
      class="clip-track"
      @pointerdown="onTrackPointerDown"
      @pointermove="onPointerMove"
      @pointerup="onPointerUp"
      @pointercancel="onPointerCancel"
    >
      <span
        v-for="tick in ticks.slice(1)"
        :key="`grid-${tick.pct}`"
        class="clip-gridline"
        :style="{ left: `${tick.pct}%` }"
      />
      <div class="clip-density">
        <span
          v-for="(bar, index) in densityBars"
          :key="`den-${index}`"
          class="clip-density-bar"
          :style="{
            left: `${bar.leftPct}%`,
            width: `${Math.max(bar.widthPct, 0.15)}%`,
            height: `${Math.max(bar.heightPct, 10)}%`,
          }"
          :title="`约 ${bar.count} 条`"
        />
      </div>
      <div
        v-for="block in blocks"
        :key="block.segment.id"
        class="clip-block"
        :class="[
          block.statusClass,
          {
            'is-selected': block.segment.id === selectedId,
            'is-dragging': dragging?.segmentId === block.segment.id,
            'is-narrow': block.narrow,
          },
        ]"
        :style="{ left: `${boundsOf(block).leftPct}%`, width: `${boundsOf(block).widthPct}%` }"
        :title="`${block.segment.title || '未命名'}（${block.segment.status}）`"
        @pointerdown.stop
        @click.stop="emit('select', block.segment.id)"
      >
        <span
          class="clip-block-edge is-start"
          @pointerdown="onEdgePointerDown($event, block.segment, 'start')"
        />
        <span class="clip-block-label">{{ block.segment.title || '未命名' }}</span>
        <span
          class="clip-block-edge is-end"
          @pointerdown="onEdgePointerDown($event, block.segment, 'end')"
        />
      </div>
      <div v-if="draftRange" class="clip-block is-draft" :style="draftStyle()" />
      <div v-if="playhead !== null" class="clip-playhead" :style="{ left: `${playhead}%` }" />
      <div v-if="blocks.length === 0" class="clip-track-empty">
        点击跳转播放；按住空白拖动框选，松手即新建切片段
      </div>
    </div>
    <div class="clip-ruler">
      <span
        v-for="tick in ticks"
        :key="`tick-${tick.pct}`"
        class="clip-tick"
        :style="{ left: `${tick.pct}%` }"
      >
        {{ tick.label }}
      </span>
    </div>
  </div>
</template>

<style scoped>
.clip-timeline {
  user-select: none;
}

/* 全场概览条：迷你色块 + 放大窗口矩形 */
.clip-overview-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.clip-overview {
  position: relative;
  flex: 1;
  height: 24px;
  border-radius: 5px;
  background: var(--el-fill-color-lighter);
  border: 1px solid var(--el-border-color-lighter);
  cursor: pointer;
  overflow: hidden;
}

.clip-overview-block {
  position: absolute;
  top: 5px;
  bottom: 5px;
  min-width: 3px;
  border-radius: 2px;
  opacity: 0.85;
}

.clip-overview-window {
  position: absolute;
  top: 0;
  bottom: 0;
  border: 1px solid var(--el-color-primary);
  background: var(--el-color-primary-light-9);
  border-radius: 3px;
  pointer-events: none;
}

.clip-overview-hint {
  position: absolute;
  right: 6px;
  top: 50%;
  transform: translateY(-50%);
  font-size: 10px;
  color: var(--el-text-color-placeholder);
  pointer-events: none;
}

.clip-overview-reset {
  flex: none;
  border: 1px solid var(--el-border-color);
  background: var(--el-fill-color-blank);
  border-radius: 4px;
  font-size: 11px;
  color: var(--el-text-color-secondary);
  padding: 2px 8px;
  cursor: pointer;
}

.clip-overview-reset:hover {
  color: var(--el-color-primary);
  border-color: var(--el-color-primary-light-5);
}

/* 主轨：色块区 + 底部密度轨 */
.clip-track {
  position: relative;
  height: 76px;
  border-radius: 8px;
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  overflow: hidden;
  touch-action: none;
}

.clip-gridline {
  position: absolute;
  top: 0;
  bottom: 18px;
  width: 1px;
  background: var(--el-border-color-lighter);
  pointer-events: none;
}

.clip-density {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 18px;
  border-top: 1px dashed var(--el-border-color-lighter);
  pointer-events: none;
}

.clip-density-bar {
  position: absolute;
  bottom: 0;
  background: var(--el-color-primary-light-5);
  border-radius: 1px 1px 0 0;
}

.clip-block {
  position: absolute;
  top: 6px;
  bottom: 22px;
  min-width: 12px;
  border-radius: 5px;
  display: flex;
  align-items: center;
  overflow: hidden;
  cursor: pointer;
  color: #fff;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.18);
  transition:
    box-shadow 0.15s,
    filter 0.15s;
}

.clip-block:hover {
  filter: brightness(1.08);
}

.clip-block.is-suggested {
  background: var(--el-color-primary);
}
.clip-block.is-confirmed {
  background: var(--el-color-warning);
}
.clip-block.is-cutting {
  background: var(--el-color-info);
}
.clip-block.is-done {
  background: var(--el-color-success);
}
.clip-block.is-failed {
  background: var(--el-color-danger);
}
.clip-block.is-selected {
  outline: 2px solid var(--el-color-primary-light-3);
  outline-offset: 1px;
  filter: brightness(1.1);
}
.clip-block.is-dragging {
  opacity: 0.85;
}

/* 概览条迷你块沿用主轨状态配色 */
.clip-overview-block.is-suggested {
  background: var(--el-color-primary);
}
.clip-overview-block.is-confirmed {
  background: var(--el-color-warning);
}
.clip-overview-block.is-cutting {
  background: var(--el-color-info);
}
.clip-overview-block.is-done {
  background: var(--el-color-success);
}
.clip-overview-block.is-failed {
  background: var(--el-color-danger);
}

.clip-block-label {
  flex: 1;
  padding: 0 8px;
  font-size: 12px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  text-shadow: 0 1px 1px rgba(0, 0, 0, 0.25);
  pointer-events: none;
}

.clip-block.is-narrow .clip-block-label {
  display: none;
}

.clip-block-edge {
  width: 8px;
  align-self: stretch;
  cursor: ew-resize;
  flex: none;
  /* 常显：隐藏到悬停才出现的把手没法被发现 */
  opacity: 0.45;
  transition:
    opacity 0.15s,
    background 0.15s;
}

.clip-block:hover .clip-block-edge,
.clip-block.is-dragging .clip-block-edge {
  opacity: 0.9;
  background: rgba(255, 255, 255, 0.45);
}

.clip-block.is-draft {
  background: var(--el-color-primary-light-5);
  border: 1px dashed var(--el-color-primary);
  box-shadow: none;
  pointer-events: none;
}

.clip-playhead {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 2px;
  margin-left: -1px;
  background: var(--el-color-danger);
  pointer-events: none;
}

.clip-playhead::before {
  content: '';
  position: absolute;
  top: 0;
  left: -4px;
  border: 5px solid transparent;
  border-top-color: var(--el-color-danger);
}

.clip-track-empty {
  position: absolute;
  inset: 0 0 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  color: var(--el-text-color-placeholder);
  pointer-events: none;
}

.clip-ruler {
  position: relative;
  height: 20px;
  margin-top: 2px;
}

.clip-tick {
  position: absolute;
  transform: translateX(-50%);
  font-size: 11px;
  color: var(--el-text-color-secondary);
  font-variant-numeric: tabular-nums;
  line-height: 20px;
}

/* 首尾刻度不居中，避免被裁掉 */
.clip-tick:first-child {
  transform: none;
}

.clip-tick:last-child {
  transform: translateX(-100%);
}
</style>
