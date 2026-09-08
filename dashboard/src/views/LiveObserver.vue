<template>
  <div class="console-page">
    <!-- ============================================================ -->
    <!-- 顶栏：页面身份 + 阶段状态 + 模拟器徽章 + 实时脉冲 + 时钟         -->
    <!-- ============================================================ -->
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
      <!-- ============================================================ -->
      <!-- 左栏：场次侧边栏（当前 + 历史回看 + 生命周期开关）               -->
      <!-- ============================================================ -->
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
            <div
              v-if="!item.is_active && item.source !== 'legacy'"
              class="session-actions"
            >
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
        <p class="sessions-hint">启动不自动开场次；未开启期间消息仅在内存中流转（测试模式，不落库）</p>
      </aside>

      <!-- ============================================================ -->
      <!-- 右区：环节横幅 + 时间线                                        -->
      <!-- ============================================================ -->
      <section class="console-main">
        <section class="slate" :class="{ 'is-idle': !agenda }" aria-label="当前环节">
          <span class="slate-eyebrow">当前环节</span>
          <template v-if="agenda">
            <span class="slate-order mono">#{{ agenda.order }}</span>
            <h2 class="slate-label" :title="agenda.label">{{ agenda.label }}</h2>
            <span class="slate-action">{{ agenda.actionLabel }}</span>
            <span v-if="agenda.note" class="slate-note" :title="agenda.note">{{
              agenda.note
            }}</span>
            <span class="grow" />
            <span v-if="agenda.startLabel" class="slate-meta mono"
              >计划 {{ agenda.startLabel }}</span
            >
            <span v-if="agenda.expectedLabel" class="slate-meta mono">
              预计 {{ agenda.expectedLabel }}
            </span>
            <span class="slate-meta mono">{{ relativeTime(agenda.changedAtSec) }}</span>
          </template>
          <span v-else class="slate-idle">节目单未运行或未接入</span>
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
              <el-button
                size="small"
                :type="injectOpen ? 'primary' : 'default'"
                @click="injectOpen = !injectOpen"
              >
                注入弹幕
              </el-button>
              <el-button size="small" @click="testDialogVisible = true">决策测试</el-button>
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

          <!-- 注入面板：显式 v-if 渲染（不依赖弹层组件的触发器绑定） -->
          <div v-if="injectOpen && sessionMode === 'live'" class="inject-panel">
            <div class="inject-form">
              <el-input v-model="injectSource" size="small" placeholder="昵称（可选）" />
              <el-input
                v-model="injectText"
                size="small"
                type="textarea"
                :rows="2"
                placeholder="弹幕内容——走与真实弹幕完全相同的处理链路"
              />
              <div class="inject-actions">
                <span class="inject-hint">消息经真实弹幕链路进入决策，结果以决策卡落在时间线</span>
                <span class="grow" />
                <el-button size="small" @click="injectOpen = false">收起</el-button>
                <el-button size="small" type="primary" :loading="injecting" @click="submitInject">
                  注入
                </el-button>
              </div>
            </div>
          </div>

          <div class="stage-body">
            <div ref="scrollRef" class="stage-scroll" @scroll.passive="onScroll">
              <div v-if="entries.length === 0" class="stage-empty">
                <el-icon class="stage-empty-icon"><Monitor /></el-icon>
                <p class="stage-empty-text">
                  {{
                    sessionMode === 'live'
                      ? '静候消息与决策——注入一条弹幕试试'
                      : '该场次暂无可回看条目'
                  }}
                </p>
              </div>

              <ol v-else class="feed">
                <li v-for="entry in entries" :key="entry.id" class="feed-row">
                  <!-- 环节推进 / 场次边界：横贯分隔行 -->
                  <div v-if="entry.kind === 'agenda' || entry.kind === 'boundary'" class="beat">
                    <span class="beat-rule" aria-hidden="true" />
                    <span class="beat-body">
                      <span class="beat-eyebrow">{{
                        entry.kind === 'boundary' ? '场次' : '环节'
                      }}</span>
                      <span class="beat-label">{{ entry.text }}</span>
                      <span v-if="entry.badge" class="beat-action">{{ entry.badge }}</span>
                      <span v-if="entry.note" class="beat-note">{{ entry.note }}</span>
                    </span>
                    <span class="beat-rule" aria-hidden="true" />
                    <time class="stamp mono">{{ relativeTime(entry.tsSec) }}</time>
                  </div>

                  <!-- 里程碑：庆祝行 -->
                  <div v-else-if="entry.kind === 'milestone'" class="milestone">
                    <span class="milestone-mark" aria-hidden="true">★</span>
                    <div class="milestone-body">
                      <p class="milestone-text">{{ entry.text }}</p>
                      <p v-if="entry.note" class="milestone-meta mono">{{ entry.note }}</p>
                    </div>
                    <time class="stamp mono">{{ relativeTime(entry.tsSec) }}</time>
                  </div>

                  <!-- 阶段状态：安静单行（决策管线在做什么/卡在哪） -->
                  <div v-else-if="entry.kind === 'stage'" class="whisper">
                    <span class="whisper-dot" aria-hidden="true" />
                    <span class="whisper-text" :class="{ 'is-running': entry.speak }">
                      {{ entry.text }}<template v-if="entry.note"> · {{ entry.note }}</template>
                    </span>
                    <span class="grow" />
                    <time class="stamp mono">{{ relativeTime(entry.tsSec) }}</time>
                  </div>

                  <!-- 进场：安静单行 -->
                  <div v-else-if="entry.kind === 'enter'" class="whisper">
                    <span class="whisper-dot" aria-hidden="true" />
                    <span class="whisper-text">{{ entry.text }}</span>
                    <span class="grow" />
                    <time class="stamp mono">{{ relativeTime(entry.tsSec) }}</time>
                  </div>

                  <!-- 决策卡：verdict（实时裁决）+ decision（沉默/失败轮或统计回填后）共用 -->
                  <div
                    v-else-if="entry.kind === 'decision' || entry.kind === 'verdict'"
                    class="decision"
                    :class="{ 'is-failed': entry.failed, 'is-silent': isSilentDecision(entry) }"
                  >
                    <div class="act-head">
                      <span class="act-kind">决策</span>
                      <code v-if="entry.roundId" class="d-round mono">{{ entry.roundId }}</code>
                      <span v-if="confidenceLabel(entry)" class="d-conf mono">{{
                        confidenceLabel(entry)
                      }}</span>
                      <span v-if="entry.replyTo" class="chip" title="回复关联键（互动分析）">
                        回复 {{ entry.replyTo }}
                      </span>
                      <span
                        v-if="entry.badge"
                        class="act-badge"
                        :class="{ 'is-silent-badge': isSilentDecision(entry) }"
                      >
                        {{ entry.badge }}
                      </span>
                      <span class="grow" />
                      <time class="stamp mono">{{ relativeTime(entry.tsSec) }}</time>
                    </div>
                    <p class="act-text">{{ entry.text }}</p>
                    <p v-if="guidanceOf(entry)" class="d-guidance">{{ guidanceOf(entry) }}</p>
                    <p v-if="entry.note" class="act-note">{{ entry.note }}</p>
                    <div class="d-meta">
                      <span v-if="batchSizeOf(entry) > 0" class="mono"
                        >批次 {{ batchSizeOf(entry) }} 条</span
                      >
                      <span v-if="plannerMsOf(entry)" class="mono"
                        >决策 {{ plannerMsOf(entry) }}ms</span
                      >
                      <span v-if="replyMsOf(entry)" class="mono"
                        >生成 {{ replyMsOf(entry) }}ms</span
                      >
                      <a
                        v-if="entry.llmRequestId"
                        class="d-link"
                        :href="`/llm/history?request_id=${encodeURIComponent(entry.llmRequestId)}`"
                        @click.stop
                      >
                        完整请求 ↗
                      </a>
                      <details v-if="plannerThinkingOf(entry.roundId)" class="d-thinking">
                        <summary>思考过程</summary>
                        <p
                          v-for="seg in plannerThinkingOf(entry.roundId)"
                          :key="seg.step"
                          class="d-thinking-phase"
                        >
                          <span class="d-thinking-tag">Planner · 步骤 {{ seg.step }}</span>
                          <span class="mono">{{ seg.text }}</span>
                        </p>
                      </details>
                      <details v-if="rawOf(entry)" class="d-raw">
                        <summary>原始输出</summary>
                        <pre class="mono">{{ rawOf(entry) }}</pre>
                      </details>
                    </div>
                  </div>

                  <!-- 工具调用卡（tool.result.*）：来源徽标在右侧（主播决策 / 游戏 Agent） -->
                  <div
                    v-else-if="entry.kind === 'tool'"
                    class="act"
                    :class="{ 'is-failed': entry.failed, 'is-speak': entry.speak }"
                  >
                    <div class="act-head">
                      <span class="act-kind">工具调用</span>
                      <code class="act-tool mono">{{ entry.actor }}</code>
                      <span class="act-arrow" aria-hidden="true">→</span>
                      <span v-if="entry.badge" class="act-badge">{{ entry.badge }}</span>
                      <span class="grow" />
                      <time class="stamp mono">{{ relativeTime(entry.tsSec) }}</time>
                    </div>
                    <p class="act-text">{{ entry.text }}</p>
                    <p v-if="entry.note" class="act-note">{{ entry.note }}</p>
                  </div>

                  <!-- 主播发言（streamer.speech）：表达语义 + Replyer 思考回看 -->
                  <div v-else-if="entry.kind === 'speech'" class="act is-speech">
                    <div class="act-head">
                      <span class="act-kind act-kind--speech">主播</span>
                      <span v-if="entry.note" class="act-emotion">{{ entry.note }}</span>
                      <span v-if="entry.replyTo" class="chip" title="本条发言回复的那条弹幕">
                        回复 {{ entry.replyTo }}
                      </span>
                      <span class="grow" />
                      <time class="stamp mono">{{ relativeTime(entry.tsSec) }}</time>
                    </div>
                    <p class="act-text">🎤 {{ entry.text }}</p>
                    <details v-if="replyerThinkingOf(entry.roundId)" class="d-thinking">
                      <summary>生成思考</summary>
                      <p class="d-thinking-phase">
                        <span class="d-thinking-tag">Replyer</span>
                        <span class="mono">{{ replyerThinkingOf(entry.roundId) }}</span>
                      </p>
                    </details>
                  </div>

                  <!-- 观众发声：弹幕 / 礼物 / SC -->
                  <div v-else class="chat" :class="`chat--${entry.kind}`">
                    <span class="avatar" aria-hidden="true">{{ entry.initial }}</span>
                    <div class="bubble">
                      <div class="bubble-head">
                        <span class="who" :title="entry.actor">{{ entry.actor }}</span>
                        <span v-if="entry.badge" class="chip">{{ entry.badge }}</span>
                        <span v-if="entry.money" class="money mono">{{ entry.money }}</span>
                        <span class="grow" />
                        <time class="stamp mono">{{ relativeTime(entry.tsSec) }}</time>
                      </div>
                      <p class="say">{{ entry.text }}</p>
                    </div>
                  </div>
                </li>
              </ol>

              <!-- 活动思考区：当前步骤的 reasoning 实时滚动（与工具卡时间交织） -->
              <div v-if="activeThinking && sessionMode === 'live'" class="thinking-live">
                <div class="thinking-live-head">
                  <span class="whisper-dot is-running" aria-hidden="true" />
                  <span class="thinking-live-title">{{ activeThinking.label }}</span>
                  <span class="grow" />
                  <code class="mono">{{ activeThinking.roundId }}</code>
                </div>
                <p class="thinking-live-phase">
                  <span class="d-thinking-tag">{{ activeThinking.phase === 'replyer' ? 'Replyer' : 'Planner' }}</span>
                  <span class="mono">{{ activeThinking.segment }}</span>
                </p>
              </div>
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
        </section>
      </section>
    </div>

    <!-- ============================================================ -->
    <!-- 决策测试对话框（手动驱动一次两阶段决策）                         -->
    <!-- ============================================================ -->
    <el-dialog v-model="testDialogVisible" title="主播决策测试" width="480px">
      <div class="test-form">
        <el-input
          v-model="testText"
          type="textarea"
          :rows="3"
          placeholder="测试弹幕文本（作为一批弹幕进入真实决策链路）"
        />
        <div class="test-options">
          <el-checkbox v-model="testForced">强制回应（豁免低置信度降级）</el-checkbox>
          <el-checkbox v-model="testProactive">主动发言（无弹幕批次）</el-checkbox>
        </div>
      </div>
      <template #footer>
        <el-button @click="testDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="testing" @click="submitTestDecision">执行</el-button>
      </template>
    </el-dialog>
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
 *   （streamer.stage）、场次边界、节目单推进、里程碑，单列居左、靠样式区分
 * - 顶栏：连接状态、决策管线阶段徽章、模拟器模式徽章
 *
 * 干预入口（复用既有 API）：注入弹幕（debug/inject-message，与真实弹幕同链路）、
 * 决策测试（streamer/test-decision，结果以决策卡形式落进时间线）。
 *
 * 数据来源：
 * - 实时：events store（全局 WS + 游标回填，刷新/断线不丢时间线）
 * - 回看：GET /live-sessions/{id}/timeline（明细行 + 事件历史按时间合并）
 * 渲染字段一律取自后端真实 Payload（src/modules/events/payloads/），不臆造字段。
 */
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue';
import { storeToRefs } from 'pinia';
import { ElMessage, ElMessageBox } from 'element-plus';
import { Monitor } from '@element-plus/icons-vue';
import { useEventsStore, useWebSocketStore } from '@/stores';
import { debugApi, liveSessionsApi, simulatorApi, streamerApi } from '@/api';
import { summarizeEvent } from '@/utils/eventSummary';
import type { LiveSessionItem, ThinkingDelta, WebSocketMessage } from '@/types';

// ============================================================
// 常量
// ============================================================

const MAX_ENTRIES = 400;
/** 距底 ≤ 此距离视为"贴底"，可自动跟随 */
const BOTTOM_THRESHOLD_PX = 40;
/** 结果载荷里可作"主播说了什么"的字段候选（按优先级） */
const TOOL_TEXT_KEYS = ['speech_text', 'text', 'speech', 'content', 'message', 'summary'] as const;

const AGENDA_ACTION_LABEL: Record<string, string> = {
  done: '已完成',
  schedule: '已改期',
  insert: '新增环节',
};

const STAGE_LABEL: Record<string, string> = {
  planning: '决策中',
  replying: '生成中',
  idle: '空闲',
};

const SOURCE_LABEL: Record<string, string> = {
  manual: '手动',
  replay: '回放',
  legacy: '历史',
};

// ============================================================
// 类型
// ============================================================

type EntryKind =
  | 'danmaku'
  | 'gift'
  | 'super_chat'
  | 'enter'
  | 'speech'
  | 'tool'
  | 'agenda'
  | 'milestone'
  | 'verdict'
  | 'decision'
  | 'stage'
  | 'boundary';

/** 事件缓冲条目：events store 在 WebSocketMessage 上补了去重 id */
type FeedEvent = WebSocketMessage & { id: string };

/** 时间线条目（view-ready，模板不再碰原始 payload） */
interface ShowEntry {
  id: string;
  kind: EntryKind;
  /** Unix 秒（后端事件 timestamp 为秒，毫秒亦兼容） */
  tsSec: number;
  /** 观众昵称 / 工具名 / 环节名 */
  actor: string;
  /** 主体文案 */
  text: string;
  /** 次要文案（错误信息 / 环节备注 / 游戏场景 / 阶段补充） */
  note: string;
  /** 类型徽标（礼物 / SC / 失败 / 回应 / 沉默 / 环节动作） */
  badge: string;
  /** 金额强调（¥50） */
  money: string;
  failed: boolean;
  speak: boolean;
  /** 头像首字 */
  initial: string;
  /** 决策轮次 ID（planner.decision；发言/工具结果经它成组） */
  roundId: string;
  /** 回复关联键（所回复弹幕的 message_id） */
  replyTo: string;
  /** 决策卡附加字段（置信度/耗时/原始输出/请求历史指针等） */
  detail: Record<string, unknown> | null;
  /** LLM 请求历史指针（决策卡"完整请求"链接） */
  llmRequestId: string;
}

interface AgendaBanner {
  order: number;
  label: string;
  actionLabel: string;
  note: string;
  startLabel: string;
  expectedLabel: string;
  changedAtSec: number;
}

// ============================================================
// 思考流（WS kind="stream"；ADR-008 best-effort 观测通道）
// ============================================================

const THINKING_ROUNDS_MAX = 20;
/** 单步思考段 */
interface ThinkingStep {
  step: number;
  text: string;
}
/** 每决策轮的思考聚合：planner 按步骤分段（与工具卡时间交织），replyer 独立一段。
 * plannerDone/replyerDone 分别由 verdict（裁决落地）与 speech（发言落地）驱动 */
interface ThinkingRound {
  steps: ThinkingStep[];
  replyerText: string;
  /** 最后活动的段："planner:<step>" | "replyer" */
  lastSegment: string;
  plannerDone: boolean;
  replyerDone: boolean;
}
const thinkingRounds = reactive(new Map<string, ThinkingRound>());

function thinkingOf(roundId: string): ThinkingRound | undefined {
  return roundId ? thinkingRounds.get(roundId) : undefined;
}

/** 决策卡回看用：Planner 各步骤思考段（replyer 段归发言卡） */
function plannerThinkingOf(roundId: string): ThinkingStep[] {
  const round = thinkingOf(roundId);
  if (!round) return [];
  return round.steps.filter(s => s.text);
}

/** 发言卡回看用：Replyer 思考段文本（空串返回空，模板不渲染 details） */
function replyerThinkingOf(roundId: string): string {
  return thinkingOf(roundId)?.replyerText ?? '';
}

/** 活动中的思考段（时间线尾部实时滚动区），显示最后活动且未完结的段 */
const activeThinking = computed<{
  roundId: string;
  round: ThinkingRound;
  segment: string;
  label: string;
  phase: string;
} | null>(() => {
  for (const [roundId, round] of thinkingRounds) {
    if (!round.plannerDone && !round.replyerDone) continue;
    if (round.lastSegment === 'replyer') {
      if (round.replyerDone) continue;
      return { roundId, round, segment: round.replyerText, label: '生成发言中', phase: 'replyer' };
    }
    if (round.plannerDone) continue;
    const step = Number(round.lastSegment.split(':')[1] ?? 1);
    const seg = round.steps.find(s => s.step === step);
    return { roundId, round, segment: seg?.text ?? '', label: `思考中 · 步骤 ${step}`, phase: 'planner' };
  }
  return null;
});

function handleThinkingMessage(message: WebSocketMessage): void {
  if (message.kind !== 'stream' || message.type !== 'thinking.delta') return;
  const deltas = (message.data.deltas ?? []) as ThinkingDelta[];
  for (const delta of deltas) {
    let round = thinkingRounds.get(delta.round_id);
    if (!round) {
      round = reactive({ steps: [], replyerText: '', lastSegment: '', plannerDone: false, replyerDone: false });
      thinkingRounds.set(delta.round_id, round);
      // 上限保尾：只保留最近 N 轮供决策卡回看，更早的文本随轮淘汰
      while (thinkingRounds.size > THINKING_ROUNDS_MAX) {
        const oldest = thinkingRounds.keys().next().value;
        if (oldest === undefined) break;
        thinkingRounds.delete(oldest);
      }
    }
    if (delta.phase === 'replyer') {
      round.replyerText += delta.text_delta;
      round.lastSegment = 'replyer';
      round.replyerDone = false;
    } else {
      let seg = round.steps.find(s => s.step === delta.step);
      if (!seg) {
        seg = reactive({ step: delta.step, text: '' });
        round.steps.push(seg);
      }
      seg.text += delta.text_delta;
      round.lastSegment = `planner:${delta.step}`;
      round.plannerDone = false;
    }
  }
}

// ============================================================
// Store 与全局状态
// ============================================================

const eventsStore = useEventsStore();
const wsStore = useWebSocketStore();
const { events } = storeToRefs(eventsStore);
const { isConnected: wsConnected } = storeToRefs(wsStore);

wsStore.subscribe(handleThinkingMessage);

// 重连清理：思考流无回填，断线期间的增量已不可得，重连后清空悬空活动段
watch(wsConnected, (connected, previous) => {
  if (connected && previous === false) {
    for (const round of thinkingRounds.values()) {
      round.plannerDone = true;
      round.replyerDone = true;
    }
  }
});

// ============================================================
// 通用取值助手
// ============================================================

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function str(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function num(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function bool(value: unknown): boolean {
  return value === true;
}

/** 时间戳归一到 Unix 秒（后端为秒；毫秒值兜底换算） */
function toSeconds(value: number): number {
  return value > 1e12 ? value / 1000 : value;
}

function formatAmount(amount: number): string {
  return Number.isInteger(amount) ? String(amount) : amount.toFixed(2);
}

function clockLabel(ms: number): string {
  return new Date(ms).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

function durationLabel(ms: number): string {
  const minutes = Math.round(ms / 60000);
  if (minutes < 1) return '<1m';
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest > 0 ? `${hours}h${rest}m` : `${hours}h`;
}

function pickToolText(result: unknown): string {
  if (!isRecord(result)) return '';
  for (const key of TOOL_TEXT_KEYS) {
    const text = str(result[key]);
    if (text) return text;
  }
  return '';
}

/** RoomMessageUser → 展示名（与 summarizeEvent 的取名口径一致） */
function userLabel(value: unknown): string {
  if (!isRecord(value)) return '匿名观众';
  const name = str(value.name);
  if (name) return name;
  const id = str(value.id);
  return id ? `#${id}` : '匿名观众';
}

function initialOf(actor: string): string {
  const chars = Array.from(actor.replace(/^#/, ''));
  return chars.length > 0 ? chars[0].toUpperCase() : '?';
}

// ============================================================
// 事件 → 时间线条目
// ============================================================

function makeEntry(base: {
  id: string;
  kind: EntryKind;
  tsSec: number;
  actor?: string;
  text: string;
  note?: string;
  badge?: string;
  money?: string;
  failed?: boolean;
  speak?: boolean;
  roundId?: string;
  replyTo?: string;
  detail?: Record<string, unknown> | null;
  llmRequestId?: string;
}): ShowEntry {
  const actor = base.actor ?? '';
  return {
    id: base.id,
    kind: base.kind,
    tsSec: base.tsSec,
    actor,
    text: base.text,
    note: base.note ?? '',
    badge: base.badge ?? '',
    money: base.money ?? '',
    failed: base.failed ?? false,
    speak: base.speak ?? false,
    initial: initialOf(actor),
    roundId: base.roundId ?? '',
    replyTo: base.replyTo ?? '',
    detail: base.detail ?? null,
    llmRequestId: base.llmRequestId ?? '',
  };
}

/** 观众行为流：room.message（RoomMessagePayload 扁平载荷，message_type 判别） */
function fromRoomMessage(event: FeedEvent, data: Record<string, unknown>): ShowEntry {
  const tsSec = toSeconds(event.timestamp);
  const actor = userLabel(data.user);
  const content = str(data.content);
  const fallback = () => content || summarizeEvent(event.type, data);
  const messageType = str(data.message_type) || 'danmaku';

  if (messageType === 'gift') {
    const gift = isRecord(data.gift) ? data.gift : null;
    const giftName = gift ? str(gift.name) : '';
    const count = (gift ? num(gift.count) : null) ?? 1;
    return makeEntry({
      id: event.id,
      kind: 'gift',
      tsSec,
      actor,
      text: giftName ? `送出 ${giftName} ×${count}` : fallback(),
      badge: '礼物',
    });
  }

  if (messageType === 'super_chat') {
    const sc = isRecord(data.sc) ? data.sc : null;
    const amount = sc ? num(sc.amount) : null;
    return makeEntry({
      id: event.id,
      kind: 'super_chat',
      tsSec,
      actor,
      text: fallback(),
      badge: 'SC',
      money: amount != null ? `¥${formatAmount(amount)}` : '',
    });
  }

  if (messageType === 'enter') {
    return makeEntry({
      id: event.id,
      kind: 'enter',
      tsSec,
      actor,
      text: `${actor} 进入直播间`,
    });
  }

  return makeEntry({
    id: event.id,
    kind: 'danmaku',
    tsSec,
    actor,
    text: fallback(),
  });
}

/** 主播动作：tool.result.*（ToolResultPayload）；按 caller_source 区分归属 Agent */
function fromToolResult(event: FeedEvent, data: Record<string, unknown>): ShowEntry {
  const toolName = str(data.tool_name) || event.type.slice('tool.result.'.length) || 'tool';
  const status = str(data.status);
  const failed = status === 'error';
  const spoken = pickToolText(data.result);
  const statusText = status ? (failed ? '执行失败' : '执行完成') : '';
  const source = str(data.caller_source);
  const caller =
    source === 'planner-react' ? '主播决策' : source === 'minecraft-react' ? '游戏 Agent' : source;
  return makeEntry({
    id: event.id,
    kind: 'tool',
    tsSec: toSeconds(event.timestamp),
    actor: toolName,
    text: spoken || statusText || summarizeEvent(event.type, data),
    note: failed ? str(data.error_message) : '',
    badge: failed ? '失败' : caller,
    failed,
    speak: toolName === 'speak',
    roundId: str(data.round_id),
  });
}

/** 主播发言：streamer.speech（StreamerSpeechPayload） */
function fromSpeech(event: FeedEvent, data: Record<string, unknown>): ShowEntry {
  const emotion = str(data.emotion);
  return makeEntry({
    id: event.id,
    kind: 'speech',
    tsSec: toSeconds(event.timestamp),
    actor: '主播',
    text: str(data.text) || summarizeEvent(event.type, data),
    note: emotion,
    speak: true,
    replyTo: str(data.reply_to_message_id),
    roundId: str(data.round_id),
  });
}

/** 决策记录：planner.decision（PlannerDecisionPayload）。
 * 决策卡只承载裁决语义（决定回应什么话题/为何沉默）；发言文本由 streamer.speech 发言卡承载 */
function fromDecision(id: string, tsSec: number, data: Record<string, unknown>): ShowEntry {
  const error = str(data.error);
  const shouldReply = bool(data.should_reply);
  const silentReason = str(data.silent_reason);
  const badge = error ? '失败' : shouldReply ? '回应' : silentReason ? '静默·压制' : '沉默';
  const text = error
    ? error
    : shouldReply
      ? str(data.topic_summary) || '决定发言'
      : silentReason === 'low_confidence'
        ? 'LLM 想回应，但置信度过低——被裁决压制为静默'
        : str(data.topic_summary)
          ? `决定不回应（${str(data.topic_summary)}）`
          : '决定不回应';
  const note = shouldReply && !error ? '' : error ? '' : str(data.error_message);
  return makeEntry({
    id,
    kind: 'decision',
    tsSec,
    actor: '决策',
    text,
    note,
    badge,
    failed: Boolean(error),
    roundId: str(data.round_id),
    replyTo: str(data.reply_to_message_id),
    detail: data,
    llmRequestId: str(data.llm_request_id),
  });
}

/** 裁决卡：planner.verdict（reply 调用时刻的即时裁决；轮末 decision 按轮回填统计） */
function fromVerdict(id: string, tsSec: number, data: Record<string, unknown>): ShowEntry {
  return makeEntry({
    id,
    kind: 'verdict',
    tsSec,
    actor: '决策',
    text: str(data.topic_summary) || '决定发言',
    badge: '回应',
    roundId: str(data.round_id),
    replyTo: str(data.reply_to_message_id),
    detail: data,
  });
}

/** 阶段状态：streamer.stage（StreamerStagePayload） */
function fromStage(id: string, tsSec: number, data: Record<string, unknown>): ShowEntry {
  const stage = str(data.stage);
  const running = str(data.agent_state) === 'running';
  const label = STAGE_LABEL[stage] ?? (stage || '阶段变化');
  return makeEntry({
    id,
    kind: 'stage',
    tsSec,
    text: `阶段：${label}`,
    note: str(data.detail),
    speak: running,
  });
}

/** 场次边界：live.started / live.ended */
function fromLiveBoundary(event: FeedEvent, data: Record<string, unknown>): ShowEntry {
  const started = event.type === 'live.started';
  const title = str(data.title);
  const reason = str(data.reason);
  return makeEntry({
    id: event.id,
    kind: 'boundary',
    tsSec: toSeconds(event.timestamp),
    text: started ? '场次开启' : '场次结束',
    badge: started ? str(data.source) : bool(data.empty_discarded) ? '空场次已丢弃' : '',
    note: title || reason,
  });
}

/** 环节推进：agenda.update（AgendaPayload） */
function fromAgenda(event: FeedEvent, data: Record<string, unknown>): ShowEntry {
  const item = isRecord(data.item) ? data.item : {};
  const action = str(data.action);
  return makeEntry({
    id: event.id,
    kind: 'agenda',
    tsSec: toSeconds(event.timestamp),
    text: str(item.label) || summarizeEvent(event.type, data) || '未命名环节',
    note: str(item.note),
    badge: AGENDA_ACTION_LABEL[action] ?? action,
  });
}

/** 里程碑：game.milestone（GamePayload） */
function fromMilestone(event: FeedEvent, data: Record<string, unknown>): ShowEntry {
  const meta = [str(data.game), str(data.scene)].filter(Boolean).join(' · ');
  return makeEntry({
    id: event.id,
    kind: 'milestone',
    tsSec: toSeconds(event.timestamp),
    text: str(data.message) || summarizeEvent(event.type, data),
    note: meta,
  });
}

/** 非控制台事件（system.* 等）返回 null，不进时间线 */
function toEntry(event: FeedEvent): ShowEntry | null {
  const data = isRecord(event.data) ? event.data : {};
  // WS 广播把 4 种 room.message.* 统一为 "room.message"，种类由 payload.message_type 判别
  if (event.type === 'room.message') return fromRoomMessage(event, data);
  if (event.type === 'streamer.speech') return fromSpeech(event, data);
  if (event.type === 'planner.verdict') return fromVerdict(event.id, toSeconds(event.timestamp), data);
  if (event.type === 'planner.decision')
    return fromDecision(event.id, toSeconds(event.timestamp), data);
  if (event.type === 'streamer.stage') return fromStage(event.id, toSeconds(event.timestamp), data);
  if (event.type === 'live.started' || event.type === 'live.ended')
    return fromLiveBoundary(event, data);
  if (event.type.startsWith('tool.result.')) return fromToolResult(event, data);
  if (event.type === 'agenda.update') return fromAgenda(event, data);
  if (event.type === 'game.milestone') return fromMilestone(event, data);
  return null;
}

// ============================================================
// 决策卡取值助手（detail 字段安全读取）
// ============================================================

function decisionDetail(entry: ShowEntry): Record<string, unknown> {
  return isRecord(entry.detail) ? entry.detail : {};
}

function isSilentDecision(entry: ShowEntry): boolean {
  const detail = decisionDetail(entry);
  return !bool(detail.should_reply) && !entry.failed;
}

function confidenceLabel(entry: ShowEntry): string {
  const value = num(decisionDetail(entry).confidence);
  return value != null ? `置信 ${value.toFixed(2)}` : '';
}

function guidanceOf(entry: ShowEntry): string {
  return str(decisionDetail(entry).reply_guidance);
}

function batchSizeOf(entry: ShowEntry): number {
  const batch = decisionDetail(entry).batch;
  return Array.isArray(batch) ? batch.length : 0;
}

function plannerMsOf(entry: ShowEntry): number | null {
  return num(decisionDetail(entry).planner_duration_ms);
}

function replyMsOf(entry: ShowEntry): number | null {
  return num(decisionDetail(entry).reply_duration_ms);
}

function rawOf(entry: ShowEntry): string {
  return str(decisionDetail(entry).planner_raw);
}

// ============================================================
// 场次侧边栏：列表 / 生命周期 / 回看
// ============================================================

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
  try {
    const { value } = await ElMessageBox.prompt('为新的直播场次起个标题（可留空）', '开启场次', {
      confirmButtonText: '开启',
      cancelButtonText: '取消',
      inputPlaceholder: '例如：周五晚间场',
    });
    await liveSessionsApi.open({ title: value?.trim() || undefined });
    ElMessage.success('场次已开启');
  } catch {
    return; // 取消输入
  }
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

// ============================================================
// 回看时间线：REST 明细 + 事件历史 → ShowEntry
// ============================================================

const replayEntries = ref<ShowEntry[]>([]);
const replayLoading = ref(false);

function decisionEntryFromData(id: string, tsMs: number, data: Record<string, unknown>): ShowEntry {
  return fromDecision(id, tsMs / 1000, data);
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
              tsSec: entry.ts_ms / 1000,
              text: type === 'live.started' ? '场次开启' : '场次结束',
              note: str(data.title) || str(data.reason),
            }),
          );
        } else if (type === 'agenda.update') {
          const agendaItem = isRecord(data.item) ? data.item : {};
          next.push(
            makeEntry({
              id,
              kind: 'agenda',
              tsSec: entry.ts_ms / 1000,
              text: str(agendaItem.label) || '未命名环节',
              note: str(agendaItem.note),
              badge: AGENDA_ACTION_LABEL[str(data.action)] ?? str(data.action),
            }),
          );
        } else if (type === 'game.milestone') {
          next.push(
            makeEntry({
              id,
              kind: 'milestone',
              tsSec: entry.ts_ms / 1000,
              text: str(data.message),
              note: [str(data.game), str(data.scene)].filter(Boolean).join(' · '),
            }),
          );
        }
        return;
      }
      if (entry.kind === 'speech') {
        next.push(
          makeEntry({
            id,
            kind: 'speech',
            tsSec: entry.ts_ms / 1000,
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
            tsSec: entry.ts_ms / 1000,
            actor: str(entry.user_name) || '匿名观众',
            text: `送出 ${str(entry.gift_name)} ×${num(entry.gift_count) ?? 1}`,
            badge: '礼物',
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
            tsSec: entry.ts_ms / 1000,
            actor: str(entry.user_name) || '匿名观众',
            text: str(entry.content),
            badge: 'SC',
            money: amount != null ? `¥${formatAmount(amount)}` : '',
          }),
        );
        return;
      }
      // danmaku / enter
      next.push(
        makeEntry({
          id,
          kind: entry.kind === 'enter' ? 'enter' : 'danmaku',
          tsSec: entry.ts_ms / 1000,
          actor: str(entry.user_name) || '匿名观众',
          text:
            entry.kind === 'enter'
              ? `${str(entry.user_name) || '观众'} 进入直播间`
              : str(entry.content),
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

// ============================================================
// 实时时间线：暂停 / 清空水位 / 条目缓冲
// ============================================================

const paused = ref(false);
/** 清空水位：记下当时缓冲区里的事件 id，之后重建时永久跳过（store 仍不丢数据） */
const hiddenIds = ref<Set<string>>(new Set());
const liveEntries = ref<ShowEntry[]>([]);

watch(
  [events, paused, hiddenIds],
  ([list, isPaused, hidden]) => {
    if (isPaused) return;
    const next: ShowEntry[] = [];
    // 轮次 → 已渲染的裁决卡；decision 到达时把统计回填进裁决卡而非新增重复卡
    const verdictByRound = new Map<string, ShowEntry>();
    for (const event of list) {
      if (hidden.has(event.id)) continue;
      const entry = toEntry(event as FeedEvent);
      if (!entry) continue;
      if (entry.kind === 'verdict' && entry.roundId) {
        verdictByRound.set(entry.roundId, entry);
      } else if (entry.kind === 'decision' && entry.roundId) {
        const verdict = verdictByRound.get(entry.roundId);
        if (verdict) {
          // decision 的统计/失败信息回填裁决卡；decision 自身不再成卡
          verdict.detail = decisionDetail(entry) || verdict.detail;
          verdict.llmRequestId = entry.llmRequestId || verdict.llmRequestId;
          verdict.failed = entry.failed;
          if (entry.failed) {
            verdict.text = entry.text;
            verdict.badge = '失败';
            verdict.note = entry.note;
          }
          continue;
        }
      }
      next.push(entry);
    }
    liveEntries.value = next.slice(-MAX_ENTRIES);
  },
  { immediate: true },
);

/** 展示条目：实时模式取 WS 流，回看模式取 REST 时间线 */
const entries = computed<ShowEntry[]>(() =>
  sessionMode.value === 'live' ? liveEntries.value : replayEntries.value,
);

function togglePause(): void {
  paused.value = !paused.value;
}

function clearTimeline(): void {
  hiddenIds.value = new Set(events.value.map(event => event.id));
  liveEntries.value = [];
  unseen.value = 0;
}

// ============================================================
// 场次生命周期事件 → 侧边栏刷新
// ============================================================

watch(events, list => {
  for (let i = list.length - 1; i >= Math.max(0, list.length - 5); i -= 1) {
    const type = list[i].type;
    if (type === 'live.started' || type === 'live.ended') {
      void loadSessions();
      break;
    }
  }
});

// ============================================================
// 当前环节横幅：取最近一条 agenda.update
// ============================================================

const agenda = computed<AgendaBanner | null>(() => {
  const list = events.value;
  for (let i = list.length - 1; i >= 0; i -= 1) {
    const event = list[i];
    if (event.type !== 'agenda.update') continue;
    const data = isRecord(event.data) ? event.data : {};
    const item = isRecord(data.item) ? data.item : {};
    const action = str(data.action);
    const startsAtMs = num(item.starts_at_ms);
    const expectedMs = num(item.expected_ms);
    const changedAtMs = num(data.changed_at_ms);
    return {
      order: num(item.order) ?? 0,
      label: str(item.label) || '未命名环节',
      actionLabel: AGENDA_ACTION_LABEL[action] ?? (action || '进行中'),
      note: str(item.note),
      startLabel: startsAtMs != null ? clockLabel(startsAtMs) : '',
      expectedLabel: expectedMs != null && expectedMs > 0 ? durationLabel(expectedMs) : '',
      changedAtSec: changedAtMs != null ? changedAtMs / 1000 : toSeconds(event.timestamp),
    };
  }
  return null;
});

// ============================================================
// 顶栏徽章：决策管线阶段 + 模拟器模式
// ============================================================

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

// ============================================================
// 干预：注入弹幕 + 决策测试
// ============================================================

const injecting = ref(false);
const injectOpen = ref(false);
const injectSource = ref('');
const injectText = ref('');

async function submitInject(): Promise<void> {
  const text = injectText.value.trim();
  if (!text) {
    ElMessage.warning('请填写弹幕内容');
    return;
  }
  injecting.value = true;
  try {
    const response = await debugApi.injectMessage({
      source: injectSource.value.trim() || '测试观众',
      text,
      data_type: 'text',
    });
    if (response.data.success) {
      ElMessage.success('已注入——观察下方决策与发言');
      injectText.value = '';
    } else {
      ElMessage.error(response.data.error || '注入失败');
    }
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '注入失败');
  } finally {
    injecting.value = false;
  }
}

const testDialogVisible = ref(false);
const testing = ref(false);
const testText = ref('');
const testForced = ref(false);
const testProactive = ref(false);

async function submitTestDecision(): Promise<void> {
  const text = testText.value.trim();
  if (!testProactive.value && !text) {
    ElMessage.warning('请填写测试弹幕文本');
    return;
  }
  testing.value = true;
  try {
    const response = await streamerApi.testDecision({
      batch: testProactive.value ? undefined : [{ nickname: '调试观众', text }],
      forced: testForced.value || undefined,
      proactive: testProactive.value || undefined,
    });
    if (response.data.success) {
      testDialogVisible.value = false;
      testText.value = '';
      const error = response.data.error ?? null;
      if (error) {
        ElMessage.warning(`决策轮已结束：${error}（详见时间线决策卡）`);
      } else if (response.data.plan?.should_reply) {
        ElMessage.success('决策完成：本轮已回应（详见时间线决策卡）');
      } else {
        ElMessage.info('决策完成：本轮未回应（详见时间线决策卡）');
      }
    } else {
      ElMessage.error(response.data.message || '测试执行失败');
    }
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '测试执行失败');
  } finally {
    testing.value = false;
  }
}

// ============================================================
// 滚动跟随：贴底自动跟随；上滚时冒出"回到最新"
// ============================================================

const scrollRef = ref<HTMLElement | null>(null);
const atBottom = ref(true);
const unseen = ref(0);

function isAtBottom(el: HTMLElement): boolean {
  return el.scrollHeight - el.scrollTop - el.clientHeight <= BOTTOM_THRESHOLD_PX;
}

function onScroll(): void {
  const el = scrollRef.value;
  if (!el) return;
  atBottom.value = isAtBottom(el);
  if (atBottom.value) unseen.value = 0;
}

function scrollToBottom(): void {
  const el = scrollRef.value;
  if (!el) return;
  el.scrollTop = el.scrollHeight;
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
  if (sessionMode.value === 'live') {
    // 思考段终态联动：verdict/decision 落地 → planner 段结束；speech 落地 → replyer 段结束
    const prevKinds = new Set((prev ?? []).map(entry => `${entry.kind}:${entry.roundId}`));
    for (const entry of next) {
      if (!entry.roundId) continue;
      const round = thinkingRounds.get(entry.roundId);
      if (!round) continue;
      const key = `${entry.kind}:${entry.roundId}`;
      if (prevKinds.has(key)) continue;
      if (entry.kind === 'verdict' || entry.kind === 'decision') round.plannerDone = true;
      if (entry.kind === 'speech') round.replyerDone = true;
    }
  }
  const added = countAdded(next, prev ?? []);
  await nextTick();
  if (atBottom.value) {
    scrollToBottom();
    unseen.value = 0;
    return;
  }
  if (added > 0) unseen.value += added;
});

// ============================================================
// 秒级时钟：驱动相对时间与台上时钟刷新
// ============================================================

const nowTick = ref(Date.now());
let tickTimer: ReturnType<typeof setInterval> | null = null;

const wallClock = computed(() =>
  new Date(nowTick.value).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }),
);

function relativeTime(tsSec: number): string {
  const diff = Math.max(0, Math.floor(nowTick.value / 1000 - tsSec));
  if (diff < 5) return '刚刚';
  if (diff < 60) return `${diff}s 前`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m 前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h 前`;
  return `${Math.floor(diff / 86400)}d 前`;
}

// ============================================================
// 生命周期
// ============================================================

onMounted(async () => {
  tickTimer = setInterval(() => {
    nowTick.value = Date.now();
  }, 1000);
  void loadSessions();
  void loadSimulatorStatus();
  await nextTick();
  scrollToBottom();
});

onUnmounted(() => {
  wsStore.unsubscribe(handleThinkingMessage);
  if (tickTimer) {
    clearInterval(tickTimer);
    tickTimer = null;
  }
});
</script>

<style scoped>
/* ============================================================ */
/* 版面：顶栏常驻；左场次栏 + 右时间线                             */
/* ============================================================ */
.console-page {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
  height: calc(100vh - var(--header-height) - 2 * var(--spacing-lg));
  min-height: 560px;
}

.grow {
  flex: 1;
  min-width: 0;
}

.mono {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}

/* ============================================================ */
/* 顶栏                                                          */
/* ============================================================ */
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

/* ============================================================ */
/* 双栏：场次侧边栏 + 主区                                        */
/* ============================================================ */
.console-body {
  flex: 1;
  min-height: 0;
  display: flex;
  gap: var(--spacing-sm);
}

/* ============================================================ */
/* 场次侧边栏                                                    */
/* ============================================================ */
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
  color: var(--color-agenda);
  border-color: var(--color-agenda);
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

/* ============================================================ */
/* 主区                                                          */
/* ============================================================ */
.console-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
  min-height: 0;
}

/* ============================================================ */
/* 环节横幅（常驻，不滚动）                                       */
/* ============================================================ */
.slate {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  flex-shrink: 0;
  min-height: 48px;
  padding: var(--spacing-sm) var(--spacing-md);
  border: 1px solid var(--border-color-light);
  border-left: 3px solid var(--color-agenda);
  border-radius: var(--radius-md);
  background: var(--color-agenda-bg);
}
.slate.is-idle {
  border-left-color: var(--border-color-dark);
  background: var(--bg-card);
}

.slate-eyebrow {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 1.6px;
  color: var(--color-agenda);
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
  border: 1px solid var(--color-agenda);
  font-size: 11px;
  font-weight: 600;
  color: var(--color-agenda);
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

/* ============================================================ */
/* 时间线容器                                                    */
/* ============================================================ */
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

.inject-panel {
  flex-shrink: 0;
  padding: 10px var(--spacing-md);
  border-bottom: 1px solid var(--border-color-light);
  background: var(--bg-hover);
}

.inject-form {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.inject-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.inject-hint {
  font-size: 10px;
  color: var(--text-placeholder);
}

.test-form {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.test-options {
  display: flex;
  gap: 16px;
}

/* ============================================================ */
/* 滚动体 + 顶部渐隐 + 回到最新                                   */
/* ============================================================ */
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

/* ============================================================ */
/* 空态                                                          */
/* ============================================================ */
.stage-empty {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--spacing-sm);
}
.stage-empty-icon {
  font-size: 44px;
  color: var(--border-color-dark);
}
.stage-empty-text {
  margin: 0;
  font-size: 13px;
  letter-spacing: 0.3px;
  color: var(--text-placeholder);
}

/* ============================================================ */
/* 流：单列居左时间轴脊线——所有条目沿轴排布，靠样式区分             */
/* ============================================================ */
.feed {
  position: relative;
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.feed::before {
  content: '';
  position: absolute;
  top: 4px;
  bottom: 4px;
  left: 14px;
  width: 1px;
  background: var(--border-color);
}

.feed-row {
  display: flex;
  flex-direction: column;
  animation: rowIn 0.22s cubic-bezier(0.33, 1, 0.68, 1);
}

@keyframes rowIn {
  from {
    opacity: 0;
    transform: translateY(5px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.stamp {
  font-size: 10px;
  color: var(--text-placeholder);
  flex-shrink: 0;
  white-space: nowrap;
}

/* ============================================================ */
/* 观众发声：气泡                                                */
/* ============================================================ */
.chat {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  max-width: 82%;
}

.avatar {
  width: 28px;
  height: 28px;
  flex-shrink: 0;
  border-radius: 50%;
  display: grid;
  place-items: center;
  font-size: 11px;
  font-weight: 700;
  background: var(--bg-card);
  border: 1px solid var(--color-collector);
  color: var(--color-collector);
  box-shadow: 0 0 0 3px var(--bg-card);
  z-index: 1;
}

.bubble {
  flex: 1;
  min-width: 0;
  padding: 7px 12px;
  border-radius: 4px 12px 12px 12px;
  background: var(--bg-hover);
}

.bubble-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 2px;
}

.who {
  font-size: 11px;
  font-weight: 600;
  color: var(--color-collector);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 220px;
}

.chip {
  padding: 0 6px;
  border-radius: var(--radius-sm);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.4px;
  background: var(--bg-card);
  color: var(--text-secondary);
  border: 1px solid var(--border-color-light);
  flex-shrink: 0;
}

.money {
  font-size: 12px;
  font-weight: 700;
  flex-shrink: 0;
}

.say {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-regular);
  white-space: pre-wrap;
  word-break: break-word;
}

/* --- 礼物：暖色高亮 --- */
.chat--gift {
  max-width: 88%;
}
.chat--gift .avatar {
  border-color: var(--color-warning);
  color: var(--color-warning);
}
.chat--gift .bubble {
  background: var(--color-warning-bg);
  border-left: 2px solid var(--color-warning);
  box-shadow: var(--shadow-sm);
}
.chat--gift .who {
  color: var(--color-warning);
}
.chat--gift .say {
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
}

/* --- SC：最强高亮（暖色渐变 + 金额） --- */
.chat--super_chat {
  max-width: 92%;
}
.chat--super_chat .avatar {
  border-color: var(--color-danger);
  color: var(--color-danger);
}
.chat--super_chat .bubble {
  padding: 10px 14px;
  border-left: 3px solid var(--color-danger);
  background: linear-gradient(100deg, var(--color-danger-bg), var(--color-warning-bg));
  box-shadow: var(--shadow-md);
}
.chat--super_chat .who {
  font-size: 12px;
  color: var(--color-danger);
}
.chat--super_chat .chip {
  background: var(--color-danger);
  color: var(--text-inverse);
  border-color: var(--color-danger);
}
.chat--super_chat .money {
  font-size: 14px;
  color: var(--color-danger);
}
.chat--super_chat .say {
  font-size: 15px;
  font-weight: 500;
  line-height: 1.55;
  color: var(--text-primary);
}

/* ============================================================ */
/* 安静单行：进场 / 阶段状态                                      */
/* ============================================================ */
.whisper {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 18px;
}
/* 占位宽度与头像一致（28px），使圆点正落在时间轴脊线上 */
.whisper-dot {
  width: 28px;
  height: 12px;
  flex-shrink: 0;
  display: grid;
  place-items: center;
  z-index: 1;
}
.whisper-dot::before {
  content: '';
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--text-placeholder);
  box-shadow: 0 0 0 3px var(--bg-card);
}
.whisper-text {
  font-size: 11px;
  color: var(--text-placeholder);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.whisper-text.is-running {
  color: var(--color-agent);
}

/* ============================================================ */
/* 决策记录：居左宽卡——本轮为什么这么做                            */
/* ============================================================ */
.decision {
  margin-left: 38px; /* 28px 头像 + 10px 间距：与气泡体对齐 */
  max-width: 92%;
  padding: 9px 12px;
  border-radius: var(--radius-md);
  background: var(--color-agent-bg);
  border-left: 3px solid var(--color-agent);
}
.decision.is-failed {
  background: var(--color-danger-bg);
  border-left-color: var(--color-danger);
}
.decision.is-silent {
  background: var(--bg-hover);
  border-left-color: var(--border-color-dark);
}

.act-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 3px;
}

.act-kind {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 1.4px;
  color: var(--color-agent);
  flex-shrink: 0;
}
.act-kind--speech {
  color: var(--color-agent);
}
.act-emotion {
  padding: 0 6px;
  border-radius: var(--radius-sm);
  font-size: 10px;
  font-weight: 600;
  color: var(--color-agent);
  background: var(--color-agent-bg);
  flex-shrink: 0;
}

.d-round {
  font-size: 10px;
  color: var(--text-placeholder);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 180px;
}

.d-conf {
  font-size: 10px;
  color: var(--text-secondary);
  flex-shrink: 0;
}

.act-badge {
  padding: 0 6px;
  border-radius: var(--radius-sm);
  font-size: 10px;
  font-weight: 700;
  background: var(--color-danger);
  color: var(--text-inverse);
  flex-shrink: 0;
}
.act-badge.is-silent-badge {
  background: var(--bg-active);
  color: var(--text-secondary);
  border: 1px solid var(--border-color-dark);
}

.act-text {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-regular);
  white-space: pre-wrap;
  word-break: break-word;
}
.decision.is-failed .act-text {
  color: var(--color-danger);
}
.decision.is-silent .act-text {
  color: var(--text-secondary);
}

.d-guidance {
  margin: 4px 0 0;
  font-size: 11px;
  line-height: 1.5;
  color: var(--text-secondary);
  word-break: break-word;
}

.d-speech {
  margin: 5px 0 0;
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
  word-break: break-word;
}

.act-note {
  margin: 4px 0 0;
  font-size: 11px;
  line-height: 1.5;
  color: var(--color-danger);
  word-break: break-word;
}

.d-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 6px;
  font-size: 10px;
  color: var(--text-placeholder);
}

.d-link {
  font-size: 11px;
  font-weight: 600;
  color: var(--color-primary);
  text-decoration: none;
}
.d-link:hover {
  text-decoration: underline;
}

.d-raw {
  flex-basis: 100%;
}
.d-raw summary {
  cursor: pointer;
  font-size: 10px;
  color: var(--text-placeholder);
  user-select: none;
}
.d-raw pre {
  margin: 6px 0 0;
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  background: var(--bg-hover);
  font-size: 10px;
  line-height: 1.5;
  color: var(--text-secondary);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 220px;
  overflow-y: auto;
}

/* 思考过程：决策卡内回看（同 d-raw 形态） */
.d-thinking {
  flex-basis: 100%;
}
.d-thinking summary {
  cursor: pointer;
  font-size: 10px;
  color: var(--text-placeholder);
  user-select: none;
}
.d-thinking-phase {
  margin: 6px 0 0;
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  background: var(--bg-hover);
  font-size: 10px;
  line-height: 1.6;
  color: var(--text-secondary);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 180px;
  overflow-y: auto;
}
.d-thinking-tag {
  display: inline-block;
  margin-right: 6px;
  padding: 0 5px;
  border-radius: var(--radius-sm);
  background: var(--color-agent-bg);
  color: var(--color-agent);
  font-size: 9px;
  font-weight: 600;
  line-height: 16px;
  vertical-align: 1px;
}

/* 活动思考区：时间线尾部的实时滚动卡（生成中观感） */
.thinking-live {
  margin: 10px 0 0 38px;
  max-width: 92%;
  padding: 8px 12px;
  border-radius: var(--radius-md);
  background: var(--color-agent-bg);
  border-left: 2px solid var(--color-agent);
  opacity: 0.85;
}
.thinking-live-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
  font-size: 10px;
  color: var(--text-placeholder);
}
.thinking-live-title {
  font-weight: 600;
  color: var(--text-secondary);
}
.thinking-live-phase {
  margin: 4px 0 0;
  font-size: 10px;
  line-height: 1.6;
  color: var(--text-secondary);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 96px;
  overflow-y: auto;
}

/* ============================================================ */
/* 主播动作：居左卡（工具结果 / 发言）                             */
/* ============================================================ */
.act {
  margin-left: 38px; /* 与决策卡同列对齐 */
  max-width: 92%;
  min-width: 240px;
  padding: 8px 12px;
  border-radius: var(--radius-md);
  background: var(--color-tool-bg);
  border-left: 2px solid var(--color-tool);
}
.act.is-speak {
  padding: 10px 14px;
  box-shadow: var(--shadow-sm);
}
.act.is-failed {
  background: var(--color-danger-bg);
  border-left-color: var(--color-danger);
}
.act.is-speech {
  background: var(--color-agent-bg);
  border-left: 2px solid var(--color-agent);
}

.act-tool {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.act-arrow {
  font-size: 11px;
  color: var(--text-placeholder);
  flex-shrink: 0;
}

.act.is-failed .act-kind {
  color: var(--color-danger);
}

.act.is-speak .act-text {
  font-size: 15px;
  font-weight: 500;
  color: var(--text-primary);
}
.act.is-speak .act-text::before {
  content: '「';
  color: var(--color-tool);
}
.act.is-speak .act-text::after {
  content: '」';
  color: var(--color-tool);
}

/* ============================================================ */
/* 环节推进 / 场次边界：横贯分隔行                                 */
/* ============================================================ */
.beat {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 6px 0;
}
.beat-rule {
  height: 1px;
  background: var(--color-agenda);
  opacity: 0.45;
}
.beat-rule:first-child {
  width: 24px;
  flex-shrink: 0;
}
.beat-rule:last-of-type {
  flex: 1;
}
.beat-body {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.beat-eyebrow {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 1.6px;
  color: var(--color-agenda);
  flex-shrink: 0;
}
.beat-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.beat-action {
  padding: 0 7px;
  border-radius: 999px;
  font-size: 10px;
  font-weight: 700;
  background: var(--color-agenda-bg);
  color: var(--color-agenda);
  flex-shrink: 0;
}
.beat-note {
  font-size: 11px;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ============================================================ */
/* 里程碑：庆祝行                                                */
/* ============================================================ */
.milestone {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 4px 0;
  padding: 8px 14px;
  border-radius: var(--radius-md);
  background: var(--color-game-bg);
  border: 1px dashed var(--color-game);
}
.milestone-mark {
  font-size: 15px;
  color: var(--color-game);
  flex-shrink: 0;
}
.milestone-body {
  flex: 1;
  min-width: 0;
}
.milestone-text {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  word-break: break-word;
}
.milestone-meta {
  margin: 2px 0 0;
  font-size: 11px;
  color: var(--text-secondary);
}

/* ============================================================ */
/* 窄屏                                                          */
/* ============================================================ */
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
  .chat,
  .chat--gift,
  .chat--super_chat,
  .decision,
  .act {
    max-width: 100%;
    margin-left: 0;
  }
  .slate-label {
    max-width: 55%;
  }
}
</style>
