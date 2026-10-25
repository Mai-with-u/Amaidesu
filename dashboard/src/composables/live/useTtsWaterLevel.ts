/**
 * TTS 台前水位：聚合 tts.utterance.started/finished/failed 三事件，回答
 * "引擎此刻出声了吗、等了多久、坏了几句"。
 *
 * 欠载判读口径（历史实测沉淀）：每句约 2.5s 空窗属正常冲入动作（上一句
 * 播完到下一句开始出声的编排间隔）；空窗超过 3s 过警戒线，提示引擎可能
 * 欠载（合成排队/服务不可达）。空窗 = 距最近一句 settled（finished 或
 * failed）经过的毫秒数；在途句存在（已 started 未 settled）视为正在出
 * 声，空窗记 0。
 *
 * 生命周期：本组合式无自有定时器与订阅——事件序列读全局 events store
 * （WS 连接与缓冲归 store），逐秒刷新读共享 useNowTick 时钟（引用计数
 * 启停、keep-alive 激活态持有，隐藏即停走）。所有状态均为派生计算，无
 * 需启停，离开页面自然静止。
 *
 * 计数口径：events store 为有界缓冲（约千条窗口），失败/发言计数是
 * "近期窗口"口径而非整场累计——窗口滚动后早期事件不再计入。
 */
import { computed } from 'vue';
import { storeToRefs } from 'pinia';
import { useEventsStore } from '@/stores';
import { EVENT } from '@/types/events';
import { useNowTick } from '@/composables/useNowTick';
import type { WebSocketMessage } from '@/types';

/** 正常冲入动作空窗：约 2.5s（编排间隔的历史实测值，仅作 UI 提示口径） */
export const NORMAL_GAP_MS = 2500;
/** 警戒线空窗：超过 3s 提示引擎可能欠载 */
export const WARN_GAP_MS = 3000;

/** TTS 台前水位快照（纯派生，无副作用） */
export interface TtsWaterLevel {
  /** 在途句数：已 started 且未 finished/failed 的发声实例 */
  inFlightCount: number;
  /** 失败句数（近期事件窗口内 tts.utterance.failed 计数） */
  failureCount: number;
  /** 最近空窗毫秒：距最近一句 settled 的时间；正在出声时为 0；窗口内尚无 settled 事件为 null */
  gapMs: number | null;
}

interface AggState {
  inFlight: Set<string>;
  failureCount: number;
  lastSettledMs: number | null;
}

function settled(state: AggState, message: WebSocketMessage): void {
  const utteranceId = message.data.utterance_id;
  if (typeof utteranceId === 'string') state.inFlight.delete(utteranceId);
  state.lastSettledMs = message.timestamp_ms;
}

/** 事件序列 → 水位聚合（纯函数，测试与派生计算共用） */
export function aggregateTtsWaterLevel(events: WebSocketMessage[], nowMs: number): TtsWaterLevel {
  const state: AggState = { inFlight: new Set(), failureCount: 0, lastSettledMs: null };
  for (const message of events) {
    if (message.type === EVENT.ttsUtteranceStarted) {
      const utteranceId = message.data.utterance_id;
      if (typeof utteranceId === 'string') state.inFlight.add(utteranceId);
    } else if (message.type === EVENT.ttsUtteranceFinished) {
      settled(state, message);
    } else if (message.type === EVENT.ttsUtteranceFailed) {
      settled(state, message);
      state.failureCount += 1;
    }
  }
  const gapMs =
    state.inFlight.size > 0 ? 0 : state.lastSettledMs === null ? null : nowMs - state.lastSettledMs;
  return { inFlightCount: state.inFlight.size, failureCount: state.failureCount, gapMs };
}

/** 空窗是否过警戒线（null = 无从判定，不算警戒） */
export function isGapWarning(gapMs: number | null): boolean {
  return gapMs !== null && gapMs > WARN_GAP_MS;
}

export function useTtsWaterLevel() {
  const { events } = storeToRefs(useEventsStore());
  const nowTick = useNowTick();

  const waterLevel = computed<TtsWaterLevel>(() =>
    aggregateTtsWaterLevel(events.value, nowTick.value),
  );
  const warning = computed(() => isGapWarning(waterLevel.value.gapMs));

  return { waterLevel, warning };
}
