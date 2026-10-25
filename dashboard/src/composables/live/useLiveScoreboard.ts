/**
 * 控制台实时计分板：当前场次的六胶囊数据（时长/发言数/弹幕数/礼物折算¥/
 * tokens/缓存命中率）。
 *
 * 数据源口径：
 * - 时长：进行中场次 started_at_ms → 当前时刻（共享秒级时钟刷新）；无进行
 *   中场次时为 null（胶囊显示占位）。
 * - 发言数/弹幕数/礼物折算：events store 事件计数（近期窗口口径，store 为
 *   有界缓冲，非整场累计）。
 * - 礼物折算人民币：B 站金瓜子 1000 = 1 元（口径同后端
 *   src/modules/types/currency.py 的 to_cny）；银瓜子等免费币种不计入；
 *   空币种按金瓜子处理（历史生产平台兜底口径）。Super Chat 金额原生即
 *   人民币，直接并入折算合计。
 * - tokens/缓存命中率：GET /llm/usage/summary 汇总（进程累计口径，非本场
 *   截断——用量表无场次维度）；命中率取 hit/prompt_tokens（与
 *   useRoundTokenStats 同口径，OpenAI 风格只报 cached_tokens，聚合的
 *   hit/(hit+miss) 会算出假满值）。
 *
 * 生命周期：summary 轮询定时器按 keep-alive 激活态启停——onActivated 启动、
 * onDeactivated 停止、组件卸载兜底清理；激活时立即拉一次。事件计数与时长
 * 为派生计算，无需启停。
 */
import { computed, onActivated, onDeactivated, onScopeDispose, ref, type Ref } from 'vue';
import { storeToRefs } from 'pinia';
import { useEventsStore } from '@/stores';
import { EVENT } from '@/types/events';
import { llmApi } from '@/api';
import { useNowTick } from '@/composables/useNowTick';
import type { LiveSessionItem, WebSocketMessage } from '@/types';

/** summary 轮询间隔：token 汇总为低频运营指标，30s 足够 */
const SUMMARY_POLL_MS = 30_000;

/** B 站金瓜子 → 人民币：1000 金瓜子 = 1 元（口径来源 src/modules/types/currency.py） */
const CNY_PER_GOLD_COIN = 0.001;

/** 单条付费消息折算人民币（元）；无可折算金额返回 null */
export function giftPriceToCny(totalPrice: unknown, currency: unknown): number | null {
  if (typeof totalPrice !== 'number' || !Number.isFinite(totalPrice) || totalPrice <= 0) {
    return null;
  }
  // 空币种按 B 站金瓜子处理（历史生产平台兜底口径）；银瓜子等免费/未知币种不计入
  const isGold = currency === '' || currency == null || currency === 'bilibili_gold_coin';
  if (!isGold) return null;
  return totalPrice * CNY_PER_GOLD_COIN;
}

/** 计分板事件计数快照（纯函数，测试与派生计算共用） */
export interface FeedCounts {
  speechCount: number;
  danmakuCount: number;
  /** 礼物折算人民币合计（元）；无可折算付费为 null（与"0 元"区分） */
  giftCny: number | null;
}

/** 事件序列 → 发言/弹幕/礼物折算计数（纯函数） */
export function summarizeFeed(events: WebSocketMessage[]): FeedCounts {
  let speechCount = 0;
  let danmakuCount = 0;
  let giftCny: number | null = null;
  for (const message of events) {
    if (message.type === EVENT.streamerSpeech) {
      speechCount += 1;
      continue;
    }
    if (message.type !== EVENT.roomMessage) continue;
    const messageType = message.data.message_type;
    if (messageType === 'danmaku') {
      danmakuCount += 1;
    } else if (messageType === 'gift') {
      const gift = message.data.gift;
      if (typeof gift === 'object' && gift !== null) {
        const cny = giftPriceToCny(
          (gift as Record<string, unknown>).total_price,
          (gift as Record<string, unknown>).currency,
        );
        if (cny !== null) giftCny = (giftCny ?? 0) + cny;
      }
    } else if (messageType === 'super_chat') {
      const sc = message.data.sc;
      if (typeof sc === 'object' && sc !== null) {
        const amount = (sc as Record<string, unknown>).amount;
        if (typeof amount === 'number' && Number.isFinite(amount) && amount > 0) {
          giftCny = (giftCny ?? 0) + amount;
        }
      }
    }
  }
  return { speechCount, danmakuCount, giftCny };
}

/** LLM 汇总指标（summary 接口派生；null = 上游未上报或未取到） */
export interface LlmTotals {
  totalTokens: number | null;
  /** 缓存命中率：hit/prompt_tokens 口径（0-1）；无 prompt 用量为 null */
  cacheHitRate: number | null;
}

/** usage summary → tokens/缓存命中率（纯函数） */
export function llmTotalsOf(
  summary: {
    total_tokens?: number;
    total_prompt_tokens?: number;
    cache_hit_tokens?: number;
  } | null,
): LlmTotals {
  if (!summary) return { totalTokens: null, cacheHitRate: null };
  const promptTokens = summary.total_prompt_tokens ?? 0;
  return {
    totalTokens: summary.total_tokens ?? null,
    cacheHitRate: promptTokens > 0 ? (summary.cache_hit_tokens ?? 0) / promptTokens : null,
  };
}

export function useLiveScoreboard(activeSession: Ref<LiveSessionItem | null>) {
  const { events } = storeToRefs(useEventsStore());
  const nowTick = useNowTick();

  const counts = computed<FeedCounts>(() => summarizeFeed(events.value));

  /** 进行中场次已播时长毫秒；无进行中场次为 null */
  const durationMs = computed<number | null>(() => {
    const session = activeSession.value;
    if (!session) return null;
    return Math.max(0, nowTick.value - session.started_at_ms);
  });

  const summary = ref<{
    total_tokens?: number;
    total_prompt_tokens?: number;
    cache_hit_tokens?: number;
  } | null>(null);
  const llmTotals = computed<LlmTotals>(() => llmTotalsOf(summary.value));

  async function refreshSummary(): Promise<void> {
    try {
      const response = await llmApi.getUsageSummary();
      summary.value = response.data;
    } catch {
      /* 汇总不可用时胶囊保持占位，不打断计分板 */
    }
  }

  // summary 轮询按 keep-alive 激活态启停；激活即拉一次保证切回页面数据新鲜
  let pollTimer: ReturnType<typeof setInterval> | null = null;

  function startPolling(): void {
    if (pollTimer) return;
    void refreshSummary();
    pollTimer = setInterval(() => void refreshSummary(), SUMMARY_POLL_MS);
  }

  function stopPolling(): void {
    if (!pollTimer) return;
    clearInterval(pollTimer);
    pollTimer = null;
  }

  onActivated(startPolling);
  onDeactivated(stopPolling);
  // 非 keep-alive 消费方（测试/直挂）兜底：作用域销毁即停
  onScopeDispose(stopPolling);

  return { counts, durationMs, llmTotals, refreshSummary };
}
