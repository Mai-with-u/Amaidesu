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
      <span v-if="watchedLabel" class="watched-chip" title="本场累计观看人次">
        累计观看 {{ watchedLabel }}
      </span>
      <span class="grow" />
      <!-- 动态主从状态与导演切权入口：常态紧凑胶囊，点击弹浮层一键切换 -->
      <AgentLeadControl />
    </header>

    <!-- 状态条：实时计分板 + TTS 台前水位 -->
    <div class="console-strip">
      <ScoreboardStrip :active-session="activeSessionItem" />
      <TtsWaterLevel />
    </div>

    <div class="console-body">
      <!-- 左栏：场次侧边栏（当前 + 历史回看 + 生命周期开关）               -->
      <aside class="sessions" aria-label="直播场次">
        <header class="sessions-head">
          <h2 class="sessions-title">场次</h2>
          <span class="grow" />
          <el-button type="primary" @click="openStartStream">开播</el-button>
          <el-button :disabled="!activeExplicitSession" @click="closeSession">下播</el-button>
        </header>
        <div class="sessions-filters">
          <!-- 搜索单独一行：标题关键字最常改，占满整行不必挤在下拉框边上 -->
          <el-input
            v-model="sessionQuery"
            size="small"
            class="sessions-search"
            placeholder="搜索场次标题"
            clearable
            aria-label="搜索场次标题"
          />
          <!-- 两个下拉框各带常驻前缀（来源 / 类型），不选时也知道自己筛的是什么 -->
          <div class="sessions-filter-row">
            <el-select
              v-model="sessionSourceFilter"
              size="small"
              class="sessions-source"
              aria-label="按来源筛选场次"
            >
              <template #prefix><span class="filter-cap">来源</span></template>
              <el-option label="全部" :value="SESSION_FILTER_ALL" />
              <el-option
                v-for="opt in SESSION_SOURCE_OPTIONS"
                :key="opt.value"
                :label="opt.label"
                :value="opt.value"
              />
            </el-select>
            <el-select
              v-model="sessionKindFilter"
              size="small"
              class="sessions-kind"
              aria-label="按类型筛选场次"
            >
              <template #prefix><span class="filter-cap">类型</span></template>
              <el-option label="全部" :value="SESSION_FILTER_ALL" />
              <el-option
                v-for="opt in SESSION_KIND_OPTIONS"
                :key="opt.value"
                :label="opt.label"
                :value="opt.value"
              />
            </el-select>
          </div>
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
              <!-- 标题是回看入口（整场数据浏览）；回放（逐条放映）另给按钮。
                   两个入口都用真实 button，避免把交互元素套进 role="button" 里 -->
              <button type="button" class="session-open" @click.stop="selectSession(item)">
                <span class="session-name" :title="sessionTitle(item)">{{
                  sessionTitle(item)
                }}</span>
              </button>
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
              <!-- 起止时刻独占一行：加进时长后同一行放不下，挤下去会把结束时刻甩到下一行 -->
              <span class="session-time">{{ sessionTimeLabel(item) }}</span>
              <span class="session-stats">
                <span
                  v-if="sessionDurationLabel(item, nowTick)"
                  class="session-duration"
                  :class="{ 'is-running': item.is_active }"
                  >{{ sessionDurationLabel(item, nowTick) }}</span
                >
                <span class="grow" />
                <span class="msg-count">{{ item.message_count }} 条</span>
                <button
                  v-if="!item.is_active"
                  type="button"
                  class="session-replay"
                  title="回放这场直播：逐条放映当时的决策链"
                  @click.stop="enterReplay(item)"
                >
                  回放
                </button>
              </span>
            </div>
          </li>
        </ul>
        <p class="sessions-hint">启动不自动开场次</p>
      </aside>

      <!-- 右区：环节横幅 + 时间线                                        -->
      <section class="console-main">
        <!-- 环节横幅：当前段 + 本段要聊什么 + 时间账；点开是流程单时间轴 -->
        <RundownSlate
          :rows="rundownRows"
          :current-row="rundownCurrentRow"
          :live="sessionMode === 'live' && activeExplicitSession"
          :paused="rundownPaused"
          :action-loading="rundownActionLoading"
          :idle-text="rundownIdleText"
          @control="onRundownControl"
        />

        <div v-if="!wsConnected" class="ws-down-notice" role="status">
          连接断开 · 自动重连中，期间消息可能缺失
        </div>

        <section class="stage" aria-label="时间线">
          <!-- 回放态切换的读屏播报：界面上已有整条着色与标题，这里只把状态变化念出来 -->
          <p class="sr-only" role="status">{{ replayAnnouncement }}</p>
          <header
            class="stage-bar"
            :class="{
              'is-paused': paused && sessionMode === 'live' && !replayActive,
              'is-replay': replayActive,
            }"
          >
            <h2 class="stage-title">{{ stageTitle }}</h2>
            <span class="stage-hint">{{ stageHint }}</span>
            <!-- 测试模式警示 chip：独立提示行已并入此处，悬停给完整解释 -->
            <span
              v-if="showTestModeNotice"
              class="test-mode-chip"
              title="未开启场次，消息仅在内存中流转不落库；正式直播请先在左侧开启场次"
            >
              测试模式
            </span>
            <span class="grow" />
            <!-- 三档视图共用同一份条目流，只换组织方式与密度：
                 观众视角=观众看到的单栏流；后台视角=两个 Agent 各自看到的双栏卡片；
                 决策轨迹=一次请求一条记录的紧凑调试视图 -->
            <el-radio-group v-model="stageView" size="small" class="stage-view-switch">
              <el-radio-button value="audience">观众视角</el-radio-button>
              <el-radio-button value="backstage">后台视角</el-radio-button>
              <el-radio-button value="trajectory">决策轨迹</el-radio-button>
            </el-radio-group>
            <!-- 回放控制条：走带控制 + 节奏倍速 + 时间轴 + 定位 + 退出（状态机在 useTimelineReplay）。
                 独占一行（flex-basis 100%），与标题区分开 -->
            <ReplayControlBar
              v-if="replayActive"
              class="replay-transport"
              :playing="replayPlaying"
              :speed="replaySpeed"
              :pace="replayPace"
              :cursor="replayCursor"
              :total="replayTotalCount"
              :loading="replayPhase === 'loading'"
              :cursor-ts-ms="replayCursorTs"
              :archive-start-ms="replayArchiveStartMs"
              :archive-end-ms="replayArchiveEndMs"
              @toggle-play="replayTogglePlay"
              @step-forward="replayStepForward"
              @step-back="replayStepBack"
              @update:speed="replaySetSpeed"
              @update:pace="replaySetPace"
              @locate="replayJumpToTime"
              @exit="exitReplay"
            />
            <template v-if="sessionMode === 'live' && !replayActive">
              <span v-if="stageView === 'audience'" class="stage-count mono"
                >{{ entries.length }} / {{ MAX_ENTRIES }}</span
              >
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
            <!-- 回到实时只跟场次模式挂钩（回看态导航）；不能 v-else 接前一 template，
                 那个模板块还带着实时态条件 -->
            <el-button
              v-if="sessionMode !== 'live' && !replayActive"
              type="primary"
              title="快捷键：F"
              @click="backToLive"
              >回到实时</el-button
            >
          </header>

          <!-- 内容搜索：整场检索（落库明细 + 事件载荷），命中清单跳到那一刻。
               主时间线只加命中竖条，不隐藏不重排；回放调试档不提供（跳转会
               打乱放映游标） -->
          <LiveSearchBar
            v-if="!replayActive"
            ref="searchBarRef"
            v-model:query="searchQuery"
            class="stage-search"
            :hits="searchHits"
            :index="searchIndex"
            :loading="searchLoading"
            :scope-note="searchScopeNote"
            :show-list="searchExpanded"
            :has-more="searchHasMore"
            :categories="searchBarCategories"
            :active-category="searchActiveCategory"
            @next="searchNext()"
            @previous="searchPrevious()"
            @confirm="onSearchConfirm"
            @select="onSearchSelect"
            @hover="onSearchHover"
            @set-category="searchSetCategory"
            @clear="searchClear()"
            @collapse="searchExpanded = false"
          />

          <div class="stage-body">
            <div
              ref="scrollRef"
              class="stage-scroll"
              :class="{ 'is-lanes': stageView === 'backstage' }"
              @scroll.passive="onScroll"
            >
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
                v-if="stageView === 'audience'"
                :entries="audienceEntries"
                :empty-text="audienceEmptyText"
                :round-clickable="true"
                :simple="true"
                :matched-ts="searchMatchedTs"
                :anchor-entry-id="searchAnchorId || timelineAnchoredId"
                @round-click="onTimelineRoundClick"
              />
              <LiveLanes
                v-else-if="stageView === 'backstage'"
                ref="lanesRef"
                :entries="allEntries"
                :tasks="taskWindows"
                :task-facts="taskFacts"
                :boundaries="laneBoundaries"
                :matched-ts="searchMatchedTs"
                :empty-text="timelineEmptyText"
              />
              <RequestTimeline
                v-else
                :records="requestRecords"
                :empty-text="timelineEmptyText"
                :selected-id="selectedMessage?.id"
                @select="openMessage"
              />
            </div>

            <RequestDetail
              v-if="stageView === 'trajectory' && selectedRecord && selectedMessage"
              :record="selectedRecord"
              :message="selectedMessage"
              @close="closeMessage"
            />

            <button v-if="showJumpToLatest" type="button" class="jump" @click="jumpToLatest">
              {{ jumpToLatestLabel }}
            </button>
          </div>

          <!-- minecraft 目标的进行中任务卡条：发话→看任务→取消就地闭环。
               Agent 无任务记录、无进行中任务时整条隐藏，不占控制台空间 -->
          <div
            v-if="interventionTarget === MINECRAFT_AGENT_NAME && runningTasks.length > 0"
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

          <!-- 游戏 Agent 备忘录/待办面板：minecraft 的待办 / 工作笔记 / 近期上报（纯观测只读）。
             只在实际时档 + 完整档显示（回看无快照语义、简洁档藏运营面） -->
          <AgentMemoPanel v-if="sessionMode === 'live' && !replayActive" class="memo-panel-dock" />

          <!-- 干预输入区：页级目标选择（主播 / minecraft；adv 不支持收话不出现）+
               共享输入条（Tab/Shift+Tab 切模式、Enter 发送、↑↓ 回溯历史）。
               模式与传输随目标切换，主播目标复用既有行为（昵称插槽、在途 chip）；
               强制回应为后台执行：发送后立即可继续输入，在途状态由状态 chip 承载 -->
          <div v-if="sessionMode === 'live' && !replayActive" class="input-dock">
            <div class="input-target">
              <span class="input-target-label">发给</span>
              <el-radio-group v-model="interventionTarget" size="small">
                <el-radio-button :value="STREAMER_AGENT_NAME">主播</el-radio-button>
                <el-radio-button :value="MINECRAFT_AGENT_NAME">minecraft</el-radio-button>
              </el-radio-group>
              <!-- 游戏主控态挑明：给主播的递话会被后端同时转一份非强杀建议给主控游戏 -->
              <span v-if="interventionTarget === STREAMER_AGENT_NAME && streamerNudgeHint" class="input-target-hint">
                {{ streamerNudgeHint }}
              </span>
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
        <li><kbd>F</kbd><span>回到实时（回看模式下）</span></li>
        <li><kbd>/</kbd><span>聚焦内容搜索框</span></li>
        <li><kbd>F3</kbd> <kbd>Shift+F3</kbd><span>下一条 / 上一条搜索命中</span></li>
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
 * - 中部三档视图，同一份条目流的三种投影：观众视角（观众在直播间里看到的
 *   单栏流）/ 后台视角（左列主播 Agent 看到的、右列游戏 Agent 看到的，两列
 *   都是卡片，按流程单环节分节、可整节折叠）/ 决策轨迹（一次请求一条记录，
 *   行内点开右侧详情）。三档只换组织方式与密度，不丢数据
 * - 顶栏：连接状态、决策管线阶段徽章、模拟器模式徽章
 *
 * 干预输入区（时间线底部）：页级目标选择（主播 / minecraft）+ 共享输入条。
 * 主播目标三模式 = 场控的三种操作——注入弹幕（debug/inject-message，与真实弹幕
 * 同链路）、强制回应（streamer/test-decision，直驱决策、结果以决策卡落进时间线，
 * 留空=自由发挥）、幕后提醒（agents/{name}/prompt，运营递话：文字必达进下个
 * 决策参考块，并顺带敲门催醒主播提前决策）；minecraft 目标递话/委派走 agents 域
 * 接口。目标→模式→传输的单一事实源在 useAgentIntervention。
 *
 * 决策轨迹的块头点击深链：跳 Agent 页对应决策轮（/agents?agent=&round=，
 * 契约见 useAgentDeepLink）。
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
 * 三档视图（顶栏切换）是同一批条目的三种投影：观众视角=这场直播观众看到的内容
 * （发言、弹幕、礼物、场次边界、公开游戏聊天；口径见 liveFeed.AUDIENCE_KINDS，
 * 卡上的 LLM 统计胶囊由 FeedTimeline simple 档隐藏）；后台视角=两个 Agent 各自
 * 看到的世界（双栏、各自滚动、点卡片让对侧跳到同一刻）；决策轨迹=一次 LLM 请求
 * 一条记录，右侧详情栏看单行完整内容。运营操作件（开播/下播、干预输入条、
 * 任务条、暂停清空）常驻，不再有独立的「完整/简洁」档位开关。
 *
 * 页面私有逻辑按块拆在 composables/live/：场次侧边栏（useLiveSessions）、
 * 思考流（useThinkingStream）、时间线内容合成（useLiveTimeline）、顶栏/环节
 * 徽章（useLiveStatus）、干预输入条（useInterventionBar）、滚动跟随的页面侧
 * 组装（useTimelineScroll）。全局共享态（events 流、WS 连接）仍在 Pinia store。
 * 本组件只做装配与跨块的少量编排（清空时间线联动未读计数）。
 */
import {
  computed,
  nextTick,
  onActivated,
  onDeactivated,
  onMounted,
  onUnmounted,
  ref,
  watch,
} from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { storeToRefs } from 'pinia';
import { Loading, QuestionFilled } from '@element-plus/icons-vue';
import { useWebSocketStore, useEventsStore } from '@/stores';
import { EVENT } from '@/types/events';
import InterventionInput from '@/components/dashboard/InterventionInput.vue';
import StartStreamDialog from '@/components/live/StartStreamDialog.vue';
import { useNowTick } from '@/composables/useNowTick';
import { useLiveSessions } from '@/composables/live/useLiveSessions';
import {
  SESSION_FILTER_ALL,
  SESSION_SOURCE_OPTIONS,
  SESSION_KIND_OPTIONS,
  kindLabel,
  sessionDurationLabel,
  sourceLabel,
} from '@/utils/sessionManager';
import { useThinkingStream } from '@/composables/live/useThinkingStream';
import { useLiveTimeline } from '@/composables/live/useLiveTimeline';
import { useLiveStatus } from '@/composables/live/useLiveStatus';
import { useRundownConsole } from '@/composables/live/useRundownConsole';
import {
  MINECRAFT_AGENT_NAME,
  STREAMER_AGENT_NAME,
  useAgentIntervention,
} from '@/composables/useAgentIntervention';
import { agentRoundLocation } from '@/composables/useAgentDeepLink';
import { useAgentLead } from '@/composables/useAgentLead';
import { taskStatusText, useAgentTasks } from '@/composables/useAgentTasks';
import { useTimelineScroll } from '@/composables/live/useTimelineScroll';
import { useTimelineAnchor } from '@/composables/live/useTimelineAnchor';
import { useTimelineReplay } from '@/composables/live/useTimelineReplay';
import { useLiveSearch } from '@/composables/live/useLiveSearch';
import ReplayControlBar from '@/components/live/ReplayControlBar.vue';
import LiveSearchBar from '@/components/live/LiveSearchBar.vue';
import { confirmAction } from '@/utils/confirmAction';
import { ElMessage } from 'element-plus';
import { formatNumber } from '@/utils/format';
import { waitFor } from '@/utils/waitFor';
import { MAX_ENTRIES, type ShowEntry } from '@/utils/liveFeed';
import type { LiveSessionItem, RundownControlAction } from '@/types';
import LiveLanes from '@/components/live/LiveLanes.vue';
import RequestDetail from '@/components/live/RequestDetail.vue';
import RequestTimeline from '@/components/live/RequestTimeline.vue';
import { buildRequestRecords } from '@/utils/requestRecords';
import { buildTaskWindows, laneOf, taskFactOf } from '@/utils/liveLanes';
import { isAudienceEntry } from '@/utils/liveFeed';
import type { SearchHit } from '@/utils/liveSearch';
import type { RequestMessage, RequestRecord } from '@/utils/requestRecords';
import type { TaskFact, TaskWindow } from '@/utils/liveLanes';
import AgentLeadControl from '@/components/agents/AgentLeadControl.vue';
import AgentMemoPanel from '@/components/live/AgentMemoPanel.vue';
import RundownSlate from '@/components/live/RundownSlate.vue';
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
  activeSessionId,
  activeExplicitSession,
  showTestModeNotice,
  sessionQuery,
  sessionSourceFilter,
  sessionKindFilter,
  sessionMode,
  selectedSession,
  sessionTitle,
  sessionTimeLabel,
  isSelected,
  loadSessions,
  startStreamVisible,
  openStartStream,
  onStreamOpened,
  closeSession,
  selectSession,
  backToLive,
} = useLiveSessions();

const { liveThinkingRows, thinkingHiddenBeforeMs } = useThinkingStream();

const {
  paused,
  agentFilter,
  entries,
  allEntries,
  togglePause,
  clearTimeline: clearTimelineEntries,
  replayHasMore,
  replayPruned,
  replayLoadingMore,
  replayRawItems,
  replaySegmentBoundaries,
  loadOlderReplay,
} = useLiveTimeline({
  liveThinkingRows,
  thinkingHiddenBeforeMs,
  sessionMode,
  selectedSession,
});

/**
 * 双轨分节的环节边界：回看时按场次查流程单执行事实（环节名 + 起止时刻），
 * 用来判断窗口起点所在环节；实时模式没有这个信息渠道，不传。
 */
const laneBoundaries = computed(() =>
  sessionMode.value === 'replay' ? replaySegmentBoundaries.value : undefined,
);

/**
 * 时间线三档视图，是同一份条目流的三种投影：
 * - 观众视角：只留观众看得见的内容（弹幕、礼物、她的发言、游戏侧公开进展），单栏
 * - 后台视角：左列主播 Agent 看到的、右列游戏 Agent 看到的，两列都是卡片
 * - 决策轨迹：一次请求一条记录，输入/思考/调用/结果/发言各一行，点行看详情
 */
const stageView = ref<'audience' | 'backstage' | 'trajectory'>('audience');

/**
 * 游戏侧任务区间：`task.changed` 的受理与终态各一条，配成"这次工具调用从几点到几点"。
 * 实时读事件缓冲，回看读归档条目（两边都是同一形状的载荷）。
 */
const taskFacts = computed<TaskFact[]>(() => {
  // 看历史场次时条目来自归档，事件缓冲里没有那场的数据——按会话模式取源，不按回放调试取源
  if (sessionMode.value !== 'live') {
    return replayRawItems.value
      .filter(item => item.event_type === EVENT.taskChanged && item.data)
      .map(item => taskFactOf(item.data as Record<string, unknown>, item.ts_ms));
  }
  return eventsStore.events
    .filter(event => event.type === EVENT.taskChanged)
    .map(event => taskFactOf(event.data, event.timestamp_ms));
});

const taskWindows = computed<TaskWindow[]>(() => buildTaskWindows(taskFacts.value));

/** 决策轨迹的记录：从同一份条目流折算，卡片视角将来复用同一份结构 */
const requestRecords = computed(() => buildRequestRecords(allEntries.value));

/**
 * 轨迹行的详情面板。点同一行是开关，点另一行直接换内容——
 * 面板在列表右侧而不是行下，切换查看时列表位置不动。
 */
const selectedMessage = ref<RequestMessage | null>(null);
const selectedRecord = ref<RequestRecord | null>(null);

function openMessage(record: RequestRecord, message: RequestMessage): void {
  if (selectedMessage.value?.id === message.id) {
    closeMessage();
    return;
  }
  selectedRecord.value = record;
  selectedMessage.value = message;
}

function closeMessage(): void {
  selectedMessage.value = null;
  selectedRecord.value = null;
}

/** 观众视角：只留观众看得见的内容（弹幕/礼物/她的发言/游戏公开进展/环节切换） */
const audienceEntries = computed(() => displayEntries.value.filter(isAudienceEntry));

const audienceEmptyText = computed<string>(() =>
  sessionMode.value === 'replay'
    ? '该场次没有观众可见的内容'
    : '还没有观众可见的消息——弹幕、礼物与她的发言会出现在这里',
);

/** 区头标题：回放/回看带场次名，实况态不再复读档位名（视图切换组已显示选中档） */
const stageTitle = computed<string>(() => {
  if (replayActive.value) return `回放 · ${sessionTitle(replaySession.value)}`;
  if (sessionMode.value === 'live') return '实况';
  return `回看 · ${sessionTitle(selectedSession.value)}`;
});

/** 区头提示：每档说清自己在讲什么；暂停与数据清理的提示照旧 */
const stageHint = computed<string>(() => {
  if (replayActive.value) {
    // 行一只说"与实况有什么不同"：逐条播放历史归档、实时仍在跑、归档可能不完整。
    // 游标时刻与条数在回放控制条里，不在这里重复
    const parts = [`逐条播放历史归档，实时仍在进行（已缓冲 ${replayBufferedCount.value} 条）`];
    if (replayArchivePruned.value)
      parts.push('部分时段的事件诊断数据已清理，对应区间仅显示消息明细');
    if (replayArchiveTruncated.value) parts.push('本场事件数超过上限，仅回放最近一段');
    return parts.join(' · ');
  }
  if (stageView.value === 'trajectory')
    return '一次请求一条记录：输入、思考、调用、结果、发言各占一行；点任意一行看详情';
  if (stageView.value === 'backstage')
    return '左列主播 Agent 看到的、右列游戏 Agent 看到的，两列各自滚动';
  if (sessionMode.value === 'live') {
    return paused.value
      ? '已暂停 · 事件仍在后台累积'
      : '观众在直播间里看到的：弹幕、礼物、她的发言';
  }
  return replayPruned.value
    ? '部分时段的事件诊断数据已清理，对应区间仅显示消息明细'
    : '该场次观众看到的内容：弹幕、礼物、她的发言、游戏侧公开进展';
});

const { stageChip, simulatorChip, loadSimulatorStatus } = useLiveStatus();

/**
 * 环节横幅的数据源：按场次取流程单事实——实时模式看进行中的场次，回看
 * 模式看正在回看的那场（事件流不带场次范围，按事件取会对错场）。
 */
const rundownSessionId = computed<number | null>(() =>
  sessionMode.value === 'replay'
    ? (selectedSession.value?.live_session_id ?? null)
    : activeSessionId.value,
);

const {
  rows: rundownRows,
  currentRow: rundownCurrentRow,
  paused: rundownPaused,
  actionLoading: rundownActionLoading,
  performControl: performRundownControl,
} = useRundownConsole({
  sessionId: rundownSessionId,
  live: computed(() => sessionMode.value === 'live'),
});

/** 无流程单时的横幅占位文案：实时与回看的说法不同 */
const rundownIdleText = computed(() =>
  sessionMode.value === 'replay' ? '该场次没有流程单记录' : '流程单未运行或未接入',
);

/** 横幅操作件：下一环节 / 暂停恢复 / 跳转 / 插话题都走后端控制口 */
function onRundownControl(
  action: RundownControlAction,
  extra?: { segment_id?: string; payload?: { title: string; content?: string } },
): void {
  void performRundownControl(action, extra);
}

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
  pace: replayPace,
  speed: replaySpeed,
  active: replayActive,
  cursor: replayCursor,
  cursorTsMs: replayCursorTs,
  archiveStartMs: replayArchiveStartMs,
  archiveEndMs: replayArchiveEndMs,
  pruned: replayArchivePruned,
  truncated: replayArchiveTruncated,
  visibleEntries: replayVisibleEntries,
  totalCount: replayTotalCount,
  start: startReplay,
  exit: exitReplayState,
  togglePlay: replayTogglePlay,
  pause: replayPause,
  stepForward: replayStepForward,
  stepBack: replayStepBack,
  setPace: replaySetPace,
  setSpeed: replaySetSpeed,
  jumpToTime: replayJumpToTime,
} = useTimelineReplay({ agentFilter });

/**
 * 进入回放（入口在左栏场次卡上）：整条时间线切到归档渲染，从第一条开始（暂停态）。
 * 一并选中该场次，退出回放后落在这场直播的回看视图，而不是跳回实时流。
 * 场次没有归档内容时状态机内部会提示并复位，这里不重复处理。
 */
async function enterReplay(item: LiveSessionItem): Promise<void> {
  if (item.is_active) return;
  selectSession(item);
  await startReplay(item);
}

/** 回放期间到达的实时事件数：进入回放时快照事件缓冲长度，退出时差值即错过量
 *  （events store 全程缓冲不丢，退出回放不需要补拉请求） */
const replayMissedSnapshot = ref(0);
watch(replayActive, active => {
  if (active) replayMissedSnapshot.value = eventsStore.events.length;
});

/** 回放期间已缓冲的实时事件数：回放中就看得见，不必等退出时才告知 */
const replayBufferedCount = computed<number>(() =>
  replayActive.value ? Math.max(0, eventsStore.events.length - replayMissedSnapshot.value) : 0,
);

/** 回放态切换的读屏播报（界面另有整条着色，这里只把状态变化念出来） */
const replayAnnouncement = ref('');
watch(replayActive, active => {
  replayAnnouncement.value = active
    ? `进入回放：${sessionTitle(replaySession.value)}`
    : '已退出回放';
});

/**
 * 退出回放：确认后离开回放态，落到退出前所在的模式（从场次卡进回放时落在那场
 * 的回看视图，从深链进回放时落在实时流）；有错过条数时如实说明它们在哪。
 */
async function exitReplay(): Promise<void> {
  if (!replayActive.value) return;
  const missed = replayBufferedCount.value;
  const landsIn = sessionMode.value === 'replay' ? '这场直播的回看视图' : '实时流';
  const confirmed = await confirmAction(
    missed > 0
      ? `退出回放？将回到${landsIn}。回放期间到达的 ${missed} 条实时事件已缓存在后台，切回实时流后可见。`
      : `退出回放？将回到${landsIn}。`,
    '退出回放',
  );
  if (!confirmed) return;
  exitReplayState();
  if (missed > 0) {
    ElMessage.info(`已退出回放，缓冲中的 ${missed} 条实时事件在切回实时流后可见`);
  }
}

/** 喂给时间线的展示条目：回放调试=归档前缀，其余模式=实时/回看缓冲；
 *  内容投影由上方三档视图各自决定（观众口径在 liveFeed.AUDIENCE_KINDS） */
const displayEntries = computed<ShowEntry[]>(() =>
  replayActive.value ? replayVisibleEntries.value : entries.value,
);

const timelineEmptyText = computed(() => {
  if (replayActive.value) return '该场次归档暂无条目';
  if (sessionMode.value === 'live') return '静候消息与决策——注入一条弹幕试试';
  return '该场次暂无可回看条目';
});

const {
  scrollRef,
  unseen,
  onScroll,
  jumpToLatest: jumpToLatestOuter,
  resetUnseen,
} = useTimelineScroll(displayEntries);

/**
 * "回到最新"按钮：后台视角滚的是双轨两列（这一档外层容器不滚），
 * 其余视角滚外层时间线。待看条数同理分两处取——后台视角看两列各自
 * 落下的内容，其余视角看外层流的未读计数。
 */
const lanesRef = ref<InstanceType<typeof LiveLanes> | null>(null);

const newContentCount = computed<number>(() =>
  stageView.value === 'backstage' ? (lanesRef.value?.pendingCount ?? 0) : unseen.value,
);

const showJumpToLatest = computed<boolean>(
  () => sessionMode.value === 'live' && newContentCount.value > 0,
);

const jumpToLatestLabel = computed<string>(() => {
  const count = newContentCount.value >= 99 ? '99+' : String(newContentCount.value);
  return `${count} 条新内容 · 回到最新 ↓`;
});

function jumpToLatest(): void {
  if (stageView.value === 'backstage') {
    lanesRef.value?.scrollToLatest();
    return;
  }
  jumpToLatestOuter();
}

// 回看时刻锚定：复盘页指标卡深链（/live?session=&at=<ms>）跳入时自动向前
// 翻页到目标时刻，滚动定位并短暂高亮（翻页上限与降级提示在组合式内）。
// anchoredId 同时供搜索的时刻跳转复用（同一套滚动+高亮机制）
const { anchorAt, anchoredId: timelineAnchoredId } = useTimelineAnchor({
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

// ---------------------------------------------------------------------------
// 时间线内容搜索：整场检索（落库明细 + 事件载荷），命中清单跳到那一刻。
// 检索源随档位走（观众口径 / 全部条目 / 已加载的请求记录）；主时间线只加
// 命中竖条，不隐藏不重排——"跳到那一刻"才是搜索的出口
// ---------------------------------------------------------------------------

const SEARCH_ANCHOR_MS = 4_000;

const searchEntries = computed<ShowEntry[]>(() =>
  stageView.value === 'audience' ? audienceEntries.value : allEntries.value,
);

/** 可检索的场次主键：实时档=进行中场次（整场落库内容）；回看=所选场次；
 *  回放调试不显示搜索条（该档为 null，检索源自然落回已加载窗口） */
const searchSessionId = computed<number | null>(() => {
  if (replayActive.value) return replaySession.value?.live_session_id ?? null;
  if (sessionMode.value === 'replay') return selectedSession.value?.live_session_id ?? null;
  return activeSessionId.value;
});

/** 搜索跳到的本地条目高亮（数秒后熄灭）；时刻跳转的高亮由 useTimelineAnchor 承担 */
const searchAnchorId = ref<string | null>(null);
let searchAnchorTimer: ReturnType<typeof setTimeout> | null = null;

function flashSearchAnchor(entryId: string): void {
  searchAnchorId.value = entryId;
  if (searchAnchorTimer) clearTimeout(searchAnchorTimer);
  searchAnchorTimer = setTimeout(() => {
    searchAnchorId.value = null;
  }, SEARCH_ANCHOR_MS);
}

/** 按档位跳到一条命中：观众=滚动+高亮；后台=定位到那张卡；轨迹=打开详情 */
async function onSearchJump(hit: SearchHit): Promise<void> {
  if (stageView.value === 'trajectory') {
    if (hit.record && hit.message) openMessage(hit.record, hit.message);
    return;
  }
  if (stageView.value === 'backstage') {
    if (hit.entry) lanesRef.value?.focusEntry(hit.entry.id, laneOf(hit.entry));
    else lanesRef.value?.revealNearest(hit.tsMs);
    return;
  }
  if (hit.entry) {
    flashSearchAnchor(hit.entry.id);
    await nextTick();
    scrollRef.value
      ?.querySelector(`[data-entry-id="${CSS.escape(hit.entry.id)}"]`)
      ?.scrollIntoView({ block: 'center', behavior: 'smooth' });
    return;
  }
  // 服务端命中没有本地条目可定位：按时刻锚定（滚动+高亮，与回看深链 ?at= 同一套机制）
  await anchorAt(hit.tsMs);
}

const {
  query: searchQuery,
  filteredHits: searchHits,
  categoryCounts: searchCategoryCounts,
  activeCategory: searchActiveCategory,
  setCategory: searchSetCategory,
  index: searchIndex,
  loading: searchLoading,
  scopeNote: searchScopeNote,
  expanded: searchExpanded,
  hasMore: searchHasMore,
  matchedTimestamps: searchMatchedTs,
  goTo: searchGoTo,
  next: searchNext,
  previous: searchPrevious,
  clear: searchClear,
} = useLiveSearch({
  view: stageView,
  entries: searchEntries,
  records: requestRecords,
  sessionId: searchSessionId,
  jump: onSearchJump,
});

/** 决策轨迹档的命中是请求记录粒度，没有五桶分类——chips 不传，清单自然不渲染 */
const searchBarCategories = computed(() =>
  stageView.value === 'trajectory' ? [] : searchCategoryCounts.value,
);

/** Enter：跳到当前选中的命中 */
function onSearchConfirm(): void {
  if (searchIndex.value >= 0) void searchGoTo(searchIndex.value);
}

/** 清单行点击：跳到那一条 */
function onSearchSelect(index: number): void {
  void searchGoTo(index);
}

/** 鼠标移过清单行：只改选中不跳转（跟随命令面板的既有交互） */
function onSearchHover(index: number): void {
  searchIndex.value = index;
}

const searchBarRef = ref<InstanceType<typeof LiveSearchBar> | null>(null);

const sendBarRef = ref<InstanceType<typeof InterventionInput> | null>(null);

// 干预发话：目标二选一（默认主播），模式集合与传输随目标切换——
// 单一事实源在 useAgentIntervention，主播目标复用既有行为（昵称插槽、在途 chip）。
// 这里与 AgentLeadControl 面板各持一个 useAgentLead 实例：开销只是单次 GET + 一条
// WS 订阅，生命周期由组合式自身随激活态启停，互不干扰
const router = useRouter();
const route = useRoute();
const interventionTarget = ref<string>(STREAMER_AGENT_NAME);
const leadState = useAgentLead();
const {
  activeMode,
  injectNickname,
  forcePending,
  onModeChange,
  modesForTarget,
  sendToTarget,
  sending,
} = useAgentIntervention({
  settle: async () => sendBarRef.value?.settle(),
  isGameLead: leadState.isGameLead,
});

const interventionModes = computed(() => modesForTarget(interventionTarget.value));

/** 游戏主控态下给主播递话的提示：后端会把运营递话静默转发一份非强杀建议给主控游戏 */
const streamerNudgeHint = computed(() =>
  leadState.isGameLead.value
    ? `当前由 ${leadState.leadState.value?.lead_agent || 'minecraft'} 主控，此提醒会同时转达给它`
    : '',
);

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

/** 卡片流行点击：带轮次 ID 的行跳 Agent 页对应决策轮 */
function onTimelineRoundClick(entry: ShowEntry): void {
  jumpToRoundId(entry.roundId);
}

/** 轨迹块头点入 Agent 页的对应决策轮 */
function jumpToRoundId(roundId: string): void {
  if (!roundId) return;
  void router.push(agentRoundLocation(STREAMER_AGENT_NAME, roundId));
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

// 键盘快捷键：直播中双手常不在鼠标上，最小集覆盖暂停与回实时。
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
  // 搜索：/ 聚焦输入框；F3 / Shift+F3 下一条 / 上一条命中（回放调试档不参与）
  if (e.key === '/') {
    e.preventDefault();
    searchBarRef.value?.focus();
    return;
  }
  if (e.key === 'F3' && !replayActive.value) {
    e.preventDefault();
    if (e.shiftKey) void searchPrevious();
    else void searchNext();
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
  // 游标时刻必须在 await 之前取：startReplay 载入归档期间，URL 回写钩子见游标
  // 时刻为空会把 rpt 从地址栏删掉，等 await 返回再读就读不到了
  const ts = Number(route.query[REPLAY_QUERY_CURSOR]);
  await startReplay(target);
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

/* 筛选区：搜索独占一行，两个下拉框并排一行 */
.sessions-filters {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px 10px;
  border-bottom: 1px solid var(--border-color-light);
  flex-shrink: 0;
}

.sessions-search {
  width: 100%;
}

.sessions-filter-row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.sessions-source,
.sessions-kind {
  flex: 1;
  min-width: 0;
}

/* 下拉框常驻前缀（来源 / 类型）：不选值时也说明这个框筛的是什么 */
.filter-cap {
  font-size: 11px;
  color: var(--text-placeholder);
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
}

/* 标题按钮：承载回看入口的键盘可达性，外观仍是原来的标题文字 */
.session-open {
  flex: 0 1 auto;
  min-width: 0;
  padding: 0;
  border: none;
  background: none;
  font: inherit;
  color: inherit;
  text-align: left;
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.session-open:focus-visible {
  outline: 2px solid var(--color-primary);
  outline-offset: 2px;
  border-radius: 2px;
}

/* 回放按钮：默认安静，悬停或聚焦才提亮 */
.session-replay {
  flex-shrink: 0;
  padding: 0 6px;
  border: 1px solid var(--border-color-dark);
  border-radius: var(--radius-sm);
  background: var(--bg-card);
  color: var(--text-secondary);
  font-family: inherit;
  font-size: 11px;
  line-height: 16px;
  cursor: pointer;
}

.session-replay:hover,
.session-replay:focus-visible {
  border-color: var(--color-primary);
  color: var(--color-primary);
}

.session-replay:focus-visible {
  outline: 2px solid var(--color-primary);
  outline-offset: 2px;
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
  flex-direction: column;
  gap: 2px;
  margin-top: 3px;
  font-size: 11px;
  color: var(--text-secondary);
}

/* 时长与消息数同一行（消息数右对齐）；起止时刻在其上方独占一行 */
.session-stats {
  display: flex;
  align-items: center;
  gap: 6px;
}

.session-time {
  min-width: 0;
}
.session-duration {
  flex-shrink: 0;
  white-space: nowrap;
  font-weight: 700;
  color: var(--text-secondary);
}
/* 进行中的场次：时长随时钟走，用醒目色标出"还在播" */
.session-duration.is-running {
  color: var(--color-danger);
}
.msg-count {
  flex-shrink: 0;
  white-space: nowrap;
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

/* 测试模式警示 chip：琥珀配色与断线横幅一致，一眼可辨"现在没在录制" */
.test-mode-chip {
  padding: 2px 10px;
  border-radius: 999px;
  background: var(--color-warning-bg);
  font-size: 12px;
  color: var(--color-warning);
  cursor: help;
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

/* 回放态：整条工具栏着色，一眼可辨当前看到的是历史归档而不是实况 */
.stage-bar.is-replay {
  background: var(--color-primary-bg);
  box-shadow: inset 3px 0 0 var(--color-primary);
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
/* 游戏主控态的递话转达提示：跟随目标行的弱化小字（与 label 同层） */
.input-target-hint {
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
  display: flex;
  min-width: 0;
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

/* 后台视角：两列各自滚动，外层不再承担滚动，让出全部高度给双轨 */
.stage-scroll.is-lanes {
  display: flex;
  flex-direction: column;
  overflow: hidden;
  padding: 0;
}

.stage-scroll {
  flex: 1;
  min-width: 0;
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

/* 回放控制条独占一行：标题与提示在上行，走带控制在下行 */
.replay-transport {
  flex-basis: 100%;
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

/* 只给读屏用：视觉隐藏但仍留在无障碍树里 */
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  margin: -1px;
  padding: 0;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
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
