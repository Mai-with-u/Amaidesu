<!--
  SessionReview - 场次复盘页（只读聚合）

  布局按设计定案：信息 chips → 指标卡（超阈仅染色）→ 水位图 + LLM 用量表 →
  深入入口（事件回看 / LLM 历史 · 本场预过滤）。全部数字来自后端结构化聚合
  （GET /api/v1/review），本页不做告警判定；多选合并视图由后端窗口合并、
  水位图按 session_id 断线留空隙。深挖取证不在本页——带数字开 AI 会话。
-->
<template>
  <div class="session-review">
    <header class="review-head">
      <div>
        <h1>场次复盘</h1>
        <p class="review-sub">
          只读聚合 · 超阈数字仅染色（阈值为前端常量）· 支持多选合并跨场对比 · 深挖取证走 AI 会话
        </p>
      </div>
      <div class="review-actions">
        <el-select
          v-model="selectedIds"
          multiple
          collapse-tags
          collapse-tags-tooltip
          clearable
          placeholder="选择场次（可多选合并）"
          class="review-select"
          :loading="sessionsLoading"
        >
          <el-option
            v-for="item in sessions"
            :key="item.live_session_id"
            :label="sessionLabel(item)"
            :value="item.live_session_id"
          />
        </el-select>
        <el-button
          type="primary"
          :disabled="!review || !review.sessions.length"
          @click="exportPackage"
        >
          导出数据包
        </el-button>
      </div>
    </header>

    <el-alert
      v-if="errorMessage"
      :title="errorMessage"
      type="error"
      show-icon
      :closable="false"
      class="review-alert"
    />
    <el-alert
      v-else-if="review?.degraded"
      title="部分诊断事件天目录缺失（被清理或从未落盘），决策侧指标按现存数据降级"
      type="warning"
      show-icon
      :closable="false"
      class="review-alert"
    />

    <template v-if="review">
      <!-- 场次信息 chips -->
      <section class="chips" aria-label="场次信息">
        <span v-for="chip in chips" :key="chip" class="chip mono">{{ chip }}</span>
      </section>

      <!-- 核心指标卡 -->
      <section class="stats" aria-label="核心指标">
        <div class="stat">
          <div class="stat-label">场内发言</div>
          <div class="stat-value">{{ review.speech.count }}<span class="stat-unit">条</span></div>
          <div class="stat-sub">口径：live_chat 主播发言行（sender_role=assistant）</div>
        </div>
        <div class="stat">
          <div class="stat-label">最长沉默</div>
          <div class="stat-value">{{ formatMsAsSeconds(review.speech.longest_silence_ms) }}</div>
          <div class="stat-sub">相邻发言最大间隔，场次首尾计入边界</div>
        </div>
        <div class="stat">
          <div class="stat-label">决策</div>
          <div class="stat-value">
            {{ review.decisions.total }}<span class="stat-unit">次</span>
          </div>
          <div class="stat-sub">{{ decisionSummary }}</div>
        </div>
        <div
          class="stat"
          :class="{ 'is-red': latencyLevel(review.decisions.latency.p90_ms) === 'red' }"
        >
          <div class="stat-label">决策→发言时延</div>
          <div class="stat-value">
            {{ formatMsAsSeconds(review.decisions.latency.p90_ms)
            }}<span class="stat-unit">· p90</span>
          </div>
          <div class="stat-sub">
            p50 {{ formatMsAsSeconds(review.decisions.latency.p50_ms) }} · max
            {{ formatMsAsSeconds(review.decisions.latency.max_ms) }} · 超过 30s 染红（verdict→speech
            按 round_id 配对）
          </div>
        </div>
        <div class="stat">
          <div class="stat-label">LLM 失败</div>
          <div class="stat-value">
            {{ review.llm.overall.failed_calls
            }}<span class="stat-unit">/ {{ review.llm.overall.calls }} 笔</span>
          </div>
          <div class="stat-sub">缓存命中率 {{ cacheRateLabel }}</div>
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
        </div>
        <div class="stat">
          <div class="stat-label">场内游戏事件</div>
          <div class="stat-value">{{ review.game.total }}<span class="stat-unit">次</span></div>
          <div class="stat-sub">{{ gameSummary }}</div>
        </div>
      </section>

      <div class="columns">
        <!-- 水位图 -->
        <section class="panel">
          <div class="panel-title">单笔输入水位（千 tokens，按 Profile）</div>
          <div ref="chartRef" class="waterline-chart"></div>
        </section>

        <!-- LLM 用量表 -->
        <section class="panel">
          <div class="panel-title">LLM 用量（按 Profile）</div>
          <table class="usage-table">
            <thead>
              <tr>
                <th>Profile</th>
                <th class="num">笔数</th>
                <th class="num">输入</th>
                <th class="num">缓存</th>
                <th class="num">输出</th>
                <th class="num">成本</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in review.llm.by_profile" :key="row.profile_name">
                <td>
                  <span
                    class="tier"
                    :style="{ borderColor: waterlineColor(row.profile_name, 0) }"
                    >{{ row.profile_name || 'unknown' }}</span
                  >
                </td>
                <td class="num">{{ row.calls }}</td>
                <td class="num">{{ compactNumber(row.prompt_tokens) }}</td>
                <td class="num">{{ cacheRate(row.cache_hit_rate) }}</td>
                <td class="num">{{ compactNumber(row.completion_tokens) }}</td>
                <td class="num">{{ formatCost(row.cost, 2) }}</td>
              </tr>
              <tr class="total-row">
                <td>合计</td>
                <td class="num">{{ review.llm.overall.calls }}</td>
                <td class="num">{{ compactNumber(review.llm.overall.prompt_tokens) }}</td>
                <td class="num">{{ cacheRate(review.llm.overall.cache_hit_rate) }}</td>
                <td class="num">{{ compactNumber(review.llm.overall.completion_tokens) }}</td>
                <td class="num">{{ formatCost(review.llm.overall.cost, 2) }}</td>
              </tr>
            </tbody>
          </table>
          <p class="table-note">
            成本按计价配置折算；缓存命中率 = cache_hit / (hit + miss)，未上报显示 —
          </p>
        </section>
      </div>

      <!-- 深入入口 -->
      <section class="panel">
        <div class="panel-title">深入入口</div>
        <div class="links">
          <router-link class="link" :to="eventReviewLink">事件回看 · 本场预过滤</router-link>
          <router-link class="link" :to="llmHistoryLink">LLM 历史 · 本场预过滤</router-link>
          <span class="link-hint"
            >告警叙事与关键事件拣选不在本页——带着上面的数字开 AI 会话深挖</span
          >
        </div>
      </section>
    </template>
    <el-empty v-else-if="!errorMessage && !loading" description="选择一场直播查看复盘聚合" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import type { EChartsCoreOption } from 'echarts/core';
import { liveSessionsApi, reviewApi } from '@/api';
import type { LiveSessionItem, SessionReviewResponse } from '@/types';
import { getApiErrorMessage } from '@/utils/apiError';
import { compactNumber } from '@/utils/chartFormat';
import { formatCost, formatDateTime, formatTimeHMS } from '@/utils/format';
import {
  buildWaterlineData,
  formatDurationMs,
  formatMsAsSeconds,
  latencyLevel,
  slowCallsLevel,
  waterlineColor,
} from '@/utils/sessionReview';
import { useChartPalette, useECharts } from '@/composables/useECharts';

const sessions = ref<LiveSessionItem[]>([]);
const sessionsLoading = ref(false);
const selectedIds = ref<number[]>([]);
const review = ref<SessionReviewResponse | null>(null);
const loading = ref(false);
const errorMessage = ref('');

const chartRef = ref<HTMLElement | null>(null);
const palette = useChartPalette();

onMounted(async () => {
  sessionsLoading.value = true;
  try {
    const resp = await liveSessionsApi.list({ limit: 200 });
    sessions.value = resp.data.items;
    // 默认选最新已结束场次（列表倒序，第一个 ended 非空的）
    const latestEnded = resp.data.items.find(item => item.ended_at_ms !== null);
    if (latestEnded) selectedIds.value = [latestEnded.live_session_id];
  } catch (error) {
    errorMessage.value = getApiErrorMessage(error, '场次列表加载失败');
  } finally {
    sessionsLoading.value = false;
  }
});

watch(selectedIds, loadReview, { deep: true });

async function loadReview(): Promise<void> {
  loading.value = true;
  errorMessage.value = '';
  try {
    const resp = await reviewApi.get(selectedIds.value);
    review.value = resp.data;
  } catch (error) {
    review.value = null;
    errorMessage.value = getApiErrorMessage(error, '复盘聚合加载失败');
  } finally {
    loading.value = false;
  }
}

function sessionLabel(item: LiveSessionItem): string {
  const title = item.title || `场次 #${item.live_session_id}`;
  return `${title} · ${formatDateTime(item.started_at_ms)}`;
}

const chips = computed<string[]>(() => {
  const r = review.value;
  if (!r) return [];
  const labels: string[] = r.sessions.map(
    s => `场次 #${s.live_session_id}${s.title ? ` · ${s.title}` : ''}`,
  );
  if (r.sessions.length === 1) {
    const s = r.sessions[0];
    const endLabel = s.ended_at_ms !== null ? formatTimeHMS(s.ended_at_ms) : '进行中';
    labels.push(`${formatTimeHMS(s.started_at_ms)} – ${endLabel}`);
    labels.push(formatDurationMs(s.duration_ms));
    labels.push(`来源 ${s.source}`);
    labels.push(`heat ${s.heat}`);
    labels.push(`观众数 ${s.viewer_count}`);
  } else {
    labels.push(formatDurationMs(r.window_end_ms - r.window_start_ms));
    labels.push(`${r.sessions.length} 场合并`);
  }
  labels.push(`弹幕 ${r.chips.danmaku_count}`);
  labels.push(`进入 ${r.chips.enter_count}`);
  labels.push(`礼物 ${r.chips.gift_count}`);
  labels.push(`SC ${r.chips.super_chat_count}`);
  return labels;
});

const decisionSummary = computed<string>(() => {
  const d = review.value?.decisions;
  if (!d) return '';
  const parts = Object.entries(d.distribution)
    .slice(0, 3)
    .map(([reason, count]) => `${reason} ${count}`);
  if (d.silent_count) parts.push(`静默 ${d.silent_count}`);
  if (d.failed_count) parts.push(`失败 ${d.failed_count}`);
  return parts.join(' · ') || '无决策记录';
});

const gameSummary = computed<string>(() => {
  const g = review.value?.game;
  if (!g || g.total === 0) return '无游戏事件';
  return (
    Object.entries(g.by_type)
      .map(([type, count]) => `${type} ${count}`)
      .join(' · ') || '无游戏事件'
  );
});

const cacheRateLabel = computed<string>(() =>
  cacheRate(review.value?.llm.overall.cache_hit_rate ?? null),
);

function cacheRate(rate: number | null): string {
  return rate === null ? '—' : `${Math.round(rate * 100)}%`;
}

// 水位图 option：时间轴 + 按 profile 多折线（null 断线留空隙）
const chartOption = computed<EChartsCoreOption | null>(() => {
  const r = review.value;
  if (!r || !r.waterline.length) return null;
  const data = buildWaterlineData(r.waterline);
  return {
    backgroundColor: 'transparent',
    textStyle: { color: palette.value.textColor, fontFamily: palette.value.monoFont },
    tooltip: {
      trigger: 'axis',
      formatter: (params: unknown) => {
        const list = params as Array<{ seriesName: string; value: [number, number | null] }>;
        return list
          .filter(p => p.value?.[1] !== null && p.value?.[1] !== undefined)
          .map(p => `${p.seriesName}: ${p.value[1]}k tokens`)
          .join('<br/>');
      },
    },
    legend: { textStyle: { color: palette.value.textColor } },
    grid: { left: 60, right: 20, top: 36, bottom: 40 },
    xAxis: {
      type: 'time',
      axisLabel: { color: palette.value.textColor },
      splitLine: { show: false },
      axisLine: { lineStyle: { color: palette.value.borderColor } },
    },
    yAxis: {
      type: 'value',
      name: '千 tokens',
      nameTextStyle: { color: palette.value.textColor },
      axisLabel: { color: palette.value.textColor },
      splitLine: { lineStyle: { color: palette.value.borderColorLight } },
    },
    series: data.map((s, i) => ({
      name: s.profile,
      type: 'line' as const,
      showSymbol: false,
      connectNulls: false,
      data: s.data,
      lineStyle: { color: waterlineColor(s.profile, i), width: 1.5 },
      itemStyle: { color: waterlineColor(s.profile, i) },
    })),
  };
});

// useECharts 需要非空 option：无数据时给空 option（空态由 waterline 图例区自然留白）
const safeChartOption = computed<EChartsCoreOption>(() => chartOption.value ?? {});
useECharts(chartRef, safeChartOption);

const eventReviewLink = computed<string>(() => {
  const first = review.value?.sessions[0];
  return first ? `/live?session=${first.live_session_id}` : '/live';
});

const llmHistoryLink = computed<string>(() => {
  const r = review.value;
  if (!r || !r.window_start_ms) return '/llm/history';
  return `/llm/history?start_time=${r.window_start_ms}&end_time=${r.window_end_ms}`;
});

function exportPackage(): void {
  // 同源直链下载（浏览器接管 zip 流式响应）；合并多场由 ids 表达
  const url = reviewApi.exportUrl(selectedIds.value);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = '';
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
}
</script>

<style scoped>
.session-review {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.review-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  flex-wrap: wrap;
}

.review-head h1 {
  margin: 0 0 4px;
  font-size: 20px;
}

.review-sub {
  margin: 0;
  color: var(--text-secondary);
  font-size: 13px;
}

.review-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}

.review-select {
  min-width: 280px;
}

.review-alert {
  border-radius: var(--radius-md);
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.chip {
  padding: 4px 10px;
  border: 1px solid var(--border-color-light);
  border-radius: 999px;
  background: var(--bg-card);
  font-size: 12px;
  color: var(--text-regular);
}

.mono {
  font-family: var(--font-mono);
}

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

.columns {
  display: grid;
  grid-template-columns: 1.4fr 1fr;
  gap: 16px;
}

@media (max-width: 1100px) {
  .columns {
    grid-template-columns: 1fr;
  }
}

.panel {
  padding: 14px 16px;
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-md);
  background: var(--bg-card);
}

.panel-title {
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 12px;
}

.waterline-chart {
  height: 260px;
}

.usage-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.usage-table th,
.usage-table td {
  padding: 6px 8px;
  border-bottom: 1px solid var(--border-color-light);
  text-align: left;
}

.usage-table th {
  color: var(--text-secondary);
  font-weight: 500;
}

.usage-table .num {
  text-align: right;
  font-family: var(--font-mono);
}

.total-row td {
  font-weight: 600;
  border-top: 1px solid var(--border-color);
}

.tier {
  display: inline-block;
  padding: 1px 8px;
  border: 1px solid;
  border-radius: 999px;
  font-size: 12px;
}

.table-note {
  margin: 10px 0 0;
  font-size: 12px;
  color: var(--text-secondary);
}

.links {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}

.link {
  color: var(--color-primary);
  text-decoration: none;
  font-size: 13px;
}

.link:hover {
  text-decoration: underline;
}

.link-hint {
  font-size: 12px;
  color: var(--text-secondary);
}
</style>
