<template>
  <section class="rv-panel">
    <div class="rv-panel-title">单笔输入水位（千 tokens，按 Profile）</div>
    <div ref="chartRef" class="waterline-chart"></div>
  </section>
</template>

<script setup lang="ts">
/**
 * 单笔输入水位图：时间轴 + 按 profile 多折线（跨场断线留空隙）。
 * 折线颜色按主题域色取值（useChartPalette 随主题重算），未知 profile
 * 依次取兜底域色；取色入口与 LLM 用量表色圈共用（utils/sessionReview）。
 */
import { computed, ref } from 'vue';
import type { EChartsCoreOption } from 'echarts/core';
import type { WaterlineSeries } from '@/types';
import { buildWaterlineData, waterlineColorForProfile } from '@/utils/sessionReview';
import { useChartPalette, useECharts } from '@/composables/useECharts';

const props = defineProps<{ series: WaterlineSeries[] }>();

const chartRef = ref<HTMLElement | null>(null);
const palette = useChartPalette();

// profile 名全集：图表折线与用表色圈共用同一取色入口，保证同 profile 同色
const profileNames = computed<string[]>(() => props.series.map(s => s.profile_name));

function profileColor(profile: string): string {
  return waterlineColorForProfile(profile, profileNames.value, palette.value.readVar);
}

// 水位图 option：时间轴 + 按 profile 多折线（null 断线留空隙）
const chartOption = computed<EChartsCoreOption | null>(() => {
  if (!props.series.length) return null;
  const data = buildWaterlineData(props.series);
  return {
    backgroundColor: 'transparent',
    textStyle: { color: palette.value.textColor, fontFamily: palette.value.monoFont },
    tooltip: {
      trigger: 'axis',
      formatter: (params: unknown) => {
        const list = params as Array<{ seriesName: string; value: [number, number | null] }>;
        return list
          .filter(p => p.value?.[1] !== null && p.value?.[1] !== undefined)
          .map(p => `${p.seriesName}: ${p.value[1]}k tokens`)
          .join('<br/>');
      },
    },
    legend: { textStyle: { color: palette.value.textColor } },
    grid: { left: 60, right: 20, top: 36, bottom: 40 },
    xAxis: {
      type: 'time',
      axisLabel: { color: palette.value.textColor },
      splitLine: { show: false },
      axisLine: { lineStyle: { color: palette.value.borderColor } },
    },
    yAxis: {
      type: 'value',
      name: '千 tokens',
      nameTextStyle: { color: palette.value.textColor },
      axisLabel: { color: palette.value.textColor },
      splitLine: { lineStyle: { color: palette.value.borderColorLight } },
    },
    series: data.map(s => ({
      name: s.profile,
      type: 'line' as const,
      showSymbol: false,
      connectNulls: false,
      data: s.data,
      lineStyle: { color: profileColor(s.profile), width: 1.5 },
      itemStyle: { color: profileColor(s.profile) },
    })),
  };
});

// useECharts 需要非空 option：无数据时给空 option（空态由 waterline 图例区自然留白）
const safeChartOption = computed<EChartsCoreOption>(() => chartOption.value ?? {});
useECharts(chartRef, safeChartOption);
</script>

<style scoped>
.waterline-chart {
  height: 260px;
}
</style>
