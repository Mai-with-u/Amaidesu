/**
 * 直播场次侧边栏：列表 / 筛选 / 生命周期开关 / 回看模式切换。
 *
 * 页面私有状态（仅直播控制台消费），走 composable 而非 Pinia——场次列表与
 * 筛选词离开页面即失效，没有跨页共享语义。场次生命周期本身归后端
 * LiveSessionManager，这里只是控制面：选场回看 + 开启 / 结束 + 生命周期事件
 * 驱动侧边栏刷新。改名、改类型、删除这些"回头补账"的动作在场次管理页
 * （/sessions），那边是逐场处置的地方。
 */

import { computed, ref, watch } from 'vue';
import { storeToRefs } from 'pinia';
import { ElMessage } from 'element-plus';
import { useEventsStore } from '@/stores';
import { liveSessionsApi } from '@/api';
import { getApiErrorMessage } from '@/utils/apiError';
import { formatDateMD, formatTimeHM } from '@/utils/format';
import { SESSION_FILTER_ALL, type SessionSource } from '@/utils/sessionManager';
import type { LiveSessionItem, SessionKind } from '@/types';

/** 侧栏时钟/时长的时刻标签（事件→条目取值助手见 utils/liveFeed.ts） */
function clockLabel(ms: number): string {
  return formatTimeHM(ms);
}

export function useLiveSessions() {
  const { events } = storeToRefs(useEventsStore());

  const sessions = ref<LiveSessionItem[]>([]);
  /** 进行中的显式场次主键（来自 API 响应，不受侧边栏筛选影响——筛选只是视图） */
  const activeSessionId = ref<number | null>(null);
  const activeExplicitSession = computed(() => activeSessionId.value !== null);
  /** 测试模式提示：实时模式下无任何进行中的显式场次，消息仅在内存中流转、不落库 */
  const showTestModeNotice = computed(
    () => sessionMode.value === 'live' && !activeExplicitSession.value,
  );
  /** 场次筛选：标题关键字 + 来源 + 类型（服务端筛选） */
  const sessionQuery = ref('');
  const sessionSourceFilter = ref<SessionSource | typeof SESSION_FILTER_ALL>(SESSION_FILTER_ALL);
  const sessionKindFilter = ref<SessionKind | typeof SESSION_FILTER_ALL>(SESSION_FILTER_ALL);
  const sessionMode = ref<'live' | 'replay'>('live');
  const selectedSession = ref<LiveSessionItem | null>(null);

  function sessionTitle(item: LiveSessionItem | null): string {
    if (!item) return '';
    if (item.title) return item.title;
    return `场次 #${item.live_session_id}`;
  }

  /** 场次卡时间标签：卡标题默认已含日期+开始时刻，这里不再复读开始时刻 */
  function sessionTimeLabel(item: LiveSessionItem): string {
    const startClock = clockLabel(item.started_at_ms);
    if (item.ended_at_ms == null) return `${startClock} 起`;
    const sameDay =
      new Date(item.ended_at_ms).toDateString() === new Date(item.started_at_ms).toDateString();
    if (sameDay) return `– ${clockLabel(item.ended_at_ms)}`;
    const end = `${formatDateMD(item.ended_at_ms)} ${clockLabel(item.ended_at_ms)}`;
    const start = `${formatDateMD(item.started_at_ms)} ${startClock}`;
    return `${start} – ${end}`;
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
        source:
          sessionSourceFilter.value === SESSION_FILTER_ALL ? undefined : sessionSourceFilter.value,
        kind: sessionKindFilter.value === SESSION_FILTER_ALL ? undefined : sessionKindFilter.value,
        q: sessionQuery.value.trim() || undefined,
      });
      sessions.value = response.data.items;
      activeSessionId.value = response.data.active_session_id;
    } catch {
      /* 场次面不可用时侧边栏保持空态，不阻断时间线 */
    }
  }

  let sessionFilterTimer: ReturnType<typeof setTimeout> | null = null;
  watch([sessionQuery, sessionSourceFilter, sessionKindFilter], () => {
    if (sessionFilterTimer) clearTimeout(sessionFilterTimer);
    sessionFilterTimer = setTimeout(() => {
      void loadSessions();
    }, 250);
  });

  /** 开播引导弹窗显隐：开场次 + 组件意图对齐的交互在 StartStreamDialog 内 */
  const startStreamVisible = ref(false);

  function openStartStream(): void {
    startStreamVisible.value = true;
  }

  /** 开播干净成功后：回实时视图并刷新侧栏（场次事件也会触发刷新，这里保底） */
  async function onStreamOpened(): Promise<void> {
    backToLive();
    await loadSessions();
  }

  async function closeSession(): Promise<void> {
    if (activeSessionId.value == null) return;
    try {
      await liveSessionsApi.close(activeSessionId.value);
      ElMessage.success('场次已结束');
    } catch (error) {
      ElMessage.error(getApiErrorMessage(error, '结束场次失败'));
    }
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

  return {
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
  };
}
