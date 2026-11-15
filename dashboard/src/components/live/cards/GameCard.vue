<!-- 游戏 Agent 上报（game.*）：白底 + 游戏域色（粉）左边线——上报是"通知"，
     不与响应卡（模型发言，粉底）同款；失败（error）仍走 .act.is-failed 红底。
     attention_required 随带 body_events 时渲染成缩进小列表（原始事实清单） -->
<script setup lang="ts">
import { computed } from 'vue';
import { isRecord, str } from '@/utils/guards';
import { formatTimeHMS } from '@/utils/format';
import type { CardProps } from './registry';

const props = defineProps<CardProps>();

/** body_events：上报载荷随带的原始身体事件清单（成员含 event_type/message/cursor）。
 *  非对象成员与缺 message 的条目安全跳过，空清单不渲染 */
const bodyEvents = computed(() => {
  const raw = isRecord(props.entry.detail) ? props.entry.detail.body_events : null;
  if (!Array.isArray(raw)) return [];
  return raw.flatMap(item => {
    if (!isRecord(item)) return [];
    const message = str(item.message);
    if (!message) return [];
    return [{ eventType: str(item.event_type), message }];
  });
});
</script>

<template>
  <div class="act is-agent-game" :class="{ 'is-failed': entry.failed }">
    <div class="act-head">
      <span class="act-kind act-kind--game">游戏 Agent</span>
      <span v-if="entry.badge" class="act-badge" :class="{ 'is-success-badge': !entry.failed }">
        {{ entry.badge }}
      </span>
      <span class="grow" />
      <time class="stamp mono">{{ formatTimeHMS(entry.tsMs) }}</time>
    </div>
    <p class="act-text">{{ entry.text }}</p>
    <p v-if="entry.note" class="act-note is-game-note">{{ entry.note }}</p>
    <ul v-if="bodyEvents.length > 0" class="report-body-events">
      <li v-for="(item, i) in bodyEvents" :key="i" class="report-body-event">
        <span v-if="item.eventType" class="report-body-event-type mono">{{ item.eventType }}</span>
        <span class="report-body-event-text">{{ item.message }}</span>
      </li>
    </ul>
  </div>
</template>

<style scoped>
/* 上报卡白底化：通知类卡不与模型发言（粉底）同款，游戏色只留在左边线。
   :not(.is-failed) 保证失败（error）卡仍走 .act.is-failed 的红底 */
.act.is-agent-game:not(.is-failed) {
  background: var(--bg-card);
}
.act-note.is-game-note {
  color: var(--text-secondary);
}

/* body_events 原始事实清单：缩进小列表，event_type 作前缀小字 */
.report-body-events {
  margin: 6px 0 0;
  padding: 4px 0 0 10px;
  border-top: 1px solid var(--border-color-light);
  list-style: none;
}
.report-body-event {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-size: 11px;
  line-height: 1.6;
  color: var(--text-secondary);
  word-break: break-word;
}
.report-body-event-type {
  flex-shrink: 0;
  font-size: 10px;
  color: var(--text-placeholder);
}
.report-body-event-text {
  min-width: 0;
}
</style>
