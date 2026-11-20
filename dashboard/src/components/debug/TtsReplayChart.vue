<template>
  <div class="replay-chart" role="img" aria-label="每句耗时趋势图">
    <div ref="containerRef" class="replay-chart-canvas"></div>
  </div>
</template>

<script setup lang="ts">
// TTS 整场回放每句耗时趋势图：一句一点（单位秒）。
// 有锚点分段数据时画两条线——"首声延迟"（等开口，流式合成的代价）与
// "总耗时"（两线之间即出声期），各带均值虚线标尺；无分段数据（仅合成
// 测速 / 事件缺席）回退单条总耗时线。失败句无耗时断线（null）。
// 数据只读自回放结果（results 逐句 push，computed 随之重算，图表逐句增长）；
// 色板随主题（useChartPalette）；内部口径毫秒、显示换算秒。
import { computed, ref } from 'vue';
import type { EChartsCoreOption } from 'echarts/core';
import { useChartPalette, useECharts } from '@/composables/useECharts';
import { formatSeconds } from '@/utils/format';
import type { ReplayLineResult } from '@/composables/useTtsReplay';

const props = defineProps<{
  /** 已回放句的结果（顺序即句序） */
  results: ReplayLineResult[];
  /** 成功句平均总耗时（毫秒），画平均总耗时标尺线 */
  avgMs: number;
  /** 成功句平均首声延迟（毫秒）；无分段数据为 null，不画首声标尺线 */
  avgFirstAudioMs: number | null;
}>();

const palette = useChartPalette();
const containerRef = ref<HTMLElement | null>(null);

/** 任一句带锚点分段即按双线渲染；否则回退单条总耗时线 */
const hasTimeline = computed(() => props.results.some(r => r.firstAudioMs !== null || r.playbackMs !== null));

function toSeconds(values: Array<number | null>): Array<number | null> {
  return values.map(v => (v === null ? null : v / 1000));
}

const option = computed<EChartsCoreOption>(() => {
  const labels = props.results.map((_, index) => index + 1);
  const series: Record<string, unknown>[] = [];
  if (hasTimeline.value) {
    series.push({
      name: '首声延迟',
      type: 'line',
      data: toSeconds(props.results.map(r => r.firstAudioMs)),
      symbol: 'none',
      lineStyle: { width: 1.5, color: palette.value.collectorColor },
      itemStyle: { color: palette.value.collectorColor },
      markLine: averageMarkLine(props.avgFirstAudioMs, '平均首声', palette.value.collectorColor),
    });
  }
  series.push({
    name: hasTimeline.value ? '总耗时' : '耗时',
    type: 'line',
    data: toSeconds(props.results.map(r => r.elapsedMs)),
    symbol: 'none',
    lineStyle: { width: 1.5, color: palette.value.primaryColor },
    itemStyle: { color: palette.value.primaryColor },
    markLine: averageMarkLine(props.avgMs, '平均总', palette.value.primaryColor),
  });
  return {
    grid: { left: 56, right: 24, top: hasTimeline.value ? 28 : 16, bottom: 28, containLabel: false },
    legend: hasTimeline.value
      ? {
          data: ['首声延迟', '总耗时'],
          top: 0,
          right: 0,
          itemWidth: 14,
          itemHeight: 2,
          textStyle: { color: palette.value.textColor, fontSize: 11 },
        }
      : undefined,
    tooltip: { trigger: 'axis', formatter: (params: unknown) => tooltipFormatter(params) },
    xAxis: {
      type: 'category',
      data: labels,
      axisLabel: { color: palette.value.textColor },
      axisLine: { lineStyle: { color: palette.value.borderColorLight } },
    },
    yAxis: {
      type: 'value',
      name: '秒',
      nameTextStyle: { color: palette.value.textColor },
      axisLabel: { color: palette.value.textColor },
      splitLine: { lineStyle: { color: palette.value.borderColorLight } },
    },
    series,
  };
});

function averageMarkLine(avgMs: number | null, label: string, color: string): Record<string, unknown> | undefined {
  if (avgMs === null || avgMs <= 0) return undefined;
  return {
    silent: true,
    symbol: 'none',
    data: [{ yAxis: avgMs / 1000 }],
    lineStyle: { color, type: 'dashed', width: 1 },
    label: { formatter: `${label} ${formatSeconds(avgMs)}`, position: 'insideEndTop', color },
  };
}

type TooltipPoint = { dataIndex: number; seriesName: unknown; value: number | null };

function tooltipFormatter(rawParams: unknown): string {
  const params = rawParams as TooltipPoint[];
  const first = params[0];
  if (!first) return '';
  const index = first.dataIndex;
  const line = props.results[index];
  if (!line) return '';
  const head = `第 ${index + 1} 句（${line.chars} 字）`;
  if (!line.success) return `${head}<br/>失败`;
  if (hasTimeline.value && line.firstAudioMs !== null && line.playbackMs !== null) {
    return `${head}<br/>首声 ${formatSeconds(line.firstAudioMs)} · 出声 ${formatSeconds(
      line.playbackMs,
    )} · 计 ${formatSeconds(line.elapsedMs ?? 0)}`;
  }
  return `${head}<br/>${formatSeconds(line.elapsedMs ?? 0)}`;
}

useECharts(containerRef, option);
</script>

<style scoped>
.replay-chart {
  height: 180px;
}

.replay-chart-canvas {
  width: 100%;
  height: 100%;
}
</style>
