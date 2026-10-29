<template>
  <el-card class="gaze-targets" shadow="never">
    <template #header>
      <div class="card-header">
        <span>眼神目标</span>
        <span class="hint">拖动圆点设位置，点空白处让她看过去</span>
      </div>
    </template>

    <div class="gaze-body">
      <div
        ref="boxRef"
        class="gaze-box"
        @pointerdown="onBoxPointerDown"
        @pointermove="onPointerMove"
        @pointerup="onPointerUp"
        @pointercancel="onPointerUp"
      >
        <div class="axis axis-x" />
        <div class="axis axis-y" />
        <span class="box-label top">上</span>
        <span class="box-label right">右</span>
        <!-- 思考时允许两侧都看：镜像的另一侧画成虚线点 -->
        <div
          v-if="mirror"
          class="marker ghost"
          :style="markerStyle(-points.thinking.x, points.thinking.y, '#67c23a')"
          title="思考（镜像一侧）"
        />
        <div
          v-for="target in GAZE_TARGETS"
          :key="target.id"
          class="marker"
          :class="{ dragging: dragging === target.id }"
          :style="markerStyle(points[target.id].x, points[target.id].y, target.color)"
          :title="target.label"
          @pointerdown.stop="onMarkerPointerDown($event, target.id)"
        />
        <!-- 此刻实际的视线（来自实时帧） -->
        <div class="live-dot" :style="liveStyle" />
      </div>

      <div class="legend">
        <div v-for="target in GAZE_TARGETS" :key="target.id" class="legend-row">
          <span class="swatch" :style="{ background: target.color }" />
          <span class="legend-name">{{ target.label }}</span>
          <span class="legend-value">
            {{ points[target.id].x.toFixed(2) }}, {{ points[target.id].y.toFixed(2) }}
          </span>
          <el-button size="small" text @click="lookAt(points[target.id].x, points[target.id].y)">
            看一下
          </el-button>
        </div>
        <div class="legend-row">
          <span class="swatch live" />
          <span class="legend-name">此刻视线</span>
          <span class="legend-value">
            {{ (frame.gaze_x ?? 0).toFixed(2) }}, {{ (frame.gaze_y ?? 0).toFixed(2) }}
          </span>
        </div>
        <p class="note">
          方框按观众看直播画面的方向画（右、上为正）。若形象实际看向反了，以形象为准，
          把点拖到对侧即可。
        </p>
      </div>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import { avatarTuningApi } from '@/api';
import type { AvatarTuningField } from '@/types';
import { getApiErrorMessage } from '@/utils/apiError';
import { GAZE_TARGETS, gazeToPercent, pointerToGaze, throttleLatest } from '@/utils/avatarTuning';

/**
 * 眼神目标方框（形象调校页的一块）：镜头、弹幕区、思考时望向的三个点。
 *
 * 拖动一个点：改对应的两个调参（只在运行中生效），同时让形象看向这个点，边拖边看
 * 效果；点方框空白处只是让形象看过去（预览，不改调参）。实际视线由页面轮询的实时
 * 帧经 props 传入，画成小灰点。
 *
 * 生命周期语义：无定时器与订阅（轮询归页面），拖动状态随指针抬起结束。
 */

type TargetId = (typeof GAZE_TARGETS)[number]['id'];

const props = defineProps<{
  fields: AvatarTuningField[];
  frame: Record<string, number>;
}>();

const emit = defineEmits<{
  /** 调参在运行中改了（页面据此提示"有未保存的修改"并同步本地字段值） */
  changed: [changes: Record<string, number>];
}>();

const boxRef = ref<HTMLElement | null>(null);
const dragging = ref<TargetId | null>(null);
const points = reactive<Record<TargetId, { x: number; y: number }>>({
  camera: { x: 0, y: 0 },
  chat: { x: 0, y: 0 },
  thinking: { x: 0, y: 0 },
});

function fieldValue(key: string): number {
  const value = props.fields.find(f => f.key === key)?.value;
  return typeof value === 'number' ? value : 0;
}

const mirror = computed(
  () => props.fields.find(f => f.key === 'thinking_gaze_mirror')?.value === true,
);

// 字段值变了（首次加载、刷新、保存后重载）就同步到圆点；拖动中不被轮询覆盖
watch(
  () => props.fields,
  () => {
    for (const target of GAZE_TARGETS) {
      if (dragging.value === target.id) continue;
      points[target.id] = { x: fieldValue(target.xKey), y: fieldValue(target.yKey) };
    }
  },
  { immediate: true, deep: true },
);

function markerStyle(x: number, y: number, color: string): Record<string, string> {
  const { left, top } = gazeToPercent(x, y);
  return { left: `${left}%`, top: `${top}%`, borderColor: color, background: `${color}33` };
}

const liveStyle = computed(() => {
  const { left, top } = gazeToPercent(props.frame.gaze_x ?? 0, props.frame.gaze_y ?? 0);
  return { left: `${left}%`, top: `${top}%` };
});

async function lookAt(x: number, y: number): Promise<void> {
  try {
    await avatarTuningApi.look(x, y, 1500);
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '预览视线失败'));
  }
}

const sendTarget = throttleLatest((id: TargetId, x: number, y: number) => {
  const target = GAZE_TARGETS.find(t => t.id === id);
  if (!target) return;
  const changes = { [target.xKey]: x, [target.yKey]: y };
  avatarTuningApi
    .patchConfig(changes)
    .then(() => emit('changed', changes))
    .catch(error => ElMessage.error(getApiErrorMessage(error, '修改眼神目标失败')));
  void lookAt(x, y);
}, 120);

function pointFromEvent(event: PointerEvent): { x: number; y: number } | null {
  const box = boxRef.value;
  return box ? pointerToGaze(event.clientX, event.clientY, box.getBoundingClientRect()) : null;
}

function onMarkerPointerDown(event: PointerEvent, id: TargetId): void {
  dragging.value = id;
  boxRef.value?.setPointerCapture(event.pointerId);
}

function onBoxPointerDown(event: PointerEvent): void {
  const point = pointFromEvent(event);
  if (point) void lookAt(point.x, point.y);
}

function onPointerMove(event: PointerEvent): void {
  const id = dragging.value;
  if (!id) return;
  const point = pointFromEvent(event);
  if (!point) return;
  points[id] = point;
  sendTarget(id, point.x, point.y);
}

function onPointerUp(event: PointerEvent): void {
  if (dragging.value && boxRef.value?.hasPointerCapture(event.pointerId)) {
    boxRef.value.releasePointerCapture(event.pointerId);
  }
  dragging.value = null;
}
</script>

<style scoped>
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.hint,
.note {
  font-size: 12px;
  color: var(--text-secondary);
}

.gaze-body {
  display: flex;
  flex-wrap: wrap;
  gap: var(--spacing-lg);
  align-items: flex-start;
}

.gaze-box {
  position: relative;
  width: 260px;
  height: 260px;
  flex-shrink: 0;
  border: 1px solid var(--el-border-color, #dcdfe6);
  border-radius: 6px;
  background: var(--el-fill-color-lighter, #fafafa);
  cursor: crosshair;
  touch-action: none;
  user-select: none;
}

.axis {
  position: absolute;
  background: var(--el-border-color, #dcdfe6);
}

.axis-x {
  left: 0;
  right: 0;
  top: 50%;
  height: 1px;
}

.axis-y {
  top: 0;
  bottom: 0;
  left: 50%;
  width: 1px;
}

.box-label {
  position: absolute;
  font-size: 11px;
  color: var(--text-secondary);
}

.box-label.top {
  top: 2px;
  left: calc(50% + 4px);
}

.box-label.right {
  right: 4px;
  top: calc(50% + 2px);
}

.marker {
  position: absolute;
  width: 18px;
  height: 18px;
  margin: -9px 0 0 -9px;
  border: 2px solid;
  border-radius: 50%;
  cursor: grab;
}

.marker.dragging {
  cursor: grabbing;
  transform: scale(1.2);
}

.marker.ghost {
  border-style: dashed;
  cursor: default;
  pointer-events: none;
  opacity: 0.6;
}

.live-dot {
  position: absolute;
  width: 8px;
  height: 8px;
  margin: -4px 0 0 -4px;
  border-radius: 50%;
  background: var(--text-secondary);
  pointer-events: none;
  transition:
    left 0.15s linear,
    top 0.15s linear;
}

.legend {
  flex: 1;
  min-width: 260px;
}

.legend-row {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  min-height: 30px;
}

.swatch {
  width: 12px;
  height: 12px;
  border-radius: 50%;
}

.swatch.live {
  width: 8px;
  height: 8px;
  margin: 0 2px;
  background: var(--text-secondary);
}

.legend-name {
  width: 64px;
  font-size: 13px;
}

.legend-value {
  width: 90px;
  font-family: var(--font-mono);
  font-size: 12px;
}
</style>
