/**
 * ECharts 生命周期承接：init / setOption / 容器自适应 / 卸载释放。
 * 按需注册图表与组件（echarts/core），保持 tree-shaking。
 */
import { computed, onBeforeUnmount, onMounted, shallowRef, watch, type Ref } from 'vue';
import * as echarts from 'echarts/core';
import { BarChart, LineChart, PieChart } from 'echarts/charts';
import { GridComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { useThemeStore } from '@/stores/theme';

echarts.use([BarChart, LineChart, PieChart, GridComponent, TooltipComponent, CanvasRenderer]);

/** 图表内可用的主题色板；canvas 不解析 CSS 变量，需运行时读取实际色值 */
export interface ChartPalette {
  textColor: string;
  placeholderColor: string;
  borderColor: string;
  borderColorLight: string;
  hoverBg: string;
  monoFont: string;
  primaryColor: string;
  collectorColor: string;
  agentColor: string;
}

/**
 * 解析当前主题的图表色板。computed 依赖 themeStore.theme，
 * 主题切换时引用方 option 重算，图表随之换色。
 */
export function useChartPalette(): Ref<ChartPalette> {
  const themeStore = useThemeStore();
  return computed<ChartPalette>(() => {
    void themeStore.theme;
    const style = getComputedStyle(document.documentElement);
    const read = (name: string): string => style.getPropertyValue(name).trim();
    return {
      textColor: read('--text-secondary'),
      placeholderColor: read('--text-placeholder'),
      borderColor: read('--border-color'),
      borderColorLight: read('--border-color-light'),
      hoverBg: read('--bg-hover'),
      monoFont: read('--font-mono'),
      primaryColor: read('--color-primary'),
      collectorColor: read('--color-collector'),
      agentColor: read('--color-agent'),
    };
  });
}

/**
 * 在容器元素上驱动一个 ECharts 实例：
 * 挂载时 init + setOption，容器尺寸变化时 resize，option 变化时增量更新，卸载时 dispose。
 */
export function useECharts(
  container: Ref<HTMLElement | null>,
  option: Ref<echarts.EChartsCoreOption>,
): void {
  const chart = shallowRef<echarts.ECharts | null>(null);
  let observer: ResizeObserver | null = null;

  onMounted(() => {
    const el = container.value;
    if (!el) return;
    chart.value = echarts.init(el);
    chart.value.setOption(option.value);
    observer = new ResizeObserver(() => chart.value?.resize());
    observer.observe(el);
  });

  watch(option, next => chart.value?.setOption(next), { deep: true });

  onBeforeUnmount(() => {
    observer?.disconnect();
    observer = null;
    chart.value?.dispose();
    chart.value = null;
  });
}
