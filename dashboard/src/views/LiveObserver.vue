<template>
  <div class="console-page">
    <!-- 顶栏：页面身份 + 阶段状态 + 模拟器徽章 + 实时脉冲 + 时钟         -->
    <header class="console-head">
      <span class="pulse" :class="wsConnected ? 'is-live' : 'is-dead'" aria-hidden="true" />
      <h1 class="console-title">直播控制台</h1>
      <span class="console-tagline">实时观察 · 决策回看 · 现场干预</span>
      <span
        v-if="stageChip"
        class="stage-chip"
        :class="{ 'is-running': stageChip.running }"
        :title="stageChip.detail"
      >
        {{ stageChip.label }}
      </span>
      <router-link
        v-if="simulatorChip"
        class="sim-chip"
        to="/simulator"
        :class="{ 'is-on': simulatorChip.on }"
      >
        {{ simulatorChip.label }}
      </router-link>
      <span class="grow" />
      <span class="show-feed" :class="{ 'is-dead': !wsConnected }">
        {{ wsConnected ? '实时' : '连接中断' }}
      </span>
      <time class="show-clock mono">{{ wallClock }}</time>
    </header>

    <div class="console-body">
      <!-- 左栏：场次侧边栏（当前 + 历史回看 + 生命周期开关）               -->
      <aside class="sessions" aria-label="直播场次">
        <header class="sessions-head">
          <h2 class="sessions-title">场次</h2>
          <span class="grow" />
          <el-button size="small" type="primary" @click="openSession">开启</el-button>
          <el-button size="small" :disabled="!activeExplicitSession" @click="closeSession"
            >结束</el-button
          >
        </header>
        <div class="sessions-filters">
          <el-input v-model="sessionQuery" size="small" placeholder="搜索场次标题" clearable />
          <el-select v-model="sessionSourceFilter" size="small" class="sessions-source">
            <el-option label="全部来源" value="" />
            <el-option label="手动" value="manual" />
            <el-option label="回放" value="replay" />
            <el-option label="历史" value="legacy" />
          </el-select>
        </div>
        <ul class="session-list">
          <li
            v-for="item in sessions"
            :key="item.live_session_id"
            class="session-card"
            :class="{ 'is-selected': isSelected(item), 'is-live': item.is_active }"
            @click="selectSession(item)"
          >
            <div class="session-top">
              <span class="session-name" :title="sessionTitle(item)">{{ sessionTitle(item) }}</span>
              <span class="session-badge" :class="`is-${item.source}`">{{
                sourceLabel(item.source)
              }}</span>
            </div>
            <div class="session-meta mono">
              <span>{{ sessionTimeLabel(item) }}</span>
              <span class="grow" />
              <span>{{ item.message_count }} 条</span>
            </div>
            <div v-if="!item.is_active && item.source !== 'legacy'" class="session-actions">
              <el-popconfirm
                title="删除该场次及其全部明细？"
                width="220"
                @confirm="removeSession(item)"
              >
                <template #reference>
                  <el-button class="session-del" link size="small" @click.stop>删除</el-button>
                </template>
              </el-popconfirm>
            </div>
          </li>
        </ul>
        <p class="sessions-hint">
          启动不自动开场次；未开启期间消息仅在内存中流转（测试模式，不落库）
        </p>
      </aside>

      <!-- 右区：环节横幅 + 时间线                                        -->
      <section class="console-main">
        <section class="slate" :class="{ 'is-idle': !rundownBanner }" aria-label="当前环节">
          <span class="slate-eyebrow">当前环节</span>
          <template v-if="rundownBanner">
            <span class="slate-order mono">#{{ rundownBanner.order }}</span>
            <h2 class="slate-label" :title="rundownBanner.label">{{ rundownBanner.label }}</h2>
            <span class="slate-action">{{ rundownBanner.actionLabel }}</span>
            <span v-if="rundownBanner.note" class="slate-note" :title="rundownBanner.note">{{
              rundownBanner.note
            }}</span>
            <span class="grow" />
            <span v-if="rundownBanner.startLabel" class="slate-meta mono"
              >计划 {{ rundownBanner.startLabel }}</span
            >
            <span v-if="rundownBanner.expectedLabel" class="slate-meta mono">
              预计 {{ rundownBanner.expectedLabel }}
            </span>
            <span class="slate-meta mono">{{
              relativeTime(nowTick, rundownBanner.changedAtMs)
            }}</span>
          </template>
          <span v-else class="slate-idle">流程单未运行或未接入</span>
        </section>

        <div v-if="showTestModeNotice" class="test-mode-notice" title="正式直播请先在左侧开启场次">
          测试模式：未开启场次，消息仅在内存中流转不落库
        </div>

        <section class="stage" aria-label="时间线">
          <header class="stage-bar" :class="{ 'is-paused': paused && sessionMode === 'live' }">
            <h2 class="stage-title">
              {{
                sessionMode === 'live' ? '实时时间线' : `回看 · ${sessionTitle(selectedSession)}`
              }}
            </h2>
            <span class="stage-hint">
              {{
                sessionMode === 'live'
                  ? paused
                    ? '已暂停 · 事件仍在后台累积'
                    : '消息与决策按时间交织，最新在下方'
                  : '历史回看（事件侧仅保留环形缓冲窗口内的记录）'
              }}
            </span>
            <span class="grow" />
            <template v-if="sessionMode === 'live'">
              <!-- 显示模式：时间线=单列沿脊线；会话=观众左/主播右气泡对齐 -->
              <el-radio-group v-model="displayMode" size="small">
                <el-radio-button value="timeline">时间线</el-radio-button>
                <el-radio-button value="chat">会话</el-radio-button>
              </el-radio-group>
              <!-- 来源过滤 chips：仅过滤 Agent 产生的卡（tool/speech/decision/verdict/stage/game），
                观众消息与场次边界始终可见；与既有 el-button 族风格一致 -->
              <span class="agent-filter">
                <el-check-tag
                  :checked="agentFilter === 'all'"
                  size="small"
                  @change="(checked: boolean) => handleAgentChip('all', checked)"
                >
                  全部
                </el-check-tag>
                <el-check-tag
                  :checked="agentFilter === 'streamer'"
                  size="small"
                  @change="(checked: boolean) => handleAgentChip('streamer', checked)"
                >
                  主播
                </el-check-tag>
                <el-check-tag
                  :checked="agentFilter === 'game'"
                  size="small"
                  @change="(checked: boolean) => handleAgentChip('game', checked)"
                >
                  游戏 Agent
                </el-check-tag>
              </span>
              <span class="stage-count mono">{{ entries.length }} / {{ MAX_ENTRIES }}</span>
              <el-button size="small" :type="paused ? 'primary' : 'default'" @click="togglePause">
                {{ paused ? '继续' : '暂停' }}
              </el-button>
              <el-button size="small" :disabled="entries.length === 0" @click="clearTimeline">
                清空
              </el-button>
            </template>
            <el-button v-else size="small" type="primary" @click="backToLive">回到实时</el-button>
          </header>

          <div class="stage-body">
            <div ref="scrollRef" class="stage-scroll" @scroll.passive="onScroll">
              <FeedTimeline
                :entries="entries"
                :layout="displayMode"
                :empty-text="
                  sessionMode === 'live'
                    ? '静候消息与决策——注入一条弹幕试试'
                    : '该场次暂无可回看条目'
                "
              />
            </div>

            <button
              v-if="unseen > 0 && sessionMode === 'live'"
              type="button"
              class="jump"
              @click="jumpToLatest"
            >
              {{ unseen >= 99 ? '99+' : unseen }} 条新内容 · 回到最新 ↓
            </button>
          </div>

          <!-- 干预输入条（code agent 风格）：卡片容器内嵌无边框输入 + 模式 chip +
               圆形发送钮；Tab/Shift+Tab 切模式、Enter 发送、↑↓ 回溯历史。
               强制回应为后台执行：发送后立即可继续输入，在途状态由状态 chip 承载 -->
          <div v-if="sessionMode === 'live'" class="input-bar">
            <div class="input-shell">
              <el-input
                ref="sendInputRef"
                v-model="sendText"
                type="textarea"
                :rows="1"
                :autosize="{ minRows: 1, maxRows: 5 }"
                resize="none"
                class="input-main"
                :disabled="sending"
                :placeholder="sendModeDef.placeholder"
                @keydown="onSendKeydown"
              />
              <div class="input-toolbar">
                <el-select
                  v-model="sendMode"
                  size="small"
                  class="input-mode"
                  popper-class="input-mode-popper"
                  :disabled="sending"
                  :title="sendModeDef.desc"
                >
                  <el-option
                    v-for="mode in SEND_MODES"
                    :key="mode.key"
                    :label="mode.label"
                    :value="mode.key"
                  >
                    <div class="mode-option">
                      <span class="mode-option-label">{{ mode.label }}</span>
                      <span class="mode-option-desc">{{ mode.desc }}</span>
                    </div>
                  </el-option>
                </el-select>
                <el-input
                  v-if="sendMode === 'danmaku'"
                  v-model="injectNickname"
                  size="small"
                  class="input-nick"
                  placeholder="观众昵称（可选）"
                  @keydown="onSendKeydown"
                />
                <span v-if="forcePending > 0" class="input-status">
                  <el-icon class="is-loading"><Loading /></el-icon>
                  主播正在想…
                </span>
                <span class="input-kbd mono" title="在输入框内按 Tab 切换模式，↑↓ 回溯发送历史">
                  Tab 切模式 · ↑↓ 历史
                </span>
                <el-button
                  class="input-send"
                  type="primary"
                  circle
                  :loading="sending"
                  @click="sendCurrent"
                >
                  <el-icon v-if="!sending"><Promotion /></el-icon>
                </el-button>
              </div>
            </div>
          </div>
        </section>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 直播控制台 —— 实时观察 + 决策回看 + 现场干预
 *
 * 三区布局：
 * - 左侧场次侧边栏：当前进行中场次 + 历史场次（点击回看该场完整时间线），
 *   顶部提供开启/结束/删除开关（场次生命周期归 LiveSessionManager）
 * - 中部时间线：观众消息、主播发言、决策记录（planner.decision）、阶段状态
 *   （streamer.stage）、场次边界、节目单推进、里程碑；显示模式二选一——
 *   时间线（单列居左、靠样式区分）/ 会话（观众左、主播右气泡对齐）
 * - 顶栏：连接状态、决策管线阶段徽章、模拟器模式徽章
 *
 * 干预输入条（时间线底部，复用既有 API）：三模式 = 场控的三种操作——
 * 注入弹幕（debug/inject-message，与真实弹幕同链路）、强制回应
 * （streamer/test-decision，直驱决策、结果以决策卡落进时间线，留空=自由发挥）、
 * 幕后提醒（streamer/trigger-proactive，由真实 ProactiveTrigger 限流判定）。
 * Tab/Shift+Tab 切模式、Enter 发送、↑↓ 回溯发送历史。
 *
 * 数据来源：
 * - 实时：events store（全局 WS + 游标回填，刷新/断线不丢时间线）+ 思考流旁路
 *   （kind="stream"，仅视图层合成思考行、按时间归并进时间线，不入 store 不回看）
 * - 回看：GET /live-sessions/{id}/timeline（明细行 + 事件历史按时间合并；无思考数据）
 * 渲染字段一律取自后端真实 Payload（src/modules/events/payloads/），不臆造字段。
 */
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue';
import { storeToRefs } from 'pinia';
import { ElMessage, ElMessageBox } from 'element-plus';
import { Promotion, Loading } from '@element-plus/icons-vue';
import { useEventsStore, useWebSocketStore } from '@/stores';
import { debugApi, liveSessionsApi, simulatorApi, streamerApi } from '@/api';
import { useNowTick } from '@/composables/useNowTick';
import { useScrollFollow } from '@/composables/useScrollFollow';
import {
  STAGE_LABEL,
  MAX_ENTRIES,
  agentGroupOf,
  buildLiveEntries,
  buildThinkingRow,
  bool,
  formatAmount,
  fromDecision,
  fromStage,
  isRecord,
  makeEntry,
  mergeEntriesByTime,
  num,
  relativeTime,
  str,
  toGameEntry,
  type AgentGroup,
  type FeedEvent,
  type ShowEntry,
  type ThinkingSegmentInput,
  type ThinkingStep,
} from '@/utils/liveFeed';
import FeedTimeline from '@/components/live/FeedTimeline.vue';
import type { LiveSessionItem, ThinkingDelta, WebSocketMessage } from '@/types';

// 常量

/** 距底 ≤ 此距离视为"贴底"，可自动跟随 */

const SOURCE_LABEL: Record<string, string> = {
  manual: '手动',
  replay: '回放',
  legacy: '历史',
};

// 类型

interface RundownBanner {
  order: number;
  label: string;
  actionLabel: string;
  note: string;
  startLabel: string;
  expectedLabel: string;
  changedAtMs: number;
}

// 思考流（WS kind="stream"；ADR-008 best-effort 观测通道）
//
// 只在视图层消费：按 (round_id, phase, step) 累积思考段，再合成 kind='thinking'
// 的时间线行与事件条目按时间归并（不进 events store、不落库、不回看）。

const THINKING_ROUNDS_MAX = 20;
/** 每决策轮的思考聚合：planner 与 minecraft 共用按步分段（与工具卡时间交织），
 * 段携带自身 phase——两边步骤号各自从头计数，只按步号查找会互相踩段；replyer 独立一段 */
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
const thinkingRounds = reactive(new Map<string, ThinkingRound>());

/** 思考行隐藏水位：清空时间线时记下当前思考行最大时刻，此前的思考行一并隐藏
 *  （思考行不进 hiddenIds 体系——它不是事件，没有事件 id） */
const thinkingHiddenBeforeMs = ref(0);

/** 思考流增量合批：WS 每消息触发一次落状态会带起整条时间线重渲染，复杂任务期间
 * 思考增量高频涌入时把页面拖死——先缓冲，按固定间隔一次性落进 reactive 状态，
 * 渲染频率与消息频率解耦；缓冲条目携带信封时间戳（段首定位用） */
const THINKING_FLUSH_INTERVAL_MS = 150;
const pendingDeltas: Array<{ delta: ThinkingDelta; tsMs: number }> = [];
let thinkingFlushTimer: ReturnType<typeof setTimeout> | null = null;

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

// Store 与全局状态

const eventsStore = useEventsStore();
const wsStore = useWebSocketStore();
const { events } = storeToRefs(eventsStore);
const { isConnected: wsConnected } = storeToRefs(wsStore);

wsStore.subscribe(handleThinkingMessage);

// 通用取值助手（侧栏时钟/时长；事件→条目取值助手见 utils/liveFeed.ts）

function clockLabel(ms: number): string {
  return new Date(ms).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

// 事件 → 时间线条目（折叠规则与取值助手统一在 utils/liveFeed.ts；
// 回看时间线仍按条目类型直接调 makeEntry / fromDecision / fromStage）

// 场次侧边栏：列表 / 生命周期 / 回看

const sessions = ref<LiveSessionItem[]>([]);
/** 进行中的显式场次主键（来自 API 响应，不受侧边栏筛选影响——筛选只是视图） */
const activeSessionId = ref<number | null>(null);
const activeExplicitSession = computed(() => activeSessionId.value !== null);
/** 测试模式提示：实时模式下无任何进行中的显式场次，消息仅在内存中流转、不落库 */
const showTestModeNotice = computed(
  () => sessionMode.value === 'live' && !activeExplicitSession.value,
);
/** 场次筛选：标题关键字 + 来源（服务端筛选） */
const sessionQuery = ref('');
const sessionSourceFilter = ref('');
const sessionMode = ref<'live' | 'replay'>('live');
const selectedSession = ref<LiveSessionItem | null>(null);

function sessionTitle(item: LiveSessionItem | null): string {
  if (!item) return '';
  if (item.title) return item.title;
  return `场次 #${item.live_session_id}`;
}

function sourceLabel(source: string): string {
  return SOURCE_LABEL[source] ?? source;
}

function sessionTimeLabel(item: LiveSessionItem): string {
  const start = clockLabel(item.started_at_ms);
  if (item.ended_at_ms == null) return `${start} 起`;
  return `${start} – ${clockLabel(item.ended_at_ms)}`;
}

function isSelected(item: LiveSessionItem): boolean {
  if (sessionMode.value === 'live') {
    return item.is_active;
  }
  return selectedSession.value?.live_session_id === item.live_session_id;
}

async function loadSessions(): Promise<void> {
  try {
    const response = await liveSessionsApi.list({
      source: sessionSourceFilter.value || undefined,
      q: sessionQuery.value.trim() || undefined,
    });
    sessions.value = response.data.items;
    activeSessionId.value = response.data.active_session_id;
  } catch {
    /* 场次面不可用时侧边栏保持空态，不阻断时间线 */
  }
}

let sessionFilterTimer: ReturnType<typeof setTimeout> | null = null;
watch([sessionQuery, sessionSourceFilter], () => {
  if (sessionFilterTimer) clearTimeout(sessionFilterTimer);
  sessionFilterTimer = setTimeout(() => {
    void loadSessions();
  }, 250);
});

async function openSession(): Promise<void> {
  let title: string | undefined;
  try {
    const { value } = await ElMessageBox.prompt('为新的直播场次起个标题（可留空）', '开启场次', {
      confirmButtonText: '开启',
      cancelButtonText: '取消',
      inputPlaceholder: '例如：周五晚间场',
    });
    title = value?.trim() || undefined;
  } catch {
    return; // 取消输入
  }
  try {
    await liveSessionsApi.open({ title });
    ElMessage.success('场次已开启');
  } catch (error) {
    ElMessage.error(error instanceof Error ? `开启场次失败：${error.message}` : '开启场次失败');
    return;
  }
  backToLive(); // 开了新场次即回到实时视图，避免停留在旧场次的回看里
  await loadSessions();
}

async function closeSession(): Promise<void> {
  if (activeSessionId.value == null) return;
  try {
    await liveSessionsApi.close(activeSessionId.value);
    ElMessage.success('场次已结束');
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '结束场次失败');
  }
  await loadSessions();
}

async function removeSession(item: LiveSessionItem): Promise<void> {
  try {
    await liveSessionsApi.remove(item.live_session_id);
    ElMessage.success('场次已删除');
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '删除失败');
  }
  if (selectedSession.value?.live_session_id === item.live_session_id) backToLive();
  await loadSessions();
}

/** 历史场次 → 回看模式；进行中场次 → 实时模式 */
function selectSession(item: LiveSessionItem): void {
  if (item.is_active) {
    backToLive();
    return;
  }
  sessionMode.value = 'replay';
  selectedSession.value = item;
}

function backToLive(): void {
  sessionMode.value = 'live';
  selectedSession.value = null;
}

// 回看时间线：REST 明细 + 事件历史 → ShowEntry

const replayEntries = ref<ShowEntry[]>([]);
const replayLoading = ref(false);

function decisionEntryFromData(id: string, tsMs: number, data: Record<string, unknown>): ShowEntry {
  return fromDecision(id, tsMs, data);
}

async function loadReplayTimeline(item: LiveSessionItem): Promise<void> {
  replayLoading.value = true;
  try {
    const response = await liveSessionsApi.timeline(item.live_session_id);
    const next: ShowEntry[] = [];
    response.data.items.forEach((entry, index) => {
      const id = `rp-${entry.ts_ms}-${index}`;
      if (entry.kind === 'event') {
        const data = isRecord(entry.data) ? entry.data : {};
        const type = str(entry.event_type);
        if (type === 'planner.decision') {
          next.push(decisionEntryFromData(id, entry.ts_ms, data));
        } else if (type === 'streamer.stage') {
          next.push(fromStage(id, entry.ts_ms, data));
        } else if (type === 'live.started' || type === 'live.ended') {
          next.push(
            makeEntry({
              id,
              kind: 'boundary',
              tsMs: entry.ts_ms,
              text: type === 'live.started' ? '场次开启' : '场次结束',
              note: str(data.title) || str(data.reason),
            }),
          );
        } else if (type === 'rundown.changed') {
          const index = typeof data.index === 'number' ? data.index : 0;
          const total = typeof data.total === 'number' ? data.total : 0;
          const finished = total > 0 && index >= total;
          next.push(
            makeEntry({
              id,
              kind: 'rundown',
              tsMs: entry.ts_ms,
              text: str(data.segment_title) || (finished ? '流程单完成' : '环节切换'),
              note: finished ? '流程单已全部完成' : `环节 ${index}/${total}`,
              badge:
                str(data.by) === 'human' ? '手动' : str(data.by) === 'system' ? '系统' : 'Agent',
            }),
          );
        } else if (type === 'game.milestone') {
          next.push(
            makeEntry({
              id,
              kind: 'milestone',
              tsMs: entry.ts_ms,
              text: str(data.message),
              note: [str(data.game), str(data.scene)].filter(Boolean).join(' · '),
            }),
          );
        } else if (
          type === 'game.report' ||
          type === 'game.attention_required' ||
          type === 'game.error'
        ) {
          // 实时路径已由共享层 toEntry→toGameEntry 自动入列；
          // 回看路径手工拼出 FeedEvent 调用同一函数，保持条目构造逻辑单点维护
          const gameEntry = toGameEntry({
            id,
            type,
            timestamp_ms: entry.ts_ms,
            data,
          } as FeedEvent);
          if (gameEntry) next.push(gameEntry);
        }
        return;
      }
      if (entry.kind === 'speech') {
        next.push(
          makeEntry({
            id,
            kind: 'speech',
            tsMs: entry.ts_ms,
            actor: '主播',
            text: str(entry.text),
            speak: true,
            replyTo: str(entry.reply_to_message_id),
          }),
        );
        return;
      }
      if (entry.kind === 'gift') {
        next.push(
          makeEntry({
            id,
            kind: 'gift',
            tsMs: entry.ts_ms,
            actor: str(entry.user_name) || '匿名观众',
            text: `送出 ${str(entry.gift_name)} ×${num(entry.gift_count) ?? 1}`,
            badge: '礼物',
            messageId: str(entry.message_id),
            simulated: entry.simulated === true,
          }),
        );
        return;
      }
      if (entry.kind === 'super_chat') {
        const amount = num(entry.amount);
        next.push(
          makeEntry({
            id,
            kind: 'super_chat',
            tsMs: entry.ts_ms,
            actor: str(entry.user_name) || '匿名观众',
            text: str(entry.content),
            badge: 'SC',
            money: amount != null ? `¥${formatAmount(amount)}` : '',
            messageId: str(entry.message_id),
            simulated: entry.simulated === true,
          }),
        );
        return;
      }
      // danmaku / enter
      next.push(
        makeEntry({
          id,
          kind: entry.kind === 'enter' ? 'enter' : 'danmaku',
          tsMs: entry.ts_ms,
          actor: str(entry.user_name) || '匿名观众',
          text:
            entry.kind === 'enter'
              ? `${str(entry.user_name) || '观众'} 进入直播间`
              : str(entry.content),
          messageId: str(entry.message_id),
          simulated: entry.simulated === true,
        }),
      );
    });
    replayEntries.value = next;
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '回看加载失败');
    replayEntries.value = [];
  } finally {
    replayLoading.value = false;
  }
}

watch(
  [sessionMode, selectedSession],
  ([mode, selected]) => {
    if (mode === 'replay' && selected) {
      void loadReplayTimeline(selected);
    }
  },
  { immediate: true },
);

// 实时时间线：暂停 / 清空水位 / 条目缓冲

const paused = ref(false);
/** 清空水位：记下当时缓冲区里的事件 id，之后重建时永久跳过（store 仍不丢数据） */
const hiddenIds = ref<Set<string>>(new Set());
const liveEntries = ref<ShowEntry[]>([]);

/** 暂停期思考行快照：暂停时锁存当前思考行，恢复后回到实时
 *  （事件流靠 watch 跳过重建实现冻结，思考行是 computed、需单独锁存） */
const frozenThinkingRows = ref<ShowEntry[] | null>(null);

/** 时间线显示模式：timeline=单列沿脊线；chat=会话模式（观众左/主播右气泡对齐，
 * 原独立会话调试页的显示形态） */
const displayMode = ref<'timeline' | 'chat'>('timeline');

/** 来源过滤：实时模式下按 Agent 组别过滤展示条目（观众消息与场次边界不过滤——观众始终可见）；
 *  回看模式不生效（场次条目全量呈现） */
const agentFilter = ref<'all' | AgentGroup>('all');

/** chips 点击处理：el-check-tag 在「勾选→取消勾选」时都会触发 change；
 *  排他语义下只接受「点亮」动作，避免误触把已选中态切走 */
function handleAgentChip(value: 'all' | AgentGroup, checked: boolean): void {
  if (checked) agentFilter.value = value;
}

/** 条目重建节流：每条事件到达都会触发 buildLiveEntries 全量折叠，复杂任务期间
 * 工具结果高频涌入时按固定间隔合并重建（尾沿触发，静默后最终态仍会落地） */
const REBUILD_INTERVAL_MS = 250;
let rebuildTimer: ReturnType<typeof setTimeout> | null = null;
let lastRebuildMs = 0;
let pendingRebuild: Array<FeedEvent[]> | null = null;
let pendingHidden: Set<string> | null = null;

watch(
  [events, paused, hiddenIds],
  ([list, isPaused, hidden]) => {
    if (isPaused) return;
    pendingRebuild = [list as FeedEvent[]];
    pendingHidden = hidden;
    if (rebuildTimer) return;
    const wait = Math.max(0, REBUILD_INTERVAL_MS - (Date.now() - lastRebuildMs));
    rebuildTimer = setTimeout(() => {
      rebuildTimer = null;
      lastRebuildMs = Date.now();
      // 暂停期不重建（与原 watch 跳过重建的冻结语义一致）；恢复时 watch 会再排程
      if (paused.value || !pendingRebuild || pendingHidden === null) return;
      liveEntries.value = buildLiveEntries(pendingRebuild[0], pendingHidden);
    }, wait);
  },
  { immediate: true },
);

/** 展示条目：实时模式把思考行与事件条目按时间归并后过 agentFilter；
 *  回看模式取 REST 时间线全量（思考流不落库，回看没有思考行）。
 *  过滤只针对 Agent 产生的卡，观众消息与场次边界（room 组）始终可见 */
const entries = computed<ShowEntry[]>(() => {
  if (sessionMode.value === 'replay') return replayEntries.value;
  const thinkingRows = paused.value ? (frozenThinkingRows.value ?? []) : liveThinkingRows.value;
  const list = mergeEntriesByTime(liveEntries.value, thinkingRows);
  if (agentFilter.value === 'all') return list;
  return list.filter(
    entry => agentGroupOf(entry) === 'room' || agentGroupOf(entry) === agentFilter.value,
  );
});

function togglePause(): void {
  const next = !paused.value;
  // 暂停沿锁存当前思考行，恢复沿放回实时流（事件条目的冻结由 watch 跳过重建实现）
  frozenThinkingRows.value = next ? liveThinkingRows.value : null;
  paused.value = next;
}

function clearTimeline(): void {
  hiddenIds.value = new Set(events.value.map(event => event.id));
  liveEntries.value = [];
  // 思考行按时间水位隐藏（与 hiddenIds 同语义：只藏不删）
  const rows = paused.value ? (frozenThinkingRows.value ?? []) : liveThinkingRows.value;
  thinkingHiddenBeforeMs.value = rows.reduce(
    (max, row) => Math.max(max, row.tsMs),
    thinkingHiddenBeforeMs.value,
  );
  unseen.value = 0;
}

// 场次生命周期事件 → 侧边栏刷新

watch(events, list => {
  for (let i = list.length - 1; i >= Math.max(0, list.length - 5); i -= 1) {
    const type = list[i].type;
    if (type === 'live.started' || type === 'live.ended') {
      void loadSessions();
      break;
    }
  }
});

// 当前环节横幅：取最近一条 rundownBanner.update

const rundownBanner = computed<RundownBanner | null>(() => {
  const list = events.value;
  for (let i = list.length - 1; i >= 0; i -= 1) {
    const event = list[i];
    if (event.type !== 'rundown.changed') continue;
    const data = isRecord(event.data) ? event.data : {};
    const by = typeof data.by === 'string' ? data.by : 'agent';
    const changedAtMs = typeof data.at_ms === 'number' ? data.at_ms : null;
    const index = typeof data.index === 'number' ? data.index : 0;
    return {
      order: index + 1,
      label: str(data.segment_title) || '未命名环节',
      actionLabel: by === 'human' ? '手动切换' : by === 'system' ? '系统切换' : 'Agent 切换',
      note: '',
      startLabel: '',
      expectedLabel: '',
      changedAtMs: changedAtMs ?? event.timestamp_ms,
    };
  }
  return null;
});

// 顶栏徽章：决策管线阶段 + 模拟器模式

const stageChip = computed<{ label: string; running: boolean; detail: string } | null>(() => {
  const list = events.value;
  for (let i = list.length - 1; i >= 0; i -= 1) {
    const event = list[i];
    if (event.type !== 'streamer.stage') continue;
    const data = isRecord(event.data) ? event.data : {};
    const stage = str(data.stage);
    const running = str(data.agent_state) === 'running';
    return {
      label: STAGE_LABEL[stage] ?? stage,
      running,
      detail: str(data.detail),
    };
  }
  return null;
});

const simulatorChip = ref<{ label: string; on: boolean } | null>(null);

async function loadSimulatorStatus(): Promise<void> {
  try {
    const response = await simulatorApi.getStatus();
    const mode = str(response.data.mode) || 'off';
    const running = bool(response.data.is_running);
    const label =
      mode === 'generate'
        ? running
          ? '模拟器 · 生成中'
          : '模拟器 · 生成待启'
        : mode === 'replay'
          ? running
            ? '模拟器 · 回放中'
            : '模拟器 · 回放待启'
          : '模拟器未启用';
    simulatorChip.value = { label, on: mode !== 'off' && running };
  } catch {
    simulatorChip.value = null;
  }
}

// 干预输入条：三个发送模式 = 你以"幕后场控"身份对直播间的三种操作。
// Tab/Shift+Tab 循环切模式、Enter 发送、↑↓ 回溯发送历史（Terminal 习惯）。

interface SendMode {
  key: 'danmaku' | 'force' | 'nudge';
  label: string;
  /** 模式说明：下拉选项与输入条下方提示共用 */
  desc: string;
  placeholder: string;
}

const SEND_MODES: SendMode[] = [
  {
    key: 'danmaku',
    label: '注入弹幕',
    desc: '假装一名观众发弹幕，走与真实弹幕完全相同的链路——主播自然反应，可能要等几秒、也可能不理你',
    placeholder: '弹幕内容（观众昵称在下方填写，可选）',
  },
  {
    key: 'force',
    label: '强制回应',
    desc: '不排队不限流：把文字直接交给主播立即开跑，结果落时间线决策卡；留空则主播自由发挥',
    placeholder: '给主播的文字（立即开跑；留空 = 主播自由发挥）',
  },
  {
    key: 'nudge',
    label: '幕后提醒',
    desc: '幕后场控式提醒"该开口了"：主播是否真开口由她的发言规则决定（防接龙 / 每小时上限 / 话题要求），可能被忽略',
    placeholder: '话题提示（可选，留空 = 纯提醒）',
  },
];

const sendMode = ref<SendMode['key']>('danmaku');
const sendModeDef = computed<SendMode>(
  () => SEND_MODES.find(mode => mode.key === sendMode.value) ?? SEND_MODES[0],
);
const sendText = ref('');
const sending = ref(false);
/** 输入框组件实例：发送完成后拉回焦点（Tab 切模式依赖焦点在输入框内） */
const sendInputRef = ref<{ focus: (options?: { preventScroll?: boolean }) => void } | null>(null);

// 发送历史（跨模式共享，去重保尾，上限 50 条）；↑ 进入历史前暂存草稿，
// ↓ 翻到尽头自动还原草稿
const sendHistory = ref<string[]>([]);
const sendHistoryIdx = ref<number | null>(null);
let sendDraft = '';

function cycleSendMode(step: number): void {
  const idx = SEND_MODES.findIndex(mode => mode.key === sendMode.value);
  const next = (idx + step + SEND_MODES.length) % SEND_MODES.length;
  sendMode.value = SEND_MODES[next].key;
}

function historyNav(step: number): void {
  const list = sendHistory.value;
  if (list.length === 0) return;
  if (sendHistoryIdx.value === null) {
    if (step > 0) return;
    sendDraft = sendText.value;
    sendHistoryIdx.value = list.length - 1;
  } else {
    const next = sendHistoryIdx.value + step;
    if (next < 0) return;
    if (next >= list.length) {
      sendHistoryIdx.value = null;
      sendText.value = sendDraft;
      return;
    }
    sendHistoryIdx.value = next;
  }
  sendText.value = list[sendHistoryIdx.value];
}

function onSendKeydown(event: KeyboardEvent): void {
  if (event.isComposing) return; // IME 组字中不劫持按键
  if (event.key === 'Tab') {
    event.preventDefault();
    cycleSendMode(event.shiftKey ? -1 : 1);
  } else if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    void sendCurrent();
  } else if (event.key === 'ArrowUp') {
    event.preventDefault();
    historyNav(-1);
  } else if (event.key === 'ArrowDown') {
    event.preventDefault();
    historyNav(1);
  }
}

/** 注入弹幕模式的观众昵称（可选，跨发送保留——方便扮演同一位观众连发） */
const injectNickname = ref('');
/** 在途的强制回应决策轮数：后台执行期间在状态 chip 上显示"主播正在想…" */
const forcePending = ref(0);

async function sendCurrent(): Promise<void> {
  if (sending.value) return;
  const text = sendText.value.trim();
  if (sendMode.value === 'danmaku' && !text) {
    ElMessage.warning('请填写弹幕内容');
    return;
  }
  sending.value = true;
  try {
    if (sendMode.value === 'danmaku') {
      const response = await debugApi.injectMessage({
        source: injectNickname.value.trim() || '测试观众',
        text,
      });
      if (response.data.success) {
        ElMessage.success('已注入——主播自然反应中，可能要等、也可能不理');
      } else {
        ElMessage.error(response.data.error || '注入失败');
        return;
      }
    } else if (sendMode.value === 'force') {
      // 后台执行：决策可能耗时数十秒，不等返回——输入框立即可继续用，
      // 在途状态由 forcePending chip 承载，结果落时间线决策卡
      const payload = {
        batch: text ? [{ nickname: '调试观众', text }] : undefined,
        forced: true,
        proactive: text ? undefined : true,
      };
      forcePending.value += 1;
      void streamerApi
        .testDecision(payload)
        .then(response => {
          if (response.data.success) {
            const error = response.data.error ?? null;
            if (error) {
              ElMessage.warning(`决策轮已结束：${error}（详见时间线决策卡）`);
            } else if (response.data.plan?.should_reply) {
              ElMessage.success('已回应（详见时间线决策卡）');
            } else {
              ElMessage.info('本轮未回应（详见时间线决策卡）');
            }
          } else {
            ElMessage.error(response.data.message || '测试执行失败');
          }
        })
        .catch((error: unknown) => {
          ElMessage.error(error instanceof Error ? error.message : '测试执行失败');
        })
        .finally(() => {
          forcePending.value = Math.max(0, forcePending.value - 1);
        });
    } else {
      const response = await streamerApi.triggerProactive({
        topic_hint: text || undefined,
      });
      if (response.data.success) {
        ElMessage.info(response.data.message || '已提醒，等主播在下个决策周期决定是否开口');
      } else {
        ElMessage.warning(response.data.message || '提醒没有送达');
        return;
      }
    }
    // 发送成功才入历史：失败的原样留在输入框里改了重发
    if (text && sendHistory.value[sendHistory.value.length - 1] !== text) {
      sendHistory.value.push(text);
      if (sendHistory.value.length > 50) sendHistory.value.shift();
    }
    sendHistoryIdx.value = null;
    sendText.value = '';
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : `${sendModeDef.value.label}失败`);
  } finally {
    sending.value = false;
    // sending 翻转会让输入框经历 disabled 往返而失焦——连发/接着 Tab 切模式
    // 都依赖焦点在此，发送完成后拉回来
    void nextTick(() => sendInputRef.value?.focus());
  }
}

// 滚动跟随：贴底自动跟随；上滚时冒出"回到最新"

const unseen = ref(0);
let resizeObserver: ResizeObserver | null = null;

const { scrollRef, atBottom, onScroll: followOnScroll, scrollToBottom } = useScrollFollow();

function onScroll(): void {
  followOnScroll();
  if (atBottom.value) unseen.value = 0;
}

function jumpToLatest(): void {
  atBottom.value = true;
  unseen.value = 0;
  scrollToBottom();
}

/** 新增条目数：以上一帧末条 id 为锚，找不到锚点则视为全新 */
function countAdded(next: ShowEntry[], prev: ShowEntry[]): number {
  const anchor = prev.length > 0 ? prev[prev.length - 1].id : null;
  if (!anchor) return next.length;
  const index = next.findIndex(entry => entry.id === anchor);
  return index === -1 ? next.length : next.length - 1 - index;
}

watch(entries, async (next, prev) => {
  const added = countAdded(next, prev ?? []);
  await nextTick();
  if (atBottom.value) {
    scrollToBottom();
    unseen.value = 0;
    return;
  }
  if (added > 0) unseen.value += added;
});

// 秒级时钟：驱动相对时间与台上时钟刷新

const nowTick = useNowTick();

/** 当前 Unix 秒（相对时间标签入参；FeedTimeline 自带 tick，这里仅供顶部环节横幅使用） */

const wallClock = computed(() =>
  new Date(nowTick.value).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }),
);

// 生命周期

onMounted(async () => {
  void loadSessions();
  void loadSimulatorStatus();
  await nextTick();
  scrollToBottom();
  // 容器尺寸变化（窗口缩放、注入面板开合）时维持贴底跟随。仅靠 entries
  // 变化触发不够——布局一变，最新条目就会滑出可视区且不再自动回位
  if (scrollRef.value) {
    resizeObserver = new ResizeObserver(() => {
      if (atBottom.value) scrollToBottom();
    });
    resizeObserver.observe(scrollRef.value);
  }
});

onUnmounted(() => {
  wsStore.unsubscribe(handleThinkingMessage);
  if (thinkingFlushTimer) clearTimeout(thinkingFlushTimer);
  if (rebuildTimer) clearTimeout(rebuildTimer);
  resizeObserver?.disconnect();
  resizeObserver = null;
});
</script>

<style scoped>
/* 版面：顶栏常驻；左场次栏 + 右时间线                             */
/* 高度直接铺满父容器 .app-main 的内容盒（它已扣掉顶栏与自身内边距）。
   早先用 100vh 自算高度并加 min-height 夹底，矮窗口下会反超父容器：外层
   .app-main 冒出第二条几乎无用的滚动条，而时间线仍被 .stage 的
   overflow:hidden 裁住，看起来就是"内容被截断且滚不动"。 */
.console-page {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
  height: 100%;
  min-height: 0;
}

/* 顶栏                                                          */
.console-head {
  display: flex;
  align-items: baseline;
  gap: var(--spacing-sm);
  flex-shrink: 0;
}

.console-title {
  margin: 0;
  font-size: 22px;
  font-weight: 650;
  letter-spacing: -0.2px;
  color: var(--text-primary);
}

.console-tagline {
  font-size: 12px;
  color: var(--text-secondary);
  letter-spacing: 0.2px;
}

.pulse {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  flex-shrink: 0;
  align-self: center;
  background: var(--text-placeholder);
}
.pulse.is-live {
  background: var(--color-danger);
  animation: onAir 2s ease-in-out infinite;
}
.pulse.is-dead {
  background: var(--text-placeholder);
}

@keyframes onAir {
  0%,
  100% {
    box-shadow: 0 0 0 0 var(--color-danger-bg);
    opacity: 1;
  }
  50% {
    box-shadow: 0 0 0 5px var(--color-danger-bg);
    opacity: 0.65;
  }
}

.show-feed {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 1.4px;
  text-transform: uppercase;
  color: var(--color-danger);
}
.show-feed.is-dead {
  color: var(--text-placeholder);
}

.show-clock {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-secondary);
}

/* --- 阶段徽章：决策管线正在做什么 --- */
.stage-chip {
  padding: 2px 10px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
  border: 1px solid var(--border-color-dark);
  color: var(--text-secondary);
  background: var(--bg-card);
  flex-shrink: 0;
  align-self: center;
}
.stage-chip.is-running {
  border-color: var(--color-agent);
  color: var(--color-agent);
  background: var(--color-agent-bg);
  animation: stagePulse 1.6s ease-in-out infinite;
}

@keyframes stagePulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.6;
  }
}

/* --- 模拟器徽章 --- */
.sim-chip {
  padding: 2px 10px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
  border: 1px solid var(--border-color-dark);
  color: var(--text-placeholder);
  background: var(--bg-card);
  text-decoration: none;
  flex-shrink: 0;
  align-self: center;
  transition:
    border-color var(--transition-fast),
    color var(--transition-fast);
}
.sim-chip.is-on {
  border-color: var(--color-collector);
  color: var(--color-collector);
}
.sim-chip:hover {
  border-color: var(--color-primary);
  color: var(--color-primary);
}

/* 双栏：场次侧边栏 + 主区                                        */
.console-body {
  flex: 1;
  min-height: 0;
  display: flex;
  gap: var(--spacing-sm);
}

/* 场次侧边栏                                                    */
.sessions {
  width: 250px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
  overflow: hidden;
}

.sessions-head {
  display: flex;
  align-items: center;
  gap: var(--spacing-xs);
  padding: 10px var(--spacing-sm);
  border-bottom: 1px solid var(--border-color-light);
}

.sessions-title {
  margin: 0;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 1.2px;
  color: var(--text-primary);
}

.sessions-filters {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 10px;
  border-bottom: 1px solid var(--border-color-light);
  flex-shrink: 0;
}

.sessions-source {
  width: 96px;
  flex-shrink: 0;
}

.session-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  list-style: none;
  margin: 0;
  padding: var(--spacing-xs);
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.session-list::-webkit-scrollbar {
  width: 6px;
}
.session-list::-webkit-scrollbar-thumb {
  background: var(--border-color-dark);
  border-radius: 3px;
}

.session-card {
  position: relative;
  padding: 8px 10px;
  border-radius: var(--radius-md);
  border: 1px solid var(--border-color-light);
  background: var(--bg-hover);
  cursor: pointer;
  transition:
    border-color var(--transition-fast),
    background var(--transition-fast);
}
.session-card:hover {
  border-color: var(--color-primary);
}
.session-card.is-selected {
  border-color: var(--color-primary);
  background: var(--bg-active);
}
.session-card.is-live {
  border-left: 3px solid var(--color-danger);
}

.session-top {
  display: flex;
  align-items: center;
  gap: 6px;
}

.session-name {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.session-badge {
  flex-shrink: 0;
  padding: 0 6px;
  border-radius: var(--radius-sm);
  font-size: 10px;
  font-weight: 700;
  background: var(--bg-card);
  color: var(--text-secondary);
  border: 1px solid var(--border-color-light);
}
.session-badge.is-replay {
  color: var(--color-rundown);
  border-color: var(--color-rundown);
}

.session-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 3px;
  font-size: 10px;
  color: var(--text-secondary);
}

.session-actions {
  position: absolute;
  right: 8px;
  bottom: 4px;
}
.session-del {
  color: var(--text-placeholder);
}
.session-del:hover {
  color: var(--color-danger);
}

.sessions-hint {
  margin: 0;
  padding: 8px 10px;
  font-size: 10px;
  line-height: 1.5;
  color: var(--text-placeholder);
  border-top: 1px solid var(--border-color-light);
}

/* 主区                                                          */
.console-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
  min-height: 0;
}

/* 环节横幅（常驻，不滚动）                                       */
.slate {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  flex-shrink: 0;
  min-height: 48px;
  padding: var(--spacing-sm) var(--spacing-md);
  border: 1px solid var(--border-color-light);
  border-left: 3px solid var(--color-rundown);
  border-radius: var(--radius-md);
  background: var(--color-rundown-bg);
}
.slate.is-idle {
  border-left-color: var(--border-color-dark);
  background: var(--bg-card);
}

.slate-eyebrow {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 1.6px;
  color: var(--color-rundown);
  flex-shrink: 0;
}
.slate.is-idle .slate-eyebrow {
  color: var(--text-placeholder);
}

.slate-order {
  font-size: 11px;
  color: var(--text-secondary);
  flex-shrink: 0;
}

.slate-label {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 40%;
}

.slate-action {
  padding: 1px 8px;
  border-radius: 999px;
  border: 1px solid var(--color-rundown);
  font-size: 11px;
  font-weight: 600;
  color: var(--color-rundown);
  flex-shrink: 0;
}

.slate-note {
  font-size: 12px;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 28%;
}

.slate-meta {
  font-size: 11px;
  color: var(--text-secondary);
  flex-shrink: 0;
}

.slate-idle {
  font-size: 13px;
  color: var(--text-placeholder);
}

/* 测试模式提示：单行小字，不占用时间线空间 */
.test-mode-notice {
  padding: 4px 12px;
  border-radius: var(--radius-sm);
  background: var(--bg-hover);
  font-size: 11px;
  color: var(--text-placeholder);
}

/* 时间线容器                                                    */
.stage {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
  overflow: hidden;
}

.stage-bar {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  flex-shrink: 0;
  flex-wrap: wrap;
  padding: 10px var(--spacing-md);
  border-bottom: 1px solid var(--border-color-light);
  transition: background var(--transition-normal);
}
.stage-bar.is-paused {
  background: var(--color-warning-bg);
}

.stage-title {
  margin: 0;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 1.2px;
  color: var(--text-primary);
}

.stage-hint {
  font-size: 11px;
  color: var(--text-secondary);
}

.stage-count {
  font-size: 11px;
  color: var(--text-secondary);
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  background: var(--bg-hover);
}

/* 来源过滤 chips：与既有按钮族尺寸对齐，行内排布 */
.agent-filter {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
}
.agent-filter :deep(.el-check-tag) {
  font-size: 11px;
}

/* 干预输入条：code agent 风格卡片——圆角容器内嵌无边框输入，
   底行模式 chip + 快捷键提示 + 圆形发送钮；聚焦时描边亮起 */
.input-bar {
  flex-shrink: 0;
  padding: 8px var(--spacing-md) 10px;
  background: var(--bg-card);
}
.input-shell {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 10px 6px;
  border-radius: 12px;
  border: 1px solid var(--border-color-light);
  background: var(--bg-hover);
  transition:
    border-color var(--transition-fast),
    box-shadow var(--transition-fast);
}
.input-shell:focus-within {
  border-color: var(--color-primary);
  box-shadow: 0 0 0 3px rgba(64, 158, 255, 0.12);
}
.input-shell.is-sending {
  opacity: 0.75;
}
/* 内嵌 textarea：去壳自带边框，与容器融为一体 */
.input-shell .input-main :deep(.el-textarea__inner) {
  border: none;
  box-shadow: none;
  background: transparent;
  padding: 2px 4px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-primary);
}
.input-shell .input-main :deep(.el-textarea__inner)::placeholder {
  color: var(--text-placeholder);
}
.input-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 26px;
}
/* 模式 chip：无边框透明 select，标签即按钮 */
.input-mode {
  width: auto;
  min-width: 96px;
  flex-shrink: 0;
}
.input-mode :deep(.el-select__wrapper) {
  box-shadow: none;
  background: transparent;
  min-height: 24px;
  padding: 2px 4px;
  gap: 2px;
}
.input-mode :deep(.el-select__wrapper:hover) {
  background: var(--bg-active);
  border-radius: var(--radius-sm);
}
.input-mode :deep(.el-select__placeholder) {
  font-size: 12px;
  font-weight: 600;
  color: var(--color-primary);
}
/* 注入弹幕模式的昵称小输入框：迷你描边框，与 chip 同一视觉层 */
.input-nick {
  width: 132px;
  flex-shrink: 0;
}
.input-nick :deep(.el-input__wrapper) {
  border-radius: 999px;
  padding: 1px 10px;
  box-shadow: 0 0 0 1px var(--border-color-light) inset;
  background: var(--bg-card);
}
.input-nick :deep(.el-input__inner) {
  font-size: 11px;
  height: 20px;
}
/* 强制回应在途状态 chip：转圈 + 文案 */
.input-status {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
  padding: 1px 8px;
  border-radius: 999px;
  background: var(--color-agent-bg);
  color: var(--color-agent);
  font-size: 10px;
  font-weight: 600;
  white-space: nowrap;
}
.input-status .el-icon {
  font-size: 11px;
}
.input-kbd {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 10px;
  color: var(--text-placeholder);
  cursor: default;
}
.input-send {
  flex-shrink: 0;
  width: 28px;
  height: 28px;
}

/* 模式下拉选项：标题 + 说明两行；popper 收窄防止说明行过长 */
.mode-option {
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding: 2px 0;
  line-height: 1.4;
}
.mode-option-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary);
}
.mode-option-desc {
  font-size: 10px;
  color: var(--text-secondary);
  white-space: normal;
}

/* 滚动体 + 顶部渐隐 + 回到最新                                   */
.stage-body {
  position: relative;
  flex: 1;
  min-height: 0;
}
.stage-body::before {
  content: '';
  position: absolute;
  inset: 0 0 auto 0;
  height: 18px;
  z-index: 2;
  pointer-events: none;
  background: linear-gradient(to bottom, var(--bg-card), transparent);
}

.stage-scroll {
  height: 100%;
  overflow-y: auto;
  padding: var(--spacing-md) var(--spacing-md) var(--spacing-lg);
}
.stage-scroll::-webkit-scrollbar {
  width: 6px;
}
.stage-scroll::-webkit-scrollbar-thumb {
  background: var(--border-color-dark);
  border-radius: 3px;
}

.jump {
  position: absolute;
  bottom: var(--spacing-md);
  left: 50%;
  transform: translateX(-50%);
  z-index: 3;
  padding: 5px 14px;
  border: 1px solid var(--color-primary);
  border-radius: 999px;
  background: var(--bg-elevated);
  color: var(--color-primary);
  font-family: inherit;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  box-shadow: var(--shadow-md);
  transition:
    background var(--transition-fast),
    transform var(--transition-fast);
}
.jump:hover {
  background: var(--bg-active);
  transform: translateX(-50%) translateY(-1px);
}

/* 窄屏                                                          */
@media (max-width: 1100px) {
  .sessions {
    width: 200px;
  }
  .console-tagline,
  .slate-note {
    display: none;
  }
}

@media (max-width: 860px) {
  .sessions {
    display: none;
  }
  .slate-label {
    max-width: 55%;
  }
}
</style>

<style>
/* 干预输入条模式下拉的 popper 挂在 body 下，scoped 样式够不着——
   全局收窄宽度并放开选项的两行排版（标题+说明） */
.input-mode-popper {
  max-width: 460px;
}
.input-mode-popper .el-select-dropdown__item {
  height: auto;
  padding-top: 6px;
  padding-bottom: 6px;
  white-space: normal;
}
</style>
