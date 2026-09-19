<!--
  PulseChart - 活动脉搏柱状图（多系列分桶计数）
  图形由 ECharts 渲染；图例保留 HTML 排版，空态用容器内浮层提示。
-->
<template>
  <div class="pulse-chart" :style="{ '--pulse-height': `${height}px` }">
    <div v-if="series.length > 0" class="pulse-legend">
      <div v-for="item in series" :key="item.label" class="pulse-legend-item">
        <span class="pulse-dot" :style="{ backgroundColor: item.color }"></span>
        <span class="pulse-legend-label">{{ item.label }}</span>
      </div>
    </div>

    <div ref="containerRef" class="pulse-canvas" role="img" :aria-label="ariaLabel">
      <div v-if="isEmpty" class="pulse-empty">{{ emptyText }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import type { EChartsCoreOption } from 'echarts/core';
import { useChartPalette, useECharts } from '@/composables/useECharts';

/** 单系列：label 用于图例与 tooltip，values 与其他系列等长（不等长时按 0 补齐） */
export interface PulseSeries {
  label: string;
  color: string;
  values: number[];
}

interface Props {
  /** 多系列柱状数据；典型为 2 条（弹幕 / 发言）。 */
  series: PulseSeries[];
  /** 每个分桶的 tooltip 前缀（如时钟分钟 "14:32"），缺省回退到分桶索引 */
  labels?: string[];
  /** 图表容器高度（像素），默认 120 */
  height?: number;
  /** 空态文案（所有柱为 0 时） */
  emptyText?: string;
}

const props = withDefaults(defineProps<Props>(), {
  labels: () => [],
  height: 120,
  emptyText: '近段时间暂无活动',
});

const containerRef = ref<HTMLElement | null>(null);
const palette = useChartPalette();

/** 分桶数 = 所有系列中最长数组的长度；其他系列按 0 补齐 */
const bucketCount = computed(() => {
  let max = 0;
  for (const s of props.series) {
    if (s.values.length > max) max = s.values.length;
  }
  return Math.max(1, max);
});

/** Y 轴量程：max(1, 全局最大值加 5% 呼吸量)，保证零数据也能渲染可见基线 */
const yMax = computed(() => {
  let max = 0;
  for (const s of props.series) {
    for (const v of s.values) {
      if (v > max) max = v;
    }
  }
  return Math.max(1, Math.ceil(max * 1.05));
});

const isEmpty = computed(
  () => yMax.value === 1 && props.series.every(s => s.values.every(v => v === 0)),
);

/** 读取第 i 桶的 series[j] 值；越界返回 0（防御不等长输入） */
function bucketValue(seriesIdx: number, bucketIdx: number): number {
  const arr = props.series[seriesIdx]?.values;
  if (!arr) return 0;
  return bucketIdx < arr.length ? arr[bucketIdx] : 0;
}

const option = computed<EChartsCoreOption>(() => {
  const count = bucketCount.value;
  const categories = Array.from({ length: count }, (_, i) =>
    i < props.labels.length ? props.labels[i] : `#${i}`,
  );
  const paletteValue = palette.value;

  return {
    animation: false,
    grid: { left: 0, right: 0, top: 6, bottom: 0 },
    xAxis: {
      type: 'category',
      data: categories,
      axisLabel: { show: false },
      axisTick: { show: false },
      axisLine: { show: true, lineStyle: { color: paletteValue.borderColor } },
    },
    yAxis: { type: 'value', show: false, max: yMax.value },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      backgroundColor: paletteValue.hoverBg,
      borderColor: paletteValue.borderColorLight,
      textStyle: { color: paletteValue.textColor, fontSize: 12, fontFamily: paletteValue.monoFont },
      formatter: (params: unknown): string => {
        const list = params as Array<{ dataIndex: number }>;
        const index = list[0]?.dataIndex ?? 0;
        const parts = [categories[index] ?? `#${index}`];
        props.series.forEach((s, seriesIdx) => {
          parts.push(`${s.label} ${bucketValue(seriesIdx, index)}`);
        });
        return parts.join(' · ');
      },
    },
    series: props.series.map((s, seriesIdx) => ({
      type: 'bar',
      name: s.label,
      // 多系列时内柱叠放在外柱之上（barGap 负值使各系列同轴心对齐）
      barGap: '-100%',
      barWidth: seriesIdx === 0 ? '70%' : '40%',
      itemStyle: { color: s.color },
      data: Array.from({ length: count }, (_, i) => bucketValue(seriesIdx, i)),
    })),
  };
});

useECharts(containerRef, option);

/** 屏幕阅读器用的简要描述 */
const ariaLabel = computed(() => {
  if (props.series.length === 0) return props.emptyText;
  const names = props.series.map(s => s.label).join('、');
  return `活动脉冲图：${names}，共 ${bucketCount.value} 个分桶`;
});
</script>

<style scoped>
.pulse-chart {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: var(--spacing-xs);
}

.pulse-legend {
  display: flex;
  flex-wrap: wrap;
  gap: var(--spacing-md);
  font-size: 12px;
  color: var(--text-secondary);
  font-family: var(--font-family);
}

.pulse-legend-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.pulse-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
  flex-shrink: 0;
}

.pulse-legend-label {
  white-space: nowrap;
}

.pulse-canvas {
  position: relative;
  width: 100%;
  height: var(--pulse-height);
  border-radius: var(--radius-sm);
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
}

.pulse-empty {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-placeholder);
  font-size: 12px;
  font-family: var(--font-mono);
  letter-spacing: 0.2px;
  pointer-events: none;
}
</style>
