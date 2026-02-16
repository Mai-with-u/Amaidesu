<template>
  <div class="log-viewer">
    <!-- 页面标题 -->
    <header class="page-header">
      <div class="header-left">
        <h1 class="page-title">日志查看器</h1>
        <p class="page-subtitle">实时监控系统日志</p>
      </div>
      <div class="header-actions">
        <el-tag :type="isConnected ? 'success' : 'danger'" effect="plain" size="small">
          {{ isConnected ? '已连接' : '未连接' }}
        </el-tag>
        <el-tag :type="isRealtimeScroll ? 'primary' : 'info'" effect="plain" size="small">
          {{ isRealtimeScroll ? '实时滚动' : '自由滚动' }}
        </el-tag>
        <el-tag type="info" effect="plain" size="small">
          {{ filteredLogs.length }} / {{ logs.length }} 条日志
        </el-tag>
      </div>
    </header>

    <!-- 筛选工具栏 -->
    <section class="filter-toolbar">
      <div class="filter-left">
        <!-- 搜索框 -->
        <el-input
          v-model="searchQuery"
          placeholder="搜索日志内容..."
          :prefix-icon="Search"
          clearable
          style="width: 240px"
        />

        <!-- 日志级别筛选 -->
        <el-select
          v-model="selectedLevels"
          multiple
          collapse-tags
          collapse-tags-tooltip
          placeholder="筛选日志级别"
          clearable
          style="width: 200px"
        >
          <el-option v-for="level in LOG_LEVELS" :key="level" :label="level" :value="level">
            <span class="level-option">
              <span class="level-dot" :class="`level-${level.toLowerCase()}`"></span>
              <span class="level-name">{{ level }}</span>
              <span class="level-count">{{ getLevelCount(level) }}</span>
            </span>
          </el-option>
        </el-select>

        <!-- 模块筛选 -->
        <el-select
          v-model="selectedModules"
          multiple
          collapse-tags
          collapse-tags-tooltip
          placeholder="筛选模块"
          clearable
          filterable
          style="width: 280px"
        >
          <el-option
            v-for="module in availableModules"
            :key="module"
            :label="module"
            :value="module"
          >
            <span class="module-option">
              <span class="module-name">{{ module }}</span>
              <span class="module-count">{{ getModuleCount(module) }}</span>
            </span>
          </el-option>
        </el-select>
      </div>

      <div class="filter-right">
        <!-- 暂停/继续（显示点击后的动作） -->
        <el-button :type="isPaused ? 'primary' : 'default'" @click="togglePause">
          <el-icon><component :is="pausePlayIcon" /></el-icon>
          {{ isPaused ? '继续' : '暂停' }}
        </el-button>

        <!-- 滚动模式切换（显示点击后的动作） -->
        <el-button :type="isRealtimeScroll ? 'default' : 'primary'" @click="toggleScrollMode">
          <el-icon><component :is="scrollModeIcon" /></el-icon>
          {{ isRealtimeScroll ? '自由滚动' : '实时滚动' }}
        </el-button>

        <!-- 清空 -->
        <el-button :disabled="logs.length === 0" @click="clearLogs">
          <el-icon><Delete /></el-icon>
          清空
        </el-button>
      </div>
    </section>

    <!-- 日志列表 -->
    <section class="log-list-section">
      <div ref="logListRef" class="log-list" @scroll="handleScroll">
        <div v-if="filteredLogs.length === 0" class="empty-state">
          <el-icon :size="48" color="var(--text-placeholder)">
            <Document />
          </el-icon>
          <span>{{
            isPaused ? '日志流已暂停' : logs.length === 0 ? '等待日志...' : '没有匹配的日志'
          }}</span>
        </div>

        <div v-else class="logs-container">
          <div
            v-for="(log, index) in filteredLogs"
            :key="index"
            class="log-row"
            :class="`log-level-${log.level.toLowerCase()}`"
            @click="toggleLogExpand(index)"
          >
            <span class="log-time mono">{{ formatTime(log.timestamp) }}</span>
            <span class="log-level" :class="`level-${log.level.toLowerCase()}`">
              {{ log.level }}
            </span>
            <span class="log-module">{{ log.module }}</span>
            <div class="log-message-wrapper">
              <pre class="log-message" :class="{ expanded: expandedLogs.has(index) }">{{
                log.message
              }}</pre>
              <span v-if="shouldShowExpand(log.message)" class="expand-hint">
                {{ expandedLogs.has(index) ? '点击收起' : '点击展开' }}
              </span>
            </div>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, nextTick, markRaw } from 'vue';
import {
  Document,
  VideoPause,
  VideoPlay,
  Search,
  Delete,
  RefreshRight,
  Unlock,
} from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';
import { useLogsStore, useWebSocketStore } from '@/stores';
import { storeToRefs } from 'pinia';

const logsStore = useLogsStore();
const wsStore = useWebSocketStore();
const { logs, isPaused, availableModules } = storeToRefs(logsStore);
const { isConnected } = storeToRefs(wsStore);

// 日志级别常量
const LOG_LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'] as const;

// 状态
const logListRef = ref<HTMLElement | null>(null);
const searchQuery = ref('');
const selectedLevels = ref<string[]>([]);
const selectedModules = ref<string[]>([]);
const expandedLogs = ref<Set<number>>(new Set());
const isRealtimeScroll = ref(true);

// 动态图标
const pausePlayIcon = computed(() => (isPaused.value ? markRaw(VideoPlay) : markRaw(VideoPause)));

const scrollModeIcon = computed(() =>
  isRealtimeScroll.value ? markRaw(RefreshRight) : markRaw(Unlock),
);

// 获取某个日志级别的数量
function getLevelCount(level: string): number {
  return logs.value.filter((log) => log.level === level).length;
}

// 获取某个模块的数量
function getModuleCount(module: string): number {
  return logs.value.filter((log) => log.module === module).length;
}

// 筛选后的日志列表
const filteredLogs = computed(() => {
  let result = logs.value;

  // 1. 按日志级别筛选
  if (selectedLevels.value.length > 0) {
    result = result.filter((log) => selectedLevels.value.includes(log.level));
  }

  // 2. 按模块筛选
  if (selectedModules.value.length > 0) {
    result = result.filter((log) => selectedModules.value.includes(log.module));
  }

  // 3. 按搜索关键词筛选
  if (searchQuery.value.trim()) {
    const query = searchQuery.value.toLowerCase();
    result = result.filter((log) => {
      // 搜索日志内容
      if (log.message.toLowerCase().includes(query)) return true;
      // 搜索模块名
      if (log.module.toLowerCase().includes(query)) return true;
      return false;
    });
  }

  return result;
});

// 切换滚动模式
function toggleScrollMode() {
  isRealtimeScroll.value = !isRealtimeScroll.value;
  if (isRealtimeScroll.value) {
    scrollToBottom();
    ElMessage.success('已切换到实时滚动模式');
  } else {
    ElMessage.info('已切换到自由滚动模式');
  }
}

// 暂停/继续日志流
function togglePause() {
  logsStore.togglePause();
  if (isPaused.value) {
    ElMessage.info('日志流已暂停');
  } else {
    ElMessage.success('日志流已恢复');
  }
}

// 清空日志
function clearLogs() {
  logsStore.clearLogs();
  expandedLogs.value.clear();
  ElMessage.success('已清空日志列表');
}

// 格式化时间
function formatTime(timestamp: string): string {
  // 处理 ISO 格式或其他时间格式
  const date = new Date(timestamp);
  const hours = date.getHours().toString().padStart(2, '0');
  const minutes = date.getMinutes().toString().padStart(2, '0');
  const seconds = date.getSeconds().toString().padStart(2, '0');
  const ms = date.getMilliseconds().toString().padStart(3, '0');
  return `${hours}:${minutes}:${seconds}.${ms}`;
}

// 判断是否需要展开按钮
function shouldShowExpand(message: string): boolean {
  return message.length > 100 || message.includes('\n');
}

// 切换日志展开状态
function toggleLogExpand(index: number) {
  if (expandedLogs.value.has(index)) {
    expandedLogs.value.delete(index);
  } else {
    expandedLogs.value.add(index);
  }
  expandedLogs.value = new Set(expandedLogs.value);
}

// 自动滚动到底部
function scrollToBottom() {
  nextTick(() => {
    if (logListRef.value) {
      logListRef.value.scrollTop = logListRef.value.scrollHeight;
    }
  });
}

// 处理滚动事件
function handleScroll() {
  if (!logListRef.value || isRealtimeScroll.value === false) return;

  const { scrollTop, scrollHeight, clientHeight } = logListRef.value;
  const isAtBottom = scrollHeight - scrollTop - clientHeight < 50;

  // 如果用户手动滚动离开底部，切换到自由滚动模式
  if (!isAtBottom) {
    isRealtimeScroll.value = false;
  }
}

// 监听日志变化，自动滚动
let unsubscribe: (() => void) | null = null;

onMounted(() => {
  // 连接 WebSocket
  logsStore.connect();

  // 订阅日志变化，自动滚动
  unsubscribe = logsStore.$subscribe(() => {
    if (!isPaused.value && isRealtimeScroll.value) {
      scrollToBottom();
    }
  });

  // 初始滚动到底部
  scrollToBottom();
});

onUnmounted(() => {
  if (unsubscribe) {
    unsubscribe();
  }
});
</script>

<style scoped>
.log-viewer {
  display: flex;
  flex-direction: column;
  height: calc(100vh - var(--header-height) - var(--spacing-lg) * 2);
  overflow: hidden;
}

/* 页面标题 */
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: var(--spacing-md);
  flex-shrink: 0;
}

.header-left {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-xs);
}

.header-actions {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
}

/* 筛选工具栏 */
.filter-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--spacing-md);
  padding: var(--spacing-md);
  background: var(--bg-card);
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-color-light);
  margin-bottom: var(--spacing-md);
  flex-shrink: 0;
}

.filter-left {
  display: flex;
  align-items: center;
  gap: var(--spacing-md);
}

.filter-right {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
}

.level-option {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
  gap: var(--spacing-sm);
}

.level-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.level-dot.level-debug {
  background-color: var(--text-placeholder);
}

.level-dot.level-info {
  background-color: var(--color-primary);
}

.level-dot.level-warning {
  background-color: var(--color-warning);
}

.level-dot.level-error {
  background-color: var(--color-danger);
}

.level-dot.level-critical {
  background-color: #9c27b0;
}

.level-count {
  font-size: 10px;
  font-weight: 500;
  color: var(--text-placeholder);
  background: transparent;
  padding: 0;
  min-width: 28px;
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.module-option {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
  gap: var(--spacing-sm);
}

.module-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}

.module-count {
  font-size: 10px;
  font-weight: 500;
  color: var(--text-placeholder);
  background: transparent;
  padding: 0;
  min-width: 28px;
  text-align: right;
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
}

/* 日志列表 */
.log-list-section {
  flex: 1;
  background: var(--bg-card);
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-color-light);
  overflow: hidden;
}

.log-list {
  height: 100%;
  overflow-y: auto;
  background-color: var(--bg-elevated);
}

.empty-state {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--spacing-md);
  color: var(--text-placeholder);
}

.logs-container {
  padding: var(--spacing-sm) 0;
}

.log-row {
  display: flex;
  align-items: flex-start;
  gap: var(--spacing-md);
  padding: var(--spacing-sm) var(--spacing-md);
  border-bottom: 1px solid var(--border-color-light);
  font-size: 13px;
  cursor: pointer;
  transition: background-color var(--transition-fast);
}

.log-row:hover {
  background-color: var(--bg-hover);
}

.log-row:last-child {
  border-bottom: none;
}

/* 日志级别背景色 */
.log-row.log-level-debug {
  background-color: rgba(128, 128, 128, 0.03);
}

.log-row.log-level-info {
  background-color: rgba(64, 158, 255, 0.03);
}

.log-row.log-level-warning {
  background-color: rgba(230, 162, 60, 0.05);
}

.log-row.log-level-error {
  background-color: rgba(245, 108, 108, 0.08);
}

.log-row.log-level-critical {
  background-color: rgba(156, 39, 176, 0.1);
}

.log-row:hover {
  background-color: var(--bg-hover);
}

.log-time {
  color: var(--text-secondary);
  white-space: nowrap;
  min-width: 95px;
  font-size: 12px;
}

.log-level {
  padding: 2px 8px;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.5px;
  border-radius: var(--radius-sm);
  white-space: nowrap;
  min-width: 70px;
  text-align: center;
  flex-shrink: 0;
}

.log-level.level-debug {
  background-color: rgba(128, 128, 128, 0.15);
  color: #808080;
}

.log-level.level-info {
  background-color: var(--color-primary-bg);
  color: var(--color-primary);
}

.log-level.level-warning {
  background-color: rgba(230, 162, 60, 0.2);
  color: #e6a23c;
}

.log-level.level-error {
  background-color: var(--color-danger-bg);
  color: var(--color-danger);
}

.log-level.level-critical {
  background-color: rgba(156, 39, 176, 0.2);
  color: #9c27b0;
}

.log-module {
  color: var(--text-secondary);
  white-space: nowrap;
  min-width: 120px;
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  font-family: var(--font-mono);
  font-size: 11px;
  flex-shrink: 0;
}

.log-message-wrapper {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.log-message {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-primary);
  margin: 0;
  word-break: break-all;
  white-space: pre-wrap;
  max-height: 40px;
  overflow: hidden;
  transition: max-height var(--transition-normal);
}

.log-message.expanded {
  max-height: 300px;
  overflow-y: auto;
  background-color: var(--bg-elevated);
  padding: var(--spacing-sm);
  border-radius: var(--radius-sm);
  white-space: pre-wrap;
}

.expand-hint {
  font-size: 10px;
  color: var(--color-primary);
  opacity: 0;
  transition: opacity var(--transition-fast);
}

.log-row:hover .expand-hint {
  opacity: 1;
}

/* 滚动条样式 */
.log-list::-webkit-scrollbar {
  width: 8px;
}

.log-list::-webkit-scrollbar-track {
  background: transparent;
}

.log-list::-webkit-scrollbar-thumb {
  background: var(--border-color-dark);
  border-radius: 4px;
}

.log-list::-webkit-scrollbar-thumb:hover {
  background: var(--text-secondary);
}

/* 响应式 */
@media (max-width: 1200px) {
  .filter-toolbar {
    flex-direction: column;
    align-items: stretch;
  }

  .filter-left,
  .filter-right {
    flex-wrap: wrap;
  }
}

@media (max-width: 768px) {
  .page-header {
    flex-direction: column;
    gap: var(--spacing-sm);
  }

  .header-actions {
    width: 100%;
    justify-content: flex-end;
    flex-wrap: wrap;
  }

  .filter-left {
    flex-direction: column;
    align-items: stretch;
  }

  .filter-left .el-input,
  .filter-left .el-select {
    width: 100% !important;
  }

  .log-module {
    display: none;
  }
}
</style>
