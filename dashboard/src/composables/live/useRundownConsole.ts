/**
 * 直播控制台"当前环节"横幅的数据源：流程单执行事实 + 实时快照。
 *
 * 两种模式同源：
 * - 实时（live=true）：场次 id 取进行中的显式场次，另拉 /rundown/state 拿
 *   当前环节的实时已播时长与提纲要点（续期后以快照为准）；
 * - 回看（live=false）：场次 id 取正在回看的场次，只要执行事实即可——
 *   尾段已封口，停留时长是定值。
 *
 * 生命周期语义：本组合式随直播控制台页面存在，页面在 App.vue 的 keep-alive
 * 内——隐藏不卸载。WS 订阅与在途防抖按激活态启停（onActivated 重启并静默
 * 重拉，onDeactivated 停止），本地秒级时钟由 useNowTick 自行按激活态启停。
 */
import {
  computed,
  onActivated,
  onDeactivated,
  onMounted,
  onScopeDispose,
  ref,
  watch,
  type Ref,
} from 'vue';
import { ElMessage } from 'element-plus';
import { liveSessionsApi, rundownApi } from '@/api';
import { wsClient } from '@/api/websocket';
import { getApiErrorMessage } from '@/utils/apiError';
import { buildRundownRows, type RundownConsoleRow } from '@/utils/rundownConsole';
import { useNowTick } from '@/composables/useNowTick';
import type {
  RundownControlAction,
  RundownControlResponse,
  RundownStateResponse,
  SessionRundownExecutionResponse,
} from '@/types';

/** 变更事件触发的重拉防抖窗口（毫秒）——避免事件风暴期间反复拉取 */
const RELOAD_DEBOUNCE_MS = 300;

export interface RundownConsoleOptions {
  /** 当前关注的场次主键（实时=进行中场次；回看=正在回看的场次） */
  sessionId: Ref<number | null>;
  /** 是否实时模式（实时模式额外拉 /rundown/state 拿实时快照） */
  live: Ref<boolean>;
}

export function useRundownConsole(options: RundownConsoleOptions) {
  const execution = ref<SessionRundownExecutionResponse | null>(null);
  const liveState = ref<RundownStateResponse | null>(null);
  const loading = ref(false);
  const loadError = ref<string | null>(null);
  const actionLoading = ref<RundownControlAction | null>(null);

  const nowTick = useNowTick();
  /** 快照取回时刻——已播时长从该时刻起算，吸收取数耗时造成的漂移 */
  const snapshotBaselineMs = ref(Date.now());

  async function fetchExecution(sessionId: number): Promise<void> {
    const response = await liveSessionsApi.rundownExecution(sessionId);
    execution.value = response.data;
  }

  async function fetchLiveState(): Promise<void> {
    try {
      const response = await rundownApi.getState();
      liveState.value = response.data;
      snapshotBaselineMs.value = Date.now();
    } catch {
      // 实时快照不可用（流程单未加载 / Agent 未起）不阻断执行事实的展示
      liveState.value = null;
    }
  }

  async function fetchAll(opts: { silent?: boolean } = {}): Promise<void> {
    const sessionId = options.sessionId.value;
    if (!opts.silent) loading.value = true;
    loadError.value = null;
    try {
      if (options.live.value) {
        // 实时态：快照（当前环节实时计时）+ 执行事实（走过的段与计划全量）
        await fetchLiveState();
        if (sessionId == null) {
          // 还没开场次：变更事实行无处落库，只剩快照与模板环节
          execution.value = null;
        } else {
          await fetchExecution(sessionId);
        }
      } else {
        execution.value = null;
        liveState.value = null;
        if (sessionId != null) await fetchExecution(sessionId);
      }
    } catch (error) {
      loadError.value = getApiErrorMessage(error, '流程单数据加载失败');
      execution.value = null;
      liveState.value = null;
    } finally {
      loading.value = false;
    }
  }

  /** 环节行：执行事实 + 计划环节合成；实时态当前段的已播时长按本地时钟续走 */
  const rows = computed<RundownConsoleRow[]>(() => {
    const list = buildRundownRows(
      execution.value,
      liveState.value?.snapshot ?? null,
      liveState.value?.segments ?? [],
    );
    const snapshot = liveState.value?.snapshot;
    if (!snapshot?.current) return list;
    const drift = nowTick.value - snapshotBaselineMs.value;
    if (drift <= 0) return list;
    return list.map(row =>
      row.state === 'current' && row.segId === snapshot.current?.id
        ? { ...row, dwellMs: row.dwellMs + drift }
        : row,
    );
  });

  const currentRow = computed<RundownConsoleRow | null>(
    () => rows.value.find(row => row.state === 'current') ?? null,
  );

  const doneCount = computed(() => rows.value.filter(row => row.state !== 'upcoming').length);
  const totalCount = computed(() => rows.value.length);

  /** 流程单运行态（实时模式才有；回看模式恒 null） */
  const statusLabel = computed<string>(() => {
    const status = liveState.value?.snapshot?.status;
    switch (status) {
      case 'running':
        return '进行中';
      case 'armed':
        return '待开播';
      case 'paused':
        return '已暂停';
      case 'done':
        return '已完成';
      default:
        return '';
    }
  });

  const paused = computed(() => liveState.value?.snapshot?.paused === true);

  // 场次或模式切换 → 换数据源
  watch([options.sessionId, options.live], () => {
    void fetchAll();
  });

  // WS 变更事件 → 防抖重拉（实时模式才有事件）
  let reloadTimer: ReturnType<typeof setTimeout> | null = null;
  let wsActive = false;

  function onWsMessage(message: { type: string }): void {
    if (!wsActive) return;
    if (message.type !== 'rundown.changed') return;
    if (reloadTimer) clearTimeout(reloadTimer);
    reloadTimer = setTimeout(() => {
      if (!wsActive) return;
      void fetchAll({ silent: true });
    }, RELOAD_DEBOUNCE_MS);
  }

  function startWs(): void {
    wsActive = true;
    wsClient.onMessage(onWsMessage);
  }

  function stopWs(): void {
    wsActive = false;
    if (reloadTimer) {
      clearTimeout(reloadTimer);
      reloadTimer = null;
    }
  }

  onMounted(() => {
    startWs();
    void fetchAll();
  });

  // keep-alive 激活态启停：首次激活的数据拉取由 onMounted 负责，再激活时
  // 重启订阅并静默重拉（隐藏期间错过的变更不可知）。
  let hasActivatedOnce = false;
  onActivated(() => {
    if (!hasActivatedOnce) {
      hasActivatedOnce = true;
      return;
    }
    startWs();
    void fetchAll({ silent: true });
  });

  onDeactivated(stopWs);
  onScopeDispose(stopWs);

  // 手动控制流程单（下一环节 / 暂停 / 恢复 / 跳转 / 插话题）
  async function performControl(
    action: RundownControlAction,
    extra: { segment_id?: string; payload?: { title: string; content?: string } } = {},
  ): Promise<RundownControlResponse | null> {
    if (actionLoading.value) return null;
    actionLoading.value = action;
    try {
      const response = await rundownApi.control({ action, ...extra });
      const data = response.data;
      if (!data.success) {
        ElMessage.error(data.message || '操作失败');
        return null;
      }
      ElMessage.success(data.message || '操作成功');
      await fetchAll({ silent: true });
      return data;
    } catch (error) {
      ElMessage.error(getApiErrorMessage(error, '操作失败'));
      return null;
    } finally {
      actionLoading.value = null;
    }
  }

  return {
    rows,
    currentRow,
    doneCount,
    totalCount,
    statusLabel,
    paused,
    loading,
    loadError,
    actionLoading,
    refresh: () => fetchAll({ silent: true }),
    performControl,
  };
}
