<!-- 环节推进 / 场次边界：横贯分隔行（rundown / boundary 两类共用） -->
<script setup lang="ts">
import { relativeTime } from '@/utils/liveFeed';
import type { CardProps } from './registry';

defineProps<CardProps>();
</script>

<template>
  <div class="beat">
    <span class="beat-rule" aria-hidden="true" />
    <span class="beat-body">
      <span class="beat-eyebrow">{{ entry.kind === 'boundary' ? '场次' : '环节' }}</span>
      <span class="beat-label">{{ entry.text }}</span>
      <span v-if="entry.badge" class="beat-action">{{ entry.badge }}</span>
      <span v-if="entry.note" class="beat-note">{{ entry.note }}</span>
    </span>
    <span class="beat-rule" aria-hidden="true" />
    <time class="stamp mono">{{ relativeTime(nowMs, entry.tsMs) }}</time>
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
</style>
