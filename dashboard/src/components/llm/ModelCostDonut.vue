<!--
  ModelCostDonut - 环形占比图
  圆环由 ECharts 饼图渲染，中心总额用容器内浮层排版；
  图例保留 HTML 侧栏（名称 + 金额 + 占比）。
-->
<template>
  <div class="donut-chart">
    <div
      class="dc-stage"
      :style="{ width: `${size}px`, height: `${size}px` }"
      role="img"
      aria-label="模型费用占比环形图"
    >
      <div ref="containerRef" class="dc-canvas"></div>
      <div class="dc-center">
        <span class="dc-total-label">{{ totalLabel }}</span>
        <span class="dc-total-value">{{ totalText }}</span>
      </div>
    </div>

    <ul class="dc-legend">
      <li v-for="item in legendItems" :key="item.model" class="dc-legend-item">
        <span class="dc-dot" :style="{ backgroundColor: item.color }"></span>
        <span class="dc-name" :title="item.model">{{ item.model }}</span>
        <span class="dc-value">{{ item.valueText }}</span>
        <span class="dc-percent">{{ item.percentText }}</span>
      </li>
    </ul>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import type { EChartsCoreOption } from 'echarts/core';
import { useChartPalette, useECharts } from '@/composables/useECharts';

/** 单个占比条目（value 需非负；全 0 时渲染空态底环） */
interface DonutItem {
  name: string;
  value: number;
}

const props = withDefaults(
  defineProps<{
    items: DonutItem[];
    /** 各条目颜色（与 items 一一对应；不足时循环取色板） */
    colors?: string[];
    /** 中心行上方的小标签（如 "总费用"） */
    totalLabel?: string;
    /** 中心数值与图例金额的格式化 */
    formatValue?: (value: number) => string;
    size?: number;
  }>(),
  {
    colors: () => ['#409eff', '#10b981', '#f59e0b', '#8b5cf6', '#f56c6c', '#0ea5e9', '#d946ef'],
    totalLabel: '总计',
    formatValue: (value: number): string => `${value}`,
    size: 168,
  },
);

const containerRef = ref<HTMLElement | null>(null);
const palette = useChartPalette();

const total = computed(() => props.items.reduce((sum, item) => sum + Math.max(0, item.value), 0));

const totalText = computed(() => props.formatValue(total.value));

const legendItems = computed(() =>
  props.items.map((item, index) => {
    const ratio = total.value > 0 ? Math.max(0, item.value) / total.value : 0;
    return {
      model: item.name,
      color: props.colors[index % props.colors.length],
      valueText: props.formatValue(item.value),
      percentText: `${(ratio * 100).toFixed(1)}%`,
    };
  }),
);

const option = computed<EChartsCoreOption>(() => {
  const paletteValue = palette.value;
  const data = props.items.map((item, index) => ({
    name: item.name,
    value: Math.max(0, item.value),
    itemStyle: { color: props.colors[index % props.colors.length] },
  }));

  return {
    animation: false,
    tooltip: {
      trigger: 'item',
      backgroundColor: paletteValue.hoverBg,
      borderColor: paletteValue.borderColorLight,
      textStyle: { color: paletteValue.textColor, fontSize: 12, fontFamily: paletteValue.monoFont },
      formatter: (params: unknown): string => {
        const p = params as { name: string; value: number; percent: number };
        return `${p.name}：${props.formatValue(p.value)}（${p.percent}%）`;
      },
    },
    series: [
      {
        type: 'pie',
        radius: ['65%', '87%'],
        center: ['50%', '50%'],
        label: { show: false },
        emphasis: { scale: false },
        data,
      },
      {
        // 底环：空态或占比不满时提供视觉基座
        type: 'pie',
        radius: ['65%', '87%'],
        center: ['50%', '50%'],
        silent: true,
        label: { show: false },
        tooltip: { show: false },
        emphasis: { scale: false },
        data: [{ value: 1, itemStyle: { color: paletteValue.hoverBg } }],
        z: 0,
      },
    ],
  };
});

useECharts(containerRef, option);
</script>

<style scoped>
.donut-chart {
  display: flex;
  align-items: center;
  gap: var(--spacing-lg);
  flex-wrap: wrap;
}

.dc-stage {
  position: relative;
  flex-shrink: 0;
}

/* echarts 独占此节点：容器内不渲染任何 Vue 子节点，避免 patch 撞上 canvas */
.dc-canvas {
  position: absolute;
  inset: 0;
}

.dc-center {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  pointer-events: none;
}

.dc-total-label {
  color: var(--text-secondary);
  font-size: 11px;
}

.dc-total-value {
  color: var(--text-primary);
  font-size: 15px;
  font-weight: 700;
  font-family: var(--font-mono);
}

.dc-legend {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
  min-width: 0;
  flex: 1;
}

.dc-legend-item {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  font-size: 12px;
}

.dc-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.dc-name {
  color: var(--text-primary);
  font-family: var(--font-mono);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex: 1;
}

.dc-value {
  color: var(--text-secondary);
  font-family: var(--font-mono);
}

.dc-percent {
  color: var(--text-secondary);
  font-family: var(--font-mono);
  min-width: 48px;
  text-align: right;
}
</style>
