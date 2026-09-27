/**
 * 流程单运行态：状态拉取 / 手动控制 / WS 触发的防抖重拉 / 本地倒计时。
 *
 * 页面私有状态（仅流程单编排页消费）。数据来源：REST 轮询
 * GET /api/v1/rundown/state（手动刷新 + rundown.changed 触发的 300ms
 * 防抖重拉）+ 本地 1s tick 重算当前环节 elapsed/remaining（快照是时刻值，
 * 本地 tick 从基线起算已播时长吸收取数耗时漂移）。
 * WS 订阅与防抖计时器的清理走 onScopeDispose（setup 中等价于组件卸载）。
 */

import { computed, onMounted, onScopeDispose, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import { rundownApi } from '@/api';
import { wsClient } from '@/api/websocket';
import { useNowTick } from '@/composables/useNowTick';
import type {
  RundownControlAction,
  RundownControlResponse,
  RundownCurrentSegment,
  RundownSegmentView,
  RundownSnapshot,
  RundownStateResponse,
  RundownTransitionEntry,
  WebSocketMessage,
} from '@/types';

export function useRundownState() {
  const state = ref<RundownStateResponse | null>(null);
  const initialLoading = ref(true);
  const loadingState = ref(false);
  const loadError = ref<string | null>(null);
  const actionLoading = ref<RundownControlAction | null>(null);

  // 本地 1s tick：仅重算当前环节 elapsed/remaining 展示
  const nowTickMs = useNowTick();

  const snapshot = computed<RundownSnapshot | null>(() => state.value?.snapshot ?? null);

  const isNotLoaded = computed(() => snapshot.value?.status === 'idle');

  const statusLabel = computed(() => {
    const s = snapshot.value;
    if (!s) return '—';
    switch (s.status) {
      case 'running':
        return '进行中';
      case 'paused':
        return '已暂停';
      case 'done':
        return '已完成';
      default:
        return '未启动';
    }
  });

  const statusTagType = computed<'success' | 'warning' | 'info' | 'primary' | 'danger'>(() => {
    const s = snapshot.value;
    if (!s) return 'info';
    if (s.status === 'paused') return 'warning';
    if (s.status === 'running') return 'success';
    return 'info';
  });

  const progressPercent = computed(() => {
    const p = snapshot.value?.progress_percent;
    if (p == null || Number.isNaN(p)) return 0;
    return Math.max(0, Math.min(100, p));
  });

  const progressColor = computed(() => {
    if (snapshot.value?.status === 'done') return 'var(--color-info)';
    return 'var(--color-rundown)';
  });

  const currentSegment = computed<RundownCurrentSegment | null>(
    () => snapshot.value?.current ?? null,
  );

  /** 快照基线时刻（用于本地 tick 漂移计算） */
  const snapshotBaselineMs = ref(Date.now());

  const tickElapsedMs = computed(() => {
    const seg = currentSegment.value;
    if (!seg) return 0;
    // 后端 elapsed_ms 是快照时刻的累计；paused 时不递增
    if (snapshot.value?.paused) return Math.max(0, seg.elapsed_ms);
    const drift = nowTickMs.value - snapshotBaselineMs.value;
    return Math.max(0, Math.min(seg.expected_ms, seg.elapsed_ms + drift));
  });

  const tickRemainingMs = computed(() => {
    const seg = currentSegment.value;
    if (!seg) return 0;
    return Math.max(0, seg.expected_ms - tickElapsedMs.value);
  });

  /** 变更历史：仅展示最近 20 条，按时间倒序 */
  const historyEntries = computed<RundownTransitionEntry[]>(() => {
    const list = state.value?.transitions ?? [];
    return [...list].sort((a, b) => b.at_ms - a.at_ms).slice(0, 20);
  });

  /** 下一环节预览（无下一环节/已结束时为 null） */
  const nextSegment = computed<RundownSegmentView | null>(() => {
    const s = snapshot.value;
    if (!s || s.status === 'done') return null;
    return state.value?.segments[s.index + 1] ?? null;
  });

  /** 配置当前指向的流程单 id；空串 = 使用内置默认流程单 */
  const currentRundownId = computed(() => state.value?.config.rundown_id ?? '');

  // 数据加载

  async function fetchState(opts: { silent?: boolean } = {}): Promise<void> {
    if (!opts.silent) loadingState.value = true;
    loadError.value = null;
    try {
      const res = await rundownApi.getState();
      state.value = res.data;
      // 本地 tick 从该基准起算已播时长，吸收取数耗时造成的漂移
      snapshotBaselineMs.value = Date.now();
    } catch (e) {
      loadError.value = e instanceof Error ? e.message : '无法加载流程单状态';
      state.value = null;
    } finally {
      initialLoading.value = false;
      loadingState.value = false;
    }
  }

  function refresh(): void {
    void fetchState();
  }

  // 控制操作

  async function performControl(
    action: RundownControlAction,
    extra: { segment_id?: string } = {},
  ): Promise<RundownControlResponse['snapshot'] | null> {
    if (actionLoading.value) return null;
    actionLoading.value = action;
    try {
      const res = await rundownApi.control({ action, ...extra });
      const data = res.data;
      if (!data.success) {
        ElMessage.error(data.message || '操作失败');
        return null;
      }
      ElMessage.success(data.message || '操作成功');
      // 用响应内嵌的 snapshot 立即刷新（避免等 WS 抖动）
      if (data.snapshot && state.value) {
        state.value = { ...state.value, snapshot: data.snapshot };
        snapshotBaselineMs.value = Date.now();
      } else {
        // 控制后无 snapshot，回拉完整 state
        await fetchState({ silent: true });
      }
      return data.snapshot;
    } catch (e) {
      ElMessage.error(e instanceof Error ? e.message : '操作失败');
      return null;
    } finally {
      actionLoading.value = null;
    }
  }

  function togglePause(): void {
    const s = snapshot.value;
    if (!s) return;
    void performControl(s.paused ? 'resume' : 'pause');
  }

  function handleNext(): void {
    void performControl('next');
  }

  // WS 订阅 + 防抖重拉

  let reloadTimer: ReturnType<typeof setTimeout> | null = null;
  let wsActive = false;

  function onWsMessage(msg: WebSocketMessage): void {
    if (!wsActive) return;
    if (msg.type !== 'rundown.changed') return;
    // 300ms 防抖：避免事件风暴期间反复拉取
    if (reloadTimer) clearTimeout(reloadTimer);
    reloadTimer = setTimeout(() => {
      if (!wsActive) return;
      void fetchState({ silent: true });
    }, 300);
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
    void fetchState();
  });

  onScopeDispose(stopWs);

  // 状态切换时同步基线：snapshot 改变（如切换环节）时重置本地 tick
  watch(
    () => currentSegment.value?.id,
    () => {
      snapshotBaselineMs.value = Date.now();
    },
  );

  return {
    state,
    initialLoading,
    loadingState,
    loadError,
    actionLoading,
    snapshot,
    isNotLoaded,
    statusLabel,
    statusTagType,
    progressPercent,
    progressColor,
    currentSegment,
    tickElapsedMs,
    tickRemainingMs,
    historyEntries,
    nextSegment,
    currentRundownId,
    fetchState,
    refresh,
    performControl,
    togglePause,
    handleNext,
  };
}
