/* eslint-disable vue/one-component-per-file -- 测试需要 KeepAlive 宿主与消费组件 */
// @vitest-environment happy-dom
// useAgentLead.ts 动态主从观测与切权组合式的测试
//
// 覆盖：
//   1. 纯函数（主控徽章文案、模式标签、一键切换选项矩阵、WS 载荷就地合入）
//   2. 组合式状态拉取与 503 静默降级、手动切换成功/失败（经 getApiErrorMessage 反馈）、禁用态防误点
//   3. WebSocket 推送 agent.lead.changed 的实时更新 + 防抖补拉，以及 KeepAlive 失活退订/再激活恢复

import { defineComponent, h, KeepAlive } from 'vue';
import { mount, type VueWrapper } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { agentsApi } from '@/api';
import {
  applyLeadChangedPayload,
  buildLeadSwitchOptions,
  leadBadgeLabelOf,
  leadModeDetailOf,
  leadModeLabelOf,
  switchConfirmMessageOf,
  useAgentLead,
} from '@/composables/useAgentLead';
import { useWebSocketStore } from '@/stores';
import type { LeadStateResponse, WebSocketMessage, WsMessageType } from '@/types';

const { elMessageSuccess, elMessageError, elMessageBoxConfirm } = vi.hoisted(() => ({
  elMessageSuccess: vi.fn(),
  elMessageError: vi.fn(),
  elMessageBoxConfirm: vi.fn(),
}));

vi.mock('element-plus', () => ({
  ElMessage: {
    success: elMessageSuccess,
    error: elMessageError,
  },
  ElMessageBox: {
    confirm: elMessageBoxConfirm,
  },
}));

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
  agentsApi: {
    getAgentLeadState: vi.fn(),
    switchAgentLead: vi.fn(),
  },
}));

const getLeadMock = vi.mocked(agentsApi.getAgentLeadState);
const switchLeadMock = vi.mocked(agentsApi.switchAgentLead);

function leadSnapshot(patch: Partial<LeadStateResponse> = {}): LeadStateResponse {
  return {
    mode: 'streamer',
    lead_agent: 'streamer',
    override: 'auto',
    enabled: true,
    candidates: ['minecraft'],
    ready_candidates: ['minecraft'],
    since_ms: 1_000_000,
    reason: '',
    ...patch,
  };
}

describe('useAgentLead 纯函数', () => {
  it('leadBadgeLabelOf：主播主控 / 游戏主控（<agent>） / 禁用态固定主播主控', () => {
    expect(leadBadgeLabelOf(null)).toBe('');
    expect(leadBadgeLabelOf(leadSnapshot())).toBe('主播主控');
    expect(leadBadgeLabelOf(leadSnapshot({ mode: 'game', lead_agent: 'minecraft' }))).toBe(
      '游戏主控（minecraft）',
    );
    // enabled=false 即使字段漂移也固定显示主播主控
    expect(
      leadBadgeLabelOf(leadSnapshot({ enabled: false, mode: 'game', lead_agent: 'minecraft' })),
    ).toBe('主播主控');
  });

  it('leadModeLabelOf / leadModeDetailOf：区分自动跟随、手动锁定与固定主播主控（streamer_only）', () => {
    expect(leadModeLabelOf(leadSnapshot({ override: 'auto' }))).toBe('自动跟随');
    expect(leadModeDetailOf(leadSnapshot({ override: 'auto' }))).toBe('自动跟随');

    expect(leadModeLabelOf(leadSnapshot({ override: 'minecraft' }))).toBe('手动锁定');
    expect(leadModeDetailOf(leadSnapshot({ override: 'minecraft' }))).toBe('手动锁定（minecraft）');
    expect(leadModeDetailOf(leadSnapshot({ override: 'streamer' }))).toBe('手动锁定（主播）');

    expect(leadModeLabelOf(leadSnapshot({ enabled: false }))).toBe('固定主播主控');
    expect(leadModeDetailOf(leadSnapshot({ enabled: false }))).toBe(
      '固定主播主控（streamer_only）',
    );
  });

  it('buildLeadSwitchOptions：自动/主播/候选游戏矩阵，未就绪游戏置灰、禁用态全置灰', () => {
    const opts = buildLeadSwitchOptions(
      leadSnapshot({
        override: 'auto',
        candidates: ['minecraft', 'factorio'],
        ready_candidates: ['minecraft'],
      }),
    );
    expect(
      opts.map(o => ({ target: o.target, active: o.active, disabled: o.disabled, ready: o.ready })),
    ).toEqual([
      { target: 'auto', active: true, disabled: false, ready: true },
      { target: 'streamer', active: false, disabled: false, ready: true },
      { target: 'minecraft', active: false, disabled: false, ready: true },
      { target: 'factorio', active: false, disabled: true, ready: false },
    ]);

    const disabledOpts = buildLeadSwitchOptions(leadSnapshot({ enabled: false }));
    expect(disabledOpts.every(o => o.disabled && !o.active)).toBe(true);
  });

  it('switchConfirmMessageOf：按目标区分打断语义，主播→主播免确认', () => {
    const gameLead = leadSnapshot({ mode: 'game', lead_agent: 'minecraft' });
    expect(switchConfirmMessageOf('factorio', gameLead)).toBe(
      '切换到 factorio 主控将打断它当前的自主执行，确认？',
    );
    expect(switchConfirmMessageOf('streamer', gameLead)).toBe(
      '收回 minecraft 的主控权将打断其当前执行，确认？',
    );
    // 当前已是主播主控：切向主播不打断任何执行，免确认
    expect(switchConfirmMessageOf('streamer', leadSnapshot())).toBeNull();
    expect(switchConfirmMessageOf('auto', gameLead)).toBe('恢复自动跟随，确认？');
  });

  it('applyLeadChangedPayload：就地更新 mode/lead_agent/override/reason/since_ms 并保留候选名单', () => {
    const base = leadSnapshot({ candidates: ['minecraft'], ready_candidates: ['minecraft'] });
    const updated = applyLeadChangedPayload(
      base,
      {
        mode: 'game',
        lead_agent: 'minecraft',
        manual_override: 'minecraft',
        reason: 'manual_override',
        timestamp_ms: 2_000_000,
      },
      1_500_000,
    );
    expect(updated).toEqual({
      ...base,
      mode: 'game',
      lead_agent: 'minecraft',
      override: 'minecraft',
      reason: 'manual_override',
      since_ms: 2_000_000,
    });

    // manual_override 为 null 时复位为 auto
    const backAuto = applyLeadChangedPayload(
      updated,
      {
        mode: 'streamer',
        lead_agent: 'streamer',
        manual_override: null,
        reason: 'rundown_changed',
      },
      3_000_000,
    );
    expect(backAuto.override).toBe('auto');
    expect(backAuto.mode).toBe('streamer');
    expect(backAuto.since_ms).toBe(3_000_000);
  });
});

describe('useAgentLead 组合式与 KeepAlive 生命周期', () => {
  let wrapper: VueWrapper | null = null;

  beforeEach(() => {
    vi.useFakeTimers();
    setActivePinia(createPinia());
    getLeadMock.mockReset();
    switchLeadMock.mockReset();
    elMessageSuccess.mockReset();
    elMessageError.mockReset();
    // 默认确认框都点确定；单测里再按需改写为拒绝
    elMessageBoxConfirm.mockResolvedValue(true);
  });

  afterEach(() => {
    wrapper?.unmount();
    wrapper = null;
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  /** 在 KeepAlive 中挂载消费组件，并暴露组合式返回值与 WS 消息投喂入口 */
  function mountLeadInKeepAlive() {
    let api!: ReturnType<typeof useAgentLead>;
    const handlers = new Set<(msg: WebSocketMessage) => void>();
    const wsStore = useWebSocketStore();
    vi.spyOn(wsStore, 'subscribe').mockImplementation(fn => {
      handlers.add(fn);
    });
    vi.spyOn(wsStore, 'unsubscribe').mockImplementation(fn => {
      handlers.delete(fn);
    });

    const Consumer = defineComponent({
      setup() {
        api = useAgentLead();
        return () => h('div');
      },
    });
    const Host = defineComponent({
      props: { show: { type: Boolean, default: true } },
      setup(props) {
        return () => h(KeepAlive, () => (props.show ? h(Consumer) : null));
      },
    });

    wrapper = mount(Host);
    return {
      wrapper,
      getApi: () => api,
      subscriberCount: () => handlers.size,
      emitWs: (msg: WebSocketMessage) => {
        for (const fn of [...handlers]) fn(msg);
      },
    };
  }

  it('首屏拉取主从状态，503 时静默降级为不可用且不弹窗', async () => {
    getLeadMock.mockRejectedValueOnce({ response: { status: 503, data: { detail: '未注入' } } });
    const { getApi } = mountLeadInKeepAlive();
    await vi.waitFor(() => expect(getLeadMock).toHaveBeenCalledTimes(1));
    await Promise.resolve();
    expect(getApi().available.value).toBe(false);
    expect(elMessageError).not.toHaveBeenCalled();
  });

  it('switchLead 成功时更新快照并提示成功，失败时经 getApiErrorMessage 展示后端 detail', async () => {
    getLeadMock.mockResolvedValueOnce({ data: leadSnapshot() } as never);
    const { getApi } = mountLeadInKeepAlive();
    await vi.waitFor(() => expect(getApi().available.value).toBe(true));

    const gameSnap = leadSnapshot({
      mode: 'game',
      lead_agent: 'minecraft',
      override: 'minecraft',
      reason: 'manual_override',
    });
    switchLeadMock.mockResolvedValueOnce({ data: gameSnap } as never);
    await getApi().switchLead('minecraft');
    expect(switchLeadMock).toHaveBeenCalledWith({ target: 'minecraft', reason: 'manual_override' });
    expect(getApi().badgeLabel.value).toBe('游戏主控（minecraft）');
    expect(getApi().modeLabel.value).toBe('手动锁定');
    expect(elMessageSuccess).toHaveBeenCalledWith('已切换为游戏主控（minecraft）');

    // 409 业务拒绝：穿透 FastAPI detail
    switchLeadMock.mockRejectedValueOnce({
      response: { status: 409, data: { detail: '目标游戏 minecraft 当前未就绪' } },
    });
    await getApi().switchLead('minecraft');
    expect(getApi().errorMessage.value).toBe('目标游戏 minecraft 当前未就绪');
    expect(elMessageError).toHaveBeenCalledWith('目标游戏 minecraft 当前未就绪');
  });

  it('switchLead 前弹二次确认：取消即中止（不发请求、不弹错误），确认后正常执行', async () => {
    getLeadMock.mockResolvedValueOnce({ data: leadSnapshot() } as never);
    const { getApi } = mountLeadInKeepAlive();
    await vi.waitFor(() => expect(getApi().available.value).toBe(true));

    // 取消：ElMessageBox 以 reject 表达取消（confirmAction 捕获后回 false），中止且不算错误
    elMessageBoxConfirm.mockRejectedValueOnce(new Error('cancel'));
    const result = await getApi().switchLead('minecraft');
    expect(result).toBeNull();
    expect(switchLeadMock).not.toHaveBeenCalled();
    expect(elMessageError).not.toHaveBeenCalled();
    expect(getApi().switchingTarget.value).toBeNull();

    // 确认：文案带目标名，请求正常发出
    const gameSnap = leadSnapshot({ mode: 'game', lead_agent: 'minecraft', override: 'minecraft' });
    switchLeadMock.mockResolvedValueOnce({ data: gameSnap } as never);
    await getApi().switchLead('minecraft');
    expect(elMessageBoxConfirm).toHaveBeenCalledWith(
      '切换到 minecraft 主控将打断它当前的自主执行，确认？',
      '切换主控权',
      expect.objectContaining({ type: 'warning' }),
    );
    expect(switchLeadMock).toHaveBeenCalledWith({ target: 'minecraft', reason: 'manual_override' });
  });

  it('激活态每 20 秒静默轮询兜底刷新快照，失活停止轮询', async () => {
    getLeadMock.mockResolvedValue({ data: leadSnapshot() } as never);
    const { wrapper: host } = mountLeadInKeepAlive();
    await vi.waitFor(() => expect(getLeadMock).toHaveBeenCalledTimes(1));

    // 轮询触发静默 refresh（激活态 20s 周期）
    await vi.advanceTimersByTimeAsync(20_000);
    expect(getLeadMock).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(20_000);
    expect(getLeadMock).toHaveBeenCalledTimes(3);

    // 失活：不再轮询
    await host.setProps({ show: false });
    await vi.advanceTimersByTimeAsync(60_000);
    expect(getLeadMock).toHaveBeenCalledTimes(3);

    // 再激活：恢复轮询；onActivated 的静默补拉先走一次，20 秒后轮询再走
    await host.setProps({ show: true });
    await vi.waitFor(() => expect(getLeadMock).toHaveBeenCalledTimes(4));
    await vi.advanceTimersByTimeAsync(20_000);
    expect(getLeadMock).toHaveBeenCalledTimes(5);
  });

  it('enabled=false（streamer_only）时不发起切换请求', async () => {
    getLeadMock.mockResolvedValueOnce({ data: leadSnapshot({ enabled: false }) } as never);
    const { getApi } = mountLeadInKeepAlive();
    await vi.waitFor(() => expect(getApi().available.value).toBe(true));

    const result = await getApi().switchLead('minecraft');
    expect(result).toBeNull();
    expect(switchLeadMock).not.toHaveBeenCalled();
  });

  it('监听 WS agent.lead.changed：就地刷新状态 + 防抖补拉，失活退订、再激活恢复并重拉', async () => {
    getLeadMock.mockResolvedValue({ data: leadSnapshot() } as never);
    const { wrapper: host, getApi, emitWs, subscriberCount } = mountLeadInKeepAlive();
    await vi.waitFor(() => expect(getLeadMock).toHaveBeenCalledTimes(1));
    expect(subscriberCount()).toBe(1);

    // 收到 WS 推送立即刷新徽章，200ms 后静默补拉全量快照
    const updatedSnap = leadSnapshot({
      mode: 'game',
      lead_agent: 'minecraft',
      override: 'auto',
      reason: 'rundown_changed',
    });
    getLeadMock.mockResolvedValueOnce({ data: updatedSnap } as never);
    emitWs({
      type: 'agent.lead.changed' as WsMessageType,
      timestamp_ms: 5_000,
      data: {
        mode: 'game',
        lead_agent: 'minecraft',
        previous_lead: 'streamer',
        manual_override: 'auto',
        reason: 'rundown_changed',
        timestamp_ms: 5_000,
      },
    });
    expect(getApi().badgeLabel.value).toBe('游戏主控（minecraft）');
    expect(getApi().reasonLabel.value).toBe('流程单环节切换');
    expect(getLeadMock).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(200);
    expect(getLeadMock).toHaveBeenCalledTimes(2);

    // 失活：解除 WS 订阅，不再响应推送
    await host.setProps({ show: false });
    expect(subscriberCount()).toBe(0);

    // 再激活：恢复 WS 订阅并静默重拉补齐隐藏期间错过的变更
    getLeadMock.mockResolvedValueOnce({ data: leadSnapshot() } as never);
    await host.setProps({ show: true });
    expect(subscriberCount()).toBe(1);
    await vi.waitFor(() => expect(getLeadMock).toHaveBeenCalledTimes(3));
    expect(getApi().badgeLabel.value).toBe('主播主控');
  });
});
