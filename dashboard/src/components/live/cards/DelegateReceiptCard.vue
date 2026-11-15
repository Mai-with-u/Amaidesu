<!-- 收活卡：游戏 Agent 按 task.changed 受理回执认领某次派活的轻量小卡。
     头行"收到派活 ← <派活时刻>"标注来处，次行是那次派活的指令摘要（20 字截断）。
     白底 + 游戏色左边线，与 OthersThingsCard 同级；整卡可点，跳回主播列对应的
     framework_delegate 卡（跳转由双轨外壳 LiveLanes 的卡片点击处理，本卡只展示） -->
<script setup lang="ts">
import { formatTimeHMS } from '@/utils/format';
import type { CardProps } from './registry';

defineProps<CardProps>();
</script>

<template>
  <div class="delegate-receipt">
    <div class="delegate-receipt-head">
      <span class="delegate-receipt-label">收到派活</span>
      <span class="delegate-receipt-arrow">←</span>
      <time class="stamp mono">{{ formatTimeHMS(entry.delegateTsMs ?? entry.tsMs) }}</time>
    </div>
    <p class="delegate-receipt-text">{{ entry.text }}</p>
  </div>
</template>

<style scoped>
.delegate-receipt {
  margin-left: 38px; /* 与决策卡同列对齐 */
  max-width: 92%;
  padding: 5px 12px;
  border-radius: var(--radius-md);
  background: var(--bg-card);
  border-left: 2px solid var(--color-game);
}
.delegate-receipt-head {
  display: flex;
  align-items: center;
  gap: 6px;
}
.delegate-receipt-label {
  font-size: 11px;
  font-weight: 700;
  color: var(--color-game);
}
.delegate-receipt-arrow {
  font-size: 11px;
  color: var(--text-placeholder);
}
.delegate-receipt-text {
  margin: 2px 0 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--text-regular);
  word-break: break-word;
}
</style>
