<!--
  ModelCostDonut - 手写 SVG 环形占比图（无第三方图表库）

  用 stroke-dasharray 分段圆环展示各模型费用占比，中心放总额；
  图例为 HTML 侧栏（名称 + 金额 + 占比）。悬停提示走原生 <title>。
-->
<template>
  <div class="donut-chart">
    <svg :width="size" :height="size" role="img" aria-label="模型费用占比环形图">
      <!-- 底环：空态或占比不满时提供视觉基座 -->
      <circle
        :cx="center"
        :cy="center"
        :r="radius"
        fill="none"
        class="dc-track"
        :stroke-width="strokeWidth"
      />
      <g :transform="`rotate(-90 ${center} ${center})`">
        <circle
          v-for="segment in segments"
          :key="segment.model"
          :cx="center"
          :cy="center"
          :r="radius"
          fill="none"
          :stroke="segment.color"
          :stroke-width="strokeWidth"
          :stroke-dasharray="`${segment.length} ${circumference - segment.length}`"
          :stroke-dashoffset="segment.offset"
        >
          <title>{{ segment.tooltip }}</title>
        </circle>
      </g>
      <text :x="center" :y="center - 6" text-anchor="middle" class="dc-total-label">
        {{ totalLabel }}
      </text>
      <text :x="center" :y="center + 12" text-anchor="middle" class="dc-total-value">
        {{ totalText }}
      </text>
    </svg>

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
import { computed } from 'vue';

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

const strokeWidth = 18;
const center = props.size / 2;
const radius = (props.size - strokeWidth) / 2 - 2;
const circumference = 2 * Math.PI * radius;

const total = computed(() => props.items.reduce((sum, item) => sum + Math.max(0, item.value), 0));

interface DonutSegment {
  model: string;
  length: number;
  offset: number;
  color: string;
  tooltip: string;
}

/** 圆环分段：dashoffset 沿圆周累进（SVG dashoffset 正值逆时针回退，形成首尾相接） */
const segments = computed<DonutSegment[]>(() => {
  if (total.value <= 0) return [];
  let consumed = 0;
  return props.items.map((item, index) => {
    const ratio = Math.max(0, item.value) / total.value;
    const segment: DonutSegment = {
      model: item.name,
      length: ratio * circumference,
      offset: -consumed * circumference,
      color: props.colors[index % props.colors.length],
      tooltip: `${item.name}：${props.formatValue(item.value)}（${(ratio * 100).toFixed(1)}%）`,
    };
    consumed += ratio;
    return segment;
  });
});

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

const totalText = computed(() => props.formatValue(total.value));
</script>

<style scoped>
.donut-chart {
  display: flex;
  align-items: center;
  gap: var(--spacing-lg);
  flex-wrap: wrap;
}

.dc-track {
  stroke: var(--bg-hover);
}

.dc-total-label {
  fill: var(--text-secondary);
  font-size: 11px;
}

.dc-total-value {
  fill: var(--text-primary);
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
