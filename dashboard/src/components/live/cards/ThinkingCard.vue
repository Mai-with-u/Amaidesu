<!-- 思考行：ReAct 各步/生成段思考流（视图层合成条目）。默认折叠只留单行预览，
     流式期间预览实时增长；展开看全文 -->
<script setup lang="ts">
import { relativeTime } from '@/utils/liveFeed';
import type { CardProps } from './registry';

defineProps<CardProps>();
</script>

<template>
  <details class="think">
    <summary>
      <span class="think-caret" aria-hidden="true">▸</span>
      <span class="think-label">{{ entry.actor }}</span>
      <span class="think-preview">{{ entry.text }}</span>
      <span class="grow" />
      <time class="stamp mono">{{ relativeTime(nowMs, entry.tsMs) }}</time>
    </summary>
    <pre class="think-body">{{ entry.text }}</pre>
  </details>
</template>

<style scoped>
.think {
  margin-left: 38px; /* 28px 头像 + 10px 间距：与气泡体对齐 */
  max-width: 92%;
  min-width: 0;
}
.think summary {
  display: flex;
  align-items: center;
  gap: 6px;
  min-height: 18px;
  cursor: pointer;
  user-select: none;
  list-style: none;
}
.think summary::-webkit-details-marker {
  display: none;
}
.think-caret {
  font-size: 11px;
  color: var(--text-placeholder);
  transition: transform var(--transition-fast);
  flex-shrink: 0;
}
.think[open] .think-caret {
  transform: rotate(90deg);
}
.think-label {
  padding: 0 5px;
  border-radius: var(--radius-sm);
  background: var(--color-agent-bg);
  color: var(--color-agent);
  font-size: 9px;
  font-weight: 600;
  line-height: 16px;
  white-space: nowrap;
  flex-shrink: 0;
}
.think-preview {
  font-size: 11px;
  color: var(--text-placeholder);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}
.think[open] .think-preview {
  display: none;
}
.think-body {
  margin: 6px 0 0;
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  background: var(--bg-hover);
  font-family: inherit;
  font-size: 11px;
  line-height: 1.6;
  color: var(--text-secondary);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 220px;
  overflow-y: auto;
}
</style>
