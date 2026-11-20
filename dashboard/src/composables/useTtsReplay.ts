/**
 * TTS 整场回放调试：把一场直播的落库台词按时间正序逐句送当前装配的 TTS 引擎。
 *
 * 逐句串行调用 /debug/tts/test——该端点与生产发言管线走同一个引擎入口
 * handle_speech，喂的文本即落库的生产实念文本，调参表现与实机同源。
 * 播放模式播完一句才发下一句（handle_speech 播完才返回），句间节奏自然成立；
 * 停止 = 置停止标志停发下一句，单句失败只记录不中断整场。
 *
 * 逐句点亮（流式进度可视化）：每句回放透传自定 utterance_id，并订阅 WS 的
 * tts.utterance.playback_started / sentence_boundary / finished 事件，把
 * "正在回放的那句"按已出声段数逐段点亮（灰→实色加粗）。分段按标点近似
 * 引擎内部分句，粒度对齐 boundary 事件的段计数；仅播放模式有点亮
 * （合成测速模式无播放事件）。WS 语义：wsClient 无退订 API，模块级只注册
 * 一次回调向"当前活跃处理器"转发，回放结束/组件卸载置空处理器即停转，
 * keep-alive 切走后回放循环与点亮继续跑，无回调堆积。
 *
 * 引擎可用性与名称由调用方注入（调试卡统一加载一次引擎状态，单句直测与
 * 整场回放共用同一状态源），本组合式自身不发状态请求。组件卸载时调
 * dispose() 置停止、清点亮态并释放测速试听的 blob URL。
 */
import { computed, ref, type ComputedRef, type Ref } from 'vue';
import { debugApi, liveSessionsApi, reviewApi } from '@/api';
import { wsClient } from '@/api/websocket';
import type { LiveSessionItem, WebSocketMessage } from '@/types';
import { EVENT } from '@/types/events';
import type { ReviewSpeechLine } from '@/types/review';
import { getApiErrorMessage } from '@/utils/apiError';
import { formatSeconds } from '@/utils/format';

/** 把台词按句末标点切成段（逐句点亮的粒度）：近似引擎内部分句，空文本返回空段 */
export function splitSpokenSegments(text: string): string[] {
  const trimmed = text.trim();
  if (!trimmed) return [];
  const parts = trimmed
    .split(/(?<=[。！？!?；;…])/)
    .map(part => part.trim())
    .filter(part => part.length > 0);
  return parts.length > 0 ? parts : [trimmed];
}

// wsClient 无退订 API（既有消费方均常驻回调）：模块级只注册一次，向当前
// 活跃处理器转发；无回放在跑时处理器为 null，消息直接落空。
type WsLiveHandler = ((message: WebSocketMessage) => void) | null;
let activeLiveHandler: WsLiveHandler = null;
let wsHooked = false;

function hookWs(): void {
  if (wsHooked) return;
  wsHooked = true;
  wsClient.onMessage(message => {
    if (activeLiveHandler) activeLiveHandler(message);
  });
}

/** 回放单句请求超时：台词可能远长于手输单句，播放 + 合成耗时上限放宽到 2 分钟 */
export const REPLAY_TIMEOUT_MS = 120000;

/** 回放模式：play=扬声器播放（全引擎可用，耗时含播放）；synth=仅合成测速（纯合成耗时） */
export type ReplayMode = 'play' | 'synth';

/** 一句台词的回放结果 */
export interface ReplayLineResult {
  chatId: number;
  /** 台词字符数（比较引擎代价的分母） */
  chars: number;
  /** 服务端处理耗时；请求本身失败（网络/超时）时为 null */
  elapsedMs: number | null;
  /** 首声延迟（请求起点→首个音频块出声）；锚点事件缺席为 null */
  firstAudioMs: number | null;
  /** 出声期（首声→播完；流式引擎此段合成与播放并行）；锚点事件缺席为 null */
  playbackMs: number | null;
  success: boolean;
  error: string;
}

/** 引擎状态依赖：由宿主组件（调试卡）注入，本组合式不自己加载 */
export interface TtsReplayDeps {
  /** 当前引擎是否可用（装配在位） */
  engineAvailable: Ref<boolean> | ComputedRef<boolean>;
  /** 引擎名（汇总文本署名用）；缺省按无名处理 */
  engineName?: Ref<string> | ComputedRef<string>;
}

export function useTtsReplay(deps: TtsReplayDeps) {
  // ----- 场次与台词 -----
  const sessions = ref<LiveSessionItem[]>([]);
  const sessionsLoading = ref(false);
  const sessionsError = ref('');
  const selectedSessionId = ref<number | null>(null);
  const lines = ref<ReviewSpeechLine[]>([]);
  const linesLoading = ref(false);
  const linesError = ref('');

  async function loadSessions(): Promise<void> {
    sessionsLoading.value = true;
    sessionsError.value = '';
    try {
      const { data } = await liveSessionsApi.list({ limit: 50 });
      sessions.value = data.items;
    } catch (err) {
      sessionsError.value = getApiErrorMessage(err, '场次列表加载失败');
    } finally {
      sessionsLoading.value = false;
    }
  }

  async function loadLines(): Promise<void> {
    if (selectedSessionId.value === null) return;
    linesLoading.value = true;
    linesError.value = '';
    try {
      const { data } = await reviewApi.listSpeech(selectedSessionId.value);
      lines.value = data.items;
    } catch (err) {
      linesError.value = getApiErrorMessage(err, '台词加载失败');
    } finally {
      linesLoading.value = false;
    }
  }

  // ----- 回放循环 -----
  const mode = ref<ReplayMode>('play');
  const running = ref(false);
  const stopRequested = ref(false);
  /** 已回放（成功或失败）的句数 */
  const doneCount = ref(0);
  const results = ref<ReplayLineResult[]>([]);
  const currentLine = ref<ReviewSpeechLine | null>(null);

  // ----- 逐句点亮（流式进度）：仅播放模式有事件，合成测速模式不点亮 -----
  const liveChatId = ref<number | null>(null);
  const liveText = ref('');
  const liveLit = ref(0);
  const liveTotal = ref(0);
  const liveActive = ref(false);
  const liveUtteranceId = ref('');

  function beginLiveLine(line: ReviewSpeechLine): void {
    liveChatId.value = line.chat_id;
    liveText.value = line.content;
    liveTotal.value = splitSpokenSegments(line.content).length;
    liveLit.value = 0;
    liveUtteranceId.value = `replay-${line.chat_id}-${Date.now()}`;
    liveActive.value = mode.value === 'play';
    activeLiveHandler = liveActive.value ? onLiveEvent : null;
  }

  function onLiveEvent(message: WebSocketMessage): void {
    const utteranceId = typeof message.data?.utterance_id === 'string' ? message.data.utterance_id : '';
    if (!utteranceId || utteranceId !== liveUtteranceId.value) return;
    if (message.type === EVENT.ttsUtterancePlaybackStarted) {
      // 首块出声：第一段已在说话（至少部分）
      liveLit.value = Math.max(liveLit.value, 1);
    } else if (message.type === EVENT.ttsUtteranceSentenceBoundary) {
      // 第 N 个句间隙 = 第 N 段已完整出声
      const segmentIndex = typeof message.data.segment_index === 'number' ? message.data.segment_index : 0;
      liveLit.value = Math.max(liveLit.value, Math.min(segmentIndex, liveTotal.value));
    } else if (message.type === EVENT.ttsUtteranceFinished) {
      liveLit.value = liveTotal.value;
    }
  }

  const canStart = computed(
    () =>
      deps.engineAvailable.value &&
      lines.value.length > 0 &&
      !running.value &&
      selectedSessionId.value !== null,
  );

  async function startReplay(): Promise<void> {
    if (!canStart.value) return;
    running.value = true;
    stopRequested.value = false;
    results.value = [];
    doneCount.value = 0;
    currentLine.value = null;
    hookWs();
    try {
      for (const line of lines.value) {
        if (stopRequested.value) break;
        currentLine.value = line;
        beginLiveLine(line);
        let succeeded = false;
        try {
          const { data } = await debugApi.testTts(
            {
              text: line.content,
              play_on_device: mode.value === 'play',
              // 透传自定 utterance_id：WS 实时进度事件按它对上"正在回放的那句"
              utterance_id: liveUtteranceId.value,
            },
            REPLAY_TIMEOUT_MS,
          );
          succeeded = data.success;
          results.value.push({
            chatId: line.chat_id,
            chars: line.content.length,
            elapsedMs: data.elapsed_ms ?? null,
            firstAudioMs: data.first_audio_ms ?? null,
            playbackMs: data.playback_ms ?? null,
            success: data.success,
            error: data.success ? '' : data.error || '合成失败',
          });
          if (mode.value === 'synth' && data.success && data.audio_base64) {
            updateLatestAudio(data.audio_base64, line);
          }
        } catch (err) {
          // 单句请求失败（网络/超时/引擎异常）只记录，不中断整场回放
          results.value.push({
            chatId: line.chat_id,
            chars: line.content.length,
            elapsedMs: null,
            firstAudioMs: null,
            playbackMs: null,
            success: false,
            error: getApiErrorMessage(err, '请求失败'),
          });
        }
        // 本句收尾兜底：WS 事件缺失（断连/事件缺席）时把成功句整句点满；失败句保持原状
        if (succeeded) liveLit.value = liveTotal.value;
        liveActive.value = false;
        doneCount.value += 1;
      }
    } finally {
      running.value = false;
      currentLine.value = null;
      activeLiveHandler = null;
    }
  }

  function stopReplay(): void {
    stopRequested.value = true;
  }

  // ----- 测速模式最近一句合成音频 -----
  const audioUrl = ref('');
  const audioFilename = ref('tts-replay.wav');

  function updateLatestAudio(base64: string, line: ReviewSpeechLine): void {
    const bytes = Uint8Array.from(atob(base64), ch => ch.charCodeAt(0));
    const blob = new Blob([bytes], { type: 'audio/wav' });
    if (audioUrl.value) URL.revokeObjectURL(audioUrl.value);
    audioUrl.value = URL.createObjectURL(blob);
    const stamp = new Date(line.timestamp_ms).toISOString().slice(11, 19).replace(/:/g, '');
    audioFilename.value = `tts-replay-${stamp}.wav`;
  }

  // ----- 汇总统计（只计成功句；失败句单列） -----
  const summary = computed(() => {
    const ok = results.value.filter(r => r.success);
    const totalMs = ok.reduce((sum, r) => sum + (r.elapsedMs ?? 0), 0);
    const totalChars = ok.reduce((sum, r) => sum + r.chars, 0);
    const firstAudioValues = ok.filter(r => r.firstAudioMs !== null).map(r => r.firstAudioMs as number);
    return {
      total: results.value.length,
      okCount: ok.length,
      failedCount: results.value.length - ok.length,
      totalMs,
      avgMs: ok.length > 0 ? Math.round(totalMs / ok.length) : 0,
      /** 每字平均耗时（毫秒/字）：跨引擎比较合成代价的主指标 */
      perCharMs: totalChars > 0 ? totalMs / totalChars : 0,
      /** 平均首声延迟（观众等开口的时长）；无任何锚点分段数据（合成测速/事件缺席）时为 null */
      avgFirstAudioMs:
        firstAudioValues.length > 0
          ? Math.round(firstAudioValues.reduce((sum, v) => sum + v, 0) / firstAudioValues.length)
          : null,
    };
  });

  /** 汇总文本（复制出去做跨引擎记录比较用）；毫秒内部口径，显示按秒 */
  function buildSummaryText(): string {
    const s = summary.value;
    const name = deps.engineName?.value || '当前引擎';
    const modeLabel = mode.value === 'play' ? '扬声器播放（耗时含播放）' : '仅合成测速（纯合成耗时）';
    const lines = [
      `TTS 整场回放 · ${name} · ${modeLabel}`,
      `句数：${s.okCount} 成功 / ${s.failedCount} 失败 / 共 ${s.total} 句已回放`,
      `总耗时：${formatSeconds(s.totalMs)}`,
      `平均每句：${formatSeconds(s.avgMs)}`,
    ];
    if (s.avgFirstAudioMs !== null) {
      lines.push(`平均首声：${formatSeconds(s.avgFirstAudioMs)}（等开口的时长）`);
    }
    lines.push(`平均每字：${formatSeconds(s.perCharMs)}/字`);
    return lines.join('\n');
  }

  /** 组件卸载时调用：置停止标志、清逐句点亮态并释放 blob URL */
  function dispose(): void {
    stopRequested.value = true;
    if (activeLiveHandler === onLiveEvent) activeLiveHandler = null;
    liveChatId.value = null;
    liveActive.value = false;
    if (audioUrl.value) {
      URL.revokeObjectURL(audioUrl.value);
      audioUrl.value = '';
    }
  }

  return {
    // 场次与台词
    sessions,
    sessionsLoading,
    sessionsError,
    loadSessions,
    selectedSessionId,
    lines,
    linesLoading,
    linesError,
    loadLines,
    // 回放
    mode,
    running,
    doneCount,
    currentLine,
    canStart,
    startReplay,
    stopReplay,
    results,
    stopRequested,
    // 逐句点亮
    liveChatId,
    liveText,
    liveLit,
    liveTotal,
    liveActive,
    liveUtteranceId,
    // 测速音频
    audioUrl,
    audioFilename,
    // 汇总
    summary,
    buildSummaryText,
    // 清理
    dispose,
  };
}
