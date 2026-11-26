<template>
  <section class="rv-panel" aria-label="游戏事件明细">
    <div class="rv-panel-title">游戏事件明细</div>
    <ul class="game-items">
      <li v-for="(item, i) in game.items" :key="i" class="game-item">
        <span class="rv-mono game-time">{{ formatTimeHMS(item.timestamp_ms) }}</span>
        <span class="game-msg">[{{ item.game }}/{{ item.event_type }}] {{ item.message }}</span>
        <router-link class="rv-link game-jump" :to="jumpLink(item.timestamp_ms)"
          >跳转 →</router-link
        >
      </li>
    </ul>
    <p v-if="game.truncated" class="rv-note">
      明细仅显示最近 {{ game.items.length }} 条（本场共 {{ game.total }} 条）
    </p>
  </section>
</template>

<script setup lang="ts">
/**
 * 游戏事件明细面板：逐条锚定跳转回看（/live?session=&at=时刻）。
 * 单场语义（合并视图明细为空，页面不传本卡数据）。
 */
import type { SessionReviewResponse } from '@/types';
import { formatTimeHMS } from '@/utils/format';
import { consoleReplayLink } from '@/utils/sessionReview';

const props = defineProps<{
  /** 场内游戏事件聚合（读 items/truncated） */
  game: SessionReviewResponse['game'];
  /** 明细归属的场次 id（逐条跳转的回看目标） */
  sessionId: number;
}>();

function jumpLink(atMs: number): string {
  return consoleReplayLink(props.sessionId, atMs);
}
</script>

<style scoped>
.game-items {
  list-style: none;
  margin: 0;
  padding: 0;
  max-height: 320px;
  overflow-y: auto;
}

.game-item {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 5px 0;
  border-bottom: 1px solid var(--border-color-light);
  font-size: 13px;
}

.game-item:last-child {
  border-bottom: none;
}

.game-time {
  color: var(--text-secondary);
  font-size: 12px;
  flex-shrink: 0;
}

.game-msg {
  flex: 1;
  min-width: 0;
}

.game-jump {
  flex-shrink: 0;
  font-size: 12px;
}
</style>
