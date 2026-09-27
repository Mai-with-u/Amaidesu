// useLiveTimeline.ts 时间线内容合成的测试
//
// 覆盖：实时条目重建节流、暂停冻结、清空水位（事件 id + 思考行时间水位）、
// Agent 组过滤（room 组始终可见）、回看加载触发与失败提示、统一 entries 出口。

import { computed, effectScope, ref, type EffectScope, type Ref } from 'vue';
import { createPinia, setActivePinia } from 'pinia';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { liveSessionsApi } from '@/api';
import { ElMessage } from 'element-plus';
import { useEventsStore, useWebSocketStore } from '@/stores';
import { useLiveTimeline } from '@/composables/live/useLiveTimeline';
import { makeEntry, type ShowEntry } from '@/utils/liveFeed';
import type { LiveSessionItem, WebSocketMessage } from '@/types';

vi.mock('@/api/websocket', () => ({
  wsClient: {
    onConnect: vi.fn(),
    onDisconnect: vi.fn(),
    onMessage: vi.fn(),
    subscribe: vi.fn(),
    unsubscribe: vi.fn(),
    connect: vi.fn().mockResolvedValue(undefined),
    disconnect: vi.fn(),
    isConnected: vi.fn(() => false),
  },
}));

vi.mock('@/api', async importOriginal => ({
  ...(await importOriginal<object>()),
  liveSessionsApi: {
    list: vi.fn(),
    open: vi.fn(),
    close: vi.fn(),
    remove: vi.fn(),
    timeline: vi.fn(),
  },
}));

vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  ElMessageBox: { prompt: vi.fn() },
}));

const timelineMock = vi.mocked(liveSessionsApi.timeline);

function event(
  id: string,
  type: string,
  tsMs: number,
  data: Record<string, unknown> = {},
): WebSocketMessage & { id: string } {
  return { id, type, timestamp_ms: tsMs, data };
}

function danmakuEvent(
  id: string,
  tsMs: number,
  content = 'hello',
): WebSocketMessage & { id: string } {
  return event(id, 'room.message', tsMs, {
    message_type: 'danmaku',
    content,
    user: { nickname: '观众' },
  });
}

function thinkingRow(id: string, tsMs: number): ShowEntry {
  return makeEntry({ id, kind: 'thinking', tsMs, actor: '思考', text: id });
}

describe('useLiveTimeline', () => {
  let scope: EffectScope;
  let rawThinkingRows: Ref<ShowEntry[]>;
  /** 模拟真实注入形状：useThinkingStream.liveThinkingRows 已按水位自过滤 */
  let thinkingRows: Ref<ShowEntry[]>;
  let watermark: Ref<number>;
  let sessionMode: Ref<'live' | 'replay'>;
  let selectedSession: Ref<LiveSessionItem | null>;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.useFakeTimers();
    useWebSocketStore();
    scope = effectScope();
    rawThinkingRows = ref([]);
    watermark = ref(0);
    thinkingRows = computed(() => rawThinkingRows.value.filter(row => row.tsMs > watermark.value));
    sessionMode = ref('live');
    selectedSession = ref(null);
    timelineMock.mockResolvedValue({ data: { live_session_id: 1, items: [] } } as never);
  });

  afterEach(() => {
    scope.stop();
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  function mount() {
    return scope.run(() =>
      useLiveTimeline({
        liveThinkingRows: thinkingRows,
        thinkingHiddenBeforeMs: watermark,
        sessionMode,
        selectedSession,
      }),
    )!;
  }

  it('实时条目经节流重建：事件到达后 250ms 内落地', async () => {
    const composable = mount();
    const store = useEventsStore();
    store.events = [danmakuEvent('e1', 1000)];
    await vi.advanceTimersByTimeAsync(250);
    expect(composable.entries.value.some(entry => entry.kind === 'danmaku')).toBe(true);
  });

  it('暂停期不重建（冻结）；恢复后追平', async () => {
    const composable = mount();
    const store = useEventsStore();
    store.events = [danmakuEvent('e1', 1000)];
    await vi.advanceTimersByTimeAsync(250);
    const frozenCount = composable.entries.value.length;
    expect(frozenCount).toBeGreaterThan(0);

    composable.togglePause();
    store.events = [...store.events, danmakuEvent('e2', 2000)];
    await vi.advanceTimersByTimeAsync(500);
    expect(composable.entries.value.length).toBe(frozenCount);

    composable.togglePause();
    await vi.advanceTimersByTimeAsync(250);
    expect(composable.entries.value.length).toBe(frozenCount + 1);
  });

  it('归并封顶：事件+思考行合计超 MAX_ENTRIES 时限长保尾', async () => {
    const composable = mount();
    const store = useEventsStore();
    store.events = [danmakuEvent('e1', 0)];
    // 450 条思考行（ts 1..450），事件条目在 ts 0；归并共 451 条
    const rows: ShowEntry[] = [];
    for (let ts = 1; ts <= 450; ts += 1) {
      rows.push(thinkingRow(`think-${ts}`, ts));
    }
    rawThinkingRows.value = rows;
    await vi.advanceTimersByTimeAsync(250);
    expect(composable.entries.value).toHaveLength(400);
    // 保尾：最旧的 ts 0..50（事件 + 前 50 条思考行）被淘汰，首条为 ts 51
    expect(composable.entries.value[0].tsMs).toBe(51);
    expect(composable.entries.value[399].tsMs).toBe(450);
  });

  it('clearTimeline：事件按 id 隐藏、思考行按时间水位隐藏，新事件仍会进入', async () => {
    const composable = mount();
    const store = useEventsStore();
    store.events = [danmakuEvent('e1', 1000)];
    await vi.advanceTimersByTimeAsync(250);
    expect(composable.entries.value.length).toBeGreaterThan(0);

    rawThinkingRows.value = [thinkingRow('think-1', 5000)];
    composable.clearTimeline();
    expect(composable.entries.value).toHaveLength(0);
    expect(watermark.value).toBe(5000);

    // 清空后新事件不带走旧 id，照常入列
    store.events = [...store.events, danmakuEvent('e2', 6000)];
    await vi.advanceTimersByTimeAsync(250);
    expect(composable.entries.value.some(entry => entry.id === 'e2')).toBe(true);
  });

  it('agent 过滤：只影响 Agent 组卡片，观众消息（room 组）始终可见', async () => {
    const composable = mount();
    const store = useEventsStore();
    store.events = [
      danmakuEvent('e1', 1000),
      event('e2', 'streamer.stage', 2000, { stage: 'planning', agent_state: 'running' }),
    ];
    await vi.advanceTimersByTimeAsync(250);
    expect(composable.entries.value.length).toBe(2);

    composable.handleAgentChip('game', true);
    const filtered = composable.entries.value;
    expect(filtered.some(entry => entry.kind === 'danmaku')).toBe(true);
    expect(filtered.some(entry => entry.kind === 'stage')).toBe(false);
  });

  it('回看模式：选中历史场次即拉取 REST 时间线；失败提示并清空', async () => {
    const composable = mount();
    const replayItem: LiveSessionItem = {
      live_session_id: 5,
      source: 'manual',
      title: '回看场',
      room_id: 'room',
      platform: 'simulator',
      started_at_ms: 1,
      ended_at_ms: 2,
      message_count: 1,
      is_active: false,
    };
    timelineMock.mockResolvedValueOnce({
      data: {
        live_session_id: 5,
        items: [{ kind: 'danmaku', ts_ms: 100, user_name: '小明', content: '回放弹幕' }],
      },
    } as never);
    sessionMode.value = 'replay';
    selectedSession.value = replayItem;
    await vi.advanceTimersByTimeAsync(0);
    expect(timelineMock).toHaveBeenCalledWith(5);
    expect(composable.entries.value).toHaveLength(1);
    expect(composable.entries.value[0]).toMatchObject({ kind: 'danmaku', text: '回放弹幕' });

    timelineMock.mockRejectedValueOnce(new Error('gone'));
    selectedSession.value = { ...replayItem, live_session_id: 6 };
    await vi.advanceTimersByTimeAsync(0);
    expect(ElMessage.error).toHaveBeenCalledWith('gone');
    expect(composable.entries.value).toEqual([]);
  });
});
