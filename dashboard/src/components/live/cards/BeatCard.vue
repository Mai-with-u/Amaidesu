<!-- 环节推进 / 主控权切换 / 场次边界：横贯分隔行（rundown / lead_change / boundary 共用） -->
<script setup lang="ts">
import { computed } from 'vue';
import { formatTimeHMS } from '@/utils/format';
import type { CardProps } from './registry';

const props = defineProps<CardProps>();

/** 横贯行眉标：按条目类型区分场次、主控与环节 */
const eyebrow = computed<string>(() => {
  if (props.entry.kind === 'boundary') return '场次';
  if (props.entry.kind === 'lead_change') return '主控';
  return '环节';
});
</script>

<template>
  <div class="beat" :class="{ 'is-lead': entry.kind === 'lead_change' }">
    <span class="beat-rule" aria-hidden="true" />
    <span class="beat-body">
      <span class="beat-eyebrow">{{ eyebrow }}</span>
      <span class="beat-label">{{ entry.text }}</span>
      <span v-if="entry.badge" class="beat-action">{{ entry.badge }}</span>
      <span v-if="entry.note" class="beat-note">{{ entry.note }}</span>
    </span>
    <span class="beat-rule" aria-hidden="true" />
    <time class="stamp mono">{{ formatTimeHMS(entry.tsMs) }}</time>
  </div>
</template>

<style scoped>
.beat {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 6px 0;
}
.beat-rule {
  height: 1px;
  background: var(--color-rundown);
  opacity: 0.45;
}
.beat-rule:first-child {
  width: 24px;
  flex-shrink: 0;
}
.beat-rule:last-of-type {
  flex: 1;
}
.beat-body {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.beat-eyebrow {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 1.6px;
  color: var(--color-rundown);
  flex-shrink: 0;
}
.beat-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.beat-action {
  padding: 0 7px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 700;
  background: var(--color-rundown-bg);
  color: var(--color-rundown);
  flex-shrink: 0;
}
.beat-note {
  font-size: 11px;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
/* 主控权切换：用 Agent 语义色（紫）与流程单环节（琥珀）视觉区分 */
.beat.is-lead .beat-rule {
  background: var(--color-agent);
}
.beat.is-lead .beat-eyebrow {
  color: var(--color-agent);
}
.beat.is-lead .beat-action {
  background: var(--color-agent-bg);
  color: var(--color-agent);
}
</style>
