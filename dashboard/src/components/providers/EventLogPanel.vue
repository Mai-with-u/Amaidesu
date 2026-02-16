<template>
  <div class="event-log-panel" :class="{ compact }">
    <el-scrollbar ref="scrollbarRef" height="150px" class="log-scrollbar">
      <div v-if="displayItems.length === 0" class="empty-state">
        <span class="empty-text">{{ emptyText }}</span>
      </div>
      <div v-else class="log-list">
        <div
          v-for="(item, index) in displayItems"
          :key="index"
          class="log-item"
          :class="getItemClass(item)"
        >
          <span class="log-time">{{ formatTime(item) }}</span>
          <span class="log-separator">|</span>
          <span
            v-if="type === 'events'"
            class="log-type"
            :class="getEventTypeClass(item as WebSocketMessage)"
          >
            {{ getEventType(item as WebSocketMessage) }}
          </span>
          <span v-else class="log-level" :class="getLogLevelClass(item as LogEntry)">
            [{{ (item as LogEntry).level }}]
          </span>
          <span class="log-separator">|</span>
          <span class="log-message">{{ truncateMessage(getMessage(item)) }}</span>
        </div>
      </div>
    </el-scrollbar>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import type { WebSocketMessage } from '@/types';
import type { LogEntry } from '@/stores/logs';

interface Props {
  type: 'events' | 'logs';
  items: WebSocketMessage[] | LogEntry[];
  maxItems?: number;
  compact?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  maxItems: 20,
  compact: false,
});

const scrollbarRef = ref();

// Empty state text based on type
const emptyText = computed(() => {
  return props.type === 'events' ? '暂无事件' : '暂无日志';
});

// Limit displayed items
const displayItems = computed(() => {
  const items = props.items;
  const maxItems = props.maxItems;
  if (items.length <= maxItems) {
    return items;
  }
  // Show most recent items
  return items.slice(-maxItems);
});

// Format timestamp to HH:mm:ss
function formatTime(item: WebSocketMessage | LogEntry): string {
  let timestamp: number;

  if (props.type === 'events') {
    timestamp = (item as WebSocketMessage).timestamp;
  } else {
    // LogEntry has string timestamp, try to parse it
    const logItem = item as LogEntry;
    if (typeof logItem.timestamp === 'string') {
      // If already in HH:mm:ss format, return as is
      if (/^\d{2}:\d{2}:\d{2}/.test(logItem.timestamp)) {
        return logItem.timestamp.substring(0, 8);
      }
      // Try to parse ISO string
      const date = new Date(logItem.timestamp);
      if (!isNaN(date.getTime())) {
        return formatTimeFromDate(date);
      }
      return logItem.timestamp;
    }
    timestamp = logItem.timestamp as unknown as number;
  }

  return formatTimeFromDate(new Date(timestamp));
}

function formatTimeFromDate(date: Date): string {
  const hours = date.getHours().toString().padStart(2, '0');
  const minutes = date.getMinutes().toString().padStart(2, '0');
  const seconds = date.getSeconds().toString().padStart(2, '0');
  return `${hours}:${minutes}:${seconds}`;
}

// Get CSS class for the item row
function getItemClass(item: WebSocketMessage | LogEntry): Record<string, boolean> {
  if (props.type === 'logs') {
    const level = (item as LogEntry).level;
    return {
      [`level-${level.toLowerCase()}`]: true,
    };
  }
  return {};
}

// Get event type for display
function getEventType(item: WebSocketMessage): string {
  return item.type || 'unknown';
}

// Get CSS class for event type badge
function getEventTypeClass(item: WebSocketMessage): Record<string, boolean> {
  const eventType = item.type;
  const classMap: Record<string, boolean> = {};

  // Event type color mapping
  const typeColors: Record<string, string> = {
    'message.received': 'type-blue',
    'decision.intent': 'type-purple',
    'output.render': 'type-green',
    'provider.connected': 'type-green',
    'provider.disconnected': 'type-red',
  };

  const colorClass = typeColors[eventType];
  if (colorClass) {
    classMap[colorClass] = true;
  }

  return classMap;
}

// Get CSS class for log level badge
function getLogLevelClass(item: LogEntry): Record<string, boolean> {
  const level = item.level.toLowerCase();
  return {
    [`level-badge-${level}`]: true,
  };
}

// Get message content
function getMessage(item: WebSocketMessage | LogEntry): string {
  if (props.type === 'events') {
    const data = (item as WebSocketMessage).data;
    if (data && typeof data === 'object') {
      try {
        return JSON.stringify(data);
      } catch {
        return '[无法序列化数据]';
      }
    }
    return String(data ?? '');
  } else {
    const logItem = item as LogEntry;
    return logItem.message || '';
  }
}

// Truncate message to 50 characters
function truncateMessage(message: string): string {
  if (message.length <= 50) {
    return message;
  }
  return message.substring(0, 47) + '...';
}
</script>

<style scoped>
.event-log-panel {
  background: var(--bg-elevated);
  border-radius: var(--radius-md);
  border: 1px solid var(--border-color-light);
  overflow: hidden;
}

.event-log-panel.compact {
  border-radius: var(--radius-sm);
}

.log-scrollbar {
  font-family: var(--font-mono);
}

.empty-state {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 150px;
  padding: var(--spacing-lg);
}

.empty-text {
  font-size: 13px;
  color: var(--text-placeholder);
  font-style: italic;
}

.log-list {
  display: flex;
  flex-direction: column;
}

.log-item {
  display: flex;
  align-items: center;
  gap: var(--spacing-xs);
  padding: var(--spacing-sm) var(--spacing-md);
  font-size: 12px;
  line-height: 1.5;
  border-bottom: 1px solid var(--border-color-light);
  transition: background-color var(--transition-fast);
}

.event-log-panel.compact .log-item {
  padding: 6px var(--spacing-sm);
  font-size: 11px;
}

.log-item:last-child {
  border-bottom: none;
}

.log-item:hover {
  background: var(--bg-hover);
}

.log-time {
  color: var(--text-secondary);
  flex-shrink: 0;
  min-width: 70px;
}

.log-separator {
  color: var(--border-color);
  flex-shrink: 0;
}

.log-type {
  flex-shrink: 0;
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 11px;
  font-weight: 500;
  max-width: 160px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.event-log-panel.compact .log-type {
  font-size: 10px;
  padding: 1px 4px;
}

/* Event type colors */
.log-type.type-blue {
  color: var(--color-primary);
  background: var(--color-primary-bg, rgba(64, 158, 255, 0.1));
}

.log-type.type-purple {
  color: var(--color-decision);
  background: var(--color-decision-bg);
}

.log-type.type-green {
  color: var(--color-success);
  background: var(--color-success-bg);
}

.log-type.type-red {
  color: var(--color-danger);
  background: var(--color-danger-bg);
}

.log-level {
  flex-shrink: 0;
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 11px;
  font-weight: 600;
}

.event-log-panel.compact .log-level {
  font-size: 10px;
  padding: 1px 4px;
}

/* Log level colors */
.log-level.level-badge-debug {
  color: var(--text-secondary);
  background: var(--bg-hover);
}

.log-level.level-badge-info {
  color: var(--color-primary);
  background: var(--color-primary-bg, rgba(64, 158, 255, 0.1));
}

.log-level.level-badge-warning {
  color: var(--color-warning);
  background: var(--color-warning-bg);
}

.log-level.level-badge-error {
  color: var(--color-danger);
  background: var(--color-danger-bg);
}

.log-level.level-badge-critical {
  color: #fff;
  background: var(--color-danger);
}

/* Row-level highlight for errors */
.log-item.level-error {
  background: rgba(245, 108, 108, 0.05);
}

.log-item.level-critical {
  background: rgba(245, 108, 108, 0.1);
  border-left: 3px solid var(--color-danger);
}

.log-message {
  color: var(--text-regular);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}

/* Dark mode adjustments */
[data-theme='dark'] .log-type.type-blue {
  background: rgba(88, 166, 255, 0.15);
}

[data-theme='dark'] .log-type.type-purple {
  background: rgba(188, 140, 255, 0.15);
}

[data-theme='dark'] .log-type.type-green {
  background: rgba(63, 185, 80, 0.15);
}

[data-theme='dark'] .log-type.type-red {
  background: rgba(248, 81, 73, 0.15);
}

[data-theme='dark'] .log-level.level-badge-info {
  background: rgba(88, 166, 255, 0.15);
}

[data-theme='dark'] .log-level.level-badge-warning {
  background: rgba(210, 153, 34, 0.15);
}

[data-theme='dark'] .log-level.level-badge-error {
  background: rgba(248, 81, 73, 0.15);
}

[data-theme='dark'] .log-item.level-error {
  background: rgba(248, 81, 73, 0.08);
}

[data-theme='dark'] .log-item.level-critical {
  background: rgba(248, 81, 73, 0.12);
}

/* Scrollbar styling for el-scrollbar */
.log-scrollbar :deep(.el-scrollbar__bar) {
  opacity: 0.3;
}

.log-scrollbar :deep(.el-scrollbar__bar:hover) {
  opacity: 0.5;
}
</style>
