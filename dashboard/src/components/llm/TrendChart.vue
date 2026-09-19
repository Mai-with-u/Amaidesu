<!--
  TrendChart - 逐日趋势图（堆叠柱 + 可选折线）
  图形由 ECharts 渲染；bars 按日堆叠共享左轴量程，line 使用独立量程
  （lineMax 可强制上限，如命中率 100），null 断线表示当日无数据；
  悬停提示复用调用方拼好的 tooltips 文案。
-->
<template>
  <div class="trend-chart">
    <div class="trend-stage" :style="{ height: `${height}px` }" role="img" :aria-label="ariaLabel">
      <div ref="containerRef" class="trend-canvas"></div>
      <div v-if="isEmpty" class="trend-empty">{{ emptyText }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import type { EChartsCoreOption } from 'echarts/core';
import { useChartPalette, useECharts } from '@/composables/useECharts';
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
const palette = useChartPalette();

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

const hasBars = computed(() => props.bars.length > 0);

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

/** 折线量程：无柱时与左轴共享同一量程（刻度即折线刻度）；有柱时独立 */
const lineAxisMax = computed(() => {
  if (!hasBars.value) return leftAxisMax.value;
  if (props.lineMax !== null) return props.lineMax;
  return niceCeil(lineDataMax.value * 1.05);
});

/** 按日数补齐序列：柱补 0，折线补 null */
function paddedData(values: Array<number | null>, fill: number | null): Array<number | null> {
  return Array.from({ length: dayCount.value }, (_, i) => values[i] ?? fill);
}

const isEmpty = computed(() => {
  const barsAllZero = props.bars.every(series => series.values.every(v => (v ?? 0) === 0));
  const lineEmpty = !props.line || props.line.values.every(v => v === null || v === 0);
  return barsAllZero && lineEmpty;
});

const option = computed<EChartsCoreOption>(() => {
  const paletteValue = palette.value;
  const categories = Array.from({ length: dayCount.value }, (_, i) => props.labels[i] ?? '');

  interface SeriesOption {
    type: string;
    name: string;
    data: Array<number | null>;
    stack?: string;
    barMaxWidth?: number;
    yAxisIndex?: number;
    connectNulls?: boolean;
    symbolSize?: number;
    itemStyle: { color: string };
    lineStyle?: { width: number };
  }

  const series: SeriesOption[] = props.bars.map(seriesItem => ({
    type: 'bar',
    name: seriesItem.name,
    stack: 'total',
    barMaxWidth: 28,
    data: paddedData(seriesItem.values, 0),
    itemStyle: { color: seriesItem.color },
  }));

  if (props.line) {
    series.push({
      type: 'line',
      name: props.line.name,
      yAxisIndex: hasBars.value ? 1 : 0,
      connectNulls: false,
      symbolSize: 5,
      data: paddedData(props.line.values, null),
      itemStyle: { color: props.line.color },
      lineStyle: { width: 2 },
    });
  }

  const monoTick = {
    color: paletteValue.textColor,
    fontSize: 10,
    fontFamily: paletteValue.monoFont,
  };

  const yAxes: Array<Record<string, unknown>> = [
    {
      type: 'value',
      max: leftAxisMax.value,
      axisLabel: {
        ...monoTick,
        formatter: hasBars.value ? props.leftTickFormat : props.lineTickFormat,
      },
      splitLine: { lineStyle: { color: paletteValue.borderColorLight } },
    },
  ];
  if (props.line && hasBars.value) {
    yAxes.push({
      type: 'value',
      position: 'right',
      max: lineAxisMax.value,
      axisLabel: { ...monoTick, formatter: props.lineTickFormat },
      splitLine: { show: false },
    });
  }

  return {
    animation: false,
    grid: { left: 8, right: 8, top: 12, bottom: 4, containLabel: true },
    xAxis: {
      type: 'category',
      data: categories,
      axisLabel: { ...monoTick, hideOverlap: true },
      axisTick: { show: false },
      axisLine: { lineStyle: { color: paletteValue.borderColor } },
    },
    yAxis: yAxes,
    series,
    tooltip: {
      trigger: 'axis',
      backgroundColor: paletteValue.hoverBg,
      borderColor: paletteValue.borderColorLight,
      textStyle: { color: paletteValue.textColor, fontSize: 12, whiteSpace: 'pre-line' },
      formatter: (params: unknown): string => {
        const list = params as Array<{ dataIndex: number }>;
        const index = list[0]?.dataIndex ?? 0;
        return props.tooltips[index] || props.labels[index] || '';
      },
    },
  };
});

useECharts(containerRef, option);

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

.trend-stage {
  position: relative;
  width: 100%;
}

/* echarts 独占此节点：容器内不渲染任何 Vue 子节点，避免 patch 撞上 canvas */
.trend-canvas {
  position: absolute;
  inset: 0;
}

.trend-empty {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-placeholder);
  font-size: 12px;
  pointer-events: none;
}
</style>
