<template>
  <section class="stats" aria-label="核心指标">
    <div class="stat">
      <div class="stat-label">场内发言</div>
      <div class="stat-value">{{ review.speech.count }}<span class="stat-unit">条</span></div>
      <div class="stat-sub">口径：live_chat 主播发言行（sender_role=assistant）</div>
      <div v-if="review.speech.rate_per_min !== null" class="stat-sub">
        ≈{{ review.speech.rate_per_min }} 条/分钟
      </div>
    </div>
    <div class="stat">
      <div class="stat-label">最长沉默</div>
      <div class="stat-value">{{ formatMsAsSeconds(review.speech.longest_silence_ms) }}</div>
      <div class="stat-sub">相邻发言最大间隔，场次首尾计入边界</div>
      <router-link v-if="silenceLink" class="rv-link stat-sub" :to="silenceLink">
        跳转沉默起点 →
      </router-link>
    </div>
    <div class="stat">
      <div class="stat-label">决策</div>
      <div class="stat-value">{{ review.decisions.total }}<span class="stat-unit">次</span></div>
      <div class="stat-sub">{{ decisionSummary }}</div>
    </div>
    <div
      class="stat"
      :class="{ 'is-red': latencyLevel(review.decisions.latency.p90_ms) === 'red' }"
    >
      <div class="stat-label">决策→发言时延</div>
      <div class="stat-value">
        {{ formatMsAsSeconds(review.decisions.latency.p90_ms) }}<span class="stat-unit">· p90</span>
      </div>
      <div class="stat-sub">
        p50 {{ formatMsAsSeconds(review.decisions.latency.p50_ms) }} · max
        {{ formatMsAsSeconds(review.decisions.latency.max_ms) }} · 样本
        {{ review.decisions.latency.sample_count }} · 超过 30s 染红（verdict→speech 按 round_id
        配对）
      </div>
      <router-link v-if="latencyMaxLink" class="rv-link stat-sub" :to="latencyMaxLink">
        跳转最慢一次 →
      </router-link>
    </div>
    <div class="stat">
      <div class="stat-label">LLM 失败</div>
      <div class="stat-value">
        {{ review.llm.overall.failed_calls
        }}<span class="stat-unit">/ {{ review.llm.overall.calls }} 笔</span>
      </div>
      <div class="stat-sub">缓存命中率 {{ cacheRateLabel(review.llm.overall.cache_hit_rate) }}</div>
      <router-link v-if="llmFailedLink" class="rv-link stat-sub" :to="llmFailedLink">
        查看失败调用 →
      </router-link>
    </div>
    <div
      class="stat"
      :class="{ 'is-warn': slowCallsLevel(review.llm.overall.slow_calls) === 'yellow' }"
    >
      <div class="stat-label">慢调用</div>
      <div class="stat-value">
        {{ review.llm.overall.slow_calls }}<span class="stat-unit">笔</span>
      </div>
      <div class="stat-sub">超过 10 笔染黄 · 按各 profile 配置阈值计</div>
      <router-link v-if="llmSlowLink" class="rv-link stat-sub" :to="llmSlowLink">
        查看慢调用 →
      </router-link>
    </div>
    <div class="stat">
      <div class="stat-label">场内游戏事件</div>
      <div class="stat-value">{{ review.game.total }}<span class="stat-unit">次</span></div>
      <div class="stat-sub">{{ gameSummary }}</div>
    </div>
  </section>
</template>

<script setup lang="ts">
/**
 * 核心指标卡（七张）：场内发言 / 最长沉默 / 决策 / 决策→发言时延 / LLM 失败 /
 * 慢调用 / 场内游戏事件。本组件只做"计算 + 染色"，不做告警判定——超阈染色阈值
 * 是前端常量（utils/sessionReview），卡片副文案自带口径便于人工核验。
 * 沉默 / 最慢时延两卡带"跳转时刻"深链进控制台回看，链接目标由页面传入。
 */
import { computed } from 'vue';
import type { SessionReviewResponse } from '@/types';
import {
  cacheRateLabel,
  formatMsAsSeconds,
  latencyLevel,
  slowCallsLevel,
  triggerReasonLabel,
} from '@/utils/sessionReview';

const props = defineProps<{
  review: SessionReviewResponse;
  /** 最长沉默起点的回看深链（无锚点为 null，卡上不渲染跳转） */
  silenceLink: string | null;
  /** 最慢一次决策的回看深链 */
  latencyMaxLink: string | null;
  /** LLM 失败调用的历史页深链（带时间窗 + 仅失败；无失败或无时间窗为 null） */
  llmFailedLink: string | null;
  /** 慢调用的历史页深链（带时间窗 + 耗时下限取各 profile 阈值最小值） */
  llmSlowLink: string | null;
}>();

const decisionSummary = computed<string>(() => {
  const d = props.review.decisions;
  // 分层表述：前段是触发来源分布（计数和=total），后段是 total 的子集
  //（静默/失败与来源分布有交叠），用分隔符与"其中"措辞区分层次
  const sources = Object.entries(d.distribution)
    .slice(0, 3)
    .map(([reason, count]) => `${triggerReasonLabel(reason)} ${count}`)
    .join(' · ');
  const extras = [
    d.silent_count ? `静默 ${d.silent_count}` : '',
    d.failed_count ? `失败 ${d.failed_count}` : '',
  ]
    .filter(Boolean)
    .join(' · ');
  if (!sources && !extras) return '无决策记录';
  return extras ? `${sources} ｜ 其中${extras}` : sources;
});

const gameSummary = computed<string>(() => {
  const g = props.review.game;
  if (!g || g.total === 0) return '无游戏事件';
  return (
    Object.entries(g.by_type)
      .map(([type, count]) => `${type} ${count}`)
      .join(' · ') || '无游戏事件'
  );
});
</script>

<style scoped>
.stats {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 12px;
}

.stat {
  padding: 12px 14px;
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-md);
  background: var(--bg-card);
}

.stat-value {
  font-size: 24px;
  font-weight: 600;
  margin: 4px 0;
}

.stat-unit {
  font-size: 12px;
  font-weight: 400;
  color: var(--text-secondary);
  margin-left: 4px;
}

.stat-sub {
  font-size: 12px;
  color: var(--text-secondary);
}

.stat.is-red {
  border-color: var(--color-danger);
  background: var(--color-danger-bg);
}

.stat.is-red .stat-value {
  color: var(--color-danger);
}

.stat.is-warn {
  border-color: var(--color-warning);
  background: var(--color-warning-bg);
}

.stat.is-warn .stat-value {
  color: var(--color-warning);
}
</style>
