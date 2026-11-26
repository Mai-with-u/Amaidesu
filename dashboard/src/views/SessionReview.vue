<!--
  SessionReview - 场次复盘页（只读聚合）

  壳只保留三类职责：场次选择协调（useSessionSelection 组合式）、复盘聚合与单场
  数据的取数编排、页面级错误提示与深入入口；各内容面板在 components/session-review/
  面板组件族（场次档案复用记忆页的 ArchiveLineList）。面板公共样式在
  styles/session-review.css（rv- 前缀），面板独有样式在各组件 scoped 块。
  全部数字来自后端结构化聚合（GET /api/v1/review），本页不做告警判定；
  多选合并视图由后端窗口合并、水位图按 session_id 断线留空隙；档案/台词/流程单/
  观众/游戏明细属单场语义，合并视图不产出。深挖取证不在本页——带数字开 AI 会话。
-->
<template>
  <div class="session-review">
    <header class="review-head">
      <div>
        <h1 class="page-title">场次复盘</h1>
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
          @change="onSelectChange"
        >
          <el-option
            v-for="item in sessions"
            :key="item.live_session_id"
            :label="sessionLabel(item)"
            :value="item.live_session_id"
          />
        </el-select>
        <el-button
          class="review-refresh-btn"
          :loading="refreshing"
          :disabled="refreshing || sessionsLoading || loading"
          @click="handleRefresh"
        >
          刷新
        </el-button>
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
      <!-- 场次档案（本场整理出的标题 + 原话；删单行只影响下一场开播的"上一场"回忆） -->
      <ReviewArchiveCard
        v-if="archiveSessionId !== null"
        :lines="archiveLines"
        :loading="archiveLoading"
        :error="archiveError"
        :action-error="archiveActionError"
        :busy-line-id="archiveBusyLineId"
        @delete="removeArchiveLine"
      />

      <!-- 本场台词（直播里说过的每一句；删一句会连带删掉引用它的档案行） -->
      <ReviewSpeechCard
        v-if="archiveSessionId !== null"
        :lines="speechLines"
        :loading="speechLoading"
        :error="speechError"
        :action-error="speechActionError"
        :busy-chat-id="speechBusyChatId"
        @delete="removeSpeechLine"
      />

      <!-- 本场记忆线索（代码每批推给规划器的旧事：承诺线索与被动想起；用上 = 她点名说了这件事） -->
      <ReviewCuesCard
        v-if="archiveSessionId !== null"
        :items="cues"
        :loading="cueLoading"
        :error="cueError"
      />

      <!-- 复盘报告（logs/reviews 可视化页，由直播复盘流程离线生成；仅单场次且有报告时渲染） -->
      <ReviewReportFrame v-if="reportSrc" :src="reportSrc" />

      <!-- 场次信息 chips（互动类零值降饱和） -->
      <ReviewChipsBar :review="review" />

      <!-- 核心指标卡 -->
      <ReviewMetricCards
        :review="review"
        :silence-link="silenceLink"
        :latency-max-link="latencyMaxLink"
        :llm-failed-link="llmFailedLink"
        :llm-slow-link="llmSlowLink"
      />

      <!-- 流程单执行（单场视图；rundown_transitions 事实行重放） -->
      <RundownExecutionCard v-if="rundown && rundown.segments.length" :rundown="rundown" />

      <div v-if="review.viewers || review.game.items.length" class="columns">
        <!-- 观众构成 + 游戏事件明细（单场视图，明细逐条锚定跳转回看） -->
        <SessionViewersCard v-if="review.viewers" :viewers="review.viewers" />
        <GameEventsCard
          v-if="review.game.items.length"
          :game="review.game"
          :session-id="singleBrief?.live_session_id ?? 0"
        />
      </div>

      <div class="columns">
        <WaterlineChartCard :series="review.waterline" />
        <LlmUsageCard :llm="review.llm" />
      </div>

      <!-- 深入入口 -->
      <section class="rv-panel">
        <div class="rv-panel-title">深入入口</div>
        <div class="rv-links">
          <router-link class="rv-link" :to="eventReviewLink">事件回看 · 本场预过滤</router-link>
          <router-link class="rv-link" :to="llmHistoryLink">LLM 历史 · 本场预过滤</router-link>
          <span class="rv-link-hint"
            >告警叙事与关键事件拣选不在本页——带着上面的数字开 AI 会话深挖</span
          >
        </div>
      </section>
    </template>
    <el-empty v-else-if="!errorMessage && !loading" description="选择一场直播查看复盘聚合" />
  </div>
</template>

<script setup lang="ts">
/**
 * 场次复盘页 —— 单场或多场合并的只读复盘聚合视图
 *
 * keep-alive 生命周期语义：本页被外层路由 `<keep-alive>` 缓存，页面自身不含持续
 * 定时器、事件订阅或 WebSocket；场次选择与 `?session=` 深链的换班协调（含首次
 * 挂载与切回静默刷新）在 useSessionSelection 组合式内，其 docstring 是该状态机
 * 规则的事实源。
 */
import { computed, nextTick, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import { ledgerApi, liveSessionsApi, reviewApi } from '@/api';
import type {
  ArchiveLineItem,
  ReviewSessionBrief,
  ReviewSpeechLine,
  SessionReviewResponse,
  SessionRundownExecutionResponse,
} from '@/types';
import { getApiErrorMessage } from '@/utils/apiError';
import { confirmAction } from '@/utils/confirmAction';
import { useArchiveLines } from '@/composables/useArchiveLines';
import { useSessionSelection } from '@/composables/session-review/useSessionSelection';
import { useReviewCues } from '@/composables/session-review/useReviewCues';
import { useReviewSpeech } from '@/composables/session-review/useReviewSpeech';
import { consoleReplayLink, minSlowThresholdMs } from '@/utils/sessionReview';
import ReviewArchiveCard from '@/components/session-review/ReviewArchiveCard.vue';
import ReviewChipsBar from '@/components/session-review/ReviewChipsBar.vue';
import ReviewCuesCard from '@/components/session-review/ReviewCuesCard.vue';
import ReviewMetricCards from '@/components/session-review/ReviewMetricCards.vue';
import ReviewReportFrame from '@/components/session-review/ReviewReportFrame.vue';
import ReviewSpeechCard from '@/components/session-review/ReviewSpeechCard.vue';
import RundownExecutionCard from '@/components/session-review/RundownExecutionCard.vue';
import SessionViewersCard from '@/components/session-review/SessionViewersCard.vue';
import WaterlineChartCard from '@/components/session-review/WaterlineChartCard.vue';
import LlmUsageCard from '@/components/session-review/LlmUsageCard.vue';
import GameEventsCard from '@/components/session-review/GameEventsCard.vue';
import '@/styles/session-review.css';

const refreshing = ref(false);
const review = ref<SessionReviewResponse | null>(null);
const loading = ref(false);
const errorMessage = ref('');

const { sessions, sessionsLoading, selectedIds, loadSessions, onSelectChange, sessionLabel } =
  useSessionSelection({ errorMessage });

/** 流程单执行回看（单场选择时加载；未装配/无事实行时为 null，面板整体隐藏） */
const rundown = ref<SessionRundownExecutionResponse | null>(null);

/**
 * 本场档案行：切场语义与记忆页档案面板共用同一组合式（切场先清空、过期响应
 * 丢弃、错误与空态分开）；删除后原地重拉这一场。
 */
const {
  lines: archiveLines,
  loading: archiveLoading,
  error: archiveError,
  load: loadArchive,
} = useArchiveLines();
const archiveBusyLineId = ref<number | null>(null);
/** 删除档案行的失败原因（加载错误由组合式管；这里只影响提示，不藏列表） */
const archiveActionError = ref('');

/** 本场台词：逐句可删，删除会连带删掉引用它的档案行（同组合式负责 busy 与错误） */
const {
  items: speechLines,
  loading: speechLoading,
  error: speechError,
  actionError: speechActionError,
  busyChatId: speechBusyChatId,
  load: loadSpeech,
  remove: deleteSpeech,
} = useReviewSpeech();

/** 本场记忆线索：只读加载（按来源的本地筛选在 ReviewCuesCard 内） */
const { items: cues, loading: cueLoading, error: cueError, load: loadCues } = useReviewCues();

/** 复盘聚合请求序号与在途 Promise：快速切换或手动刷新时防乱序、防双发 */
let reviewSeq = 0;
let inFlightReview: Promise<void> | null = null;

watch(
  () => selectedIds.value.join(','),
  () => {
    void loadReview();
  },
);

/** 工具栏「刷新」：重拉场次列表与当前复盘数据，加载态防重复点击 */
async function handleRefresh(): Promise<void> {
  if (refreshing.value || sessionsLoading.value || loading.value) return;
  refreshing.value = true;
  try {
    const beforeKey = selectedIds.value.join(',');
    const ok = await loadSessions('manual');
    if (!ok) return;
    const afterKey = selectedIds.value.join(',');
    if (afterKey !== beforeKey) {
      // 列表刷新已自动切到更新的默认场次，等待 watch 触发的 loadReview 完成即可，不重复发请求
      await nextTick();
      if (inFlightReview) await inFlightReview;
    } else if (selectedIds.value.length > 0 || review.value !== null) {
      await loadReview();
    }
  } finally {
    refreshing.value = false;
  }
}

function loadReview(): Promise<void> {
  const seq = ++reviewSeq;
  const task = (async (): Promise<void> => {
    loading.value = true;
    errorMessage.value = '';
    try {
      const resp = await reviewApi.get(selectedIds.value);
      if (seq !== reviewSeq) return;
      review.value = resp.data;
    } catch (error) {
      if (seq !== reviewSeq) return;
      review.value = null;
      errorMessage.value = getApiErrorMessage(error, '复盘聚合加载失败');
    } finally {
      if (seq === reviewSeq) loading.value = false;
    }
    if (seq !== reviewSeq) return;
    archiveActionError.value = '';
    // 单场语义的几块互不依赖：并行加载，互不阻塞
    await Promise.all([
      loadRundown(),
      loadArchive(archiveSessionId.value),
      loadSpeech(archiveSessionId.value),
      loadCues(archiveSessionId.value),
    ]);
  })();
  inFlightReview = task;
  void task.finally(() => {
    if (inFlightReview === task) inFlightReview = null;
  });
  return task;
}

/** 流程单执行仅在单场选择时加载；接口不可用/无事实行按无数据降级（面板隐藏） */
async function loadRundown(): Promise<void> {
  const ids = selectedIds.value;
  if (review.value === null || ids.length !== 1) {
    rundown.value = null;
    return;
  }
  try {
    const resp = await liveSessionsApi.rundownExecution(ids[0]);
    rundown.value = resp.data;
  } catch {
    rundown.value = null;
  }
}

/** 删一行档案：二次确认后调接口，成功原地重拉本场档案（不整体刷新复盘） */
async function removeArchiveLine(line: ArchiveLineItem): Promise<void> {
  if (
    !(await confirmAction(
      `删除档案行「${line.text}」？删掉后这一行不会再出现在下一场开播的回忆里，也不影响承诺账本。`,
      '删除档案行',
    ))
  ) {
    return;
  }
  archiveBusyLineId.value = line.id;
  archiveActionError.value = '';
  try {
    await ledgerApi.deleteArchiveLine(line.id);
    await loadArchive(archiveSessionId.value, { clearLines: false });
  } catch (error) {
    archiveActionError.value = getApiErrorMessage(error, '删除档案行失败');
  } finally {
    archiveBusyLineId.value = null;
  }
}

/**
 * 删一句台词：二次确认后调接口；成功刷新台词与档案两块——引用这句的档案行
 * 会被后端一起删掉，承诺保留但原话清空（记忆页显示"原句已删除"）。
 */
async function removeSpeechLine(line: ReviewSpeechLine): Promise<void> {
  const ok = await confirmAction(
    '删除这句台词？引用它的档案行会一起删除；对它的承诺会保留并显示「原句已删除」。此操作不可恢复。',
    '删除这句台词',
  );
  if (!ok) return;
  const result = await deleteSpeech(line.chat_id);
  if (result === null) return;
  const notes = ['已删除这句台词'];
  if (result.archive_lines_removed > 0)
    notes.push(`同时删掉 ${result.archive_lines_removed} 行档案`);
  if (result.commitments_cleared > 0)
    notes.push(`${result.commitments_cleared} 条承诺的原话已标为删除`);
  ElMessage.success(notes.join('；'));
  await Promise.all([
    loadSpeech(archiveSessionId.value, { clearLines: false }),
    loadArchive(archiveSessionId.value, { clearLines: false }),
  ]);
}

/** 单场选择的 brief（多选合并视图为 null——单场语义面板以此为开关） */
const singleBrief = computed<ReviewSessionBrief | null>(() =>
  review.value && review.value.sessions.length === 1 ? review.value.sessions[0] : null,
);

/** 场次档案挂在单场语义上：合并视图不拉档案（没有对应的单场） */
const archiveSessionId = computed<number | null>(() => singleBrief.value?.live_session_id ?? null);

/**
 * 复盘报告 iframe 地址（静态路由 /api/v1/review/files/ 下，相对引用的
 * report.css / report.js 同目录解析）；多选合并或报告未生成为 null。
 */
const reportSrc = computed<string | null>(() => {
  const file = review.value && review.value.sessions.length === 1 ? review.value.report_file : null;
  return file ? `/api/v1/review/files/${encodeURIComponent(file)}` : null;
});

/** 控制台回看深链（本场预过滤；at=时刻锚点） */
function replayLink(atMs?: number): string {
  const brief = singleBrief.value;
  return brief ? consoleReplayLink(brief.live_session_id, atMs) : '/live';
}

const silenceLink = computed<string | null>(() => {
  const start = review.value?.speech.longest_silence_start_ms;
  return start != null ? replayLink(start) : null;
});

const latencyMaxLink = computed<string | null>(() => {
  const at = review.value?.decisions.latency.max_at_ms;
  return at != null ? replayLink(at) : null;
});

const eventReviewLink = computed<string>(() => {
  const r = review.value;
  if (!r || !r.sessions.length) return '/live';
  // 多选合并时携带全部场次 id（LiveObserver 按序取首个命中进回看）
  const ids = selectedIds.value.length ? selectedIds.value : r.sessions.map(s => s.live_session_id);
  return `/live?session=${ids.join(',')}`;
});

const llmHistoryLink = computed<string>(() => {
  const r = review.value;
  if (!r || !r.window_start_ms) return '/llm/history';
  return `/llm/history?start_time=${r.window_start_ms}&end_time=${r.window_end_ms}`;
});

/** 失败调用卡深链：时间窗 + 仅失败（无失败调用或无时间窗为 null，卡上不渲染跳转） */
const llmFailedLink = computed<string | null>(() => {
  const r = review.value;
  if (!r || !r.window_start_ms || r.llm.overall.failed_calls === 0) return null;
  return `${llmHistoryLink.value}&success_only=false`;
});

/** 慢调用卡深链：时间窗 + 耗时下限（各 profile 阈值最小值，与卡计数同源宽松口径） */
const llmSlowLink = computed<string | null>(() => {
  const r = review.value;
  if (!r || !r.window_start_ms || r.llm.overall.slow_calls === 0) return null;
  return `${llmHistoryLink.value}&latency_gt_ms=${minSlowThresholdMs(r.llm.slow_thresholds ?? {})}`;
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
</style>
