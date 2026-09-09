<!--
  PulseChart - 轻量 SVG 双系列柱状图（活动脉冲）
  无第三方图表库。纯 inline SVG，使用 viewBox + preserveAspectRatio="none"
  让图表随容器宽度自适应伸缩。设计目标：在首页右列（约 380-420px 宽）展示
  弹幕/发言等近段时间分桶计数，每个分桶并列两根细柱（外宽内窄叠放），
  共享基线，单击/悬停通过原生 <title> 显示分桶明细。
-->
<template>
  <div class="pulse-chart" :style="{ '--pulse-height': `${height}px` }">
    <!-- 图例：色点 + 系列名（HTML，便于排版与点击） -->
    <div v-if="series.length > 0" class="pulse-legend">
      <div v-for="item in series" :key="item.label" class="pulse-legend-item">
        <span class="pulse-dot" :style="{ backgroundColor: item.color }"></span>
        <span class="pulse-legend-label">{{ item.label }}</span>
      </div>
    </div>

    <!--
      viewBox 宽度 = 分桶数，高度固定 100。
      preserveAspectRatio="none" 让 SVG 整体随父容器宽高伸缩；
      每桶宽 1 单位 → 60 桶时实际像素由父容器决定，柱形横向不会过密。
    -->
    <svg
      class="pulse-svg"
      :viewBox="`0 0 ${bucketCount} 100`"
      preserveAspectRatio="none"
      role="img"
      :aria-label="ariaLabel"
    >
      <!-- 基线区域：始终渲染，保证空态也能看到图表形状 -->
      <rect class="pulse-baseline-area" x="0" y="0" width="100%" height="100" />

      <!--
        按分桶分组：每桶一个 <g>，内含两根叠放的柱（外宽后景 / 内窄前景）。
        <title> 提供原生悬停提示，无需额外 JS。
      -->
      <g v-for="bucket in buckets" :key="bucket.index">
        <title>{{ bucket.tooltip }}</title>
        <!-- 外层（系列 0）宽柱：背景层 -->
        <rect
          v-if="bucket.outerHeight > 0"
          class="pulse-bar pulse-bar-outer"
          :x="bucket.outerX"
          :y="bucket.outerY"
          :width="bucket.outerWidth"
          :height="bucket.outerHeight"
          :fill="bucket.outerColor"
        />
        <!-- 内层（系列 1）窄柱：前景层（缺省时跳过，避免无意义图形） -->
        <rect
          v-if="bucket.innerHeight > 0"
          class="pulse-bar pulse-bar-inner"
          :x="bucket.innerX"
          :y="bucket.innerY"
          :width="bucket.innerWidth"
          :height="bucket.innerHeight"
          :fill="bucket.innerColor"
        />
      </g>

      <!-- 基线：1px 横线，叠在柱状底部 -->
      <line class="pulse-baseline" x1="0" :y1="100" :x2="bucketCount" y2="100" />

      <!-- 空态提示：全零时居中显示 -->
      <text
        v-if="isEmpty"
        class="pulse-empty"
        :x="bucketCount / 2"
        y="50"
        text-anchor="middle"
        dominant-baseline="middle"
      >
        {{ emptyText }}
      </text>
    </svg>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';

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
  /** SVG 容器高度（像素），默认 120 */
  height?: number;
  /** 空态文案（所有柱为 0 时） */
  emptyText?: string;
}

const props = withDefaults(defineProps<Props>(), {
  labels: () => [],
  height: 120,
  emptyText: '近段时间暂无活动',
});

/** 分桶数 = 所有系列中最长数组的长度；其他系列按 0 补齐 */
const bucketCount = computed(() => {
  let max = 0;
  for (const s of props.series) {
    if (s.values.length > max) max = s.values.length;
  }
  return Math.max(1, max);
});

/** Y 轴量程：max(1, 全局最大值)；保证零数据也能渲染一片可见基线区域 */
const yMax = computed(() => {
  let max = 0;
  for (const s of props.series) {
    for (const v of s.values) {
      if (v > max) max = v;
    }
  }
  // 顶部留 5% 视觉呼吸量，避免最高柱顶到画布顶部
  return Math.max(1, Math.ceil(max * 1.05));
});

/** 是否空态：所有系列全部为 0 或数组为空 */
const isEmpty = computed(
  () => yMax.value === 1 && props.series.every(s => s.values.every(v => v === 0)),
);

/**
 * 每个分桶的几何与提示文本。
 * - 外柱：宽 0.7 单位，居中
 * - 内柱：宽 0.4 单位，居中（叠放在外柱之上）
 * - 仅有 1 条系列时也使用外柱宽 0.7
 * - 高度按 value / yMax * 100 映射，0 值不出矩形（DOM 节点省略）
 */
interface BucketGeom {
  index: number;
  outerX: number;
  outerY: number;
  outerWidth: number;
  outerHeight: number;
  outerColor: string;
  innerX: number;
  innerY: number;
  innerWidth: number;
  innerHeight: number;
  innerColor: string;
  tooltip: string;
}

const buckets = computed<BucketGeom[]>(() => {
  const count = bucketCount.value;
  const max = yMax.value;
  const seriesList = props.series;

  /** 读取第 i 桶的 series[j] 值；越界返回 0（防御不等长输入） */
  const getValue = (seriesIdx: number, bucketIdx: number): number => {
    const arr = seriesList[seriesIdx]?.values;
    if (!arr) return 0;
    return bucketIdx < arr.length ? arr[bucketIdx] : 0;
  };

  const result: BucketGeom[] = [];
  for (let i = 0; i < count; i++) {
    // 外柱几何（共享 series[0]；缺省时退化为内柱宽）
    const s0Color = seriesList[0]?.color ?? '#888';
    const v0 = getValue(0, i);
    const h0 = (v0 / max) * 100;
    const wOuter = seriesList.length > 1 ? 0.7 : 0.7;

    // 内柱几何（series[1]，若有）
    const s1Color = seriesList[1]?.color ?? s0Color;
    const v1 = getValue(1, i);
    const h1 = (v1 / max) * 100;
    const wInner = 0.4;

    // 桶中心 = i + 0.5
    const center = i + 0.5;

    // tooltip 前缀：labels[i] 缺省用分桶索引
    const prefix = i < props.labels.length ? props.labels[i] : `#${i}`;

    const tooltipParts = [prefix];
    for (let s = 0; s < seriesList.length; s++) {
      tooltipParts.push(`${seriesList[s].label} ${getValue(s, i)}`);
    }

    result.push({
      index: i,
      outerX: center - wOuter / 2,
      outerY: 100 - h0,
      outerWidth: wOuter,
      outerHeight: h0,
      outerColor: s0Color,
      innerX: center - wInner / 2,
      innerY: 100 - h1,
      innerWidth: wInner,
      innerHeight: h1,
      innerColor: s1Color,
      tooltip: tooltipParts.join(' · '),
    });
  }
  return result;
});

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

/* 图例：紧凑横向排列 */
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

/* SVG 画布：占满宽度，按高度撑开；柱形颜色由内联 fill 决定 */
.pulse-svg {
  width: 100%;
  height: var(--pulse-height);
  display: block;
  border-radius: var(--radius-sm);
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
}

/* 基线背景区域：极淡填充，让"零数据"也能看到一片可识别图表框 */
.pulse-baseline-area {
  fill: var(--bg-hover);
  opacity: 0.4;
}

/* 柱形默认无边框；颜色由 prop 注入，避免覆盖内联 fill */
.pulse-bar {
  stroke: none;
}

/* 基线：横贯底部，浅灰 */
.pulse-baseline {
  stroke: var(--border-color);
  stroke-width: 0.05;
  vector-effect: non-scaling-stroke;
}

/* 空态提示：居中、淡色，使用等宽字体让数字与文字视觉对齐 */
.pulse-empty {
  font-size: 4px;
  fill: var(--text-placeholder);
  font-family: var(--font-mono);
  letter-spacing: 0.2px;
}
</style>
