import { defineStore } from 'pinia';
import { computed, ref, shallowRef } from 'vue';
import { useWebSocketStore } from './websocket';
import { createWindowedBuffer } from '@/composables/useWindowedBuffer';
import { logsApi } from '@/api';
import { PROTOCOL } from '@/types/events';
import type { LogEntryItem, WebSocketMessage } from '@/types';

export interface LogEntry {
  timestamp: string;
  level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL';
  module: string;
  message: string;
  exception?: string;
}

/** 实时尾部缓冲容量（WS 推送） */
const MAX_LIVE_LOGS = 500;
/** 暂停期间积压上限：超出丢最旧并计数，完整内容用历史查询兜底 */
const MAX_PENDING_LOGS = 1000;
/** 历史浏览窗口上限（约 3 页）：渲染面积有界，超出沿页进方向裁剪对侧 */
const MAX_WINDOW_LOGS = 600;
const PAGE_SIZE = 200;

/** API 条目 → LogEntry（级别收敛到已知枚举，未知级别按原值保留展示） */
function toLogEntry(item: LogEntryItem): LogEntry {
  return {
    timestamp: item.timestamp,
    level: item.level as LogEntry['level'],
    module: item.module,
    message: item.message,
    exception: item.exception,
  };
}

function toLogEntries(items: LogEntryItem[]): LogEntry[] {
  return items.map(toLogEntry);
}

/** 日志页元数据边车（随每页回报，经 onPageMeta 回写 store 状态） */
interface LogsPageMeta {
  persistAvailable?: boolean;
  windowStartMs?: number;
}

export const useLogsStore = defineStore('logs', () => {
  /** 实时尾部缓冲（WS 推送；暂停期间进积压队列不丢） */
  const logs = shallowRef<LogEntry[]>([]);
  const isPaused = ref(false);
  /** 落盘可用性（最近一次查询回报；null = 尚未查询过） */
  const persistAvailable = ref<boolean | null>(null);
  /** 实际生效的窗口起点（服务端缺省=本次启动时刻），"已到窗口起点"提示用 */
  const windowStartMs = ref(0);
  // 浏览窗口机械（状态 refs + 翻页/跳转/回实时）由共享工厂承载，
  // 响应字段名归一（has_more_older → hasOlder 等）与参数组装在 fetchPage 适配层
  const buffer = createWindowedBuffer<LogEntry>({
    pageSize: PAGE_SIZE,
    maxWindow: MAX_WINDOW_LOGS,
    entryKey: entry => `${entry.timestamp}|${entry.module}|${entry.message}`,
    entryTime: entry => Date.parse(entry.timestamp),
    // 时间排序沿用 ISO 串字典序：后端时刻串同构，字典序即时刻序，
    // 且不受 Date.parse 对同毫秒/亚毫秒精度归并的影响
    sortCompare: (a, b) => (a.timestamp < b.timestamp ? -1 : 1),
    liveList: () => logs.value,
    onPageMeta: meta => {
      const page = meta as LogsPageMeta;
      if (page.persistAvailable !== undefined) persistAvailable.value = page.persistAvailable;
      if (page.windowStartMs !== undefined) windowStartMs.value = page.windowStartMs;
    },
    fetchPage: async ({ direction, fromMs }) => {
      if (direction === 'older') {
        const response = await logsApi.list({ before_timestamp_ms: fromMs, limit: PAGE_SIZE });
        return {
          items: toLogEntries(response.data.entries),
          hasOlder: response.data.has_more_older,
          hasNewer: response.data.has_more_newer,
          meta: {
            persistAvailable: response.data.persist_available,
            windowStartMs: response.data.window_start_ms,
          },
        };
      }
      const response = await logsApi.list({ start_ms: fromMs, take: 'oldest', limit: PAGE_SIZE });
      return {
        items: toLogEntries(response.data.entries),
        hasOlder: response.data.has_more_older,
        hasNewer: response.data.has_more_newer,
        meta: {
          persistAvailable: response.data.persist_available,
          // 窗口起点只随"以时刻开窗"的查询（older/jump）刷新，newer 不改写
          windowStartMs: direction === 'jump' ? response.data.window_start_ms : undefined,
        },
      };
    },
  });
  /** 历史浏览窗口：null = 实时模式（显示实时尾部）；非 null = 显示该窗口 */
  const windowLogs = buffer.window;
  /** 窗口双向续翻信号（服务端 has_more 语义） */
  const windowHasOlder = buffer.hasOlder;
  const windowHasNewer = buffer.hasNewer;
  const windowLoading = buffer.loading;
  /** 恢复暂停时的补入报告（视图读后即清） */
  const resumeReport = ref<{ added: number; dropped: number } | null>(null);

  // 重连幂等：history 重推与已收到的实时日志按 (timestamp, module, message) 去重
  const seenKeys = new Set<string>();
  const MAX_SEEN_KEYS = 2000;

  function logKey(entry: LogEntry) {
    return `${entry.timestamp}|${entry.module}|${entry.message}`;
  }

  function isDuplicate(entry: LogEntry): boolean {
    const key = logKey(entry);
    if (seenKeys.has(key)) return true;
    seenKeys.add(key);
    if (seenKeys.size > MAX_SEEN_KEYS) {
      // 淘汰最旧插入的 key，保持与日志窗口（MAX_LIVE_LOGS）同量级
      for (const k of seenKeys) {
        seenKeys.delete(k);
        if (seenKeys.size <= MAX_SEEN_KEYS) break;
      }
    }
    return false;
  }

  let cachedModules: string[] = [];
  let logsCacheVersion = 0;
  let modulesCacheVersion = -1;

  const availableModules = computed(() => {
    if (modulesCacheVersion !== logsCacheVersion) {
      const modules = new Set<string>();
      logs.value.forEach(log => modules.add(log.module));
      cachedModules = Array.from(modules).sort();
      modulesCacheVersion = logsCacheVersion;
    }
    return cachedModules;
  });

  function isLogEntry(value: unknown): value is LogEntry {
    if (!value || typeof value !== 'object') return false;
    const d = value as Record<string, unknown>;
    return (
      typeof d.timestamp === 'string' &&
      typeof d.level === 'string' &&
      typeof d.module === 'string' &&
      typeof d.message === 'string'
    );
  }

  // 暂停积压：冻结显示但后台续收，恢复时合并补入（与事件页暂停语义对齐）
  let pendingEntries: LogEntry[] = [];
  let pendingDropped = 0;

  function appendLive(entry: LogEntry) {
    const newLogs = [...logs.value, entry];
    if (newLogs.length > MAX_LIVE_LOGS) {
      newLogs.splice(0, newLogs.length - MAX_LIVE_LOGS);
    }
    logsCacheVersion++;
    logs.value = newLogs;
  }

  function handleLog(message: WebSocketMessage) {
    // 日志通道专用：kind="log" 由 WS 协议标记（事件/握手帧不入日志缓冲）
    if (message.kind !== 'log' || message.type !== PROTOCOL.logEntry || !message.data) return;
    if (!isLogEntry(message.data)) return;
    const entry = message.data;
    if (isPaused.value) {
      if (pendingEntries.length >= MAX_PENDING_LOGS) {
        pendingEntries.shift();
        pendingDropped++;
      }
      pendingEntries.push(entry);
      return;
    }
    if (isDuplicate(entry)) return;
    appendLive(entry);
  }

  useWebSocketStore().subscribe(handleLog);

  function togglePause() {
    if (!isPaused.value) {
      isPaused.value = true;
      return;
    }
    // 恢复：补入暂停期间积压（按键去重），报告补入/溢出数
    isPaused.value = false;
    const pending = pendingEntries;
    pendingEntries = [];
    const dropped = pendingDropped;
    pendingDropped = 0;
    let added = 0;
    const accepted: LogEntry[] = [];
    for (const entry of pending) {
      if (!isDuplicate(entry)) {
        accepted.push(entry);
        added++;
      }
    }
    if (accepted.length > 0) {
      logsCacheVersion++;
      logs.value = [...logs.value, ...accepted].slice(-MAX_LIVE_LOGS);
    }
    resumeReport.value = { added, dropped };
  }

  // ------------------------------------------------------------------ #
  // 历史浏览窗口（服务端搜索；翻页/跳转/回实时由工厂承载）               #
  // ------------------------------------------------------------------ #

  /** 服务端搜索：窗口=本次启动以来（服务端缺省），取最靠新的 500 条命中 */
  async function runSearch(keyword: string): Promise<void> {
    const query = keyword.trim();
    if (!query || windowLoading.value) return;
    windowLoading.value = true;
    try {
      const response = await logsApi.list({ q: query, limit: 500 });
      persistAvailable.value = response.data.persist_available;
      windowStartMs.value = response.data.window_start_ms;
      windowLogs.value = toLogEntries(response.data.entries);
      windowHasOlder.value = response.data.has_more_older;
      windowHasNewer.value = false;
    } catch (error) {
      console.error('搜索日志失败:', error);
    } finally {
      windowLoading.value = false;
    }
  }

  function clearLogs() {
    logs.value = [];
    buffer.exitWindow();
  }

  return {
    logs,
    isPaused,
    windowLogs,
    windowHasOlder,
    windowHasNewer,
    windowLoading,
    persistAvailable,
    windowStartMs,
    resumeReport,
    availableModules,
    clearLogs,
    togglePause,
    fetchOlder: buffer.fetchOlder,
    fetchNewer: buffer.fetchNewer,
    jumpTo: buffer.jumpTo,
    runSearch,
    exitWindow: buffer.exitWindow,
  };
});
