// @vitest-environment happy-dom
// useTtsReplay 的测试：整场回放循环、停止语义、失败不中断、汇总聚合、测速音频生命周期
//
// 覆盖：场次/台词加载、canStart 门控（引擎可用性由 deps 注入）、逐句串行调用与
// 超时传参、停止后不再发下一句、单句失败只记录不中断、汇总只计成功句、synth
// 模式 blob 音频的创建与 dispose 释放、dispose 置停止。

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ref } from 'vue';

import { debugApi, liveSessionsApi, reviewApi } from '@/api';
import { wsClient } from '@/api/websocket';
import type { WebSocketMessage } from '@/types';
import { EVENT } from '@/types/events';
import { REPLAY_TIMEOUT_MS, splitSpokenSegments, useTtsReplay } from './useTtsReplay';

vi.mock('@/api', () => ({
  debugApi: { testTts: vi.fn() },
  liveSessionsApi: { list: vi.fn() },
  reviewApi: { listSpeech: vi.fn() },
}));

// WS 客户端替身：捕获模块级注册的回调，测试直接调用它模拟事件到达
vi.mock('@/api/websocket', () => ({
  wsClient: { onMessage: vi.fn(), subscribe: vi.fn() },
}));

const testTtsMock = vi.mocked(debugApi.testTts);
const onMessageMock = vi.mocked(wsClient.onMessage);
const listSessionsMock = vi.mocked(liveSessionsApi.list);
const listSpeechMock = vi.mocked(reviewApi.listSpeech);

function speechLine(chatId: number, content: string, dropped = false) {
  return { chat_id: chatId, timestamp_ms: 1700000000000, content, dropped, memory_refs: [] };
}

function ttsOk(elapsedMs: number, audioBase64?: string, firstAudioMs?: number, playbackMs?: number) {
  return {
    data: {
      success: true,
      utterance_id: 'utt-1',
      elapsed_ms: elapsedMs,
      audio_base64: audioBase64,
      first_audio_ms: firstAudioMs ?? null,
      playback_ms: playbackMs ?? null,
    },
  } as never;
}

function ttsFail(error: string) {
  return { data: { success: false, error, elapsed_ms: 5 } } as never;
}

/** 手控请求时序：每次 testTts 调用挂一个未决 promise，测试按需逐个放行 */
function deferred() {
  let resolve!: (value: { data: unknown }) => void;
  const promise = new Promise<{ data: unknown }>(res => {
    resolve = res;
  });
  return { promise, resolve };
}

/** 构造被测组合式：引擎可用性与名称为测试可控的注入 ref */
function makeState(engineAvailable = true) {
  const engineAvailableRef = ref(engineAvailable);
  const engineNameRef = ref('FakeTTS');
  const state = useTtsReplay({ engineAvailable: engineAvailableRef, engineName: engineNameRef });
  return { state, engineAvailableRef };
}

/** 载入 n 句台词（默认引擎可用），返回待测状态 */
async function loadLines(n: number, engineAvailable = true) {
  listSpeechMock.mockResolvedValueOnce({
    data: { items: Array.from({ length: n }, (_, i) => speechLine(i + 1, `台词${i + 1}`)) },
  } as never);
  const { state, engineAvailableRef } = makeState(engineAvailable);
  state.selectedSessionId.value = 1;
  await state.loadLines();
  return { state, engineAvailableRef };
}

describe('useTtsReplay', () => {
  beforeEach(() => {
    testTtsMock.mockReset();
    listSessionsMock.mockReset();
    listSpeechMock.mockReset();
    // onMessageMock 不清：hookWs 模块级只注册一次，后续测试沿用首次注册的回调
  });

  /** 已注册的模块级 WS 分发回调（hookWs 只注册一次，闭包转活跃处理器） */
  function wsDispatch(): (message: WebSocketMessage) => void {
    expect(onMessageMock).toHaveBeenCalled();
    return onMessageMock.mock.calls[0]![0];
  }

  function utteranceEvent(type: string, utteranceId: string, extra: Record<string, unknown> = {}): WebSocketMessage {
    return { type, timestamp_ms: 0, data: { utterance_id: utteranceId, ...extra } } as WebSocketMessage;
  }

  it('场次与台词加载：按场次号请求台词接口，行写入 lines；失败写 error 不抛出', async () => {
    listSessionsMock.mockResolvedValueOnce({
      data: { items: [{ live_session_id: 7, kind: 'live', title: '周末场', started_at_ms: 1700000000000 }], total: 1 },
    } as never);
    listSpeechMock.mockResolvedValueOnce({ data: { items: [speechLine(1, '你好')] } } as never);
    const { state } = makeState();

    await state.loadSessions();
    expect(listSessionsMock).toHaveBeenCalledWith({ limit: 50 });
    expect(state.sessions.value).toHaveLength(1);

    state.selectedSessionId.value = 7;
    await state.loadLines();
    expect(listSpeechMock).toHaveBeenCalledWith(7);
    expect(state.lines.value.map(line => line.content)).toEqual(['你好']);

    listSpeechMock.mockRejectedValueOnce(new Error('库炸了'));
    await state.loadLines();
    expect(state.linesError.value).toBe('库炸了');
  });

  it('canStart：引擎可用且已载入台词才允许开始', async () => {
    const { state } = await loadLines(1);
    expect(state.canStart.value).toBe(true);

    // 未载入台词（空场次）不可开始
    const empty = await loadLines(0);
    expect(empty.state.canStart.value).toBe(false);

    // 引擎不可用（未装配）不可开始
    const unavailable = await loadLines(1, false);
    expect(unavailable.state.canStart.value).toBe(false);
  });

  it('播放模式逐句串行调用：每句透传自定 utterance_id 与放宽的超时，结果与汇总逐句累计', async () => {
    const { state } = await loadLines(3);
    const deferreds = [deferred(), deferred(), deferred()];
    let call = 0;
    testTtsMock.mockImplementation(() => deferreds[call++]!.promise as never);

    const startPromise = state.startReplay();
    await vi.waitFor(() => expect(testTtsMock).toHaveBeenCalledTimes(1));
    const [body, timeout] = testTtsMock.mock.calls[0]!;
    expect(body!.text).toBe('台词1');
    expect(body!.play_on_device).toBe(true);
    expect(body!.utterance_id).toBe(state.liveUtteranceId.value);
    expect(timeout).toBe(REPLAY_TIMEOUT_MS);

    deferreds[0]!.resolve(ttsOk(120, undefined, 40, 80));
    await vi.waitFor(() => expect(testTtsMock).toHaveBeenCalledTimes(2));
    expect(state.doneCount.value).toBe(1);

    deferreds[1]!.resolve(ttsOk(240, undefined, 80, 160));
    deferreds[2]!.resolve(ttsOk(360, undefined, 120, 240));
    await startPromise;

    expect(testTtsMock).toHaveBeenCalledTimes(3);
    expect(state.running.value).toBe(false);
    expect(state.results.value.map(r => r.elapsedMs)).toEqual([120, 240, 360]);
    expect(state.summary.value).toEqual({
      total: 3,
      okCount: 3,
      failedCount: 0,
      totalMs: 720,
      avgMs: 240,
      perCharMs: 720 / 9,
      avgFirstAudioMs: 80,
    });
  });

  it('停止后不再发下一句：停止前已发出的那句照常记账', async () => {
    const { state } = await loadLines(3);
    const deferreds = [deferred(), deferred(), deferred()];
    let call = 0;
    testTtsMock.mockImplementation(() => deferreds[call++]!.promise as never);

    const startPromise = state.startReplay();
    await vi.waitFor(() => expect(testTtsMock).toHaveBeenCalledTimes(1));

    deferreds[0]!.resolve(ttsOk(100));
    await vi.waitFor(() => expect(testTtsMock).toHaveBeenCalledTimes(2));
    state.stopReplay();

    deferreds[1]!.resolve(ttsOk(200));
    await startPromise;

    expect(testTtsMock).toHaveBeenCalledTimes(2);
    expect(state.results.value).toHaveLength(2);
    expect(state.running.value).toBe(false);
  });

  it('单句失败不中断整场：失败句记入结果，后续句照常回放', async () => {
    const { state } = await loadLines(3);
    testTtsMock
      .mockResolvedValueOnce(ttsOk(100, undefined, 30, 70))
      .mockRejectedValueOnce(new Error('引擎连接断开'))
      .mockResolvedValueOnce(ttsOk(300, undefined, 100, 200));

    await state.startReplay();

    expect(testTtsMock).toHaveBeenCalledTimes(3);
    const [first, second, third] = state.results.value;
    expect(first!.success).toBe(true);
    expect(second!.success).toBe(false);
    expect(second!.error).toBe('引擎连接断开');
    expect(third!.success).toBe(true);
    expect(state.summary.value).toEqual({
      total: 3,
      okCount: 2,
      failedCount: 1,
      totalMs: 400,
      avgMs: 200,
      perCharMs: 400 / 6,
      avgFirstAudioMs: 65,
    });
  });

  it('引擎回 success=false（合成失败）也记为失败句并保留原因', async () => {
    const { state } = await loadLines(1);
    testTtsMock.mockResolvedValueOnce(ttsFail('当前引擎不支持音频合成接口（仅 GPT-SoVITS 支持）'));

    await state.startReplay();
    expect(state.results.value[0]!.success).toBe(false);
    expect(state.results.value[0]!.error).toContain('不支持音频合成接口');
    expect(state.summary.value.failedCount).toBe(1);
  });

  it('逐句点亮：WS 进度事件按 utterance_id 对上当前句，段数逐级点亮；失败句不点亮', async () => {
    listSpeechMock.mockResolvedValueOnce({
      data: { items: [speechLine(1, '你好。今天好吗？开始吧！'), speechLine(2, '第二句。')] },
    } as never);
    const { state } = makeState();
    state.selectedSessionId.value = 1;
    await state.loadLines();

    const deferreds = [deferred(), deferred()];
    let call = 0;
    testTtsMock.mockImplementation(() => deferreds[call++]!.promise as never);

    const startPromise = state.startReplay();
    await vi.waitFor(() => expect(testTtsMock).toHaveBeenCalledTimes(1));

    const utteranceId = state.liveUtteranceId.value;
    expect(utteranceId).toContain('replay-');
    expect(state.liveChatId.value).toBe(1);
    expect(state.liveTotal.value).toBe(3);
    expect(state.liveLit.value).toBe(0);

    const dispatch = wsDispatch();
    await dispatch(utteranceEvent(EVENT.ttsUtterancePlaybackStarted, utteranceId));
    expect(state.liveLit.value).toBe(1);
    await dispatch(utteranceEvent(EVENT.ttsUtteranceSentenceBoundary, utteranceId, { segment_index: 2 }));
    expect(state.liveLit.value).toBe(2);
    // 别的 utterance 不点亮
    await dispatch(utteranceEvent(EVENT.ttsUtteranceFinished, 'replay-other-1'));
    expect(state.liveLit.value).toBe(2);
    await dispatch(utteranceEvent(EVENT.ttsUtteranceFinished, utteranceId));
    expect(state.liveLit.value).toBe(3);

    deferreds[0]!.resolve(ttsOk(100, undefined, 20, 80));
    // 放行第二句让循环收尾（第二句无 WS 事件，靠成功兜底点满其单段）
    deferreds[1]!.resolve(ttsOk(200, undefined, 30, 170));
    await startPromise;
    expect(state.liveActive.value).toBe(false);
    // 循环已推进到第二句并按收尾兜底点满（其文本单段）
    expect(state.liveChatId.value).toBe(2);
    expect(state.liveLit.value).toBe(1);
  });

  it('失败句不点亮：单句请求异常时点亮停在已出声处', async () => {
    const { state } = await loadLines(1);
    testTtsMock.mockRejectedValueOnce(new Error('引擎连接断开'));

    await state.startReplay();
    expect(state.liveActive.value).toBe(false);
    expect(state.liveLit.value).toBe(0);
  });

  it('synth 模式：传 play_on_device=false，合成成功时更新试听 blob，dispose 释放 URL 并置停止', async () => {
    const { state } = await loadLines(2);
    state.mode.value = 'synth';
    const createdUrls: string[] = [];
    const revokedUrls: string[] = [];
    vi.stubGlobal('URL', {
      ...URL,
      createObjectURL: vi.fn(() => {
        const url = `blob:mock-${createdUrls.length}`;
        createdUrls.push(url);
        return url;
      }),
      revokeObjectURL: vi.fn((url: string) => revokedUrls.push(url)),
    });

    testTtsMock.mockResolvedValueOnce(ttsOk(150, 'YUJTSS1mYWtl')).mockResolvedValueOnce(ttsFail('不支持合成'));

    await state.startReplay();

    const [body, timeout] = testTtsMock.mock.calls[0]!;
    expect(body!.text).toBe('台词1');
    expect(body!.play_on_device).toBe(false);
    expect(timeout).toBe(REPLAY_TIMEOUT_MS);
    // 失败句不更新试听：只创建过一次 blob URL
    expect(createdUrls).toHaveLength(1);
    expect(state.audioUrl.value).toBe('blob:mock-0');

    state.dispose();
    expect(revokedUrls).toEqual(['blob:mock-0']);
    expect(state.audioUrl.value).toBe('');
    expect(state.stopRequested.value).toBe(true);
    vi.unstubAllGlobals();
  });

  it('dispose 在回放进行中置停止标志，当前句播完后循环收尾', async () => {
    const { state } = await loadLines(2);
    const deferreds = [deferred(), deferred()];
    let call = 0;
    testTtsMock.mockImplementation(() => deferreds[call++]!.promise as never);

    const startPromise = state.startReplay();
    await vi.waitFor(() => expect(testTtsMock).toHaveBeenCalledTimes(1));

    state.dispose();
    deferreds[0]!.resolve(ttsOk(100));
    await startPromise;

    expect(testTtsMock).toHaveBeenCalledTimes(1);
    expect(state.running.value).toBe(false);
  });
});

describe('splitSpokenSegments', () => {
  it('按句末标点切段，保留标点', () => {
    expect(splitSpokenSegments('你好。今天好吗？开始吧！')).toEqual(['你好。', '今天好吗？', '开始吧！']);
  });

  it('无标点整句一段；空文本返回空段', () => {
    expect(splitSpokenSegments('没有标点的一句话')).toEqual(['没有标点的一句话']);
    expect(splitSpokenSegments('   ')).toEqual([]);
  });
});
