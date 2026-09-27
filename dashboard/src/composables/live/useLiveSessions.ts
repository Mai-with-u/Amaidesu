/**
 * 直播场次侧边栏：列表 / 筛选 / 生命周期开关 / 回看模式切换。
 *
 * 页面私有状态（仅直播控制台消费），走 composable 而非 Pinia——场次列表与
 * 筛选词离开页面即失效，没有跨页共享语义。场次生命周期本身归后端
 * LiveSessionManager，这里只是控制面：开启 / 结束 / 删除 + 生命周期事件
 * 驱动侧边栏刷新。
 */

import { computed, ref, watch } from 'vue';
import { storeToRefs } from 'pinia';
import { ElMessage, ElMessageBox } from 'element-plus';
import { useEventsStore } from '@/stores';
import { liveSessionsApi } from '@/api';
import type { LiveSessionItem } from '@/types';

const SOURCE_LABEL: Record<string, string> = {
  manual: '手动',
  replay: '回放',
  legacy: '历史',
};

/** 侧栏时钟/时长的时刻标签（事件→条目取值助手见 utils/liveFeed.ts） */
function clockLabel(ms: number): string {
  return new Date(ms).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
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
  };
}
