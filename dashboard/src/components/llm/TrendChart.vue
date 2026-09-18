<!--
  TrendChart - 手写 SVG 逐日趋势图（堆叠柱 + 可选折线，无第三方图表库）

  与 PulseChart 同源的零依赖思路，但坐标轴文本需要不随容器拉伸变形，
  故改用 useElementSize 实测容器宽度后按像素渲染。

  布局语义：
  - bars（可多系列）按日堆叠，共享左轴量程；
  - line 为单系列折线，使用独立量程（lineMax 可强制上限，如命中率 100），
    有柱时折线刻度画在右轴，无柱时画在左轴；
  - 悬停提示走原生 <title>（每日一列透明热区），文案由调用方拼好传入。
-->
<template>
  <div ref="containerRef" class="trend-chart" :style="{ height: `${height}px` }">
    <svg v-if="plotWidth > 0" :width="plotWidth" :height="height" role="img" :aria-label="ariaLabel">
      <!-- 水平网格线 + 左轴刻度（无柱时左轴归折线使用；空态整体隐藏） -->
      <g v-if="!isEmpty">
        <g v-for="tick in axisTicks" :key="`grid-${tick.ratio}`">
          <line
            class="tc-grid"
            :x1="padLeft"
            :y1="tick.y"
            :x2="padLeft + plotInnerWidth"
            :y2="tick.y"
          />
          <text
            class="tc-tick"
            :x="padLeft - 6"
            :y="tick.y"
            text-anchor="end"
            dominant-baseline="middle"
          >
            {{ tick.text }}
          </text>
        </g>
      </g>

      <!-- 折线刻度（右轴；仅在有柱时渲染，避免与左轴刻度重叠） -->
      <g v-if="!isEmpty && hasBars && lineScaleTicks.length">
        <text
          v-for="tick in lineScaleTicks"
          :key="`rt-${tick.text}`"
          class="tc-tick"
          :x="padLeft + plotInnerWidth + 6"
          :y="tick.y"
          text-anchor="start"
          dominant-baseline="middle"
        >
          {{ tick.text }}
        </text>
      </g>

      <!-- 堆叠柱与每日热区：空态不渲染 -->
      <g v-if="!isEmpty">
        <g v-for="(day, dayIndex) in barColumns" :key="`col-${dayIndex}`">
          <rect
            v-for="seg in day.segments"
            :key="`seg-${dayIndex}-${seg.seriesIndex}`"
            :x="seg.x"
            :y="seg.y"
            :width="barWidth"
            :height="seg.height"
            :fill="seg.color"
          />
          <!-- 每日热区：覆盖整列，悬停显示当日全部明细 -->
          <rect class="tc-hotzone" :x="day.hotX" y="0" :width="step" :height="height">
            <title>{{ tooltips[dayIndex] || labels[dayIndex] || '' }}</title>
          </rect>
        </g>
      </g>

      <!-- 折线：null 断开为多段 -->
      <g>
        <polyline
          v-for="(segment, segIndex) in lineSegments"
          :key="`ln-${segIndex}`"
          class="tc-line"
          :stroke="line?.color"
          :points="segment"
          fill="none"
        />
        <circle
          v-for="(point, pointIndex) in linePoints"
          :key="`pt-${pointIndex}`"
          :cx="point.x"
          :cy="point.y"
          r="2.5"
          :fill="line?.color"
        >
          <title>{{ point.tooltip }}</title>
        </circle>
      </g>

      <!-- X 轴日期标签：均匀抽样，避免拥挤（空态隐藏） -->
      <g v-if="!isEmpty">
        <text
          v-for="label in xLabels"
          :key="`x-${label.index}`"
          class="tc-tick"
          :x="label.x"
          :y="height - 6"
          text-anchor="middle"
        >
          {{ label.text }}
        </text>
      </g>

      <!-- 空态 -->
      <text v-if="isEmpty" class="tc-empty" :x="padLeft + plotInnerWidth / 2" :y="height / 2" text-anchor="middle" dominant-baseline="middle">
        {{ emptyText }}
      </text>
    </svg>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import { useElementSize } from '@vueuse/core';
import { niceCeil } from '@/utils/chartFormat';

/** 堆叠柱单系列（values 与 labels 等长，短则按 0 补齐） */
interface TrendBarSeries {
  name: string;
  color: string;
  values: number[];
}

/** 折线单系列；null 表示当日无数据（断线而非落 0） */
interface TrendLineSeries {
  name: string;
  color: string;
  values: Array<number | null>;
}

const props = withDefaults(
  defineProps<{
    /** 每日日期标签（MM-DD），同时是 tooltip 缺省标题 */
    labels: string[];
    /** 堆叠柱系列（左轴） */
    bars?: TrendBarSeries[];
    /** 折线系列（独立量程） */
    line?: TrendLineSeries | null;
    /** 折线量程上限覆盖（如命中率固定 100） */
    lineMax?: number | null;
    /** 折线刻度文本格式（如百分比） */
    lineTickFormat?: (value: number) => string;
    /** 左轴刻度文本格式 */
    leftTickFormat?: (value: number) => string;
    /** 每日悬停文本（多行用 \n）；缺省用 labels[i] */
    tooltips?: string[];
    height?: number;
    emptyText?: string;
  }>(),
  {
    bars: () => [],
    line: null,
    lineMax: null,
    lineTickFormat: (v: number): string => `${v}`,
    leftTickFormat: (v: number): string => `${v}`,
    tooltips: () => [],
    height: 220,
    emptyText: '暂无数据',
  },
);

const containerRef = ref<HTMLElement | null>(null);
const { width: containerWidth } = useElementSize(containerRef);

// 轴区留白：左轴标签、右轴标签、底部日期标签
const padLeft = 52;
const padRight = props.line ? 56 : 14;
const padTop = 12;
const padBottom = 22;

const plotWidth = computed(() => Math.max(0, containerWidth.value));
const plotInnerWidth = computed(() => Math.max(10, plotWidth.value - padLeft - padRight));
const plotInnerHeight = computed(() => Math.max(10, props.height - padTop - padBottom));

const dayCount = computed(() => {
  let max = props.labels.length;
  for (const series of props.bars) {
    max = Math.max(max, series.values.length);
  }
  if (props.line) {
    max = Math.max(max, props.line.values.length);
  }
  return Math.max(1, max);
});

const step = computed(() => plotInnerWidth.value / dayCount.value);
const barWidth = computed(() => Math.min(step.value * 0.62, 28));

/** 折线数据最大值（null 不计入） */
const lineDataMax = computed(() => {
  let max = 0;
  if (props.line) {
    for (const v of props.line.values) {
      if (v !== null && v > max) max = v;
    }
  }
  return max;
});

/** 左轴量程：有柱取柱堆叠和；无柱时左轴归折线（显式 lineMax 原样采用，不加点余量） */
const leftAxisMax = computed(() => {
  if (!hasBars.value) {
    if (props.lineMax !== null) return props.lineMax;
    return niceCeil(lineDataMax.value * 1.05);
  }
  let max = 0;
  for (let i = 0; i < dayCount.value; i++) {
    let stackSum = 0;
    for (const series of props.bars) {
      stackSum += series.values[i] ?? 0;
    }
    if (stackSum > max) max = stackSum;
  }
  return niceCeil(max * 1.05);
});

/** 折线量程：无柱时与左轴共享同一量程（保证网格刻度即折线刻度）；有柱时独立 */
const lineAxisMax = computed(() => {
  if (!hasBars.value) return leftAxisMax.value;
  if (props.lineMax !== null) return props.lineMax;
  return niceCeil(lineDataMax.value * 1.05);
});

const hasBars = computed(() => props.bars.length > 0);

/** 网格线与左轴刻度（5 档；无柱时展示折线刻度文本） */
const axisTicks = computed(() => {
  const ticks: Array<{ ratio: number; y: number; text: string }> = [];
  for (let i = 0; i <= 4; i++) {
    const ratio = i / 4;
    const y = padTop + (1 - ratio) * plotInnerHeight.value;
    const value = leftAxisMax.value * ratio;
    const text = hasBars.value
      ? props.leftTickFormat(value)
      : props.lineTickFormat(value);
    ticks.push({ ratio, y, text });
  }
  return ticks;
});

/** 折线右轴刻度（3 档，仅位置标注不画线） */
const lineScaleTicks = computed(() => {
  if (!props.line) return [];
  return [0, 0.5, 1].map(ratio => ({
    ratio,
    y: padTop + (1 - ratio) * plotInnerHeight.value,
    text: props.lineTickFormat(lineAxisMax.value * ratio),
  }));
});

interface BarSegment {
  seriesIndex: number;
  x: number;
  y: number;
  height: number;
  color: string;
}

/** 每日柱几何 + 热区 */
const barColumns = computed(() => {
  const columns: Array<{ hotX: number; segments: BarSegment[] }> = [];
  for (let i = 0; i < dayCount.value; i++) {
    const center = padLeft + i * step.value + step.value / 2;
    const x = center - barWidth.value / 2;
    let cumulative = 0;
    const segments: BarSegment[] = [];
    props.bars.forEach((series, seriesIndex) => {
      const value = series.values[i] ?? 0;
      const heightPx = (value / leftAxisMax.value) * plotInnerHeight.value;
      if (heightPx > 0) {
        cumulative += heightPx;
        segments.push({
          seriesIndex,
          x,
          y: padTop + plotInnerHeight.value - cumulative,
          height: heightPx,
          color: series.color,
        });
      }
    });
    columns.push({ hotX: padLeft + i * step.value, segments });
  }
  return columns;
});

/** 折线点坐标序列：与 line.values 等长，null 保留为占位（断线用） */
const lineCoords = computed<Array<{ x: number; y: number } | null>>(() => {
  if (!props.line) return [];
  return props.line.values.map((value, i) => {
    if (value === null) return null;
    return {
      x: padLeft + i * step.value + step.value / 2,
      y: padTop + (1 - value / lineAxisMax.value) * plotInnerHeight.value,
    };
  });
});

/** 折线数据点（跳过 null），附带当日提示文本 */
const linePoints = computed(() => {
  if (!props.line) return [];
  const points: Array<{ x: number; y: number; tooltip: string }> = [];
  props.line.values.forEach((value, i) => {
    const coord = lineCoords.value[i];
    if (value === null || !coord) return;
    points.push({ x: coord.x, y: coord.y, tooltip: props.tooltips[i] || props.labels[i] || '' });
  });
  return points;
});

/** null 处断开的折线段（polyline points 串） */
const lineSegments = computed(() => {
  const segments: string[] = [];
  let current: string[] = [];
  for (const coord of lineCoords.value) {
    if (!coord) {
      if (current.length > 1) segments.push(current.join(' '));
      current = [];
      continue;
    }
    current.push(`${coord.x},${coord.y}`);
  }
  if (current.length > 1) segments.push(current.join(' '));
  return segments;
});

/** X 轴标签抽样：约每 60px 一个 */
const xLabels = computed(() => {
  const maxLabels = Math.max(2, Math.floor(plotInnerWidth.value / 60));
  const stride = Math.ceil(dayCount.value / maxLabels);
  const labels: Array<{ index: number; x: number; text: string }> = [];
  for (let i = 0; i < dayCount.value; i += stride) {
    labels.push({
      index: i,
      x: padLeft + i * step.value + step.value / 2,
      text: props.labels[i] ?? '',
    });
  }
  return labels;
});

const isEmpty = computed(() => {
  const barsAllZero = props.bars.every(series =>
    series.values.every(v => (v ?? 0) === 0),
  );
  const lineEmpty =
    !props.line || props.line.values.every(v => v === null || v === 0);
  return barsAllZero && lineEmpty;
});

const ariaLabel = computed(() => {
  const parts = props.bars.map(series => series.name);
  if (props.line) parts.push(props.line.name);
  return `逐日趋势图：${parts.join('、') || '无数据系列'}`;
});
</script>

<style scoped>
.trend-chart {
  width: 100%;
}

.tc-grid {
  stroke: var(--border-color-light);
  stroke-width: 1;
}

.tc-tick {
  fill: var(--text-secondary);
  font-size: 10px;
  font-family: var(--font-mono);
}

.tc-hotzone {
  fill: transparent;
}

.tc-hotzone:hover {
  fill: var(--bg-hover);
  opacity: 0.5;
}

.tc-line {
  stroke-width: 2;
  stroke-linejoin: round;
  stroke-linecap: round;
}

.tc-empty {
  fill: var(--text-placeholder);
  font-size: 12px;
}
</style>
