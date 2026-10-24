<!-- 里程碑：庆祝行（game.milestone） -->
<script setup lang="ts">
import { relativeTime } from '@/utils/liveFeed';
import type { CardProps } from './registry';

defineProps<CardProps>();
</script>

<template>
  <div class="milestone">
    <div class="milestone-body">
      <p class="milestone-text">{{ entry.text }}</p>
      <p v-if="entry.note" class="milestone-meta mono">{{ entry.note }}</p>
    </div>
    <time class="stamp mono">{{ relativeTime(nowMs, entry.tsMs) }}</time>
  </div>
</template>

<style scoped>
.milestone {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 4px 0;
  padding: 8px 14px;
  border-radius: var(--radius-md);
  background: var(--color-game-bg);
  border: 1px dashed var(--color-game);
}
.milestone-body {
  flex: 1;
  min-width: 0;
}
.milestone-text {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  word-break: break-word;
}
.milestone-meta {
  margin: 2px 0 0;
  font-size: 11px;
  color: var(--text-secondary);
}
</style>
