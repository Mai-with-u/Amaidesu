<!-- 递话受理（agent.prompted）：运营/跨 Agent 留言送达目标 -->
<script setup lang="ts">
import { relativeTime } from '@/utils/liveFeed';
import type { CardProps } from './registry';

defineProps<CardProps>();
</script>

<template>
  <div class="prompt-row">
    <div class="act-head">
      <span class="prompt-kind">递话</span>
      <span class="prompt-source">{{ entry.actor }}</span>
      <span v-if="entry.note" class="prompt-target">发给 {{ entry.note }}</span>
      <span class="grow" />
      <time class="stamp mono">{{ relativeTime(nowMs, entry.tsMs) }}</time>
    </div>
    <p class="act-text">{{ entry.text }}</p>
  </div>
</template>

<style scoped>
/* 递话行：干预动作（运营/跨 Agent 留言送达目标），主色左边线区分于 Agent 卡 */
.prompt-row {
  margin-left: 38px; /* 与决策卡同列对齐 */
  max-width: 92%;
  padding: 8px 12px;
  border-radius: var(--radius-md);
  background: var(--bg-hover);
  border-left: 3px solid var(--color-primary);
}

.prompt-kind {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 1.4px;
  color: var(--color-primary);
  flex-shrink: 0;
}

.prompt-source {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-secondary);
  flex-shrink: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 160px;
}

.prompt-target {
  padding: 0 6px;
  border-radius: var(--radius-sm);
  font-size: 11px;
  font-weight: 600;
  background: var(--bg-active);
  color: var(--text-secondary);
  flex-shrink: 0;
  white-space: nowrap;
}
</style>
