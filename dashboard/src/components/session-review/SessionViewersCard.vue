<template>
  <section class="rv-panel" aria-label="观众构成">
    <div class="rv-panel-title">观众构成</div>
    <div class="rv-chips">
      <span class="rv-chip rv-mono">发言观众 {{ viewers.active_total }}</span>
      <span class="rv-chip rv-mono">新观众 {{ viewers.new_viewers }}</span>
      <span class="rv-chip rv-mono">回头观众 {{ viewers.returning_viewers }}</span>
    </div>
    <ol v-if="viewers.top_speakers.length" class="top-speakers">
      <li v-for="sp in viewers.top_speakers" :key="sp.viewer_id" class="top-speaker">
        <span class="top-name">{{ sp.user_name || `观众 #${sp.viewer_id}` }}</span>
        <span class="grow" />
        <span class="rv-mono top-count">{{ sp.message_count }} 条</span>
      </li>
    </ol>
    <p class="rv-note">新/回头按历史首条发言时刻判定（首条在本场 = 新观众）</p>
  </section>
</template>

<script setup lang="ts">
/** 观众构成面板：发言观众 / 新·回头观众 / 发言最多观众。单场语义（合并视图页面不传本卡数据）。 */
import type { ReviewViewers } from '@/types';

defineProps<{ viewers: ReviewViewers }>();
</script>

<style scoped>
.top-speakers {
  list-style: none;
  margin: 12px 0 0;
  padding: 0;
}

.top-speaker {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 0;
  border-bottom: 1px solid var(--border-color-light);
  font-size: 13px;
}

.top-speaker:last-child {
  border-bottom: none;
}

.grow {
  flex: 1;
}

.top-count {
  color: var(--text-secondary);
  font-size: 12px;
}
</style>
