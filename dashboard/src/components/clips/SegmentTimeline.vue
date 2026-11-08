<script setup lang="ts">
/**
 * 切片段时间轴条：全场一根横条，段渲染为色块。
 *
 * 交互：
 * - 拖动色块左右边缘 → 调整起止（pointer 事件，pointerup 才提交）
 * - 双击空白处 → 在该位置新建一段（默认 5 分钟，夹进场次范围）
 * - 单击色块 → 选中（高亮 + 抛出 select 事件）
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
}>();

const emit = defineEmits<{
  (e: 'select', segmentId: number): void;
  (e: 'resize', segmentId: number, startMs: number, endMs: number): void;
  (e: 'create', startMs: number, endMs: number): void;
}>();

/** 双击新建段的默认时长 */
const CREATE_SPAN_MS = 5 * 60 * 1000;

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

// ---- 拖动状态（pointer 捕获期间非响应式值走普通变量）----
interface DragState {
  segmentId: number;
  edge: 'start' | 'end';
  baseStartMs: number;
  baseEndMs: number;
}

const dragging = ref<DragState | null>(null);
/** 拖动中的预览边界（毫秒，相对值由 blocks 内即时重算） */
const previewBounds = ref<{ id: number; startMs: number; endMs: number } | null>(null);

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
  dragging.value = { segmentId: segment.id, edge, baseStartMs: segment.start_ms, baseEndMs: segment.end_ms };
  previewBounds.value = { id: segment.id, startMs: segment.start_ms, endMs: segment.end_ms };
}

function onPointerMove(event: PointerEvent): void {
  const state = dragging.value;
  if (!state) return;
  const pointerMs = pointerToMs(event);
  const minSpanMs = 5 * 1000;
  if (state.edge === 'start') {
    const startMs = Math.min(pointerMs, state.baseEndMs - minSpanMs);
    previewBounds.value = { id: state.segmentId, startMs, endMs: state.baseEndMs };
  } else {
    const endMs = Math.max(pointerMs, state.baseStartMs + minSpanMs);
    previewBounds.value = { id: state.segmentId, startMs: state.baseStartMs, endMs };
  }
}

function onPointerUp(): void {
  const state = dragging.value;
  const bounds = previewBounds.value;
  dragging.value = null;
  previewBounds.value = null;
  if (state && bounds) {
    emit('resize', state.segmentId, Math.round(bounds.startMs), Math.round(bounds.endMs));
  }
}

function onTrackDoubleClick(event: MouseEvent): void {
  const track = trackEl.value;
  if (!track) return;
  const pointerMs = pointerToMs(event as unknown as PointerEvent);
  const startMs = Math.min(pointerMs, Math.max(props.sessionStartMs, props.sessionEndMs - CREATE_SPAN_MS));
  const endMs = Math.min(pointerMs + CREATE_SPAN_MS, props.sessionEndMs);
  emit('create', Math.round(startMs), Math.round(endMs));
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
</script>

<template>
  <div class="clip-timeline">
    <div
      ref="trackEl"
      class="clip-track"
      @pointermove="onPointerMove"
      @pointerup="onPointerUp"
      @pointercancel="onPointerUp"
      @dblclick="onTrackDoubleClick"
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
        :class="[block.statusClass, { 'is-selected': block.segment.id === selectedId, 'is-dragging': dragging?.segmentId === block.segment.id, 'is-narrow': block.narrow }]"
        :style="{ left: `${boundsOf(block).leftPct}%`, width: `${boundsOf(block).widthPct}%` }"
        :title="`${block.segment.title || '未命名'}（${block.segment.status}）`"
        @click.stop="emit('select', block.segment.id)"
      >
        <span class="clip-block-edge is-start" @pointerdown="onEdgePointerDown($event, block.segment, 'start')" />
        <span class="clip-block-label">{{ block.segment.title || '未命名' }}</span>
        <span class="clip-block-edge is-end" @pointerdown="onEdgePointerDown($event, block.segment, 'end')" />
      </div>
      <div v-if="blocks.length === 0" class="clip-track-empty">双击空白处新建切片段；AI 选段后这里会出现建议色块</div>
    </div>
    <div class="clip-ruler">
      <span v-for="tick in ticks" :key="`tick-${tick.pct}`" class="clip-tick" :style="{ left: `${tick.pct}%` }">
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
  transition: box-shadow 0.15s, filter 0.15s;
}

.clip-block:hover {
  filter: brightness(1.08);
}

.clip-block.is-suggested { background: var(--el-color-primary); }
.clip-block.is-confirmed { background: var(--el-color-warning); }
.clip-block.is-cutting { background: var(--el-color-info); }
.clip-block.is-done { background: var(--el-color-success); }
.clip-block.is-failed { background: var(--el-color-danger); }
.clip-block.is-selected {
  outline: 2px solid var(--el-color-primary-light-3);
  outline-offset: 1px;
  filter: brightness(1.1);
}
.clip-block.is-dragging { opacity: 0.85; }

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
  opacity: 0;
  transition: opacity 0.15s, background 0.15s;
}

.clip-block:hover .clip-block-edge,
.clip-block.is-dragging .clip-block-edge {
  opacity: 1;
  background: rgba(255, 255, 255, 0.45);
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
