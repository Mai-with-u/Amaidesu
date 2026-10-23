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
  /** 多系列图表的默认取色圈（主色 + 域色板，与全局色板同族） */
  seriesColors: string[];
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
      seriesColors: [
        read('--color-primary'),
        read('--color-collector'),
        read('--color-tool'),
        read('--color-rundown'),
        read('--color-agent'),
        read('--color-danger'),
        read('--color-planner'),
        read('--color-game'),
      ],
    };
  });
}

/**
 * 在容器元素上驱动一个 ECharts 实例：
 * 挂载时 init + setOption，容器尺寸变化时 resize，option 变化时增量更新，卸载时 dispose。
 * 容器允许晚于组件挂载出现（外层 v-if 等数据到达才渲染）：ref 由 null 变为元素时补初始化。
 */
export function useECharts(
  container: Ref<HTMLElement | null>,
  option: Ref<echarts.EChartsCoreOption>,
): void {
  const chart = shallowRef<echarts.ECharts | null>(null);
  let observer: ResizeObserver | null = null;

  const mount = (el: HTMLElement | null | undefined) => {
    if (!el) return;
    if (chart.value) {
      if (chart.value.getDom() === el) return;
      // 容器被 v-if 整体替换时，旧实例绑定的 DOM 已脱挂，重建
      observer?.disconnect();
      chart.value.dispose();
    }
    chart.value = echarts.init(el);
    chart.value.setOption(option.value);
    observer = new ResizeObserver(() => chart.value?.resize());
    observer.observe(el);
  };

  onMounted(() => mount(container.value));
  watch(container, el => mount(el));

  watch(option, next => chart.value?.setOption(next), { deep: true });

  onBeforeUnmount(() => {
    observer?.disconnect();
    observer = null;
    chart.value?.dispose();
    chart.value = null;
  });
}
