<template>
  <div class="log-viewer">
    <!-- 页面标题 -->
    <header class="page-header">
      <div class="header-left">
        <h1 class="page-title">日志查看器</h1>
        <p class="page-subtitle">实时监控系统日志，可回查落盘历史（默认仅 INFO 及以上落盘）</p>
      </div>
      <div class="header-actions">
        <el-tag
          :type="isRealtimeScroll && !windowActive ? 'primary' : 'info'"
          effect="plain"
          size="small"
        >
          {{ windowActive ? '历史浏览' : isRealtimeScroll ? '实时滚动' : '自由滚动' }}
        </el-tag>
        <el-tag type="info" effect="plain" size="small">
          {{ filteredLogs.length }} / {{ displayLogs.length }} 条日志
        </el-tag>
      </div>
    </header>

    <!-- 筛选工具栏 -->
    <section class="filter-toolbar">
      <div class="filter-left">
        <!-- 搜索框（回车发起服务端搜索：窗口=本次启动以来） -->
        <el-input
          v-model="searchQuery"
          placeholder="搜索日志内容（回车在本次启动范围内检索）..."
          :prefix-icon="Search"
          clearable
          style="width: 320px"
          @keyup.enter="onSearch"
          @clear="onSearchClear"
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
        <!-- 暂停/继续（暂停冻结显示后台续收，恢复补入） -->
        <el-button :type="isPaused ? 'primary' : 'default'" @click="onTogglePause">
          <el-icon><component :is="pausePlayIcon" /></el-icon>
          {{ isPaused ? '继续' : '暂停' }}
        </el-button>

        <!-- 实时/自由滚动（仅实时模式有意义） -->
        <el-button
          v-if="!windowActive"
          :type="isRealtimeScroll ? 'default' : 'primary'"
          @click="toggleScrollMode"
        >
          <el-icon><component :is="scrollModeIcon" /></el-icon>
          {{ isRealtimeScroll ? '自由滚动' : '实时滚动' }}
        </el-button>

        <!-- 清空 -->
        <el-button :disabled="displayLogs.length === 0" @click="clearLogs">
          <el-icon><Delete /></el-icon>
          清空
        </el-button>
      </div>
    </section>

    <!-- 历史查询栏（加载更早 / 加载更晚 / 时间跳转 / 回到实时） -->
    <section class="history-section">
      <HistoryBar
        :loading="windowLoading"
        :has-older="windowHasOlder"
        :has-newer="windowHasNewer"
        :window-active="windowActive"
        :exhausted-text="exhaustedText"
        @load-older="loadOlder"
        @load-newer="loadNewer"
        @jump="jump"
        @exit="onExitWindow"
      />
      <span v-if="persistAvailable === false" class="persist-note"
        >日志落盘不可用（未启用或目录缺失），无法查询历史</span
      >
      <el-tag v-if="isSearchMode" type="warning" effect="plain" size="small"
        >服务端搜索结果（本次启动以来）</el-tag
      >
    </section>

    <!-- 日志列表 -->
    <section class="log-list-section">
      <div ref="logListRef" class="log-list" @scroll="handleScroll">
        <div v-if="filteredLogs.length === 0" class="empty-state">
          <el-icon :size="48" color="var(--text-placeholder)">
            <Document />
          </el-icon>
          <span>{{
            isPaused
              ? '日志显示已暂停（后台仍在接收，继续后补入）'
              : displayLogs.length === 0
                ? '等待日志...'
                : '没有匹配的日志'
          }}</span>
        </div>

        <div v-else class="logs-container">
          <template v-for="row in collapsedRows" :key="row.key">
            <!-- 组首条 -->
            <div
              class="log-row"
              :class="`log-level-${row.entries[0]!.level.toLowerCase()}`"
              role="button"
              tabindex="0"
              @click="toggleLogExpand(messageKey(row.entries[0]!))"
              @keydown.enter.prevent="toggleLogExpand(messageKey(row.entries[0]!))"
              @keydown.space.prevent="toggleLogExpand(messageKey(row.entries[0]!))"
            >
              <span class="log-time mono">{{ formatLogTime(row.entries[0]!.timestamp) }}</span>
              <span class="log-level" :class="`level-${row.entries[0]!.level.toLowerCase()}`">
                {{ row.entries[0]!.level }}
              </span>
              <span class="log-module">{{ row.entries[0]!.module }}</span>
              <div class="log-message-wrapper">
                <pre
                  class="log-message"
                  :class="{ expanded: expandedLogs.has(messageKey(row.entries[0]!)) }"
                  >{{ row.entries[0]!.message }}</pre
                >
                <span class="row-hints">
                  <span
                    v-if="row.entries.length > 1"
                    class="collapse-badge"
                    @click.stop="toggleGroupExpand(row.key)"
                  >
                    ×{{ row.entries.length
                    }}{{ expandedGroups.has(row.key) ? ' 收起' : ' 重复，点击展开' }}
                  </span>
                  <span v-if="shouldShowExpand(row.entries[0]!.message)" class="expand-hint">
                    {{ expandedLogs.has(messageKey(row.entries[0]!)) ? '点击收起' : '点击展开' }}
                  </span>
                </span>
              </div>
            </div>
            <!-- 组内重复条目（展开时逐条显示） -->
            <template v-if="row.entries.length > 1 && expandedGroups.has(row.key)">
              <div
                v-for="entry in row.entries.slice(1)"
                :key="entry.timestamp"
                class="log-row log-row-sub"
                :class="`log-level-${entry.level.toLowerCase()}`"
                @click="toggleLogExpand(messageKey(entry))"
              >
                <span class="log-time mono">{{ formatLogTime(entry.timestamp) }}</span>
                <span class="log-level" :class="`level-${entry.level.toLowerCase()}`">{{
                  entry.level
                }}</span>
                <span class="log-module">{{ entry.module }}</span>
                <div class="log-message-wrapper">
                  <pre
                    class="log-message"
                    :class="{ expanded: expandedLogs.has(messageKey(entry)) }"
                    >{{ entry.message }}</pre
                  >
                  <span v-if="shouldShowExpand(entry.message)" class="expand-hint">
                    {{ expandedLogs.has(messageKey(entry)) ? '点击收起' : '点击展开' }}
                  </span>
                </div>
              </div>
            </template>
          </template>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, markRaw, onMounted, onUnmounted, ref, watch, type Ref } from 'vue';
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
import { confirmAction } from '@/utils/confirmAction';
import { storeToRefs } from 'pinia';
import { useLogsStore, type LogEntry } from '@/stores/logs';
import { collapseAdjacentLogs } from '@/utils/logCollapse';
import { useLogStreamQuery } from '@/composables/useLogStreamQuery';
import { useStreamPageQuery } from '@/composables/useStreamPageQuery';
import { useAutoScrollFollow } from '@/composables/useAutoScrollFollow';

const logsStore = useLogsStore();
const {
  logs,
  isPaused,
  windowLogs,
  windowHasOlder,
  windowHasNewer,
  windowLoading,
  persistAvailable,
  availableModules,
} = storeToRefs(logsStore);

// 日志级别常量
const LOG_LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'] as const;

// 状态
const logListRef = ref<HTMLElement | null>(null);
const searchQuery = ref('');
const isSearchMode = ref(false);
const selectedLevels = ref<string[]>([]);
const selectedModules = ref<string[]>([]);
const expandedLogs = ref<Set<string>>(new Set());
const expandedGroups = ref<Set<string>>(new Set());
const isRealtimeScroll = ref(true);

// 深链四件套（初始化/回写/恢复）：机械在 useStreamPageQuery，
// 键名映射与 level 级别表白名单过滤是本页域知识
useStreamPageQuery({
  fields: [
    {
      key: 'level',
      get: () => selectedLevels.value,
      set: values => {
        selectedLevels.value = values.filter(level =>
          (LOG_LEVELS as readonly string[]).includes(level),
        );
      },
    },
    {
      key: 'module',
      get: () => selectedModules.value,
      set: values => {
        selectedModules.value = values;
      },
    },
  ],
  watchSources: [selectedLevels, selectedModules],
});

// 动态图标
const pausePlayIcon = computed(() => (isPaused.value ? markRaw(VideoPlay) : markRaw(VideoPause)));

const scrollModeIcon = computed(() =>
  isRealtimeScroll.value ? markRaw(RefreshRight) : markRaw(Unlock),
);

// 显示列表/筛选/计数/翻页锚定：共享查询机械（谓词为本页域知识）
const {
  displayItems: displayLogs,
  windowActive,
  filteredItems: filteredLogs,
  countBy,
  scrollListTo,
  loadOlder,
  loadNewer,
  jump,
} = useLogStreamQuery({
  live: logs,
  window: windowLogs,
  fetchOlder: () => logsStore.fetchOlder(),
  fetchNewer: () => logsStore.fetchNewer(),
  jumpTo: timestampMs => logsStore.jumpTo(timestampMs),
  listEl: logListRef,
  predicates: [
    log => selectedLevels.value.length === 0 || selectedLevels.value.includes(log.level),
    log => selectedModules.value.length === 0 || selectedModules.value.includes(log.module),
  ],
});

// 更早耗尽提示：窗口起点随查询口径变化（默认启动、跳转后为所选时刻），文案不写死
const exhaustedText = computed(
  () => '已到可查范围最早的记录（默认范围=本次启动，更早请用时间跳转）',
);

// 级别/模块计数基于当前显示列表
function getLevelCount(level: string): number {
  return countBy(log => log.level === level);
}

function getModuleCount(module: string): number {
  return countBy(log => log.module === module);
}

// 同因折叠：相邻同级别/同模块/同正文合并为一组
const collapsedRows = computed(() => collapseAdjacentLogs(filteredLogs.value));

function messageKey(entry: LogEntry): string {
  return `${entry.timestamp}|${entry.module}|${entry.message}`;
}

// ------------------------------------------------------------------
// 动作
// ------------------------------------------------------------------

function onSearch() {
  const query = searchQuery.value.trim();
  if (!query) {
    onSearchClear();
    return;
  }
  isSearchMode.value = true;
  void logsStore.runSearch(query).then(() => scrollListTo('bottom'));
}

function onSearchClear() {
  if (!isSearchMode.value) return;
  isSearchMode.value = false;
  logsStore.exitWindow();
}

function onExitWindow() {
  isSearchMode.value = false;
  logsStore.exitWindow();
  scrollToBottom();
}

function onTogglePause() {
  logsStore.togglePause();
}

// 恢复暂停的补入报告（store 置值 → 视图提示后清空）
watch(
  () => logsStore.resumeReport,
  report => {
    if (!report) return;
    if (report.dropped > 0) {
      ElMessage.warning(
        `已补入 ${report.added} 条暂停期间的日志，积压溢出丢弃 ${report.dropped} 条（完整内容可翻历史找回）`,
      );
    } else if (report.added > 0) {
      ElMessage.success(`已补入 ${report.added} 条暂停期间的日志`);
    }
    logsStore.resumeReport = null;
  },
);

function toggleScrollMode() {
  isRealtimeScroll.value = !isRealtimeScroll.value;
  if (isRealtimeScroll.value) {
    scrollToBottom();
    ElMessage.success('已切换到实时滚动模式');
  } else {
    ElMessage.info('已切换到自由滚动模式');
  }
}

async function clearLogs() {
  if (
    !(await confirmAction(
      '将清空当前日志列表（仅内存缓冲，不影响落盘文件）。确定要清空吗？',
      '清空日志列表',
    ))
  )
    return;
  logsStore.clearLogs();
  expandedLogs.value.clear();
  expandedGroups.value.clear();
  ElMessage.success('已清空日志列表');
}

// ------------------------------------------------------------------
// 展示辅助
// ------------------------------------------------------------------

function pad2(value: number): string {
  return value.toString().padStart(2, '0');
}

// 最新一条与较旧条目跨天时，时间列补日期前缀（跨天/跨启动浏览可定位）
const newestTimestamp = computed(() => displayLogs.value.at(-1)?.timestamp ?? '');

function formatLogTime(timestamp: string): string {
  const date = new Date(timestamp);
  const hms = `${pad2(date.getHours())}:${pad2(date.getMinutes())}:${pad2(date.getSeconds())}.${date.getMilliseconds().toString().padStart(3, '0')}`;
  const newest = newestTimestamp.value ? new Date(newestTimestamp.value) : null;
  if (
    newest &&
    (date.getFullYear() !== newest.getFullYear() ||
      date.getMonth() !== newest.getMonth() ||
      date.getDate() !== newest.getDate())
  ) {
    return `${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ${hms}`;
  }
  return hms;
}

function shouldShowExpand(message: string): boolean {
  return message.length > 100 || message.includes('\n');
}

// Set 整体重赋值以触发响应式；正文展开与重复组展开共用同一机械
function toggleInSet(setRef: Ref<Set<string>>, key: string): void {
  if (setRef.value.has(key)) {
    setRef.value.delete(key);
  } else {
    setRef.value.add(key);
  }
  setRef.value = new Set(setRef.value);
}

function toggleLogExpand(key: string): void {
  toggleInSet(expandedLogs, key);
}

function toggleGroupExpand(key: string): void {
  toggleInSet(expandedGroups, key);
}

// ------------------------------------------------------------------
// 滚动
// ------------------------------------------------------------------

// 滚动锚定已收进 useLogStreamQuery（scrollListTo 随需解构；翻页/跳转自动锚定）
// 自动滚底的 RAF 节流/初始滚底/卸载清理收进 useAutoScrollFollow；
// 仅实时滚动且非历史浏览态才判离底，滚离即切自由滚动
const { scrollToBottom, handleScroll } = useAutoScrollFollow({
  container: logListRef,
  canHandleScroll: () => !windowActive.value && isRealtimeScroll.value,
  onLeaveBottom: () => {
    isRealtimeScroll.value = false;
  },
});

// 监听日志变化，自动滚动（历史浏览/暂停态不跟随）
let unsubscribe: (() => void) | null = null;

onMounted(() => {
  unsubscribe = logsStore.$subscribe(() => {
    if (!isPaused.value && isRealtimeScroll.value && !windowActive.value) {
      scrollToBottom();
    }
  });
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
  margin-bottom: var(--spacing-sm);
  flex-shrink: 0;
}

/* 历史查询栏 */
.history-section {
  display: flex;
  align-items: center;
  gap: var(--spacing-md);
  padding: var(--spacing-sm) var(--spacing-md);
  background: var(--bg-card);
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-color-light);
  margin-bottom: var(--spacing-md);
  flex-shrink: 0;
  flex-wrap: wrap;
}

.persist-note {
  font-size: 12px;
  color: var(--color-warning);
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
  background-color: var(--color-critical);
}

.level-count,
.module-count {
  font-size: 11px;
  font-weight: 500;
  color: var(--text-placeholder);
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

/* 组内重复条目缩进呈现 */
.log-row-sub {
  padding-left: calc(var(--spacing-md) * 2);
  opacity: 0.85;
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
  background-color: var(--color-critical-bg);
}

.log-time {
  color: var(--text-secondary);
  white-space: nowrap;
  min-width: 128px;
  font-size: 12px;
}

.log-level {
  padding: 2px 8px;
  font-size: 11px;
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
  background-color: var(--color-warning-bg);
  color: var(--color-warning);
}

.log-level.level-error {
  background-color: var(--color-danger-bg);
  color: var(--color-danger);
}

.log-level.level-critical {
  background-color: var(--color-critical-bg);
  color: var(--color-critical);
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

.row-hints {
  display: flex;
  gap: var(--spacing-md);
  align-items: center;
}

.collapse-badge {
  font-size: 11px;
  font-weight: 600;
  color: var(--color-warning);
  background-color: var(--color-warning-bg);
  border-radius: var(--radius-sm);
  padding: 1px 8px;
  cursor: pointer;
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
