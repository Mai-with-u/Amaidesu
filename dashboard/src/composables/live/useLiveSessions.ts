/**
 * 直播场次侧边栏：列表 / 筛选 / 生命周期开关 / 回看模式切换。
 *
 * 页面私有状态（仅直播控制台消费），走 composable 而非 Pinia——场次列表与
 * 筛选词离开页面即失效，没有跨页共享语义。场次生命周期本身归后端
 * LiveSessionManager，这里只是控制面：开启 / 结束 / 重命名 / 更正类型 / 删除 +
 * 生命周期事件驱动侧边栏刷新。
 */

import { computed, ref, watch } from 'vue';
import { storeToRefs } from 'pinia';
import { ElMessage, ElMessageBox } from 'element-plus';
import { useEventsStore } from '@/stores';
import { useNowTick } from '@/composables/useNowTick';
import { liveSessionsApi } from '@/api';
import { getApiErrorMessage } from '@/utils/apiError';
import { formatDateMD, formatTimeHM, formatElapsedMs } from '@/utils/format';
import type { LiveSessionItem, SessionKind } from '@/types';

const SOURCE_LABEL: Record<string, string> = {
  manual: '手动',
  replay: '回放',
  generate: '模拟',
  legacy: '历史',
};

const KIND_LABEL: Record<SessionKind, string> = {
  live: '正式',
  rehearsal: '彩排',
};

/** 场次默认标题（yyyy-MM-dd 周X HH:mm，按给定时刻）：开场次预填与未命名场次重命名预填共用 */
export function defaultSessionTitle(at: Date = new Date()): string {
  const pad = (n: number): string => String(n).padStart(2, '0');
  const weekday = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][at.getDay()] ?? '';
  return `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())} ${weekday} ${pad(at.getHours())}:${pad(at.getMinutes())}`;
}

/** 侧栏时钟/时长的时刻标签（事件→条目取值助手见 utils/liveFeed.ts） */
function clockLabel(ms: number): string {
  return formatTimeHM(ms);
}

export function useLiveSessions() {
  const { events } = storeToRefs(useEventsStore());
  // 共享 1s 时钟：驱动进行中场次"已播"标签逐秒刷新
  const nowTick = useNowTick();

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
  const sessionSourceFilter = ref('');
  const sessionKindFilter = ref<SessionKind | ''>('');
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

  function kindLabel(kind: SessionKind): string {
    return KIND_LABEL[kind] ?? kind;
  }

  function sessionTimeLabel(item: LiveSessionItem): string {
    const start = `${formatDateMD(item.started_at_ms)} ${clockLabel(item.started_at_ms)}`;
    if (item.ended_at_ms == null) return `${start} 起`;
    const sameDay =
      new Date(item.ended_at_ms).toDateString() === new Date(item.started_at_ms).toDateString();
    const end = sameDay
      ? clockLabel(item.ended_at_ms)
      : `${formatDateMD(item.ended_at_ms)} ${clockLabel(item.ended_at_ms)}`;
    return `${start} – ${end}`;
  }

  function isSelected(item: LiveSessionItem): boolean {
    if (sessionMode.value === 'live') {
      return item.is_active;
    }
    return selectedSession.value?.live_session_id === item.live_session_id;
  }

  /** 进行中场次已播时长标签（随共享时钟逐秒刷新；非进行中场次不显示） */
  function elapsedLabel(item: LiveSessionItem): string | null {
    if (!item.is_active) return null;
    return `已播 ${formatElapsedMs(nowTick.value - item.started_at_ms)}`;
  }

  async function loadSessions(): Promise<void> {
    try {
      const response = await liveSessionsApi.list({
        source: sessionSourceFilter.value || undefined,
        kind: sessionKindFilter.value || undefined,
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

  async function removeSession(item: LiveSessionItem): Promise<void> {
    try {
      await liveSessionsApi.remove(item.live_session_id);
      ElMessage.success('场次已删除');
    } catch (error) {
      ElMessage.error(getApiErrorMessage(error, '删除失败'));
    }
    if (selectedSession.value?.live_session_id === item.live_session_id) backToLive();
    await loadSessions();
  }

  /** 重命名场次：预填当前标题（未命名回落开播时刻默认名），保存后刷新并同步回看选中引用 */
  async function renameSession(item: LiveSessionItem): Promise<void> {
    let title: string | undefined;
    try {
      const { value } = await ElMessageBox.prompt('场次标题（留空恢复未命名）', '重命名场次', {
        confirmButtonText: '保存',
        cancelButtonText: '取消',
        inputValue: item.title ?? defaultSessionTitle(new Date(item.started_at_ms)),
      });
      title = value?.trim() || undefined;
    } catch {
      return; // 取消输入
    }
    try {
      await liveSessionsApi.rename(item.live_session_id, title);
      ElMessage.success('场次已重命名');
    } catch (error) {
      ElMessage.error(getApiErrorMessage(error, '重命名失败'));
      return;
    }
    await loadSessions();
    // 回看中的该场换上新引用，回看标题跟随新名
    if (selectedSession.value?.live_session_id === item.live_session_id) {
      selectedSession.value =
        sessions.value.find(s => s.live_session_id === item.live_session_id) ?? null;
    }
  }

  /**
   * 更正已结束场次的类型（正式 ↔ 彩排）：存量回填判错时人手改。类型决定这场的
   * 回顾进哪条记忆线、要不要对外同步，改前确认一次。
   */
  async function toggleSessionKind(item: LiveSessionItem): Promise<void> {
    const next: SessionKind = item.kind === 'live' ? 'rehearsal' : 'live';
    const hint =
      next === 'live'
        ? '改为正式后，这场的回顾会进正式直播的记忆线（下次正式开播可能被当成上一场）。'
        : '改为彩排后，这场的回顾只在彩排时被读到，正式开播不再把它当成上一场。';
    try {
      await ElMessageBox.confirm(hint, `改为${KIND_LABEL[next]}`, {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
      });
    } catch {
      return; // 取消
    }
    try {
      await liveSessionsApi.setKind(item.live_session_id, next);
      ElMessage.success(`场次已改为${KIND_LABEL[next]}`);
    } catch (error) {
      ElMessage.error(getApiErrorMessage(error, '修改类型失败'));
      return;
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
  };
}
