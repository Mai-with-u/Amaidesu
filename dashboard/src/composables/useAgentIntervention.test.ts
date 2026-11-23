// useAgentIntervention.ts 干预发话共享组合式的测试
//
// 覆盖：三目标的模式映射（主播三模式 / minecraft 递话+按主控态二选一 /
// 其他目标为空）、传输分派（主播透传 useInterventionBar 既有通道、
// minecraft 走 agents 域接口）、建议转达回执两分支、在途状态合并与空文本守卫。

import { computed } from 'vue';
import { effectScope, type EffectScope } from 'vue';
import { createPinia, setActivePinia } from 'pinia';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { agentsApi, debugApi, streamerApi } from '@/api';
import { ElMessage } from 'element-plus';
import {
  MINECRAFT_AGENT_NAME,
  STREAMER_AGENT_NAME,
  useAgentIntervention,
} from '@/composables/useAgentIntervention';

vi.mock('@/api', async importOriginal => ({
  ...(await importOriginal<object>()),
  debugApi: { injectMessage: vi.fn(), getEventBusStats: vi.fn() },
  streamerApi: { testDecision: vi.fn() },
  agentsApi: { promptAgent: vi.fn(), delegateAgent: vi.fn(), suggestLead: vi.fn() },
}));

vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

const injectMock = vi.mocked(debugApi.injectMessage);
const testDecisionMock = vi.mocked(streamerApi.testDecision);
const promptAgentMock = vi.mocked(agentsApi.promptAgent);
const delegateAgentMock = vi.mocked(agentsApi.delegateAgent);
const suggestLeadMock = vi.mocked(agentsApi.suggestLead);

describe('useAgentIntervention', () => {
  let scope: EffectScope;
  let settle: ReturnType<typeof vi.fn>;
  let onDelegated: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.useFakeTimers();
    scope = effectScope();
    settle = vi.fn().mockResolvedValue(undefined);
    onDelegated = vi.fn();
  });

  afterEach(() => {
    scope.stop();
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  function mount(gameLead = false) {
    return scope.run(() =>
      useAgentIntervention({
        settle,
        onDelegated,
        isGameLead: computed(() => gameLead),
      }),
    )!;
  }

  it('模式映射：主播三模式 / minecraft 按主控态二选一 / 其他目标为空', () => {
    const composable = mount();
    expect(composable.modesForTarget(STREAMER_AGENT_NAME).map(mode => mode.key)).toEqual([
      'danmaku',
      'force',
      'nudge',
    ]);
    // 主播主控态：递话 + 委派
    const delegateModes = composable.modesForTarget(MINECRAFT_AGENT_NAME);
    expect(delegateModes.map(mode => mode.key)).toEqual(['prompt', 'delegate']);
    expect(delegateModes.map(mode => mode.label)).toEqual(['递话', '委派']);
    expect(composable.modesForTarget('adv')).toEqual([]);
  });

  it('游戏主控态：minecraft 模式集合换为递话 + 建议', () => {
    const composable = mount(true);
    const modes = composable.modesForTarget(MINECRAFT_AGENT_NAME);
    expect(modes.map(mode => mode.key)).toEqual(['prompt', 'suggest']);
    expect(modes.map(mode => mode.label)).toEqual(['递话', '建议']);
  });

  it('建议模式：受理回执提示送达对象', async () => {
    const composable = mount(true);
    suggestLeadMock.mockResolvedValue({
      data: { accepted: true, target: 'minecraft', lead_agent: 'minecraft', reason: '' },
    } as never);
    await composable.sendToTarget(MINECRAFT_AGENT_NAME, 'suggest', '小心苦力怕');
    expect(suggestLeadMock).toHaveBeenCalledWith('小心苦力怕');
    expect(ElMessage.success).toHaveBeenCalled();
    expect(delegateAgentMock).not.toHaveBeenCalled();
    expect(settle).toHaveBeenCalled();
  });

  it('建议模式：拒收回执以 warning 提示原因', async () => {
    const composable = mount(true);
    suggestLeadMock.mockResolvedValue({
      data: {
        accepted: false,
        target: 'minecraft',
        lead_agent: 'streamer',
        reason: 'manual_streamer',
      },
    } as never);
    await composable.sendToTarget(MINECRAFT_AGENT_NAME, 'suggest', '小心苦力怕');
    expect(ElMessage.warning).toHaveBeenCalled();
    expect(ElMessage.success).not.toHaveBeenCalled();
  });

  it('主播目标：强制回应透传 useInterventionBar 既有通道', async () => {
    const composable = mount();
    testDecisionMock.mockResolvedValue({
      data: { success: true, plan: { should_reply: false } },
    } as never);
    await composable.sendToTarget(STREAMER_AGENT_NAME, 'force', '看一眼弹幕');
    expect(testDecisionMock).toHaveBeenCalledWith({
      batch: [{ nickname: '调试观众', text: '看一眼弹幕' }],
      forced: true,
      proactive: undefined,
    });
    expect(settle).toHaveBeenCalled();
  });

  it('主播目标：注入弹幕透传昵称与文本', async () => {
    const composable = mount();
    injectMock.mockResolvedValue({ data: { success: true } } as never);
    composable.injectNickname.value = '小明';
    await composable.sendToTarget(STREAMER_AGENT_NAME, 'danmaku', '主播好');
    expect(injectMock).toHaveBeenCalledWith({ source: '小明', text: '主播好' });
    expect(settle).toHaveBeenCalled();
  });

  it('minecraft 目标：递话走 promptAgent，委派走 delegateAgent 并回调页面', async () => {
    const composable = mount();
    promptAgentMock.mockResolvedValue({ data: {} } as never);
    await composable.sendToTarget(MINECRAFT_AGENT_NAME, 'prompt', '停一下');
    expect(promptAgentMock).toHaveBeenCalledWith('minecraft', '停一下');
    expect(delegateAgentMock).not.toHaveBeenCalled();
    expect(settle).toHaveBeenCalled();

    delegateAgentMock.mockResolvedValue({ data: { task_id: 'task_1' } } as never);
    await composable.sendToTarget(MINECRAFT_AGENT_NAME, 'delegate', '建个房子');
    expect(delegateAgentMock).toHaveBeenCalledWith('minecraft', '建个房子');
    expect(onDelegated).toHaveBeenCalledWith('minecraft', 'task_1');
  });

  it('minecraft 目标：发送期间 sending 为真，结束后归位', async () => {
    const composable = mount();
    let resolveApi!: (value: unknown) => void;
    promptAgentMock.mockImplementation(
      () =>
        new Promise(resolve => {
          resolveApi = resolve as unknown as typeof resolveApi;
        }),
    );
    const sending = composable.sendToTarget(MINECRAFT_AGENT_NAME, 'prompt', 'x');
    await vi.advanceTimersByTimeAsync(0);
    expect(composable.sending.value).toBe(true);
    resolveApi({ data: {} });
    await sending;
    expect(composable.sending.value).toBe(false);
  });

  it('主播目标：发送期间 sending 合并内部 bar 状态', async () => {
    const composable = mount();
    let resolveApi!: (value: unknown) => void;
    injectMock.mockImplementation(
      () =>
        new Promise(resolve => {
          resolveApi = resolve as unknown as typeof resolveApi;
        }),
    );
    const sending = composable.sendToTarget(STREAMER_AGENT_NAME, 'danmaku', '第一条');
    await vi.advanceTimersByTimeAsync(0);
    expect(composable.sending.value).toBe(true);
    resolveApi({ data: { success: true } });
    await sending;
    expect(composable.sending.value).toBe(false);
  });

  it('minecraft 目标：空文本被拦截不发请求', async () => {
    const composable = mount();
    await composable.sendToTarget(MINECRAFT_AGENT_NAME, 'prompt', '');
    await composable.sendToTarget(MINECRAFT_AGENT_NAME, 'delegate', '');
    expect(promptAgentMock).not.toHaveBeenCalled();
    expect(delegateAgentMock).not.toHaveBeenCalled();
    expect(ElMessage.warning).toHaveBeenCalledTimes(2);
  });

  it('不支持的目标：发送是空操作', async () => {
    const composable = mount();
    await composable.sendToTarget('adv', 'prompt', 'x');
    expect(promptAgentMock).not.toHaveBeenCalled();
  });
});
