// appRestart store 测试
//
// 覆盖重启状态机全流程：请求失败即 failed、成功后 restarting → checking、
// 健康检查通过 → success → 遮罩自动收起、连续失败到上限 → failed、
// retryHealthCheck 复位计数重新进入检查循环。
//
// 时间行为（3s 等待、2s 轮询间隔、1.2s 成功收起延迟）全部用 fake timers
// 推进；configApi.restart 与 systemApi.getHealth 用 vi.mock 替身，避免
// 真实网络请求。

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';

const { restart, getHealth, successMessage, errorMessage } = vi.hoisted(() => ({
  restart: vi.fn(),
  getHealth: vi.fn(),
  successMessage: vi.fn(),
  errorMessage: vi.fn(),
}));

vi.mock('@/api', () => ({
  configApi: { restart },
  systemApi: { getHealth },
}));

vi.mock('element-plus', () => ({
  ElMessage: { success: successMessage, error: errorMessage },
}));

/** 动态加载全新模块并建好 pinia */
async function setupStore() {
  vi.resetModules();
  setActivePinia(createPinia());
  const { useAppRestartStore } = await import('@/stores/appRestart');
  return useAppRestartStore();
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers();
  restart.mockResolvedValue({ data: { success: true, message: '' } });
  getHealth.mockRejectedValue(new Error('service down'));
});

afterEach(() => {
  vi.useRealTimers();
});

describe('appRestart store 状态机', () => {
  it('请求异常：直接置 failed，遮罩状态失败且 restarting 复位', async () => {
    const store = await setupStore();
    restart.mockRejectedValue(new Error('network down'));

    await store.beginRestart();

    expect(store.overlayStatus).toBe('failed');
    expect(store.restarting).toBe(false);
    expect(store.overlayVisible).toBe(true);
    expect(errorMessage).toHaveBeenCalled();
  });

  it('请求响应 success=false：置 failed 并提示后端返回的消息', async () => {
    const store = await setupStore();
    restart.mockResolvedValue({ data: { success: false, message: '后端拒绝' } });

    await store.beginRestart();

    expect(store.overlayStatus).toBe('failed');
    expect(store.restarting).toBe(false);
    expect(errorMessage).toHaveBeenCalledWith(expect.stringContaining('后端拒绝'));
    expect(getHealth).not.toHaveBeenCalled();
  });

  it('成功路径：restarting → 3s 后 checking → 健康检查通过 → success → 遮罩自动收起', async () => {
    const store = await setupStore();
    getHealth.mockResolvedValue({ data: { status: 'ok' } });

    // beginRestart 内部等 3s 定时器，fake timers 下先推进时钟再收尾 await
    const pending = store.beginRestart();
    expect(store.overlayStatus).toBe('requesting');
    expect(store.overlayVisible).toBe(true);

    await vi.advanceTimersByTimeAsync(3000);
    await pending;

    expect(store.overlayStatus).toBe('checking');
    expect(store.restarting).toBe(true);
    expect(getHealth).not.toHaveBeenCalled();

    // 轮询通过即 success，1.2 秒后遮罩收起、restarting 复位
    await vi.advanceTimersByTimeAsync(2000);
    expect(store.overlayStatus).toBe('success');
    expect(store.restarting).toBe(true);
    await vi.advanceTimersByTimeAsync(1200);
    expect(store.overlayVisible).toBe(false);
    expect(store.restarting).toBe(false);
    expect(successMessage).toHaveBeenCalled();
  });

  it('连续失败到上限：failed 且不再继续轮询', async () => {
    const store = await setupStore();

    const pending = store.beginRestart();
    await vi.advanceTimersByTimeAsync(3000);
    await pending;
    expect(store.overlayStatus).toBe('checking');

    // 推进 60 次 2s 轮询全部失败后置 failed
    for (let i = 0; i < store.overlayMaxAttempts; i++) {
      await vi.advanceTimersByTimeAsync(2000);
    }
    expect(store.overlayStatus).toBe('failed');
    expect(store.restarting).toBe(false);

    // failed 后停止轮询：再推进时间调用数不变
    const calls = getHealth.mock.calls.length;
    await vi.advanceTimersByTimeAsync(10000);
    expect(getHealth).toHaveBeenCalledTimes(calls);
  });

  it('retryHealthCheck：复位计数重新进入检查循环，通过后收尾', async () => {
    const store = await setupStore();

    const pending = store.beginRestart();
    await vi.advanceTimersByTimeAsync(3000);
    await pending;

    // 失败若干次积累计数
    await vi.advanceTimersByTimeAsync(2000);
    await vi.advanceTimersByTimeAsync(2000);
    expect(store.overlayCheckAttempts).toBe(2);

    store.retryHealthCheck();
    expect(store.overlayStatus).toBe('checking');
    expect(store.overlayCheckAttempts).toBe(0);

    // 重试后健康检查通过 → success
    getHealth.mockResolvedValue({ data: { status: 'ok' } });
    await vi.advanceTimersByTimeAsync(2000);
    expect(store.overlayStatus).toBe('success');
    await vi.advanceTimersByTimeAsync(1200);
    expect(store.overlayVisible).toBe(false);
  });
});
