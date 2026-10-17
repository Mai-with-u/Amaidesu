import { defineStore } from 'pinia';
import { ref, shallowRef } from 'vue';
import { useWebSocketStore } from './websocket';
import { eventsApi } from '@/api';
import type { WebSocketMessage } from '@/types';

const MAX_EVENTS = 1000;
/** 历史浏览窗口上限（约 2 页）：渲染面积有界，超出沿页进方向裁剪对侧 */
const MAX_WINDOW_EVENTS = 1000;
const PAGE_SIZE = 500;
/** 游标持久化键：记录最后收到的事件 id，刷新/重连后按此补缺口 */
const CURSOR_KEY = 'amaidesu-events-cursor';

/** 合法游标 id：后端事件 id（uuid4）或数字；合成前缀（stub-/live-）不入游标 */
const CURSOR_ID_RE = /^(?:\d+|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/i;

/** 事件面板条目：在 WebSocketMessage 基础上附加去重 id */
interface LoggedEvent extends WebSocketMessage {
  id: string;
}

/** 合并历史与当前条目：按 id 去重（历史优先）、时间升序、限长保尾 */
function mergeEvents(history: LoggedEvent[], current: LoggedEvent[]): LoggedEvent[] {
  const byId = new Map<string, LoggedEvent>();
  for (const event of [...current, ...history]) {
    byId.set(event.id, event);
  }
  return [...byId.values()].sort((a, b) => a.timestamp_ms - b.timestamp_ms).slice(-MAX_EVENTS);
}

/** 浏览窗口并入：按 id 去重、时间升序、按窗口上限沿页进方向裁剪 */
function mergeWindowEvents(
  chunk: LoggedEvent[],
  base: LoggedEvent[],
  keepHead: boolean,
): LoggedEvent[] {
  const byId = new Map<string, LoggedEvent>();
  for (const event of [...base, ...chunk]) {
    byId.set(event.id, event);
  }
  const merged = [...byId.values()].sort((a, b) => a.timestamp_ms - b.timestamp_ms);
  return keepHead ? merged.slice(0, MAX_WINDOW_EVENTS) : merged.slice(-MAX_WINDOW_EVENTS);
}

/**
 * 后端 events.history 载荷 / REST 回填条目（EventRecord 转储）→ LoggedEvent。
 * 按最小形状（id/type/timestamp_ms/data）校验，过滤异常条目，替代裸类型断言。
 */
function toLoggedEvent(value: unknown): LoggedEvent | null {
  if (typeof value !== 'object' || value === null) return null;
  const record = value as Record<string, unknown>;
  if (typeof record.id !== 'string' || record.id === '') return null;
  if (typeof record.type !== 'string') return null;
  if (typeof record.timestamp_ms !== 'number' || !Number.isFinite(record.timestamp_ms)) return null;
  return {
    type: record.type,
    timestamp_ms: record.timestamp_ms,
    data: (typeof record.data === 'object' && record.data !== null ? record.data : {}) as Record<
      string,
      unknown
    >,
    id: record.id,
  };
}

/** 未知条目数组 → LoggedEvent 列表（逐条校验，丢弃不合规项） */
function toLoggedEvents(value: unknown): LoggedEvent[] {
  if (!Array.isArray(value)) return [];
  return value.map(toLoggedEvent).filter((event): event is LoggedEvent => event !== null);
}

function loadCursor(): string {
  try {
    return localStorage.getItem(CURSOR_KEY) ?? '';
  } catch {
    return '';
  }
}

function saveCursor(id: string): void {
  try {
    localStorage.setItem(CURSOR_KEY, id);
  } catch {
    /* 存储不可用时游标退化为会话内有效 */
  }
}

export const useEventsStore = defineStore('events', () => {
  const events = shallowRef<LoggedEvent[]>([]);
  /** 断线/刷新续传游标（最后收到的事件 id） */
  const cursor = ref<string>(loadCursor());
  const backfillDone = ref(false);
  /** 历史浏览窗口：null = 实时模式；非 null = 显示该窗口（时间跳转/加载更早） */
  const windowEvents = shallowRef<LoggedEvent[] | null>(null);
  /** 窗口双向续翻信号（服务端 has_more 语义） */
  const windowHasOlder = ref(true);
  const windowHasNewer = ref(false);
  const windowLoading = ref(false);

  /** 断线/刷新续传游标（最后收到的事件 id）；合成条目不入游标 */
  function advanceCursor(id?: string): void {
    if (!id || !CURSOR_ID_RE.test(id)) return;
    cursor.value = id;
    saveCursor(id);
  }

  function handleMessage(message: WebSocketMessage) {
    // 观测流（kind="stream"，如 thinking.delta）不入事件通道——独立缓冲，防污染事件历史与游标
    if (message.kind === 'stream') return;
    // 后端初始历史：与当前条目合并（幂等，任意到达顺序，避免替换吞掉已到达的实时事件）
    if (message.type === 'events.history') {
      const history = toLoggedEvents(message.data.events);
      events.value = mergeEvents(history, events.value);
      return;
    }
    // 只有总线事件进事件缓冲：日志通道（kind="log"）与握手帧（kind="transport"）
    // 在 WS 协议层已被标记，事件页不再消费
    if (message.kind !== 'event') return;
    // 常规实时事件：附加去重 id 后合并（限长）；后端已带 id 时直接使用（与历史同源）
    const id = message.id ?? `live-${crypto.randomUUID()}`;
    events.value = mergeEvents([], [...events.value, { ...message, id }]);
    if (message.id) advanceCursor(message.id);
  }

  useWebSocketStore().subscribe(handleMessage);

  /**
   * 游标回填：刷新/重连后按游标拉取事件缺口（REST），与 WS 历史合并去重。
   * 无游标（首次访问）时跳过——WS 连接时的 events.history 已覆盖最近窗口。
   */
  async function backfill(): Promise<void> {
    if (backfillDone.value) return;
    backfillDone.value = true;
    // 合成条目不是后端事件，不能当续传游标
    if (!cursor.value || !CURSOR_ID_RE.test(cursor.value)) return;
    try {
      const response = await eventsApi.list({ since_id: cursor.value, limit: MAX_EVENTS });
      const gap = toLoggedEvents(response.data.events);
      if (gap.length > 0) {
        events.value = mergeEvents(gap, events.value);
        advanceCursor(gap[gap.length - 1]?.id);
      }
    } catch {
      // 回填失败静默：WS events.history 兜底，不阻断页面
    }
  }

  /** 当前浏览基线：窗口模式用窗口，实时模式用实时缓冲 */
  function browsingBase(): LoggedEvent[] {
    return windowEvents.value ?? events.value;
  }

  function guardWindowLoading(): boolean {
    if (windowLoading.value) return false;
    windowLoading.value = true;
    return true;
  }

  /** "加载更早"：从当前浏览基线最旧一条向前翻一页（实时模式下即进入窗口模式） */
  async function fetchOlder(): Promise<void> {
    const base = browsingBase();
    const oldest = base[0];
    if (!oldest || !guardWindowLoading()) return;
    try {
      const response = await eventsApi.list({
        before_timestamp_ms: oldest.timestamp_ms,
        limit: PAGE_SIZE,
      });
      windowEvents.value = mergeWindowEvents(toLoggedEvents(response.data.events), base, false);
      windowHasOlder.value = response.data.has_more;
      if (typeof response.data.has_more_newer === 'boolean' && !windowHasNewer.value) {
        windowHasNewer.value = response.data.has_more_newer;
      }
    } catch (error) {
      console.error('加载更早事件失败:', error);
    } finally {
      windowLoading.value = false;
    }
  }

  /** "加载更晚"：从窗口最新一条向后补一页（时间跳转后追赶实时边缘） */
  async function fetchNewer(): Promise<void> {
    const base = browsingBase();
    const newest = base[base.length - 1];
    if (!newest || !guardWindowLoading()) return;
    try {
      const response = await eventsApi.list({
        start_ms: newest.timestamp_ms + 1,
        take: 'oldest',
        limit: PAGE_SIZE,
      });
      windowEvents.value = mergeWindowEvents(toLoggedEvents(response.data.events), base, true);
      windowHasNewer.value =
        response.data.has_more_newer ?? response.data.events.length >= PAGE_SIZE;
    } catch (error) {
      console.error('加载更晚事件失败:', error);
    } finally {
      windowLoading.value = false;
    }
  }

  /** 时间跳转：显示 [ts, …) 之后的一页（复盘跨启动定位用） */
  async function jumpTo(timestampMs: number): Promise<void> {
    if (!guardWindowLoading()) return;
    try {
      const response = await eventsApi.list({
        start_ms: timestampMs,
        take: 'oldest',
        limit: PAGE_SIZE,
      });
      windowEvents.value = toLoggedEvents(response.data.events);
      windowHasOlder.value = response.data.has_more;
      windowHasNewer.value = response.data.has_more_newer ?? false;
    } catch (error) {
      console.error('时间跳转失败:', error);
    } finally {
      windowLoading.value = false;
    }
  }

  /** 回到实时：丢弃浏览窗口 */
  function exitWindow(): void {
    windowEvents.value = null;
    windowHasOlder.value = true;
    windowHasNewer.value = false;
  }

  function clearEvents() {
    events.value = [];
    windowEvents.value = null;
    windowHasOlder.value = true;
    windowHasNewer.value = false;
  }

  // 启动即回填（fire-and-forget）：有游标则补缺口，无游标由 WS events.history 兜底

  return {
    events,
    cursor,
    windowEvents,
    windowHasOlder,
    windowHasNewer,
    windowLoading,
    clearEvents,
    backfill,
    fetchOlder,
    fetchNewer,
    jumpTo,
    exitWindow,
  };
});
