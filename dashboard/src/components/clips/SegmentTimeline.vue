<script setup lang="ts">
/**
 * 切片段时间轴条：全场一根横条，段渲染为色块。
 *
 * 交互（对齐视频编辑器惯例，不需要文字说明也能猜对）：
 * - 单击空白 → 播放器跳到该时刻（抛出 seek）
 * - 空白按下拖动 → 框选区间，松手新建一段（抛出 create；拖动不足 5 秒视为误触，按单击跳转处理）
 * - 单击色块 → 选中（抛出 select；父组件同时让播放器跳到段起点）
 * - 拖动色块左右边缘 → 调整起止（pointer 事件，pointerup 才提交）
 *
 * 播放头（playheadMs）实时标记播放位置，仅整场文件预览时有值。
 *
 * 色块颜色按状态：suggested=AI 建议色 / confirmed=确认色 / done=完成色 /
 * failed=失败色 / cutting=进行色 / dismissed=置灰。
 */
import { computed, ref } from 'vue';
import { formatRelClock } from '@/utils/clipsTime';
import type { ClipSegment } from '@/types/clips';

const props = defineProps<{
  segments: ClipSegment[];
  /** 场次起点（墙钟毫秒） */
  sessionStartMs: number;
  /** 场次终点（墙钟毫秒；进行中场次用最后活动时刻兜底） */
  sessionEndMs: number;
  selectedId: number | null;
  /** 当前播放位置（墙钟毫秒）；无整场预览时为 null，不渲染播放头 */
  playheadMs: number | null;
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

const trackEl = ref<HTMLElement | null>(null);

const durationMs = computed(() => Math.max(1, props.sessionEndMs - props.sessionStartMs));

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
      const startRel = Math.max(0, segment.start_ms - props.sessionStartMs);
      const endRel = Math.min(durationMs.value, segment.end_ms - props.sessionStartMs);
      const widthPct = (Math.max(0, endRel - startRel) / durationMs.value) * 100;
      return {
        segment,
        leftPct: (startRel / durationMs.value) * 100,
        widthPct,
        statusClass: `is-${segment.status}`,
        narrow: widthPct < NARROW_PCT,
      };
    }),
);

/** 播放头位置（百分比，夹在场次范围内） */
const playheadPct = computed(() => {
  if (props.playheadMs === null) return 0;
  const rel = Math.min(durationMs.value, Math.max(0, props.playheadMs - props.sessionStartMs));
  return (rel / durationMs.value) * 100;
});

/** 刻度尺：从 5 分钟到 2 小时里选能让标签数 ≤ 8 的最大密度 */
const ticks = computed(() => {
  const totalMin = durationMs.value / 60000;
  const stepMin = [5, 10, 15, 30, 60, 120].find(step => totalMin / step <= 8) ?? 240;
  const count = Math.floor(totalMin / stepMin);
  return Array.from({ length: count + 1 }, (_, i) => {
    const relMs = i * stepMin * 60000;
    return { pct: (relMs / durationMs.value) * 100, label: formatRelClock(relMs) };
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
  if (!track) return props.sessionStartMs;
  const rect = track.getBoundingClientRect();
  const ratio = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
  return props.sessionStartMs + ratio * durationMs.value;
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

function boundsOf(block: Block): { leftPct: number; widthPct: number } {
  if (previewBounds.value && previewBounds.value.id === block.segment.id) {
    const startRel = Math.max(0, previewBounds.value.startMs - props.sessionStartMs);
    const endRel = Math.min(durationMs.value, previewBounds.value.endMs - props.sessionStartMs);
    return {
      leftPct: (startRel / durationMs.value) * 100,
      widthPct: (Math.max(0, endRel - startRel) / durationMs.value) * 100,
    };
  }
  return { leftPct: block.leftPct, widthPct: block.widthPct };
}

function draftStyle(): { left: string; width: string } {
  const range = draftRange.value;
  const startMs = range ? range.startMs : props.sessionStartMs;
  const endMs = range ? range.endMs : props.sessionStartMs;
  const startRel = Math.max(0, startMs - props.sessionStartMs);
  const endRel = Math.min(durationMs.value, endMs - props.sessionStartMs);
  return {
    left: `${(startRel / durationMs.value) * 100}%`,
    width: `${(Math.max(0, endRel - startRel) / durationMs.value) * 100}%`,
  };
}
</script>

<template>
  <div class="clip-timeline">
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
      <div v-if="playheadMs !== null" class="clip-playhead" :style="{ left: `${playheadPct}%` }" />
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

.clip-track {
  position: relative;
  height: 64px;
  border-radius: 8px;
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  overflow: hidden;
  touch-action: none;
}

.clip-gridline {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 1px;
  background: var(--el-border-color-lighter);
  pointer-events: none;
}

.clip-block {
  position: absolute;
  top: 8px;
  bottom: 8px;
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
  inset: 0;
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
