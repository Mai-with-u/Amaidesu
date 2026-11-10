// useStartStream.ts 开播引导组合式的测试
//
// 覆盖：清单加载与可选集过滤（主播/附属/弹幕变体不进勾选清单）、附属标注
// 组装、首次默认回落（现状运行∪启用、唯一在用弹幕变体）、上次意图恢复
// （失效键过滤、失效流程单回落当前指向）、期望全集组装（勾选+弹幕单选）、
// 提交编排（切单→开场→保存意图；activate 短路；失败回执；接口异常）、
// 场次类型（默认跟随弹幕接入、手动选择优先、每次打开重置、随开场提交）。

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { componentApi, liveSessionsApi, rundownApi } from '@/api';
import { useStartStream } from '@/composables/live/useStartStream';
import type { ComponentListResponse, ComponentSummary, RundownListResponse } from '@/types';

vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  ElMessageBox: { prompt: vi.fn(), alert: vi.fn().mockResolvedValue(undefined) },
}));

// useLiveSessions（defaultSessionTitle 来源）传递依赖 websocket store，
// node 环境无 window：按 useLiveSessions.test.ts 同款手法桩掉 WS 客户端
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
  componentApi: { getAll: vi.fn() },
  liveSessionsApi: { open: vi.fn() },
  rundownApi: { listRundowns: vi.fn(), activate: vi.fn() },
  configApi: { getConfig: vi.fn(), saveBatch: vi.fn() },
  systemApi: { fetchSystemHealth: vi.fn() },
}));

const getAllMock = vi.mocked(componentApi.getAll);
const openMock = vi.mocked(liveSessionsApi.open);
const listRundownsMock = vi.mocked(rundownApi.listRundowns);
const activateMock = vi.mocked(rundownApi.activate);
const { configApi, systemApi } = await import('@/api');
const getConfigMock = vi.mocked(configApi.getConfig);
const saveBatchMock = vi.mocked(configApi.saveBatch);
const healthMock = vi.mocked(systemApi.fetchSystemHealth);

const STORAGE_KEY = 'amaidesu.startStream.plan.v1';

function summary(partial: Partial<ComponentSummary> & { name: string }): ComponentSummary {
  return {
    group: 'collectors',
    type: 'collector',
    is_started: false,
    is_enabled: false,
    ...partial,
  };
}

function componentsResponse(): ComponentListResponse {
  return {
    collectors: [
      summary({ name: 'bili_danmaku_laplace', is_started: true, is_enabled: true }),
      summary({ name: 'bili_danmaku_official' }),
      summary({ name: 'bili_danmaku' }),
      summary({ name: 'console_input', is_enabled: true }),
      summary({ name: 'stt' }),
      summary({ name: 'live_frame', is_started: true }),
      summary({ name: 'maicraft_attention', owner: 'minecraft', is_started: true }),
    ],
    agents: [
      summary({ name: 'streamer', group: 'agents', is_started: true }),
      summary({ name: 'minecraft', group: 'agents' }),
      summary({ name: 'game_2048', group: 'agents' }),
    ],
  };
}

function rundownsResponse(): RundownListResponse {
  return {
    success: true,
    message: '',
    current_id: 'rd-current',
    rundowns: [
      { rundown_id: 'rd-current', title: '默认单', segments: [{ seg_id: 's1' } as never] },
      { rundown_id: 'rd-game', title: '游戏场', segments: [{ seg_id: 's1' } as never] },
    ],
  };
}

function healthItem(state: 'ok' | 'offline' | 'disabled' | 'unknown'): never {
  return {
    name: 'gptsovits',
    state,
    detail: '',
    last_error: '',
    supports_reconnect: false,
  } as never;
}

describe('useStartStream', () => {
  beforeEach(() => {
    getAllMock.mockResolvedValue({ data: componentsResponse() } as never);
    listRundownsMock.mockResolvedValue({ data: rundownsResponse() } as never);
    getConfigMock.mockResolvedValue({
      data: { config: { tts: { enabled: true } } },
    } as never);
    healthMock.mockResolvedValue({
      data: { status: 'ok', timestamp_ms: 0, checked_at_ms: 0, domains: { tts: [healthItem('ok')] } },
    } as never);
    saveBatchMock.mockResolvedValue({ data: {} } as never);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  /** 极简 localStorage 桩：node 测试环境无 localStorage（同其它组合式的 localStorage 桩手法） */
  function storageStub(initial: Record<string, string> = {}): void {
    const map = new Map(Object.entries(initial));
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => map.get(key) ?? null,
      setItem: (key: string, value: string) => void map.set(key, value),
      removeItem: (key: string) => void map.delete(key),
    });
  }

  async function mounted() {
    const stream = useStartStream();
    await stream.load(false);
    return stream;
  }

  it('load：可选集过滤主播/附属/弹幕变体，附属标注组装，首次回落现状', async () => {
    storageStub();
    const stream = await mounted();

    const names = stream.planGroups.value.flatMap(g => g.items.map(i => i.name));
    expect(names).toEqual(['console_input', 'stt', 'live_frame', 'minecraft', 'game_2048']);
    expect(stream.companionNotes.value['minecraft']).toBe('maicraft_attention');
    // 首次无存档：现状兜底 = 运行 ∪ 启用；弹幕取唯一在用变体；流程单跟随当前
    expect(stream.selectedKeys.value).toEqual([
      'collectors/console_input',
      'collectors/live_frame',
    ]);
    expect(stream.danmaku.value).toBe('bili_danmaku_laplace');
    expect(stream.selectedRundownId.value).toBe('rd-current');
  });

  it('load：组件清单失败记 loadError；流程单库失败不阻塞清单', async () => {
    storageStub();
    getAllMock.mockRejectedValue(new Error('network'));
    const stream = useStartStream();
    await stream.load(false);
    expect(stream.loadError.value).toBe('network');
  });

  it('上次意图恢复：失效键过滤、失效弹幕变体回落不接入、失效流程单回落当前', async () => {
    storageStub({
      [STORAGE_KEY]: JSON.stringify({
        keys: ['collectors/stt', 'agents/gone'],
        danmaku: 'bili_danmaku_official',
        rundownId: 'rd-gone',
      }),
    });
    const stream = await mounted();
    expect(stream.selectedKeys.value).toEqual(['collectors/stt']);
    expect(stream.danmaku.value).toBe('bili_danmaku_official');
    expect(stream.selectedRundownId.value).toBe('rd-current');
  });

  it('planRefs：勾选与弹幕单选组成期望全集；不接入则无弹幕条目', async () => {
    storageStub();
    const stream = await mounted();
    expect(stream.planRefs.value).toEqual([
      { group: 'collectors', name: 'console_input' },
      { group: 'collectors', name: 'live_frame' },
      { group: 'collectors', name: 'bili_danmaku_laplace' },
    ]);
    stream.danmaku.value = '';
    stream.toggle(summary({ name: 'minecraft', group: 'agents' }), true);
    expect(stream.planRefs.value).toEqual([
      { group: 'collectors', name: 'console_input' },
      { group: 'collectors', name: 'live_frame' },
      { group: 'agents', name: 'minecraft' },
    ]);
  });

  it('submit：流程单变更先 activate，再单请求开场；干净成功保存意图', async () => {
    storageStub();
    const stream = await mounted();
    stream.selectedRundownId.value = 'rd-game';
    activateMock.mockResolvedValue({ data: { success: true, message: '' } } as never);
    openMock.mockResolvedValue({
      data: { live_session_id: 1, plan_report: { items: [], config_error: '' } },
    } as never);

    const clean = await stream.submit();

    expect(clean).toBe(true);
    expect(activateMock).toHaveBeenCalledWith('rd-game');
    expect(openMock).toHaveBeenCalledWith({
      kind: 'live',
      title: stream.title.value.trim() || undefined,
      component_plan: stream.planRefs.value,
    });
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY)!);
    expect(saved.danmaku).toBe('bili_danmaku_laplace');
    expect(saved.rundownId).toBe('rd-game');
  });

  it('submit：跟随当前流程单不触发 activate；有失败项返回 false 且回执可读', async () => {
    storageStub();
    const stream = await mounted();
    openMock.mockResolvedValue({
      data: {
        live_session_id: 1,
        plan_report: {
          items: [
            {
              group: 'collectors',
              name: 'stt',
              action: 'start',
              result: 'failed',
              detail: '端口被占',
            },
          ],
          config_error: '',
        },
      },
    } as never);

    const clean = await stream.submit();

    expect(activateMock).not.toHaveBeenCalled();
    expect(clean).toBe(false);
    expect(stream.hasFailures.value).toBe(true);
    expect(stream.failedItems.value).toHaveLength(1);
    expect(stream.failedItems.value[0].detail).toBe('端口被占');
  });

  it('submit：activate 失败短路，不调开场接口', async () => {
    storageStub();
    const stream = await mounted();
    stream.selectedRundownId.value = 'rd-game';
    activateMock.mockResolvedValue({ data: { success: false, message: '模板校验未过' } } as never);

    const clean = await stream.submit();

    expect(clean).toBe(false);
    expect(stream.submitError.value).toBe('模板校验未过');
    expect(openMock).not.toHaveBeenCalled();
  });

  it('submit：开场接口异常写入 submitError', async () => {
    storageStub();
    const stream = await mounted();
    openMock.mockRejectedValue(new Error('boom'));

    const clean = await stream.submit();

    expect(clean).toBe(false);
    expect(stream.submitError.value).toBe('boom');
  });

  it('场次类型：默认跟随弹幕接入，手动选择优先且不存进上次意图，重新打开时重置', async () => {
    storageStub();
    const stream = await mounted();
    expect(stream.danmaku.value).toBe('bili_danmaku_laplace');
    expect(stream.kind.value).toBe('live'); // 接了 B 站弹幕默认正式
    stream.danmaku.value = '';
    expect(stream.kind.value).toBe('rehearsal'); // 没接弹幕默认彩排

    stream.kind.value = 'live'; // 手动选过就不再跟随弹幕
    stream.danmaku.value = '';
    expect(stream.kind.value).toBe('live');

    openMock.mockResolvedValue({
      data: { live_session_id: 2, plan_report: { items: [], config_error: '' } },
    } as never);
    stream.kind.value = 'rehearsal';
    await stream.submit();
    expect(openMock).toHaveBeenLastCalledWith(expect.objectContaining({ kind: 'rehearsal' }));
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY)!)).not.toHaveProperty('kind');

    await stream.load(false); // 重新打开：手动选择清空，回到跟随推断
    expect(stream.kind.value).toBe(stream.danmaku.value ? 'live' : 'rehearsal');
  });

  it('TTS 探针：服务不可达只落提示不拦开播；配置或健康面读不到按缺席降级', async () => {
    storageStub();
    healthMock.mockResolvedValue({
      data: { status: 'ok', timestamp_ms: 0, checked_at_ms: 0, domains: { tts: [healthItem('offline')] } },
    } as never);
    const stream = await mounted();
    expect(stream.ttsEngineReady.value).toBe(true);
    expect(stream.ttsNotice.value).toContain('不会出声');
    openMock.mockResolvedValue({
      data: { live_session_id: 1, plan_report: { items: [], config_error: '' } },
    } as never);
    expect(await stream.submit()).toBe(true); // 提示不拦开播

    healthMock.mockRejectedValue(new Error('health down'));
    const degraded = await mounted();
    expect(degraded.ttsEngineReady.value).toBe(false);
    expect(degraded.ttsNotice.value).toBe('');
  });

  it('TTS 开关：打开且引擎未装配 → 写盘 + 拦开播；引擎已装配不拦', async () => {
    storageStub();
    healthMock.mockResolvedValue({
      data: { status: 'ok', timestamp_ms: 0, checked_at_ms: 0, domains: { tts: [healthItem('disabled')] } },
    } as never);
    const stream = await mounted();
    expect(stream.ttsEnabled.value).toBe(true);
    // 先关再开：模拟引导里重新打开开关
    await stream.toggleTts(false);
    await stream.toggleTts(true);
    expect(saveBatchMock).toHaveBeenLastCalledWith([{ key: 'infra.tts.enabled', value: true }]);
    expect(stream.ttsRestartBlock.value).toBe(true);
    openMock.mockResolvedValue({
      data: { live_session_id: 1, plan_report: { items: [], config_error: '' } },
    } as never);
    expect(await stream.submit()).toBe(false);
    expect(stream.submitError.value).toContain('重启');
    expect(openMock).not.toHaveBeenCalled();

    // 引擎已装配：打开开关不拦
    healthMock.mockResolvedValue({
      data: { status: 'ok', timestamp_ms: 0, checked_at_ms: 0, domains: { tts: [healthItem('ok')] } },
    } as never);
    const ready = await mounted();
    await ready.toggleTts(false);
    await ready.toggleTts(true);
    expect(ready.ttsRestartBlock.value).toBe(false);
    expect(await ready.submit()).toBe(true);
  });

  it('TTS 开关：关闭且引擎已装配 → 写盘 + 提示重启才停止出声，不拦开播；引擎未装配才是已关闭', async () => {
    // 引擎已装配（进程内引擎仍在跑）：写盘摘不掉它，提示需重启，如实不给"不会出声"承诺
    storageStub();
    healthMock.mockResolvedValue({
      data: {
        status: 'ok',
        timestamp_ms: 0,
        checked_at_ms: 0,
        domains: { tts: [healthItem('ok')] },
      },
    } as never);
    const ready = await mounted();
    await ready.toggleTts(false);
    expect(saveBatchMock).toHaveBeenLastCalledWith([{ key: 'infra.tts.enabled', value: false }]);
    expect(ready.ttsNotice.value).toContain('重启');
    expect(ready.ttsNotice.value).not.toContain('不会出声');
    expect(ready.ttsRestartBlock.value).toBe(false);
    openMock.mockResolvedValue({
      data: { live_session_id: 1, plan_report: { items: [], config_error: '' } },
    } as never);
    expect(await ready.submit()).toBe(true); // 关方向不拦开播（重启前她仍会出声，开麦不属事故）

    // 引擎未装配（进程本就没引擎）：关闭即刻成立，才是"不会出声"
    healthMock.mockResolvedValue({
      data: {
        status: 'ok',
        timestamp_ms: 0,
        checked_at_ms: 0,
        domains: { tts: [healthItem('disabled')] },
      },
    } as never);
    const bare = await mounted();
    await bare.toggleTts(false);
    expect(bare.ttsNotice.value).toBe('TTS 已关闭：本场直播她不会出声');
    expect(bare.ttsRestartBlock.value).toBe(false);
  });

  it('TTS 开关：写盘失败回滚开关并提示，不进拦截态', async () => {
    storageStub();
    saveBatchMock.mockRejectedValue(new Error('写盘失败'));
    const stream = await mounted();
    await stream.toggleTts(false);
    expect(stream.ttsEnabled.value).toBe(true); // 回滚
    expect(stream.ttsNotice.value).toBe('写盘失败');
    expect(stream.ttsRestartBlock.value).toBe(false);
  });
});
