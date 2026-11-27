<template>
  <section v-if="rows.length" class="rv-panel" aria-label="决策触发原因分布">
    <div class="rv-panel-title">决策触发原因分布</div>
    <div class="bars">
      <div v-for="row in rows" :key="row.reason" class="bar-row">
        <span class="bar-label">{{ triggerReasonLabel(row.reason) }}</span>
        <div class="bar-track">
          <div class="bar-fill" :style="{ width: `${row.pct}%` }" />
        </div>
        <span class="rv-mono bar-count">{{ row.count }}</span>
      </div>
    </div>
    <p class="rv-note">占比按决策总数计；未收录的新触发原因按原值显示（词表为后端封闭枚举）</p>
  </section>
</template>

<script setup lang="ts">
/**
 * 决策触发原因分布面板：全量原因按次数降序的条形列表（纯 div 条形，不上图表库）。
 * 上方指标卡的"决策"卡保留前 3 项一行摘要，本面板是全量视图。
 */
import { computed } from 'vue';
import { triggerReasonLabel } from '@/utils/sessionReview';

const props = defineProps<{
  /** 触发原因（后端原值）→ 次数 */
  distribution: Record<string, number>;
  /** 决策总数（占比分母） */
  total: number;
}>();

const rows = computed(() =>
  Object.entries(props.distribution)
    .sort(([, a], [, b]) => b - a)
    .map(([reason, count]) => ({
      reason,
      count,
      // 极小占比也给最小可见宽度（2%），避免低频原因画成零长条
      pct: Math.max(2, Math.round((count / Math.max(1, props.total)) * 100)),
    })),
);
</script>

<style scoped>
.bars {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.bar-row {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
}

.bar-label {
  flex: none;
  width: 10em;
  color: var(--text-regular);
  text-align: right;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.bar-track {
  flex: 1;
  height: 10px;
  border-radius: 999px;
  background: var(--bg-hover);
  overflow: hidden;
}

.bar-fill {
  height: 100%;
  border-radius: 999px;
  background: var(--color-planner);
}

.bar-count {
  flex: none;
  min-width: 2.5em;
  color: var(--text-secondary);
  text-align: right;
}
</style>
