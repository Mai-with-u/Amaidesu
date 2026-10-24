<!-- 会话模式过程折叠条：每轮过程行折叠成一条，点击展开该轮过程（合成条目，不对应事件） -->
<script setup lang="ts">
import type { ShowEntry } from '@/utils/liveFeed';
import { useTimelineCardHost } from './context';

defineProps<{ entry: ShowEntry }>();

const host = useTimelineCardHost();
</script>

<template>
  <button
    type="button"
    class="chat-process-strip"
    :aria-expanded="host.isChatGroupExpanded(entry.id)"
    @click="host.toggleChatGroup(entry.id)"
  >
    <span class="chat-process-summary">{{ entry.note }} 个过程记录 · {{ entry.text }}</span>
    <span class="grow" />
    <span class="chat-process-action">{{
      host.isChatGroupExpanded(entry.id) ? '收起' : '展开'
    }}</span>
    <span class="chat-process-arrow" aria-hidden="true">▸</span>
  </button>
</template>

<style scoped>
.chat-process-strip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  width: fit-content;
  max-width: min(78%, 560px);
  padding: 4px 12px;
  border: 1px solid var(--border-color-light);
  border-radius: 999px;
  background: var(--bg-hover);
  font-family: inherit;
  font-size: 11px;
  color: var(--text-secondary);
  cursor: pointer;
  transition:
    background var(--transition-fast),
    border-color var(--transition-fast);
}
.chat-process-strip:hover {
  border-color: var(--color-agent);
  color: var(--text-primary);
}
.chat-process-strip[aria-expanded='true'] {
  background: var(--color-agent-bg);
  border-color: var(--color-agent);
  color: var(--color-agent);
}
.chat-process-action,
.chat-process-arrow {
  flex-shrink: 0;
}
.chat-process-action {
  font-size: 11px;
  color: var(--text-placeholder);
}
.chat-process-arrow {
  font-size: 11px;
  transition: transform var(--transition-fast);
}
.chat-process-strip[aria-expanded='true'] .chat-process-arrow {
  transform: rotate(90deg);
}
</style>
