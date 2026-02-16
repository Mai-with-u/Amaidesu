<template>
  <div
    class="provider-card"
    :class="[statusClass, { expanded: isExpanded, 'is-decision': provider.domain === 'decision' }]"
  >
    <!-- Header Section - Compact -->
    <div class="provider-header">
      <div class="provider-title-row">
        <span class="status-indicator" :class="statusClass">
          <span class="status-dot"></span>
        </span>
        <h3 class="provider-name">{{ provider.name }}</h3>
        <el-tag
          :type="provider.is_started ? 'success' : provider.is_enabled ? 'warning' : 'info'"
          size="small"
          effect="plain"
          class="status-tag"
        >
          {{ statusText }}
        </el-tag>
      </div>
    </div>

    <!-- Stats & Actions Row -->
    <div class="provider-footer">
      <div class="stats-row">
        <div class="stat-badge" title="事件" @click="handleToggleExpand">
          <span class="stat-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
            </svg>
          </span>
          <span class="stat-count">{{ eventCount }}</span>
        </div>
        <div class="stat-badge" title="日志" @click="handleToggleExpand">
          <span class="stat-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
              <polyline points="14,2 14,8 20,8" />
              <line x1="16" y1="13" x2="8" y2="13" />
              <line x1="16" y1="17" x2="8" y2="17" />
            </svg>
          </span>
          <span class="stat-count">{{ logCount }}</span>
        </div>
        <el-button size="small" text class="expand-btn" @click="handleToggleExpand">
          <span class="expand-icon" :class="{ rotated: isExpanded }">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="6,9 12,15 18,9" />
            </svg>
          </span>
        </el-button>
      </div>

      <div class="action-buttons">
        <!-- Decision Provider: Only Activate button -->
        <template v-if="provider.domain === 'decision'">
          <el-button
            size="small"
            type="primary"
            :disabled="provider.is_started"
            :loading="actionLoading['start']"
            @click="handleControl('start')"
          >
            激活
          </el-button>
        </template>

        <!-- Input/Output Providers: Start, Stop, Restart -->
        <template v-else>
          <el-button
            size="small"
            type="primary"
            :disabled="provider.is_started"
            :loading="actionLoading['start']"
            @click="handleControl('start')"
          >
            启动
          </el-button>
          <el-button
            size="small"
            :disabled="!provider.is_started"
            :loading="actionLoading['stop']"
            @click="handleControl('stop')"
          >
            停止
          </el-button>
          <el-button
            size="small"
            type="warning"
            :loading="actionLoading['restart']"
            @click="handleControl('restart')"
          >
            重启
          </el-button>
        </template>
      </div>
    </div>

    <!-- Expanded Details Section -->
    <el-collapse-transition>
      <div v-show="isExpanded" class="provider-details">
        <!-- Recent Events -->
        <div class="detail-section events-section">
          <div class="section-header">
            <span class="section-icon">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
              </svg>
            </span>
            <h4>最近事件</h4>
            <span class="count">{{ recentEvents.length }}</span>
          </div>
          <div v-if="recentEvents.length > 0" class="detail-list">
            <div v-for="(event, index) in recentEvents" :key="index" class="detail-item event-item">
              <span class="item-time">{{ formatTime(event.timestamp) }}</span>
              <span class="item-content">{{ event.type }}</span>
            </div>
          </div>
          <div v-else class="empty-state">暂无事件</div>
        </div>

        <!-- Recent Logs -->
        <div class="detail-section logs-section">
          <div class="section-header">
            <span class="section-icon">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                <polyline points="14,2 14,8 20,8" />
                <line x1="16" y1="13" x2="8" y2="13" />
                <line x1="16" y1="17" x2="8" y2="17" />
              </svg>
            </span>
            <h4>最近日志</h4>
            <span class="count">{{ recentLogs.length }}</span>
          </div>
          <div v-if="recentLogs.length > 0" class="detail-list">
            <div
              v-for="(log, index) in recentLogs"
              :key="index"
              class="detail-item log-item"
              :class="log.level.toLowerCase()"
            >
              <span class="item-level" :class="log.level.toLowerCase()">[{{ log.level }}]</span>
              <span class="item-time">{{ formatLogTime(log.timestamp) }}</span>
              <span class="item-content">{{ log.message }}</span>
            </div>
          </div>
          <div v-else class="empty-state">暂无日志</div>
        </div>

        <!-- Slot for additional content (EventLogPanel) -->
        <slot name="detail-panel"></slot>
      </div>
    </el-collapse-transition>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import type { ProviderSummary, WebSocketMessage, ProviderControlAction } from '@/types';
import type { LogEntry } from '@/stores/logs';

interface Props {
  provider: ProviderSummary;
  recentEvents: WebSocketMessage[];
  recentLogs: LogEntry[];
  eventCount: number;
  logCount: number;
  actionLoading: Record<string, boolean>;
}

interface Emits {
  (e: 'control', action: ProviderControlAction): void;
  (e: 'toggle-expand'): void;
}

const props = defineProps<Props>();
const emit = defineEmits<Emits>();

const isExpanded = ref(false);

const statusClass = computed(() => {
  if (!props.provider.is_enabled) return 'disabled';
  if (props.provider.is_started) return 'running';
  return 'stopped';
});

const statusText = computed(() => {
  if (!props.provider.is_enabled) return '已禁用';
  if (props.provider.is_started) return '运行中';
  return '已停止';
});

function handleControl(action: ProviderControlAction) {
  emit('control', action);
}

function handleToggleExpand() {
  isExpanded.value = !isExpanded.value;
  emit('toggle-expand');
}

function formatTime(timestamp: number): string {
  const date = new Date(timestamp * 1000);
  return date.toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
}

function formatLogTime(timestamp: string): string {
  try {
    const date = new Date(timestamp);
    return date.toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    });
  } catch {
    return timestamp;
  }
}
</script>

<style scoped>
.provider-card {
  background: var(--bg-card);
  border-radius: var(--radius-md);
  border: 1px solid var(--border-color-light);
  padding: var(--spacing-sm);
  box-shadow: var(--shadow-sm);
  transition: all var(--transition-normal);
  position: relative;
  overflow: hidden;
}

.provider-card::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  width: 3px;
  height: 100%;
  background: var(--border-color);
  transition: background var(--transition-normal);
}

.provider-card.running::before {
  background: var(--color-success);
}

.provider-card.stopped::before {
  background: var(--color-warning);
}

.provider-card.disabled::before {
  background: var(--text-secondary);
}

.provider-card:hover {
  box-shadow: var(--shadow-md);
}

.provider-card.expanded {
  box-shadow: var(--shadow-md);
}

/* Header - Compact */
.provider-header {
  margin-bottom: var(--spacing-xs);
}

.provider-title-row {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
}

.provider-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex: 1;
}

.status-tag {
  flex-shrink: 0;
  font-size: 11px;
}

/* Status Indicator */
.status-indicator {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  flex-shrink: 0;
}

.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--text-secondary);
}

.status-indicator.running .status-dot {
  background: var(--color-success);
  box-shadow: 0 0 6px var(--color-success);
  animation: pulse 2s infinite;
}

.status-indicator.stopped .status-dot {
  background: var(--color-warning);
}

.status-indicator.disabled .status-dot {
  background: var(--text-secondary);
}

@keyframes pulse {
  0%,
  100% {
    opacity: 1;
    transform: scale(1);
  }
  50% {
    opacity: 0.6;
    transform: scale(0.95);
  }
}

/* Footer / Actions */
.provider-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--spacing-sm);
}

.stats-row {
  display: flex;
  align-items: center;
  gap: var(--spacing-xs);
}

.stat-badge {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  background: var(--bg-hover);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all var(--transition-fast);
}

.stat-badge:hover {
  background: var(--bg-active);
}

.stat-icon {
  width: 14px;
  height: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-secondary);
}

.stat-icon svg {
  width: 12px;
  height: 12px;
}

.stat-badge:first-child .stat-icon {
  color: var(--color-input);
}

.stat-badge:nth-child(2) .stat-icon {
  color: var(--color-warning);
}

.stat-count {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary);
  min-width: 16px;
}

.expand-btn {
  padding: 4px;
  color: var(--text-secondary);
}

.expand-btn:hover {
  color: var(--color-primary);
}

.expand-icon {
  display: inline-flex;
  width: 14px;
  height: 14px;
  transition: transform var(--transition-fast);
}

.expand-icon svg {
  width: 14px;
  height: 14px;
}

.expand-icon.rotated {
  transform: rotate(180deg);
}

.action-buttons {
  display: flex;
  gap: 4px;
}

.action-buttons .el-button {
  font-size: 11px;
  padding: 4px 8px;
}

/* Expanded Details */
.provider-details {
  margin-top: var(--spacing-sm);
  padding-top: var(--spacing-sm);
  border-top: 1px solid var(--border-color-light);
}

.detail-section {
  margin-bottom: var(--spacing-sm);
}

.detail-section:last-child {
  margin-bottom: 0;
}

.detail-section .section-header {
  display: flex;
  align-items: center;
  gap: var(--spacing-xs);
  margin-bottom: var(--spacing-xs);
  padding-bottom: var(--spacing-xs);
  border-bottom: 1px solid var(--border-color-light);
}

.section-icon {
  width: 14px;
  height: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-secondary);
}

.section-icon svg {
  width: 12px;
  height: 12px;
}

.events-section .section-icon {
  color: var(--color-input);
}

.logs-section .section-icon {
  color: var(--color-warning);
}

.section-header h4 {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
}

.section-header .count {
  font-size: 10px;
  color: var(--text-secondary);
  background: var(--bg-hover);
  padding: 1px 4px;
  border-radius: var(--radius-sm);
  margin-left: auto;
}

.detail-list {
  max-height: 120px;
  overflow-y: auto;
  border-radius: var(--radius-sm);
}

.detail-item {
  display: flex;
  align-items: flex-start;
  gap: var(--spacing-xs);
  padding: 4px 6px;
  font-size: 10px;
  font-family: var(--font-mono);
  border-radius: var(--radius-sm);
  transition: background var(--transition-fast);
}

.detail-item:hover {
  background: var(--bg-hover);
}

.item-time {
  color: var(--text-secondary);
  white-space: nowrap;
  flex-shrink: 0;
  font-size: 9px;
}

.item-level {
  font-weight: 600;
  flex-shrink: 0;
  font-size: 9px;
}

.item-level.debug {
  color: var(--text-secondary);
}

.item-level.info {
  color: var(--color-primary);
}

.item-level.warning {
  color: var(--color-warning);
}

.item-level.error,
.item-level.critical {
  color: var(--color-danger);
}

.item-content {
  color: var(--text-regular);
  word-break: break-word;
  flex: 1;
}

.empty-state {
  padding: var(--spacing-sm);
  text-align: center;
  color: var(--text-secondary);
  font-size: 11px;
  background: var(--bg-hover);
  border-radius: var(--radius-sm);
}

/* Responsive */
@media (max-width: 768px) {
  .provider-footer {
    flex-direction: column;
    align-items: stretch;
    gap: var(--spacing-xs);
  }

  .stats-row {
    justify-content: center;
  }

  .action-buttons {
    justify-content: stretch;
  }

  .action-buttons .el-button {
    flex: 1;
  }
}
</style>
