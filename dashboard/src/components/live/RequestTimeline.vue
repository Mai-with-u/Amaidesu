<template>
  <div class="request-timeline">
    <p v-if="records.length === 0" class="empty">{{ emptyText }}</p>
    <article
      v-for="record in records"
      :key="record.id"
      class="rec"
      :class="[`is-${record.outcome}`, { 'is-loose': record.loose }]"
    >
      <header class="rec-head">
        <span class="rec-clock mono">{{ formatTimeHMS(record.tsMs) }}</span>
        <span v-if="record.trigger" class="rec-trigger">{{ record.trigger }}</span>
        <span v-else-if="record.loose" class="rec-trigger is-muted">未经过决策</span>
        <span v-if="record.durationMs !== null" class="rec-dur mono">{{
          formatLatency(record.durationMs)
        }}</span>
        <span v-if="record.toolCount > 0" class="rec-tools">工具 {{ record.toolCount }}</span>
        <span v-if="record.outcomeLabel" class="rec-outcome" :class="`is-${record.outcome}`">{{
          record.outcomeLabel
        }}</span>
        <span class="grow" />
        <a
          v-if="record.llmRequestId"
          class="rec-link"
          :href="`/llm/history?request_id=${record.llmRequestId}`"
          >完整请求 ↗</a
        >
        <button type="button" class="rec-toggle" @click="toggle(record.id)">
          {{ collapsed.has(record.id) ? '展开' : '收起' }}
        </button>
      </header>

      <div v-show="!collapsed.has(record.id)" class="rec-body">
        <button
          v-for="message in record.messages"
          :key="message.id"
          type="button"
          class="msg"
          :class="[
            `is-${message.kind}`,
            { 'is-failed': message.failed, 'is-selected': message.id === selectedId },
          ]"
          @click="emit('select', record, message)"
        >
          <span class="msg-clock mono">{{ formatTimeHMS(message.tsMs) }}</span>
          <span class="msg-label">{{ message.label }}</span>
          <span class="msg-body">
            <span v-if="message.toolName" class="msg-tool mono">{{ message.toolName }}</span>
            <span class="msg-text" :title="message.text">{{ message.text }}</span>
          </span>
        </button>
      </div>
    </article>
  </div>
</template>

<script setup lang="ts">
// 决策轨迹：一次请求一条记录，记录内一行一条消息（与主流 agent 记录同形）。
//
// 这里刻意不画卡片：这条视图回答"她这一步到底收到了什么、发出了什么"，
// 密度优先——一屏看完一轮。单行内容不就地展开（一轮常有七八次调用，
// 逐个展开会把整轮推高到几屏，反而失去用途），点行交给右侧详情面板。

import { ref } from 'vue';
import { formatLatency, formatTimeHMS } from '@/utils/format';
import type { RequestMessage, RequestRecord } from '@/utils/requestRecords';

defineProps<{
  records: RequestRecord[];
  emptyText: string;
  /** 当前在右侧面板打开的消息 */
  selectedId?: string;
}>();

const emit = defineEmits<{
  (e: 'select', record: RequestRecord, message: RequestMessage): void;
}>();

const collapsed = ref(new Set<string>());

function toggle(id: string): void {
  const next = new Set(collapsed.value);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  collapsed.value = next;
}
</script>

<style scoped>
.request-timeline {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 6px 0;
}

.empty {
  margin: 24px 0;
  color: var(--text-tertiary, #909399);
  text-align: center;
  font-size: 12px;
}

.rec {
  border-left: 2px solid transparent;
  padding: 2px 0 2px 8px;
}

.rec.is-reply {
  border-left-color: var(--color-primary, #409eff);
}

.rec.is-silent {
  border-left-color: var(--border-color, #dcdfe6);
}

.rec.is-failed {
  border-left-color: var(--color-danger, #f56c6c);
}

.rec-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 3px 4px;
  font-size: 11px;
  color: var(--text-secondary, #606266);
  border-bottom: 1px dashed var(--border-color, #ebeef5);
}

.rec-trigger {
  color: var(--text-primary, #303133);
}

.rec-trigger.is-muted {
  color: var(--text-tertiary, #909399);
}

.rec-outcome.is-reply {
  color: var(--color-primary, #409eff);
}

.rec-outcome.is-failed {
  color: var(--color-danger, #f56c6c);
}

.rec-link {
  color: var(--color-primary, #409eff);
  text-decoration: none;
}

.rec-toggle {
  border: none;
  background: none;
  color: var(--text-tertiary, #909399);
  cursor: pointer;
  font-size: 11px;
  padding: 0 2px;
}

.rec-toggle:hover {
  color: var(--color-primary, #409eff);
}

.rec-body {
  display: flex;
  flex-direction: column;
}

/* 整行都是可点目标：只让行尾按钮可点，会让"这一行能点开"变成要猜的事 */
.msg {
  display: grid;
  grid-template-columns: 52px 32px minmax(0, 1fr);
  align-items: baseline;
  gap: 8px;
  width: 100%;
  padding: 0 4px;
  border: none;
  background: none;
  font: inherit;
  font-size: 12px;
  line-height: 1.6;
  text-align: left;
  cursor: pointer;
  border-radius: 3px;
}

.msg:hover {
  background: var(--fill-light, #f5f7fa);
}

.msg.is-selected {
  background: var(--fill-light, #ecf5ff);
  box-shadow: inset 2px 0 0 var(--color-primary, #409eff);
}

.msg-clock {
  color: var(--text-tertiary, #909399);
  font-size: 11px;
}

.msg-label {
  color: var(--text-tertiary, #909399);
  font-size: 11px;
}

.msg.is-call .msg-label {
  color: var(--color-primary, #409eff);
}

.msg.is-think .msg-label {
  color: var(--color-game, #a06cd5);
}

.msg.is-final .msg-label {
  color: var(--color-success, #67c23a);
}

/* 工具名与正文同一格：单独占列会把正文挤到半行以内，参数就被截断了 */
.msg-body {
  display: flex;
  align-items: baseline;
  gap: 6px;
  min-width: 0;
}

.msg-tool {
  flex: none;
  color: var(--text-secondary, #606266);
  font-size: 11px;
}

.msg-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-primary, #303133);
}

.msg.is-think .msg-text,
.msg.is-result .msg-text {
  color: var(--text-secondary, #606266);
}

.msg.is-failed .msg-text {
  color: var(--color-danger, #f56c6c);
}

.grow {
  flex: 1;
}
</style>
