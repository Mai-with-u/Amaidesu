<!-- others_things 叙事小卡：游戏世界里"动了别人的东西 / 拿了别人家的吃的"两类
     富叙事（事实 + 认账口径）。轻量双行：第 1 行实词徽标 + 时间，第 2 行叙事
     全文自动换行（不截断）。白底 + 游戏色左边线；与上报卡的区分是本卡没有
     徽标块与 note，只有正文 -->
<script setup lang="ts">
import { formatTimeHMS } from '@/utils/format';
import type { CardProps } from './registry';

defineProps<CardProps>();
</script>

<template>
  <div class="others-things">
    <div class="others-things-head">
      <span v-if="entry.badge" class="others-things-badge">{{ entry.badge }}</span>
      <span class="grow" />
      <time class="stamp mono">{{ formatTimeHMS(entry.tsMs) }}</time>
    </div>
    <p class="others-things-text">{{ entry.text }}</p>
  </div>
</template>

<style scoped>
.others-things {
  margin-left: 38px; /* 与决策卡同列对齐 */
  max-width: 92%;
  padding: 6px 12px;
  border-radius: var(--radius-md);
  background: var(--bg-card);
  border-left: 2px solid var(--color-game);
}
.others-things-head {
  display: flex;
  align-items: center;
  gap: 6px;
}
.others-things-badge {
  font-size: 11px;
  font-weight: 700;
  color: var(--color-game);
}
.others-things-text {
  margin: 2px 0 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--text-regular);
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
