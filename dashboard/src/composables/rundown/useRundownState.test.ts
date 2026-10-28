// @vitest-environment happy-dom
// useRundownState.ts 流程单运行态的测试：挂载即拉取、加载失败回落、状态
// 标签/进度派生、本地 tick 计时（armed 恒零/paused 不漂移/running 按基线
// 累加且封顶）、变更历史排序截断、下一环节预览、手动控制（成功/业务失败/
// 异常）、rundown.changed 防抖重拉与销毁清理。
//
// nowTick 用受控 ref 替换真实共享时钟；WS 经 wsClient.onMessage 捕获的
// 回调触发，防抖计时走 fake timers。

import { defineComponent, h, KeepAlive, nextTick, ref, type Component } from 'vue';
import { mount, type VueWrapper } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { rundownApi } from '@/api';
import { wsClient } from '@/api/websocket';
import { ElMessage } from 'element-plus';
import { useRundownState } from '@/composables/rundown/useRundownState';
import type { RundownSegmentView, RundownStateResponse } from '@/types';

const nowTick = ref(1_000_000);

vi.mock('@/api', async importOriginal => ({
  ...(await importOriginal<object>()),
  rundownApi: {
    getState: vi.fn(),
    control: vi.fn(),
  },
}));

vi.mock('@/api/websocket', () => ({
  wsClient: {
    onMessage: vi.fn(),
    onConnect: vi.fn(),
    onDisconnect: vi.fn(),
    subscribe: vi.fn(),
    unsubscribe: vi.fn(),
  },
}));

vi.mock('@/composables/useNowTick', () => ({
  useNowTick: () => nowTick,
}));

vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

function segment(id: string, expectedMs = 60_000): RundownSegmentView {
  return {
    id,
    title: `环节${id}`,
    task_description: '',
    expected_ms: expectedMs,
    min_duration_ms: null,
    notes: null,
  };
}

function stateResponse(partial: Partial<RundownStateResponse> = {}): RundownStateResponse {
  return {
    available: true,
    message: null,
    snapshot: null,
    transitions: [],
    segments: [segment('a'), segment('b'), segment('c')],
    config: { rundown_id: 'rd_1' },
    ...partial,
  };
}

describe('useRundownState', () => {
  let wrapper: VueWrapper | null = null;
  let out: Partial<ReturnType<typeof useRundownState>>;
  let wsHandler:
    | ((msg: { type: string; timestamp_ms: number; data: Record<string, unknown> }) => void)
    | null = null;

  function makeHost(): Component {
    return defineComponent({
      setup() {
        out = useRundownState();
        return () => h('div', String(out.statusLabel!.value));
      },
    });
  }

  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers({ now: 1_000_000 });
    nowTick.value = 1_000_000;
    out = {};
    wsHandler = null;
    vi.mocked(wsClient.onMessage).mockImplementation(fn => {
      wsHandler = fn as never;
    });
  });

  afterEach(() => {
    wrapper?.unmount();
    wrapper = null;
    vi.useRealTimers();
  });

  async function mountHost(): Promise<void> {
    wrapper = mount(makeHost());
    await nextTick();
    await vi.runOnlyPendingTimersAsync();
    await nextTick();
  }

  function mockState(resp: RundownStateResponse): void {
    vi.mocked(rundownApi.getState).mockResolvedValue({ data: resp } as never);
  }

  it('挂载即拉取状态；加载标记随完成复位', async () => {
    mockState(stateResponse());
    await mountHost();

    expect(rundownApi.getState).toHaveBeenCalledTimes(1);
    expect(out.initialLoading!.value).toBe(false);
    expect(out.loadingState!.value).toBe(false);
    expect(out.state!.value?.config.rundown_id).toBe('rd_1');
  });

  it('拉取失败：loadError 透出后端 detail，state 置空', async () => {
    vi.mocked(rundownApi.getState).mockRejectedValue({
      response: { data: { detail: '流程单服务不可用' } },
    });
    await mountHost();

    expect(out.loadError!.value).toBe('流程单服务不可用');
    expect(out.state!.value).toBeNull();
  });

  it('状态派生：五种状态的标签与 Tag 类型', async () => {
    const cases: Array<
      [
        RundownStateResponse['snapshot'] extends null
          ? never
          : NonNullable<RundownStateResponse['snapshot']>['status'],
        string,
        string,
      ]
    > = [
      ['running', '进行中', 'success'],
      ['armed', '待开播', 'info'],
      ['paused', '已暂停', 'warning'],
      ['done', '已完成', 'info'],
      ['idle', '未启动', 'info'],
    ];
    for (const [status, label, tagType] of cases) {
      mockState(
        stateResponse({
          snapshot: {
            status,
            rundown_id: '',
            title: '',
            current: null,
            index: 0,
            total: 3,
            progress_percent: 10,
            paused: false,
          },
        }),
      );
      await mountHost();
      expect(out.statusLabel!.value).toBe(label);
      expect(out.statusTagType!.value).toBe(tagType);
      wrapper!.unmount();
      wrapper = null;
      out = {};
    }
  });

  it('进度钳制与完成配色', async () => {
    mockState(
      stateResponse({
        snapshot: {
          status: 'running',
          rundown_id: '',
          title: '',
          current: null,
          index: 0,
          total: 3,
          progress_percent: 150,
          paused: false,
        },
      }),
    );
    await mountHost();
    expect(out.progressPercent!.value).toBe(100);
    expect(out.progressColor!.value).toBe('var(--color-rundown)');

    mockState(
      stateResponse({
        snapshot: {
          status: 'done',
          rundown_id: '',
          title: '',
          current: null,
          index: 3,
          total: 3,
          progress_percent: -5,
          paused: false,
        },
      }),
    );
    await mountHost();
    expect(out.progressPercent!.value).toBe(0);
    expect(out.progressColor!.value).toBe('var(--color-info)');
  });

  it('本地 tick：armed 恒零、paused 不漂移、running 按基线累加且封顶', async () => {
    mockState(
      stateResponse({
        snapshot: {
          status: 'running',
          rundown_id: '',
          title: '',
          current: {
            id: 'a',
            title: '环节a',
            expected_ms: 60_000,
            elapsed_ms: 10_000,
            remaining_ms: 50_000,
          },
          index: 0,
          total: 3,
          progress_percent: 10,
          paused: false,
        },
      }),
    );
    await mountHost();
    // 基线 1_000_000，时钟前进 5s → elapsed 15s
    nowTick.value = 1_005_000;
    expect(out.tickElapsedMs!.value).toBe(15_000);
    expect(out.tickRemainingMs!.value).toBe(45_000);

    // 封顶不超过 expected
    nowTick.value = 1_200_000;
    expect(out.tickElapsedMs!.value).toBe(60_000);
    expect(out.tickRemainingMs!.value).toBe(0);

    // paused：停在快照值，不随时钟漂移
    const paused = stateResponse({
      snapshot: {
        status: 'paused',
        rundown_id: '',
        title: '',
        current: {
          id: 'a',
          title: '环节a',
          expected_ms: 60_000,
          elapsed_ms: 10_000,
          remaining_ms: 50_000,
        },
        index: 0,
        total: 3,
        progress_percent: 10,
        paused: true,
      },
    });
    mockState(paused);
    await mountHost();
    nowTick.value = 1_300_000;
    expect(out.tickElapsedMs!.value).toBe(10_000);

    // armed：计时未起算恒零
    const armed = stateResponse({
      snapshot: {
        status: 'armed',
        rundown_id: '',
        title: '',
        current: {
          id: 'a',
          title: '环节a',
          expected_ms: 60_000,
          elapsed_ms: 0,
          remaining_ms: 60_000,
        },
        index: 0,
        total: 3,
        progress_percent: 0,
        paused: false,
      },
    });
    mockState(armed);
    await mountHost();
    nowTick.value = 1_400_000;
    expect(out.tickElapsedMs!.value).toBe(0);
  });

  it('变更历史倒序截断 20 条；下一环节预览与结束置空', async () => {
    const transitions = Array.from({ length: 25 }, (_, i) => ({
      action: 'next',
      segment_id: 'a',
      by: 'human' as const,
      at_ms: i,
    }));
    mockState(
      stateResponse({
        transitions,
        snapshot: {
          status: 'running',
          rundown_id: '',
          title: '',
          current: { id: 'a', title: '环节a', expected_ms: 1, elapsed_ms: 0, remaining_ms: 1 },
          index: 0,
          total: 3,
          progress_percent: 0,
          paused: false,
        },
      }),
    );
    await mountHost();
    expect(out.historyEntries!.value).toHaveLength(20);
    expect(out.historyEntries!.value[0].at_ms).toBe(24);
    expect(out.nextSegment!.value?.id).toBe('b');

    const done = stateResponse({
      transitions,
      snapshot: {
        status: 'done',
        rundown_id: '',
        title: '',
        current: null,
        index: 3,
        total: 3,
        progress_percent: 100,
        paused: false,
      },
    });
    mockState(done);
    await mountHost();
    expect(out.nextSegment!.value).toBeNull();
  });

  it('手动控制：成功回执内嵌 snapshot 就地刷新', async () => {
    const snapA = {
      status: 'running',
      rundown_id: '',
      title: '',
      current: { id: 'a', title: '环节a', expected_ms: 1, elapsed_ms: 0, remaining_ms: 1 },
      index: 0,
      total: 3,
      progress_percent: 0,
      paused: false,
    } as NonNullable<RundownStateResponse['snapshot']>;
    mockState(stateResponse({ snapshot: snapA }));
    await mountHost();

    const snapB = { ...snapA, index: 1 };
    vi.mocked(rundownApi.control).mockResolvedValue({
      data: { success: true, message: '已切下一环节', snapshot: snapB },
    } as never);
    const result = await out.performControl!('next');

    expect(rundownApi.control).toHaveBeenCalledWith({ action: 'next' });
    expect(ElMessage.success).toHaveBeenCalledWith('已切下一环节');
    expect(out.state!.value?.snapshot?.index).toBe(1);
    expect(result).toEqual(snapB);
  });

  it('手动控制：业务失败提示错误；异常透出 detail；无 snapshot 回拉', async () => {
    mockState(stateResponse());
    await mountHost();

    vi.mocked(rundownApi.control).mockResolvedValue({
      data: { success: false, message: '已在末段', snapshot: null },
    } as never);
    expect(await out.performControl!('next')).toBeNull();
    expect(ElMessage.error).toHaveBeenCalledWith('已在末段');

    vi.mocked(rundownApi.control).mockRejectedValue({
      response: { data: { detail: '流程单未运行' } },
    });
    expect(await out.performControl!('next')).toBeNull();
    expect(ElMessage.error).toHaveBeenCalledWith('流程单未运行');

    vi.mocked(rundownApi.control).mockResolvedValue({
      data: { success: true, message: 'ok', snapshot: null },
    } as never);
    await out.performControl!('next');
    expect(rundownApi.getState).toHaveBeenCalledTimes(2);
  });

  it('togglePause 按 paused 态选 resume/pause', async () => {
    mockState(
      stateResponse({
        snapshot: {
          status: 'running',
          rundown_id: '',
          title: '',
          current: null,
          index: 0,
          total: 3,
          progress_percent: 0,
          paused: false,
        },
      }),
    );
    await mountHost();
    vi.mocked(rundownApi.control).mockResolvedValue({
      data: { success: true, message: 'ok', snapshot: null },
    } as never);

    out.togglePause!();
    await vi.runOnlyPendingTimersAsync();
    expect(rundownApi.control).toHaveBeenCalledWith({ action: 'pause' });
  });

  it('rundown.changed 事件 300ms 防抖重拉；其他事件不触发', async () => {
    mockState(stateResponse());
    await mountHost();
    expect(rundownApi.getState).toHaveBeenCalledTimes(1);
    expect(wsHandler).not.toBeNull();

    wsHandler!({ type: 'agent.replied', timestamp_ms: 1, data: {} });
    await vi.advanceTimersByTimeAsync(1_000);
    expect(rundownApi.getState).toHaveBeenCalledTimes(1);

    wsHandler!({ type: 'rundown.changed', timestamp_ms: 2, data: {} });
    wsHandler!({ type: 'rundown.changed', timestamp_ms: 3, data: {} });
    await vi.advanceTimersByTimeAsync(100);
    wsHandler!({ type: 'rundown.changed', timestamp_ms: 4, data: {} });
    await vi.advanceTimersByTimeAsync(299);
    expect(rundownApi.getState).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(rundownApi.getState).toHaveBeenCalledTimes(2);
  });

  it('销毁后：防抖计时器清除，迟到事件不再触发拉取', async () => {
    mockState(stateResponse());
    await mountHost();
    expect(rundownApi.getState).toHaveBeenCalledTimes(1);

    wrapper!.unmount();
    wrapper = null;
    wsHandler!({ type: 'rundown.changed', timestamp_ms: 2, data: {} });
    await vi.advanceTimersByTimeAsync(1_000);
    expect(rundownApi.getState).toHaveBeenCalledTimes(1);
  });

  it('keep-alive 首次挂载激活不重复拉取', async () => {
    mockState(stateResponse());
    const show = ref(true);
    wrapper = mount(
      defineComponent({
        setup() {
          const host = makeHost();
          return () => h(KeepAlive, () => (show.value ? h(host) : null));
        },
      }),
    );
    await nextTick();
    await vi.runOnlyPendingTimersAsync();
    expect(rundownApi.getState).toHaveBeenCalledTimes(1);
  });

  it('keep-alive 隐藏丢弃在途防抖并停订阅；恢复激活静默重拉并恢复订阅', async () => {
    mockState(stateResponse());
    const show = ref(true);
    wrapper = mount(
      defineComponent({
        setup() {
          const host = makeHost();
          return () => h(KeepAlive, () => (show.value ? h(host) : null));
        },
      }),
    );
    await nextTick();
    await vi.runOnlyPendingTimersAsync();
    expect(rundownApi.getState).toHaveBeenCalledTimes(1);

    // 隐藏前防抖在途：deactivated 放弃计时，期间到达的事件不触发拉取
    wsHandler!({ type: 'rundown.changed', timestamp_ms: 2, data: {} });
    show.value = false;
    await nextTick();
    await vi.advanceTimersByTimeAsync(1_000);
    expect(rundownApi.getState).toHaveBeenCalledTimes(1);

    // 恢复激活：静默重拉一次快照，并恢复 WS 订阅（防抖重拉再次生效）
    show.value = true;
    await nextTick();
    await vi.runOnlyPendingTimersAsync();
    expect(rundownApi.getState).toHaveBeenCalledTimes(2);

    wsHandler!({ type: 'rundown.changed', timestamp_ms: 3, data: {} });
    await vi.advanceTimersByTimeAsync(300);
    expect(rundownApi.getState).toHaveBeenCalledTimes(3);
  });
});
