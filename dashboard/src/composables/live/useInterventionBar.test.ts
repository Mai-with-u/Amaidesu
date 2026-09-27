// useInterventionBar.ts 干预输入条组合式函数的测试
//
// 覆盖：三模式的校验与传输（注入弹幕 / 强制回应 / 幕后提醒）、强制回应的
// 后台执行语义（不等返回、在途计数、finally 归位）、发送锁、模式切换守卫。

import { effectScope, type EffectScope } from 'vue';
import { createPinia, setActivePinia } from 'pinia';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { agentsApi, debugApi, streamerApi } from '@/api';
import { ElMessage } from 'element-plus';
import { useInterventionBar } from '@/composables/live/useInterventionBar';

vi.mock('@/api', async importOriginal => ({
  ...(await importOriginal<object>()),
  debugApi: { injectMessage: vi.fn(), getEventBusStats: vi.fn() },
  streamerApi: { testDecision: vi.fn() },
  agentsApi: { promptAgent: vi.fn() },
}));

vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

const injectMock = vi.mocked(debugApi.injectMessage);
const testDecisionMock = vi.mocked(streamerApi.testDecision);
const promptAgentMock = vi.mocked(agentsApi.promptAgent);

describe('useInterventionBar', () => {
  let scope: EffectScope;
  let settle: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.useFakeTimers();
    scope = effectScope();
    settle = vi.fn().mockResolvedValue(undefined);
  });

  afterEach(() => {
    scope.stop();
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  function mount() {
    return scope.run(() => useInterventionBar({ settle }))!;
  }

  it('注入弹幕：空内容被拦截并提示', async () => {
    const composable = mount();
    await composable.onInterventionSend('danmaku', '');
    expect(ElMessage.warning).toHaveBeenCalledWith('请填写弹幕内容');
    expect(injectMock).not.toHaveBeenCalled();
  });

  it('注入弹幕：昵称缺省用"测试观众"，成功后复位输入', async () => {
    const composable = mount();
    injectMock.mockResolvedValue({ data: { success: true } } as never);
    await composable.onInterventionSend('danmaku', '主播好');
    expect(injectMock).toHaveBeenCalledWith({ source: '测试观众', text: '主播好' });
    expect(ElMessage.success).toHaveBeenCalled();
    expect(settle).toHaveBeenCalled();
    expect(composable.sending.value).toBe(false);

    composable.injectNickname.value = '小明';
    await composable.onInterventionSend('danmaku', '又来了');
    expect(injectMock).toHaveBeenLastCalledWith({ source: '小明', text: '又来了' });
  });

  it('注入弹幕：后端返回 success=false 提示错误且不复位输入', async () => {
    const composable = mount();
    injectMock.mockResolvedValue({ data: { success: false, error: '直播间未开启' } } as never);
    await composable.onInterventionSend('danmaku', '嗨');
    expect(ElMessage.error).toHaveBeenCalledWith('直播间未开启');
    expect(settle).not.toHaveBeenCalled();
  });

  it('强制回应：后台执行——发起即返回、在途计数、完成后归位', async () => {
    const composable = mount();
    let resolveApi!: (value: unknown) => void;
    testDecisionMock.mockImplementation(
      // resolve 形参是具体响应类型，与 unknown 桥接后统一以 unknown 派发
      () =>
        new Promise(resolve => {
          resolveApi = resolve as unknown as typeof resolveApi;
        }),
    );
    const sending = composable.onInterventionSend('force', '打个招呼');
    await vi.advanceTimersByTimeAsync(0);
    expect(composable.forcePending.value).toBe(1);
    expect(testDecisionMock).toHaveBeenCalledWith({
      batch: [{ nickname: '调试观众', text: '打个招呼' }],
      forced: true,
      proactive: undefined,
    });
    resolveApi({ data: { success: true, plan: { should_reply: true } } });
    await sending;
    await vi.advanceTimersByTimeAsync(0);
    expect(composable.forcePending.value).toBe(0);
    expect(ElMessage.success).toHaveBeenCalledWith('已回应（详见时间线决策卡）');
  });

  it('强制回应：空文本 = 自由发挥（proactive 语义）', async () => {
    const composable = mount();
    testDecisionMock.mockResolvedValue({
      data: { success: true, plan: { should_reply: false } },
    } as never);
    await composable.onInterventionSend('force', '');
    expect(testDecisionMock).toHaveBeenCalledWith({
      batch: undefined,
      forced: true,
      proactive: true,
    });
    expect(ElMessage.info).toHaveBeenCalledWith('本轮未回应（详见时间线决策卡）');
  });

  it('强制回应：执行失败提示错误且计数归位', async () => {
    const composable = mount();
    testDecisionMock.mockRejectedValue(new Error('timeout'));
    await composable.onInterventionSend('force', 'x');
    await vi.advanceTimersByTimeAsync(0);
    expect(ElMessage.error).toHaveBeenCalledWith('timeout');
    expect(composable.forcePending.value).toBe(0);
  });

  it('幕后提醒：空内容拦截；成功走递话接口', async () => {
    const composable = mount();
    await composable.onInterventionSend('nudge', '');
    expect(ElMessage.warning).toHaveBeenCalled();
    expect(promptAgentMock).not.toHaveBeenCalled();

    promptAgentMock.mockResolvedValue({ data: {} } as never);
    await composable.onInterventionSend('nudge', '该唱首歌了');
    expect(promptAgentMock).toHaveBeenCalledWith('streamer', '该唱首歌了');
    expect(ElMessage.success).toHaveBeenCalled();
  });

  it('发送锁：在途期间忽略后续发送', async () => {
    const composable = mount();
    let resolveApi!: (value: unknown) => void;
    injectMock.mockImplementation(
      () =>
        new Promise(resolve => {
          resolveApi = resolve as unknown as typeof resolveApi;
        }),
    );
    const first = composable.onInterventionSend('danmaku', '第一条');
    await vi.advanceTimersByTimeAsync(0);
    expect(composable.sending.value).toBe(true);

    await composable.onInterventionSend('danmaku', '第二条');
    expect(injectMock).toHaveBeenCalledTimes(1);

    resolveApi({ data: { success: true } });
    await first;
    expect(composable.sending.value).toBe(false);
  });

  it('模式切换：仅接受三个合法模式键', () => {
    const composable = mount();
    composable.onModeChange('force');
    expect(composable.activeMode.value).toBe('force');
    composable.onModeChange('bogus');
    expect(composable.activeMode.value).toBe('force');
  });

  it('SEND_MODES 暴露三个发送模式供输入条渲染', () => {
    const composable = mount();
    expect(composable.SEND_MODES.map(mode => mode.key)).toEqual(['danmaku', 'force', 'nudge']);
  });
});
