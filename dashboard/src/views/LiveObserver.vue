<template>
  <div class="console-page">
    <!-- 顶栏：页面身份 + 阶段状态 + 模拟器徽章 + 实时脉冲         -->
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
      <!-- 累计观看人次：room.state 状态快照推送（值即当前状态，变化即重发）；仅完整档 -->
      <span v-if="isFullMode && watchedLabel" class="watched-chip" title="本场累计观看人次">
        累计观看 {{ watchedLabel }}
      </span>
      <span class="grow" />
      <!-- 视图档位：完整=运营全量；简洁=纯浏览时间线（挂第二屏纯观看） -->
      <el-radio-group v-model="viewMode" size="small" class="view-switch">
        <el-radio-button value="full">完整</el-radio-button>
        <el-radio-button value="simple">简洁</el-radio-button>
      </el-radio-group>
    </header>

    <!-- 状态条挂载区（完整档）：实时计分板 + TTS 台前水位 -->
    <div v-if="isFullMode" class="console-strip">
      <ScoreboardStrip :active-session="activeSessionItem" />
      <TtsWaterLevel />
    </div>

    <div class="console-body">
      <!-- 左栏：场次侧边栏（当前 + 历史回看 + 生命周期开关）               -->
      <aside class="sessions" aria-label="直播场次">
        <header class="sessions-head">
          <h2 class="sessions-title">场次</h2>
          <span class="grow" />
          <template v-if="isFullMode">
            <el-button type="primary" @click="openStartStream">开播</el-button>
            <el-button :disabled="!activeExplicitSession" @click="closeSession">下播</el-button>
          </template>
        </header>
        <div class="sessions-filters">
          <el-input v-model="sessionQuery" size="small" placeholder="搜索场次标题" clearable />
          <el-select v-model="sessionSourceFilter" size="small" class="sessions-source">
            <el-option label="全部来源" value="" />
            <el-option label="手动" value="manual" />
            <el-option label="回放" value="replay" />
            <el-option label="模拟" value="generate" />
            <el-option label="历史" value="legacy" />
          </el-select>
          <el-select v-model="sessionKindFilter" size="small" class="sessions-kind">
            <el-option label="全部类型" value="" />
            <el-option label="正式" value="live" />
            <el-option label="彩排" value="rehearsal" />
          </el-select>
        </div>
        <ul class="session-list">
          <li
            v-for="item in sessions"
            :key="item.live_session_id"
            class="session-card"
            :class="{ 'is-selected': isSelected(item), 'is-live': item.is_active }"
            @click="selectSession(item)"
            role="button"
            tabindex="0"
            @keydown.enter.prevent="selectSession(item)"
            @keydown.space.prevent="selectSession(item)"
          >
            <div class="session-top">
              <span class="session-name" :title="sessionTitle(item)">{{ sessionTitle(item) }}</span>
              <!-- 彩排场（模拟 / 回放 / 调试）单独标出；不标即正式场 -->
              <span
                v-if="item.kind === 'rehearsal'"
                class="session-badge is-rehearsal"
                title="彩排场：回顾只进彩排记忆线，不对外同步"
                >{{ kindLabel(item.kind) }}</span
              >
              <span class="session-badge" :class="`is-${item.source}`">{{
                sourceLabel(item.source)
              }}</span>
            </div>
            <div class="session-meta mono">
              <span class="session-time">{{ sessionTimeLabel(item) }}</span>
              <span v-if="elapsedLabel(item)" class="session-elapsed">{{
                elapsedLabel(item)
              }}</span>
              <span class="grow" />
              <span class="msg-count">{{ item.message_count }} 条</span>
              <span class="session-actions">
                <el-button class="session-ren" link size="small" @click.stop="renameSession(item)"
                  >改名</el-button
                >
                <el-button
                  v-if="!item.is_active"
                  class="session-ren"
                  link
                  size="small"
                  @click.stop="toggleSessionKind(item)"
                  >{{ item.kind === 'live' ? '改彩排' : '改正式' }}</el-button
                >
                <el-popconfirm
                  v-if="!item.is_active && item.source !== 'legacy'"
                  title="删除该场次及其全部明细？"
                  width="220"
                  @confirm="removeSession(item)"
                >
                  <template #reference>
                    <el-button class="session-del" link size="small" @click.stop>删除</el-button>
                  </template>
                </el-popconfirm>
              </span>
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

        <div
          v-if="showTestModeNotice && isFullMode"
          class="test-mode-notice"
          title="正式直播请先在左侧开启场次"
        >
          测试模式：未开启场次，消息仅在内存中流转不落库
        </div>

        <div v-if="!wsConnected" class="ws-down-notice" role="status">
          连接断开 · 自动重连中，期间消息可能缺失
        </div>

        <section class="stage" aria-label="时间线">
          <header
            class="stage-bar"
            :class="{ 'is-paused': paused && sessionMode === 'live' && !replayActive }"
          >
            <h2 class="stage-title">
              {{
                replayActive
                  ? '回放调试'
                  : sessionMode === 'live'
                    ? '实时时间线'
                    : `回看 · ${sessionTitle(selectedSession)}`
              }}
            </h2>
            <span class="stage-hint">
              {{
                replayActive
                  ? `回放 · ${sessionTitle(replaySession)}（实时新事件入缓冲不渲染）`
                  : sessionMode === 'live'
                    ? paused
                      ? '已暂停 · 事件仍在后台累积'
                      : '消息与决策按时间交织，最新在下方'
                    : replayPruned
                      ? '部分时段的事件诊断数据已清理，对应区间仅显示消息明细'
                      : '历史回看（事件侧与实时同卡面：工具/决策/思考等）'
              }}
            </span>
            <span class="grow" />
            <!-- 来源过滤 chips：仅过滤 Agent 产生的卡（tool/speech/decision/verdict/stage/game），
                观众消息与场次边界始终可见；实时与回看两模式通用 -->
            <span class="agent-filter">
              <el-check-tag
                title="快捷键：1"
                :checked="agentFilter === 'all'"
                size="small"
                @change="(checked: boolean) => handleAgentChip('all', checked)"
              >
                全部
              </el-check-tag>
              <el-check-tag
                title="快捷键：2"
                :checked="agentFilter === 'streamer'"
                size="small"
                @change="(checked: boolean) => handleAgentChip('streamer', checked)"
              >
                主播
              </el-check-tag>
              <el-check-tag
                title="快捷键：3"
                :checked="agentFilter === 'game'"
                size="small"
                @change="(checked: boolean) => handleAgentChip('game', checked)"
              >
                游戏 Agent
              </el-check-tag>
            </span>
            <!-- 回放调试入口：选历史场次 + 可选起始时刻，整条时间线切归档数据 -->
            <span v-if="!replayActive" class="replay-entry">
              <el-select
                v-model="replaySessionId"
                size="small"
                class="replay-entry-session"
                placeholder="选择场次"
                clearable
                aria-label="回放场次"
              >
                <el-option
                  v-for="item in replayCandidateSessions"
                  :key="item.live_session_id"
                  :label="sessionTitle(item)"
                  :value="item.live_session_id"
                />
              </el-select>
              <el-date-picker
                v-model="replayStartAt"
                type="datetime"
                size="small"
                class="replay-entry-start"
                placeholder="起始时间（可选）"
                :disabled="!replaySessionId"
                aria-label="回放起始时间"
              />
              <el-button
                size="small"
                :disabled="!replaySessionId || replayPhase === 'loading'"
                :loading="replayPhase === 'loading'"
                @click="enterReplay"
              >
                回放
              </el-button>
            </span>
            <!-- 回放控制条：播放/步进/倍速/进度/退出（状态机在 useTimelineReplay） -->
            <ReplayControlBar
              v-if="replayActive"
              :playing="replayPlaying"
              :speed="replaySpeed"
              :cursor="replayCursor"
              :total="replayTotalCount"
              :loading="replayPhase === 'loading'"
              @toggle-play="replayTogglePlay"
              @step-forward="replayStepForward"
              @step-back="replayStepBack"
              @update:speed="replaySetSpeed"
              @exit="exitReplay"
            />
            <template v-if="sessionMode === 'live' && isFullMode && !replayActive">
              <!-- 显示模式：时间线=单列沿脊线；会话=观众左/主播右气泡对齐 -->
              <el-radio-group v-model="displayMode" size="small">
                <el-radio-button value="timeline">时间线</el-radio-button>
                <el-radio-button value="chat">会话</el-radio-button>
              </el-radio-group>
              <span class="stage-count mono">{{ entries.length }} / {{ MAX_ENTRIES }}</span>
              <el-button
                :type="paused ? 'primary' : 'default'"
                title="快捷键：空格"
                @click="togglePause"
              >
                {{ paused ? '继续' : '暂停' }}
              </el-button>
              <el-button
                class="shortcut-help"
                text
                aria-label="键盘快捷键帮助"
                title="键盘快捷键（?）"
                @click="showShortcuts = true"
              >
                <el-icon><QuestionFilled /></el-icon>
              </el-button>
              <el-button :disabled="entries.length === 0" @click="clearTimeline"> 清空 </el-button>
            </template>
            <!-- 回到实时只跟场次模式挂钩（回看态导航）；不能 v-else 接前一 template——
                 那里复合了 isFullMode，简洁档实时态会误显本按钮 -->
            <el-button
              v-if="sessionMode !== 'live' && !replayActive"
              type="primary"
              title="快捷键：F"
              @click="backToLive"
              >回到实时</el-button
            >
          </header>

          <div class="stage-body">
            <div ref="scrollRef" class="stage-scroll" @scroll.passive="onScroll">
              <!-- 回看向前翻页：置顶"加载更早"，与滚动容器同流（滚回顶部即见） -->
              <div
                v-if="sessionMode === 'replay' && replayHasMore && !replayActive"
                class="replay-older"
              >
                <el-button
                  size="small"
                  :loading="replayLoadingMore"
                  :disabled="replayLoadingMore"
                  @click="loadOlderReplay"
                >
                  加载更早
                </el-button>
              </div>
              <FeedTimeline
                :entries="displayEntries"
                :layout="displayMode"
                round-clickable
                :simple="!isFullMode"
                :empty-text="timelineEmptyText"
                :anchor-entry-id="anchoredId"
                @round-click="jumpToAgentRound"
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

          <!-- minecraft 目标的进行中任务卡条：发话→看任务→取消就地闭环。
               Agent 无任务记录、无进行中任务时整条隐藏，不占控制台空间 -->
          <div
            v-if="
              isFullMode && interventionTarget === MINECRAFT_AGENT_NAME && runningTasks.length > 0
            "
            class="task-strip"
          >
            <ul class="task-strip-list">
              <li v-for="task in runningTasks" :key="task.task_id" class="task-strip-item">
                <span class="task-strip-status" :class="`is-${task.status}`">
                  {{ taskStatusText(task.status) }}
                </span>
                <span class="task-strip-id mono" :title="task.task_id">{{ task.task_id }}</span>
                <span class="task-strip-instruction" :title="task.instruction">
                  {{ task.instruction || '（无指令摘要）' }}
                </span>
                <el-button
                  size="small"
                  type="danger"
                  plain
                  class="task-strip-cancel"
                  @click="cancelTask(task)"
                >
                  取消
                </el-button>
              </li>
            </ul>
          </div>

          <!-- Agent 备忘录/待办面板：跟随时间线来源过滤只显对应节（纯观测只读）。
             只在实际时档 + 完整档显示（回看无快照语义、简洁档藏运营面） -->
          <AgentMemoPanel
            v-if="sessionMode === 'live' && isFullMode && !replayActive"
            :target="agentFilter"
            class="memo-panel-dock"
          />

          <!-- 干预输入区：页级目标选择（主播 / minecraft；adv 不支持收话不出现）+
               共享输入条（Tab/Shift+Tab 切模式、Enter 发送、↑↓ 回溯历史）。
               模式与传输随目标切换，主播目标复用既有行为（昵称插槽、在途 chip）；
               强制回应为后台执行：发送后立即可继续输入，在途状态由状态 chip 承载 -->
          <div v-if="sessionMode === 'live' && isFullMode && !replayActive" class="input-dock">
            <div class="input-target">
              <span class="input-target-label">发给</span>
              <el-radio-group v-model="interventionTarget" size="small">
                <el-radio-button :value="STREAMER_AGENT_NAME">主播</el-radio-button>
                <el-radio-button :value="MINECRAFT_AGENT_NAME">minecraft</el-radio-button>
              </el-radio-group>
            </div>
            <InterventionInput
              ref="sendBarRef"
              :key="interventionTarget"
              :modes="interventionModes"
              :sending="sending"
              @mode-change="onModeChange"
              @send="onInterventionSend"
            >
              <template #toolbar="{ onKeydown }">
                <el-input
                  v-if="interventionTarget === STREAMER_AGENT_NAME && activeMode === 'danmaku'"
                  v-model="injectNickname"
                  size="small"
                  class="input-nick"
                  placeholder="观众昵称（可选）"
                  @keydown="onKeydown"
                />
                <span
                  v-if="interventionTarget === STREAMER_AGENT_NAME && forcePending > 0"
                  class="input-status"
                >
                  <el-icon class="is-loading"><Loading /></el-icon>
                  主播正在想…
                </span>
              </template>
            </InterventionInput>
          </div>
        </section>
      </section>
    </div>
    <!-- 快捷键帮助：? 号或 stage-bar 的"快捷键"按钮唤起 -->
    <el-dialog v-model="showShortcuts" title="键盘快捷键" width="420px">
      <ul class="shortcut-list">
        <li><kbd>Space</kbd><span>暂停 / 继续时间线</span></li>
        <li>
          <kbd>1</kbd> <kbd>2</kbd> <kbd>3</kbd><span>来源过滤：全部 / 主播 / 游戏 Agent</span>
        </li>
        <li><kbd>F</kbd><span>回到实时（回看模式下）</span></li>
        <li><kbd>?</kbd><span>打开本帮助</span></li>
        <li>
          <kbd>Tab</kbd> <kbd>↑</kbd> <kbd>↓</kbd
          ><span>输入条内：切换模式 / 回溯历史（需输入框聚焦）</span>
        </li>
      </ul>
    </el-dialog>

    <!-- 开播引导：配置本次直播要开哪些内容 + 流程单，确认后开场次并组件对齐 -->
    <StartStreamDialog
      v-model="startStreamVisible"
      :active-session-exists="activeExplicitSession"
      @opened="onStreamOpened"
    />
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
 * 干预输入区（时间线底部）：页级目标选择（主播 / minecraft）+ 共享输入条。
 * 主播目标三模式 = 场控的三种操作——注入弹幕（debug/inject-message，与真实弹幕
 * 同链路）、强制回应（streamer/test-decision，直驱决策、结果以决策卡落进时间线，
 * 留空=自由发挥）、幕后提醒（agents/{name}/prompt，运营递话：文字必达进下个
 * 决策参考块，并顺带敲门催醒主播提前决策）；minecraft 目标递话/委派走 agents 域
 * 接口。目标→模式→传输的单一事实源在 useAgentIntervention。
 *
 * 时间线行点击深链：带轮次 ID 的决策/裁决/工具/发言行可点，跳 Agent 页对应
 * 决策轮（/agents?agent=&round=，契约见 useAgentDeepLink）。
 *
 * 数据来源：
 * - 实时：events store（全局 WS + 游标回填，刷新/断线不丢时间线）+ 思考流旁路
 *   （kind="stream"，仅视图层合成思考行、按时间归并进时间线，不入 store 不回看）
 * - 回看：GET /live-sessions/{id}/timeline——明细行 + 事件侧（诊断事件流文件
 *   logs/events ∪ 内存环形缓冲，与实时同卡面；游标"加载更早"向前翻页；诊断
 *   天目录被清理时事件侧降级，页面头部如实提示）。思考流不落库：卡片上的
 *   "思考"按 llm_request_id 从 LLM 请求历史懒取 reasoning_content
 * 渲染字段一律取自后端真实 Payload（src/modules/events/payloads/），不臆造字段。
 *
 * 视图档位（顶栏切换，useConsoleViewMode 持久化）：完整=运营全量；简洁=
 * 观众视角的纯浏览——藏起干预输入条/任务条/场次生命周期开关/暂停清空等
 * 操作件，时间线内容也过滤成对话流（发言/弹幕/礼物/场次边界，
 * 口径见 liveFeed.isConversationEntry），工具/决策/阶段等管线诊断卡整卡
 * 不出现、发言卡上的 LLM 统计胶囊一并隐藏（FeedTimeline simple 档）。
 * 实时与回看两种场次模式都适用。
 *
 * 页面私有逻辑按块拆在 composables/live/：场次侧边栏（useLiveSessions）、
 * 思考流（useThinkingStream）、时间线内容合成（useLiveTimeline）、顶栏/环节
 * 徽章（useLiveStatus）、干预输入条（useInterventionBar）、滚动跟随的页面侧
 * 组装（useTimelineScroll）。全局共享态（events 流、WS 连接）仍在 Pinia store。
 * 本组件只做装配与跨块的少量编排（清空时间线联动未读计数）。
 */
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { storeToRefs } from 'pinia';
import { Loading, QuestionFilled } from '@element-plus/icons-vue';
import { useWebSocketStore, useEventsStore } from '@/stores';
import { EVENT } from '@/types/events';
import InterventionInput from '@/components/dashboard/InterventionInput.vue';
import StartStreamDialog from '@/components/live/StartStreamDialog.vue';
import { useNowTick } from '@/composables/useNowTick';
import { useLiveSessions } from '@/composables/live/useLiveSessions';
import { useThinkingStream } from '@/composables/live/useThinkingStream';
import { useLiveTimeline } from '@/composables/live/useLiveTimeline';
import { useLiveStatus } from '@/composables/live/useLiveStatus';
import {
  MINECRAFT_AGENT_NAME,
  STREAMER_AGENT_NAME,
  useAgentIntervention,
} from '@/composables/useAgentIntervention';
import { agentRoundLocation } from '@/composables/useAgentDeepLink';
import { taskStatusText, useAgentTasks } from '@/composables/useAgentTasks';
import { useTimelineScroll } from '@/composables/live/useTimelineScroll';
import { useTimelineAnchor } from '@/composables/live/useTimelineAnchor';
import { useConsoleViewMode } from '@/composables/live/useConsoleViewMode';
import { useTimelineReplay } from '@/composables/live/useTimelineReplay';
import ReplayControlBar from '@/components/live/ReplayControlBar.vue';
import { confirmAction } from '@/utils/confirmAction';
import { ElMessage } from 'element-plus';
import { formatNumber } from '@/utils/format';
import { waitFor } from '@/utils/waitFor';
import { MAX_ENTRIES, isConversationEntry, relativeTime, type ShowEntry } from '@/utils/liveFeed';
import type { LiveSessionItem } from '@/types';
import FeedTimeline from '@/components/live/FeedTimeline.vue';
import AgentMemoPanel from '@/components/live/AgentMemoPanel.vue';
import ScoreboardStrip from '@/components/live/ScoreboardStrip.vue';
import TtsWaterLevel from '@/components/live/TtsWaterLevel.vue';

// 装配：场次侧边栏 → 思考流 → 时间线内容 → 滚动跟随 → 干预输入条
// （依赖沿参数单向流动：时间线消费场次模式与思考行，滚动跟随消费展示条目）

const wsStore = useWebSocketStore();
const { isConnected: wsConnected } = storeToRefs(wsStore);

const eventsStore = useEventsStore();
/** 顶栏「累计观看」胶囊：取事件缓冲中最新一条 room.state 快照的 audience_total（UV 口径只增不减） */
const watchedLabel = computed<string | null>(() => {
  const list = eventsStore.events;
  for (let i = list.length - 1; i >= 0; i--) {
    if (list[i].type !== EVENT.roomState) continue;
    const value = list[i].data.audience_total;
    return typeof value === 'number' && Number.isFinite(value) && value >= 0
      ? formatNumber(value)
      : null;
  }
  return null;
});

const {
  sessions,
  activeExplicitSession,
  showTestModeNotice,
  sessionQuery,
  sessionSourceFilter,
  sessionKindFilter,
  sessionMode,
  selectedSession,
  sessionTitle,
  sourceLabel,
  kindLabel,
  sessionTimeLabel,
  elapsedLabel,
  isSelected,
  loadSessions,
  startStreamVisible,
  openStartStream,
  onStreamOpened,
  closeSession,
  renameSession,
  toggleSessionKind,
  removeSession,
  selectSession,
  backToLive,
} = useLiveSessions();

const { liveThinkingRows, thinkingHiddenBeforeMs } = useThinkingStream();

const {
  paused,
  displayMode,
  agentFilter,
  handleAgentChip,
  entries,
  togglePause,
  clearTimeline: clearTimelineEntries,
  replayHasMore,
  replayPruned,
  replayLoadingMore,
  loadOlderReplay,
} = useLiveTimeline({
  liveThinkingRows,
  thinkingHiddenBeforeMs,
  sessionMode,
  selectedSession,
});

const { rundownBanner, stageChip, simulatorChip, loadSimulatorStatus } = useLiveStatus();

/** 状态条数据源：当前进行中的显式场次（无进行中场次时计分板时长显示占位） */
const activeSessionItem = computed<LiveSessionItem | null>(
  () => sessions.value.find(s => s.is_active) ?? null,
);

// 回放调试：整条时间线切归档数据按事件步进（状态机见 useTimelineReplay）。
// 来源过滤复用 agentFilter（room 组与观众消息始终可见，与实时/回看同口径）
const {
  phase: replayPhase,
  session: replaySession,
  playing: replayPlaying,
  speed: replaySpeed,
  active: replayActive,
  cursor: replayCursor,
  cursorTsMs: replayCursorTs,
  visibleEntries: replayVisibleEntries,
  totalCount: replayTotalCount,
  start: startReplay,
  exit: exitReplayState,
  togglePlay: replayTogglePlay,
  pause: replayPause,
  stepForward: replayStepForward,
  stepBack: replayStepBack,
  setSpeed: replaySetSpeed,
  jumpToTime: replayJumpToTime,
} = useTimelineReplay({ agentFilter });

/** 回放入口的场次选择与可选起始时刻 */
const replaySessionId = ref<number | null>(null);
const replayStartAt = ref<Date | null>(null);
/** 回放候选：历史场次（进行中场次没有可回放的归档全量） */
const replayCandidateSessions = computed(() => sessions.value.filter(item => !item.is_active));

/** 进入回放：整条时间线切到归档渲染；起始时刻缺省从第一条开始（暂停态） */
async function enterReplay(): Promise<void> {
  const item = replayCandidateSessions.value.find(
    candidate => candidate.live_session_id === replaySessionId.value,
  );
  if (!item) return;
  await startReplay(item, replayStartAt.value?.getTime());
}

/** 回放期间到达的实时事件数：进入回放时快照事件缓冲长度，退出时差值即错过量
 *  （events store 全程缓冲不丢，退出回放不需要补拉请求） */
const replayMissedSnapshot = ref(0);
watch(replayActive, active => {
  if (active) replayMissedSnapshot.value = eventsStore.events.length;
});

/** 退出回放：确认后回到实时流；有错过条数时如实提示 */
async function exitReplay(): Promise<void> {
  if (!replayActive.value) return;
  const missed = Math.max(0, eventsStore.events.length - replayMissedSnapshot.value);
  const confirmed = await confirmAction(
    missed > 0
      ? `退出回放并回到实时流？回放期间到达的 ${missed} 条实时事件已在缓冲中，回到实时后立即显示。`
      : '退出回放并回到实时流？',
    '退出回放',
  );
  if (!confirmed) return;
  exitReplayState();
  replaySessionId.value = null;
  replayStartAt.value = null;
  if (missed > 0) {
    ElMessage.info(`已退出回放，缓冲中的 ${missed} 条实时事件已回到时间线`);
  }
}

// 视图档位：完整=上图全量运营面；简洁=观众视角的纯浏览——除藏运营操作件外，
// 时间线内容本身也过滤成对话流（发言/弹幕/礼物/场次边界），
// 工具/决策/阶段等管线诊断卡整卡不出现
const { viewMode, isFullMode } = useConsoleViewMode();

/** 喂给时间线的展示条目：回放调试=归档前缀（组合式内已过 agentFilter，再过
 *  档位口径）；其余模式=实时/回看缓冲（完整档原样；简洁档按对话口径过滤，
 *  口径在 liveFeed.isConversationEntry） */
const displayEntries = computed<ShowEntry[]>(() => {
  if (replayActive.value) {
    const prefix = replayVisibleEntries.value;
    return isFullMode.value ? prefix : prefix.filter(isConversationEntry);
  }
  return isFullMode.value ? entries.value : entries.value.filter(isConversationEntry);
});

const timelineEmptyText = computed(() => {
  if (replayActive.value) return '该场次归档暂无条目';
  if (sessionMode.value === 'live') return '静候消息与决策——注入一条弹幕试试';
  return isFullMode.value ? '该场次暂无可回看条目' : '简洁档只显示发言与弹幕——该场次暂无对话内容';
});

const { scrollRef, unseen, onScroll, jumpToLatest, resetUnseen } =
  useTimelineScroll(displayEntries);

// 回看时刻锚定：复盘页指标卡深链（/live?session=&at=<ms>）跳入时自动向前
// 翻页到目标时刻，滚动定位并短暂高亮（翻页上限与降级提示在组合式内）
const { anchoredId, anchorAt } = useTimelineAnchor({
  entries: displayEntries,
  hasMore: replayHasMore,
  loadingMore: replayLoadingMore,
  loadOlder: loadOlderReplay,
  scrollRoot: scrollRef,
});

/** 回放首屏加载完成后再锚定（loadReplayTimeline 由场次模式 watch 异步触发，
 *  有界等待防止诊断流缺失时空转；等待期间切走场次则放弃锚定） */
async function anchorAfterReplayLoad(targetMs: number): Promise<void> {
  await waitFor(() => sessionMode.value !== 'replay' || displayEntries.value.length > 0, {
    tries: 50,
    intervalMs: 100,
  });
  if (sessionMode.value === 'replay') await anchorAt(targetMs);
}

const sendBarRef = ref<InstanceType<typeof InterventionInput> | null>(null);

// 干预发话：目标二选一（默认主播），模式集合与传输随目标切换——
// 单一事实源在 useAgentIntervention，主播目标复用既有行为（昵称插槽、在途 chip）
const router = useRouter();
const route = useRoute();
const interventionTarget = ref<string>(STREAMER_AGENT_NAME);
const {
  activeMode,
  injectNickname,
  forcePending,
  onModeChange,
  modesForTarget,
  sendToTarget,
  sending,
} = useAgentIntervention({ settle: async () => sendBarRef.value?.settle() });

const interventionModes = computed(() => modesForTarget(interventionTarget.value));

function onInterventionSend(modeKey: string, text: string): void {
  void sendToTarget(interventionTarget.value, modeKey, text);
}

// 目标切换经 :key 重挂输入条，内部模式回到首项——主播工具项状态同步复位
watch(interventionTarget, target => {
  if (target === STREAMER_AGENT_NAME) onModeChange('danmaku');
});

// minecraft 目标的进行中任务（紧循环：发话→看任务→取消）。
// 数据流与 Agent 页任务板同源（useAgentTasks）；主播 Agent 无任务记录不展示
// 取消前的确认微流程与 Agent 任务板同源（useAgentTasks 收口）
const { runningTasks, cancelTaskWithConfirm: cancelTask } = useAgentTasks(interventionTarget);

/** 时间线行点击 → Agent 页对应决策轮（可点行已由 FeedTimeline 按轮次 ID 过滤） */
function jumpToAgentRound(entry: ShowEntry): void {
  if (!entry.roundId) return;
  void router.push(agentRoundLocation(STREAMER_AGENT_NAME, entry.roundId));
}

/** 清空时间线：条目与思考行水位归时间线块，未读计数归滚动块 */
async function clearTimeline(): Promise<void> {
  if (
    !(await confirmAction(
      '将清空当前时间线展示（回看与实时缓冲都会清掉，落盘历史不受影响）。确定要清空吗？',
      '清空时间线',
    ))
  )
    return;
  clearTimelineEntries();
  resetUnseen();
}

// 秒级时钟：驱动顶部环节横幅的相对时间刷新

const nowTick = useNowTick();

// 键盘快捷键：直播中双手常不在鼠标上，最小集覆盖暂停/过滤/回实时。
// keep-alive 页面用激活态挂摘，避免别的页面误触控制台快捷键。
const showShortcuts = ref(false);

function onConsoleKeydown(e: KeyboardEvent): void {
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  const target = e.target as HTMLElement | null;
  if (
    target &&
    (target.tagName === 'INPUT' ||
      target.tagName === 'TEXTAREA' ||
      target.tagName === 'SELECT' ||
      target.isContentEditable)
  ) {
    return;
  }
  if (e.key === '?') {
    e.preventDefault();
    showShortcuts.value = !showShortcuts.value;
    return;
  }
  // 回放调试优先：空格=播放/暂停，F=退出回放（退出流程含确认）
  if (replayActive.value) {
    if (e.key === ' ') {
      e.preventDefault();
      replayTogglePlay();
    } else if (e.key === 'f' || e.key === 'F') {
      e.preventDefault();
      void exitReplay();
    }
    return;
  }
  if (sessionMode.value !== 'live') {
    if (e.key === 'f' || e.key === 'F') {
      e.preventDefault();
      backToLive();
    }
    return;
  }
  switch (e.key) {
    case ' ':
      e.preventDefault();
      togglePause();
      break;
    case '1':
      e.preventDefault();
      handleAgentChip('all', true);
      break;
    case '2':
      e.preventDefault();
      handleAgentChip('streamer', true);
      break;
    case '3':
      e.preventDefault();
      handleAgentChip('game', true);
      break;
  }
}

onMounted(() => window.addEventListener('keydown', onConsoleKeydown));
onActivated(() => window.addEventListener('keydown', onConsoleKeydown));
onDeactivated(() => {
  window.removeEventListener('keydown', onConsoleKeydown);
  // keep-alive 失活即暂停回放播放（停表）；重激活不自动续播——
  // 回放调试是主动操作，切页回来静默续播易误判时间线状态
  replayPause();
});
onUnmounted(() => window.removeEventListener('keydown', onConsoleKeydown));

// 回放位置深链（useStreamPageQuery 的同款机械，就地实现）：?rp=<场次id>&rpt=
// <游标时刻毫秒>。回放场次或游标推进即回写（replace 不产生历史记录，缺值删键）；
// 刷新页面按 query 还原：载归档后 jumpToTime 落回原游标位置。
// 就地而非复用 useStreamPageQuery 的理由：后者回写时会整表替换 query（只保留
// 注册字段），会抹掉本页既有的 ?session=&at= 回看深链；此处改为在原 query 上
// 只增删自己的两个键，两套深链互不踩踏
const REPLAY_QUERY_SESSION = 'rp';
const REPLAY_QUERY_CURSOR = 'rpt';

watch([replaySession, replayCursorTs], () => {
  const sid = replaySession.value?.live_session_id;
  const ts = replayCursorTs.value;
  const query: Record<string, string> = {};
  for (const [key, value] of Object.entries(route.query)) {
    if (typeof value === 'string') query[key] = value;
  }
  if (sid != null) query[REPLAY_QUERY_SESSION] = String(sid);
  else delete query[REPLAY_QUERY_SESSION];
  if (ts != null) query[REPLAY_QUERY_CURSOR] = String(ts);
  else delete query[REPLAY_QUERY_CURSOR];
  void router.replace({ query });
});

/** 深链还原：rp 指向的历史场次存在则进入回放，载归档完成后跳到 rpt 游标时刻 */
async function restoreReplayFromQuery(): Promise<void> {
  const sid = Number(route.query[REPLAY_QUERY_SESSION]);
  if (!Number.isFinite(sid) || sid <= 0) return;
  const target = sessions.value.find(item => item.live_session_id === sid && !item.is_active);
  if (!target) return;
  await startReplay(target);
  const ts = Number(route.query[REPLAY_QUERY_CURSOR]);
  if (Number.isFinite(ts) && ts > 0) replayJumpToTime(ts);
}

// 生命周期

onMounted(() => {
  void loadSessions().then(async () => {
    // 直达预过滤：场次复盘页经 ?session=<id[,id...]> 跳入时选中该历史场次进回看
    //（列表加载完成后才能命中；无效 id 静默忽略，维持实时模式）。
    // 复盘侧多选合并可带多场，回看视图一次只呈现一场——按链接顺序选中首个命中场次，
    // 其余 id 保留在链接中供手动切换
    let deepLinked = false;
    const deepLinkIds = String(route.query.session ?? '')
      .split(',')
      .map(Number)
      .filter(id => Number.isFinite(id) && id > 0);
    for (const id of deepLinkIds) {
      const target = sessions.value.find(s => s.live_session_id === id);
      if (target && !target.is_active) {
        selectSession(target);
        deepLinked = true;
        // 时刻锚定（复盘页指标卡深链）：at=毫秒时刻，回看加载完成后定位高亮
        const anchorMs = Number(route.query.at);
        if (Number.isFinite(anchorMs) && anchorMs > 0) {
          void anchorAfterReplayLoad(anchorMs);
        }
        break;
      }
    }
    // 回放调试深链还原（?rp=&rpt=）：与回看深链并存时优先回放（链接语义更具体）
    if (!deepLinked) await restoreReplayFromQuery();
  });
  void loadSimulatorStatus();
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

/* 状态条挂载区（顶栏与主体之间）：计分板左、TTS 水位右 */
.console-strip {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--spacing-md, 16px);
  flex-shrink: 0;
}

.console-strip > .tts-water {
  margin-left: auto;
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

/* 累计观看人次胶囊：与 stage-chip 同一视觉层的只读数字 */
.watched-chip {
  padding: 2px 10px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
  border: 1px solid var(--border-color-dark);
  color: var(--text-secondary);
  background: var(--bg-card);
  flex-shrink: 0;
  align-self: center;
  white-space: nowrap;
}

/* --- 视图档位切换 --- */
.view-switch {
  flex-shrink: 0;
  align-self: center;
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
.sessions-kind {
  width: 92px;
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
  font-size: 11px;
  font-weight: 700;
  background: var(--bg-card);
  color: var(--text-secondary);
  border: 1px solid var(--border-color-light);
}
.session-badge.is-replay {
  color: var(--color-rundown);
  border-color: var(--color-rundown);
}
.session-badge.is-rehearsal {
  color: var(--text-placeholder);
  border-style: dashed;
}

.session-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 3px;
  font-size: 11px;
  color: var(--text-secondary);
}
.session-time {
  min-width: 0;
}
.session-elapsed {
  flex-shrink: 0;
  white-space: nowrap;
  font-weight: 700;
  color: var(--color-danger);
}
.msg-count {
  flex-shrink: 0;
  white-space: nowrap;
}

/* 操作按钮与消息数共用 meta 行右槽：悬停/选中时按钮换入，避免绝对定位叠字；
   时间标签仅悬停时收为单行省略，静止态始终完整（超长自然换行） */
.session-card:hover .session-time,
.session-card.is-selected .session-time {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.session-actions {
  display: none;
  flex-shrink: 0;
  align-items: center;
}
.session-actions :deep(.el-button) {
  padding: 0;
  font-size: inherit;
}
.session-actions :deep(.el-button + .el-button) {
  margin-left: 6px;
}
.session-card:hover .session-actions,
.session-card.is-selected .session-actions {
  display: inline-flex;
}
.session-card:hover .msg-count,
.session-card.is-selected .msg-count {
  display: none;
}
.session-ren {
  color: var(--text-placeholder);
}
.session-ren:hover {
  color: var(--color-primary);
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
  font-size: 11px;
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
  font-size: 11px;
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

/* 快捷键帮助入口：弱化的 ? 图标位 */
.shortcut-help {
  margin-left: auto;
}

/* 快捷键帮助列表 */
.shortcut-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
}
.shortcut-list li {
  display: flex;
  align-items: center;
  gap: var(--spacing-md);
  font-size: 13px;
  color: var(--text-regular);
}
.shortcut-list kbd {
  padding: 2px 8px;
  border: 1px solid var(--border-color);
  border-bottom-width: 2px;
  border-radius: var(--radius-sm);
  background: var(--bg-hover);
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-primary);
}

/* 断线横幅：whisper 音量，但必须可读——直播中"时间线不动了"要有明确解释 */
.ws-down-notice {
  padding: 6px 12px;
  border-radius: var(--radius-sm);
  background: var(--color-warning-bg);
  font-size: 12px;
  color: var(--color-warning);
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

/* 进行中任务卡条：minecraft 目标的紧循环（发话→看任务→取消），限高内滚动 */
.task-strip {
  flex-shrink: 0;
  padding: 6px var(--spacing-md) 0;
  background: var(--bg-card);
}

/* 备忘录/待办面板：任务卡条下方、干预输入区上方的常驻观测条。
   允许让位：矮窗口下压缩自身（下限=标题行），内容由面板内部滚动消化；
   固定不让位会把时间线滚动区挤到 0，时间线整体被裁剪且无法滚动 */
.memo-panel-dock {
  flex-shrink: 1;
  min-height: 28px;
  margin: 6px var(--spacing-md) 0;
}

.task-strip-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 120px;
  overflow-y: auto;
}
.task-strip-list::-webkit-scrollbar {
  width: 6px;
}
.task-strip-list::-webkit-scrollbar-thumb {
  background: var(--border-color-dark);
  border-radius: 3px;
}

.task-strip-item {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  padding: 3px 10px;
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-sm);
  background: var(--bg-hover);
}

.task-strip-status {
  flex-shrink: 0;
  font-size: 11px;
  font-weight: 700;
  padding: 1px 8px;
  border-radius: 999px;
  background: var(--color-agent-bg);
  color: var(--color-agent);
}
.task-strip-status.is-waiting_for_decision {
  background: var(--el-color-warning-light-9);
  color: var(--el-color-warning);
}

.task-strip-id {
  flex-shrink: 0;
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 11px;
  color: var(--text-secondary);
}

.task-strip-instruction {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  color: var(--text-primary);
}

.task-strip-cancel {
  flex-shrink: 0;
}

/* 干预输入区：目标选择行 + 共享输入条（输入交互样式在共享组件内） */
.input-dock {
  flex-shrink: 0;
  padding: 6px var(--spacing-md) 10px;
  background: var(--bg-card);
  border-top: 1px solid var(--border-color-light);
}

.input-target {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  padding-bottom: 6px;
}

.input-target-label {
  font-size: 11px;
  color: var(--text-secondary);
}
/* 注入弹幕模式的昵称小输入框：迷你描边框，与模式 chip 同一视觉层 */
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
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
}
.input-status .el-icon {
  font-size: 11px;
}

/* 滚动体 + 顶部渐隐 + 回到最新                                   */
.stage-body {
  position: relative;
  flex: 1;
  /* 时间线是本列主内容：备忘录面板与输入区把固定高度吃满后仍保底可读高度，
     不足的差额由可让位的备忘录面板（.memo-panel-dock）承担 */
  min-height: 120px;
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

/* 回放调试入口：场次下拉 + 起始时刻 + 进入按钮（行内排布，随 stage-bar 折行） */
.replay-entry {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.replay-entry-session {
  width: 150px;
}
.replay-entry-start {
  width: 180px;
}
.replay-entry-start :deep(.el-input__wrapper) {
  padding-left: 6px;
}

/* 回看向旧翻页条：滚动流内置顶居中 */
.replay-older {
  display: flex;
  justify-content: center;
  padding: 2px 0 12px;
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
