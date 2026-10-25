<!-- 游戏 Agent 上报（game.*）：游戏域色（粉）左边线，act 卡形态变体 -->
<script setup lang="ts">
import { relativeTime } from '@/utils/liveFeed';
import type { CardProps } from './registry';

defineProps<CardProps>();
</script>

<template>
  <div class="act is-agent-game" :class="{ 'is-failed': entry.failed }">
    <div class="act-head">
      <span class="act-kind act-kind--game">游戏 Agent</span>
      <span v-if="entry.badge" class="act-badge" :class="{ 'is-success-badge': !entry.failed }">
        {{ entry.badge }}
      </span>
      <span class="grow" />
      <time class="stamp mono">{{ relativeTime(nowMs, entry.tsMs) }}</time>
    </div>
    <p class="act-text">{{ entry.text }}</p>
    <p v-if="entry.note" class="act-note is-game-note">{{ entry.note }}</p>
  </div>
</template>

<style scoped>
.act-note.is-game-note {
  color: var(--text-secondary);
}
</style>
