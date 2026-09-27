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

          <!-- 干预输入条（code agent 风格，共享组件）：Tab/Shift+Tab 切模式、
               Enter 发送、↑↓ 回溯历史。模式与传输归本页，工具项走插槽；
               强制回应为后台执行：发送后立即可继续输入，在途状态由状态 chip 承载 -->
          <InterventionInput
            v-if="sessionMode === 'live'"
            ref="sendBarRef"
            class="input-bar"
            :modes="SEND_MODES"
            :sending="sending"
            @mode-change="onModeChange"
            @send="onInterventionSend"
          >
            <template #toolbar="{ onKeydown }">
              <el-input
                v-if="activeMode === 'danmaku'"
                v-model="injectNickname"
                size="small"
                class="input-nick"
                placeholder="观众昵称（可选）"
                @keydown="onKeydown"
              />
              <span v-if="forcePending > 0" class="input-status">
                <el-icon class="is-loading"><Loading /></el-icon>
                主播正在想…
              </span>
            </template>
          </InterventionInput>
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
 * 干预输入条（时间线底部，共享组件 InterventionInput）：三模式 = 场控的三种
 * 操作——注入弹幕（debug/inject-message，与真实弹幕同链路）、强制回应
 * （streamer/test-decision，直驱决策、结果以决策卡落进时间线，留空=自由发挥）、
 * 幕后提醒（agents/{name}/prompt，运营递话：文字必达进下个决策参考块，
 * 并顺带敲门催醒主播提前决策）。
 *
 * 数据来源：
 * - 实时：events store（全局 WS + 游标回填，刷新/断线不丢时间线）+ 思考流旁路
 *   （kind="stream"，仅视图层合成思考行、按时间归并进时间线，不入 store 不回看）
 * - 回看：GET /live-sessions/{id}/timeline（明细行 + 事件历史按时间合并；无思考数据）
 * 渲染字段一律取自后端真实 Payload（src/modules/events/payloads/），不臆造字段。
 *
 * 页面私有逻辑按块拆在 composables/live/：场次侧边栏（useLiveSessions）、
 * 思考流（useThinkingStream）、时间线内容合成（useLiveTimeline）、顶栏/环节
 * 徽章（useLiveStatus）、干预输入条（useInterventionBar）、滚动跟随接线
 * （useTimelineScroll）。全局共享态（events 流、WS 连接）仍在 Pinia store。
 * 本组件只做装配与跨块的少量编排（清空时间线联动未读计数）。
 */
import { computed, onMounted, ref } from 'vue';
import { storeToRefs } from 'pinia';
import { Loading } from '@element-plus/icons-vue';
import { useWebSocketStore } from '@/stores';
import InterventionInput from '@/components/dashboard/InterventionInput.vue';
import { useNowTick } from '@/composables/useNowTick';
import { useLiveSessions } from '@/composables/live/useLiveSessions';
import { useThinkingStream } from '@/composables/live/useThinkingStream';
import { useLiveTimeline } from '@/composables/live/useLiveTimeline';
import { useLiveStatus } from '@/composables/live/useLiveStatus';
import { useInterventionBar } from '@/composables/live/useInterventionBar';
import { useTimelineScroll } from '@/composables/live/useTimelineScroll';
import { MAX_ENTRIES, relativeTime } from '@/utils/liveFeed';
import FeedTimeline from '@/components/live/FeedTimeline.vue';

// 装配：场次侧边栏 → 思考流 → 时间线内容 → 滚动跟随 → 干预输入条
// （依赖沿参数单向流动：时间线消费场次模式与思考行，滚动跟随消费展示条目）

const wsStore = useWebSocketStore();
const { isConnected: wsConnected } = storeToRefs(wsStore);

const {
  sessions,
  activeExplicitSession,
  showTestModeNotice,
  sessionQuery,
  sessionSourceFilter,
  sessionMode,
  selectedSession,
  sessionTitle,
  sourceLabel,
  sessionTimeLabel,
  isSelected,
  loadSessions,
  openSession,
  closeSession,
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
} = useLiveTimeline({
  liveThinkingRows,
  thinkingHiddenBeforeMs,
  sessionMode,
  selectedSession,
});

const { rundownBanner, stageChip, simulatorChip, loadSimulatorStatus } = useLiveStatus();

const { scrollRef, unseen, onScroll, jumpToLatest, resetUnseen } = useTimelineScroll(entries);

const sendBarRef = ref<InstanceType<typeof InterventionInput> | null>(null);
const {
  SEND_MODES,
  activeMode,
  sending,
  injectNickname,
  forcePending,
  onModeChange,
  onInterventionSend,
} = useInterventionBar({ settle: async () => sendBarRef.value?.settle() });

/** 清空时间线：条目与思考行水位归时间线块，未读计数归滚动块 */
function clearTimeline(): void {
  clearTimelineEntries();
  resetUnseen();
}

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

onMounted(() => {
  void loadSessions();
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

/* 干预输入条容器：卡片底衬与内边距；输入交互样式在共享组件内 */
.input-bar {
  flex-shrink: 0;
  padding: 8px var(--spacing-md) 10px;
  background: var(--bg-card);
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
  font-size: 10px;
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
