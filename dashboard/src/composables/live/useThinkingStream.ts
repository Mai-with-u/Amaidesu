/**
 * 思考流（WS kind="stream"；ADR-008 best-effort 观测通道）的视图层消费。
 *
 * 只在视图层合成：按 (round_id, phase, step) 累积思考段，再合成 kind='thinking'
 * 的时间线行与事件条目按时间归并（不进 events store、不落库、不回看）。
 * 状态是模块级单例——思考流不落库，但同一访问内切页再回来时时间线事件行
 * （全局 store）不丢，思考行不应因页面切换单独消失；常驻内存靠轮数/步数
 * 上限保尾约束。WS 订阅在首次使用时挂接一次，随应用生命周期存在。
 */

import { computed, reactive, ref } from 'vue';
import { useWebSocketStore } from '@/stores';
import {
  buildThinkingRow,
  type ShowEntry,
  type ThinkingSegmentInput,
  type ThinkingStep,
} from '@/utils/liveFeed';
import type { ThinkingDelta, WebSocketMessage } from '@/types';

const THINKING_ROUNDS_MAX = 20;
/** 单轮步骤上限：ReAct 步数无理论上限，长任务期间会无限累积推高渲染面；
 *  超限淘汰最旧段（上限保尾，与轮数上限同语义） */
const THINKING_STEPS_PER_ROUND_MAX = 100;

/** 每决策轮的思考聚合：planner 与 minecraft 共用按步分段（与工具卡时间交织），
 *  段携带自身 phase——两边步骤号各自从头计数，只按步号查找会互相踩段；
 *  replyer 独立一段 */
interface ThinkingStepSeg extends ThinkingStep {
  /** 段归属：planner（主播 ReAct）/ minecraft（游戏 Agent ReAct） */
  phase: string;
}

interface ThinkingRound {
  steps: ThinkingStepSeg[];
  replyerText: string;
  /** replyer 段首增量到达时刻（Unix 毫秒；0 = 尚未开始） */
  replyerTsMs: number;
}

// ==== 模块级单例状态（跨页面共享，生命周期与应用一致）====
const thinkingRounds = reactive(new Map<string, ThinkingRound>());

/** 思考行隐藏水位：清空时间线时记下当前思考行最大时刻，此前的思考行一并隐藏
 *  （思考行不进 hiddenIds 体系——它不是事件，没有事件 id） */
const thinkingHiddenBeforeMs = ref(0);

/** 思考流增量合批：WS 每消息触发一次落状态会带起整条时间线重渲染，复杂任务期间
 *  思考增量高频涌入时把页面拖死——先缓冲，按固定间隔一次性落进 reactive 状态，
 *  渲染频率与消息频率解耦；缓冲条目携带信封时间戳（段首定位用） */
const THINKING_FLUSH_INTERVAL_MS = 150;
const pendingDeltas: Array<{ delta: ThinkingDelta; tsMs: number }> = [];
let thinkingFlushTimer: ReturnType<typeof setTimeout> | null = null;
let wsAttached = false;

function handleThinkingMessage(message: WebSocketMessage): void {
  if (message.kind !== 'stream' || message.type !== 'thinking.delta') return;
  const deltas = (message.data.deltas ?? []) as ThinkingDelta[];
  for (const delta of deltas) pendingDeltas.push({ delta, tsMs: message.timestamp_ms });
  if (thinkingFlushTimer) return;
  thinkingFlushTimer = setTimeout(() => {
    thinkingFlushTimer = null;
    applyThinkingDeltas(pendingDeltas.splice(0, pendingDeltas.length));
  }, THINKING_FLUSH_INTERVAL_MS);
}

function applyThinkingDeltas(batch: Array<{ delta: ThinkingDelta; tsMs: number }>): void {
  for (const { delta, tsMs } of batch) {
    let round = thinkingRounds.get(delta.round_id);
    if (!round) {
      round = reactive({
        steps: [],
        replyerText: '',
        replyerTsMs: 0,
      });
      thinkingRounds.set(delta.round_id, round);
      // 上限保尾：只保留最近 N 轮供时间线回看，更早的文本随轮淘汰
      while (thinkingRounds.size > THINKING_ROUNDS_MAX) {
        const oldest = thinkingRounds.keys().next().value;
        if (oldest === undefined) break;
        thinkingRounds.delete(oldest);
      }
    }
    if (delta.phase === 'replyer') {
      if (!round.replyerTsMs) round.replyerTsMs = tsMs;
      round.replyerText += delta.text_delta;
    } else {
      // planner 与 minecraft 各按 (phase, step) 分段累积：步骤号两边独立计数，
      // 段归属（含时间线分组与行标签）随 phase 一路传递
      let seg = round.steps.find(s => s.phase === delta.phase && s.step === delta.step);
      if (!seg) {
        seg = reactive({ phase: delta.phase, step: delta.step, text: '', tsMs });
        round.steps.push(seg);
        while (round.steps.length > THINKING_STEPS_PER_ROUND_MAX) {
          round.steps.shift();
        }
      }
      seg.text += delta.text_delta;
    }
  }
}

/** 当前全部思考行（buildThinkingRow 内 id 稳定，流式增量原地刷新；升序交给归并函数）。
 *  仅实时模式使用——思考流不落库，回看场次的 REST 时间线没有思考数据 */
const liveThinkingRows = computed<ShowEntry[]>(() => {
  const watermark = thinkingHiddenBeforeMs.value;
  const segments: ThinkingSegmentInput[] = [];
  for (const [roundId, round] of thinkingRounds) {
    for (const step of round.steps) {
      if (!step.text || step.tsMs <= watermark) continue;
      segments.push({
        roundId,
        phase: step.phase,
        step: step.step,
        tsMs: step.tsMs,
        text: step.text,
      });
    }
    if (round.replyerText && round.replyerTsMs > watermark) {
      segments.push({
        roundId,
        phase: 'replyer',
        step: 1,
        tsMs: round.replyerTsMs,
        text: round.replyerText,
      });
    }
  }
  return segments.map(buildThinkingRow);
});

export function useThinkingStream() {
  if (!wsAttached) {
    const wsStore = useWebSocketStore();
    wsStore.subscribe(handleThinkingMessage);
    wsAttached = true;
  }

  return { liveThinkingRows, thinkingHiddenBeforeMs };
}
