<!--
  组件管理页共享状态摘要条：label + value 成对的状态 chip 行。

  采集器 / Agent / 工具三页共用的元信息条。chip 的着色语义（是/否、
  新鲜/滞后/失活、强调、警示）在此只有一份实现；页面通过 chips 注入
  自己的指标，刷新按钮等条尾控件走 append 插槽。
-->
<template>
  <div class="md-details-strip" aria-label="状态摘要">
    <div
      v-for="chip in chips"
      :key="chip.label"
      class="md-stat-chip"
      :class="{
        'md-stat-chip--accent': chip.accent,
        'md-stat-chip--warning': chip.warning,
      }"
    >
      <span class="md-chip-label">{{ chip.label }}</span>
      <span
        class="md-chip-value"
        :class="[chip.tone ? `md-chip-${chip.tone}` : '', chip.tone === 'mono' ? 'mono' : '']"
        :title="chip.title"
      >
        {{ chip.value }}
      </span>
    </div>
    <slot name="append" />
  </div>
</template>

<script setup lang="ts">
/** 单枚状态 chip：label 固定样式，value 按 tone 着色 */
export interface StatChip {
  label: string;
  value: string;
  /**
   * 着色语义（对应共享 CSS 的 md-chip-* 类）：yes=强调绿 / no=弱化红 /
   * mono=等宽中性 / ok=新鲜绿 / stale=滞后黄 / dead=失活红
   */
  tone?: 'yes' | 'no' | 'mono' | 'ok' | 'stale' | 'dead';
  /** 强调色底（各页当前选中对象的招牌指标） */
  accent?: boolean;
  /** 警示底（待重启这类需要留意的计数） */
  warning?: boolean;
  /** 悬停说明 */
  title?: string;
}

defineProps<{
  chips: StatChip[];
}>();
</script>
