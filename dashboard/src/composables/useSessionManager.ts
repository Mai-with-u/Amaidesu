/**
 * 场次管理页：场次清单的取数、筛选、分页、多选与逐场处置。
 *
 * 页面私有状态（只有场次管理页消费），走 composable 而非 Pinia。取数走服务端
 * 筛选（标题关键字 / 来源 / 类型 / 开播区间 / 整理状态），页面上的"共多少场"
 * 与当页同口径（服务端用同一套筛选条件计数）。
 *
 * 处置动作都落在既有的场次端点上，不新造机制：
 * - 改标题 / 改备注：`PATCH /live-sessions/{id}`（各改各的）
 * - 改类型：`PATCH /live-sessions/{id}/kind`（进行中的场次改不了）
 * - 删除：`DELETE /live-sessions/{id}`（级联清明细，承诺不随场次删除）
 * - 重新整理 / 标已审：记忆账本的整理端点
 * - 导出数据包：复盘页的导出端点（多场打包）
 */

import { computed, nextTick, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ElMessage, ElMessageBox } from 'element-plus';
import { ledgerApi, liveSessionsApi, reviewApi } from '@/api';
import { getApiErrorMessage } from '@/utils/apiError';
import { useNowTick } from '@/composables/useNowTick';
import {
  SESSION_FILTER_ALL,
  SESSION_SOURCE_OPTIONS,
  defaultSessionTitle,
  kindLabel,
  sessionDurationLabel,
  settleStateOf,
  summarizeSessions,
  type SessionSource,
} from '@/utils/sessionManager';
import type { LiveSessionItem, SessionKind } from '@/types';

/** 整理状态筛选：all=不限，其余与后端筛选值同名 */
export type SessionSettleFilter = 'all' | 'pending' | 'failed' | 'review' | 'reviewed';

/** 筛选状态在 URL 上的键（可分享的清单视图） */
const QUERY_KEYS = {
  q: 'q',
  source: 'source',
  kind: 'kind',
  from: 'from',
  to: 'to',
  state: 'state',
} as const;

/** 一天的开头与末尾（毫秒）：开播日期区间按自然日取，含首尾两天 */
function startOfDay(at: Date): number {
  return new Date(at.getFullYear(), at.getMonth(), at.getDate()).getTime();
}

function endOfDay(at: Date): number {
  return startOfDay(at) + 86_400_000 - 1;
}

export function useSessionManager() {
  const route = useRoute();
  const router = useRouter();
  /** 共享 1s 时钟：进行中场次的"已播"时长与汇总时长随时钟走 */
  const nowTick = useNowTick();

  const items = ref<LiveSessionItem[]>([]);
  const total = ref(0);
  const loading = ref(false);
  const page = ref(1);
  const pageSize = ref(20);

  /** 筛选条件：标题关键字 / 来源 / 类型 / 开播区间 / 整理状态 */
  const query = ref('');
  const sourceFilter = ref<SessionSource | typeof SESSION_FILTER_ALL>(SESSION_FILTER_ALL);
  const kindFilter = ref<SessionKind | typeof SESSION_FILTER_ALL>(SESSION_FILTER_ALL);
  const dateRange = ref<[Date, Date] | null>(null);
  const settleFilter = ref<SessionSettleFilter>('all');

  /** 多选（批量处置用）：选中的是场次主键 */
  const selectedIds = ref<number[]>([]);

  const summary = computed(() => summarizeSessions(items.value, nowTick.value));

  async function load(): Promise<void> {
    loading.value = true;
    try {
      const response = await liveSessionsApi.list({
        q: query.value.trim() || undefined,
        source: sourceFilter.value === SESSION_FILTER_ALL ? undefined : sourceFilter.value,
        kind: kindFilter.value === SESSION_FILTER_ALL ? undefined : kindFilter.value,
        started_after_ms: dateRange.value ? startOfDay(dateRange.value[0]) : undefined,
        started_before_ms: dateRange.value ? endOfDay(dateRange.value[1]) : undefined,
        settle_state: settleFilter.value === 'all' ? undefined : settleFilter.value,
        limit: pageSize.value,
        offset: (page.value - 1) * pageSize.value,
      });
      items.value = response.data.items;
      total.value = response.data.total;
    } catch (error) {
      ElMessage.error(getApiErrorMessage(error, '场次清单加载失败'));
    } finally {
      loading.value = false;
    }
  }

  /**
   * 筛选变化：回到第一页重取（关键字防抖，下拉框立即取）。
   * 页码复位本身不单独取数——否则防抖窗口会被页码变化的取数顶掉。
   */
  let filterTimer: ReturnType<typeof setTimeout> | null = null;
  let pageResetByFilter = false;
  watch([query, sourceFilter, kindFilter, dateRange, settleFilter], () => {
    pageResetByFilter = true;
    page.value = 1;
    // 页码没真变时不会有页码 watcher 来清这个标志，这里兜底清掉
    void nextTick(() => {
      pageResetByFilter = false;
    });
    if (filterTimer) clearTimeout(filterTimer);
    filterTimer = setTimeout(() => {
      void load();
    }, 250);
  });

  watch([page, pageSize], () => {
    if (pageResetByFilter) {
      pageResetByFilter = false;
      return;
    }
    void load();
  });

  /** 筛选状态写回 URL：清单视图可以直接分享（replace 不产生历史记录） */
  watch(
    [query, sourceFilter, kindFilter, dateRange, settleFilter],
    () => {
      const next: Record<string, string> = {};
      if (query.value.trim()) next[QUERY_KEYS.q] = query.value.trim();
      if (sourceFilter.value !== SESSION_FILTER_ALL) next[QUERY_KEYS.source] = sourceFilter.value;
      if (kindFilter.value !== SESSION_FILTER_ALL) next[QUERY_KEYS.kind] = kindFilter.value;
      if (dateRange.value) {
        next[QUERY_KEYS.from] = dateRange.value[0].toISOString().slice(0, 10);
        next[QUERY_KEYS.to] = dateRange.value[1].toISOString().slice(0, 10);
      }
      if (settleFilter.value !== 'all') next[QUERY_KEYS.state] = settleFilter.value;
      void router.replace({ query: next });
    },
    { deep: true },
  );

  /** 从 URL 还原筛选（进场时一次）：非法值一律忽略，落回"不限" */
  function applyQueryFromUrl(): void {
    const q = String(route.query[QUERY_KEYS.q] ?? '').trim();
    if (q) query.value = q;
    const source = String(route.query[QUERY_KEYS.source] ?? '');
    if (SESSION_SOURCE_OPTIONS.some(option => option.value === source)) {
      sourceFilter.value = source as SessionSource;
    }
    const kind = String(route.query[QUERY_KEYS.kind] ?? '');
    if (kind === 'live' || kind === 'rehearsal') kindFilter.value = kind;
    const state = String(route.query[QUERY_KEYS.state] ?? '');
    if (state === 'pending' || state === 'failed' || state === 'review' || state === 'reviewed') {
      settleFilter.value = state;
    }
    const from = String(route.query[QUERY_KEYS.from] ?? '');
    const to = String(route.query[QUERY_KEYS.to] ?? '');
    if (from && to) {
      const start = new Date(from);
      const end = new Date(to);
      if (!Number.isNaN(start.getTime()) && !Number.isNaN(end.getTime())) {
        dateRange.value = [start, end];
      }
    }
  }

  // -------------------- 多选与批量 --------------------

  function setSelection(ids: number[]): void {
    selectedIds.value = ids;
  }

  function clearSelection(): void {
    selectedIds.value = [];
  }

  /** 导出所选场次的数据包地址（复盘页的导出端点，多场合并一个包）；没勾选返回 null */
  function exportUrl(ids: number[] = selectedIds.value): string | null {
    if (!ids.length) {
      ElMessage.info('先勾选要导出的场次');
      return null;
    }
    return reviewApi.exportUrl(ids);
  }

  // -------------------- 逐场处置 --------------------

  /** 改标题：弹输入框预填当前标题（未命名回落开播时刻默认名），空串=清除标题 */
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
      return;
    }
    try {
      await liveSessionsApi.update(item.live_session_id, { title });
      ElMessage.success('场次已重命名');
    } catch (error) {
      ElMessage.error(getApiErrorMessage(error, '重命名失败'));
      return;
    }
    await load();
  }

  /** 写备注（空串=清除备注）：备注由对话框里的输入框给过来 */
  async function saveNotes(item: LiveSessionItem, notes: string): Promise<void> {
    try {
      await liveSessionsApi.update(item.live_session_id, { notes: notes.trim() });
      ElMessage.success('备注已保存');
    } catch (error) {
      ElMessage.error(getApiErrorMessage(error, '备注保存失败'));
      return;
    }
    await load();
  }

  /**
   * 更正类型（正式 ↔ 彩排）：类型决定这场的回顾进哪条记忆线、要不要对外同步，
   * 改前确认一次后果。
   */
  async function toggleSessionKind(item: LiveSessionItem): Promise<void> {
    const next: SessionKind = item.kind === 'live' ? 'rehearsal' : 'live';
    const hint =
      next === 'live'
        ? '改为正式后，这场的回顾会进正式直播的记忆线（下次正式开播可能被当成上一场）。'
        : '改为彩排后，这场的回顾只在彩排时被读到，正式开播不再把它当成上一场。';
    try {
      await ElMessageBox.confirm(hint, `改为${kindLabel(next)}`, {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
      });
    } catch {
      return;
    }
    try {
      await liveSessionsApi.setKind(item.live_session_id, next);
      ElMessage.success(`场次已改为${kindLabel(next)}`);
    } catch (error) {
      ElMessage.error(getApiErrorMessage(error, '修改类型失败'));
      return;
    }
    await load();
  }

  /** 删除场次：级联清除该场明细，承诺不随场次删除（这条要说在确认里） */
  async function removeSession(item: LiveSessionItem): Promise<void> {
    const confirmed = await ElMessageBox.confirm(
      `删除「${item.title ?? `场次 #${item.live_session_id}`}」及其全部明细？此操作不可撤销。` +
        '该场登记过的承诺不受影响。',
      '删除场次',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' },
    ).catch(() => false);
    if (!confirmed) return;
    try {
      await liveSessionsApi.remove(item.live_session_id);
      ElMessage.success('场次已删除');
    } catch (error) {
      ElMessage.error(getApiErrorMessage(error, '删除失败'));
      return;
    }
    clearSelection();
    await load();
  }

  /** 批量改类型：逐场调用既有端点，失败的一场单独报出来 */
  async function batchSetKind(kind: SessionKind, ids: number[] = selectedIds.value): Promise<void> {
    if (!ids.length) {
      ElMessage.info('先勾选要改类型的场次');
      return;
    }
    const confirmed = await ElMessageBox.confirm(
      `把选中的 ${ids.length} 场都改为${kindLabel(kind)}？改类型会动到这场登记的账本（改成彩排会清掉本场档案与承诺）。`,
      '批量改类型',
      { confirmButtonText: '确定', cancelButtonText: '取消', type: 'warning' },
    ).catch(() => false);
    if (!confirmed) return;
    let failed = 0;
    for (const id of ids) {
      try {
        await liveSessionsApi.setKind(id, kind);
      } catch {
        failed += 1;
      }
    }
    if (failed) ElMessage.warning(`${failed} 场改不了（进行中的场次不能改类型）`);
    else ElMessage.success(`已把 ${ids.length} 场改为${kindLabel(kind)}`);
    clearSelection();
    await load();
  }

  /** 批量删除：逐场删除，每场单独确认（不可撤销的动作用逐场确认更稳） */
  async function batchRemove(ids: number[] = selectedIds.value): Promise<void> {
    if (!ids.length) {
      ElMessage.info('先勾选要删除的场次');
      return;
    }
    const confirmed = await ElMessageBox.confirm(
      `删除选中的 ${ids.length} 场及其全部明细？此操作不可撤销。这些场次登记过的承诺不受影响。`,
      '批量删除场次',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' },
    ).catch(() => false);
    if (!confirmed) return;
    let failed = 0;
    for (const id of ids) {
      try {
        await liveSessionsApi.remove(id);
      } catch {
        failed += 1;
      }
    }
    if (failed) ElMessage.warning(`${failed} 场删不掉`);
    else ElMessage.success(`已删除 ${ids.length} 场`);
    clearSelection();
    await load();
  }

  /** 重新整理：把这场放回待整理队列（整理产物要重跑） */
  async function resettle(item: LiveSessionItem): Promise<void> {
    try {
      await ledgerApi.resettle(item.live_session_id);
      ElMessage.success('已排回待整理');
    } catch (error) {
      ElMessage.error(getApiErrorMessage(error, '重新整理失败'));
      return;
    }
    await load();
  }

  /** 标记已审：整理出来的东西人工过了一遍 */
  async function markReviewed(item: LiveSessionItem): Promise<void> {
    try {
      await ledgerApi.markReviewed(item.live_session_id);
      ElMessage.success('已标记为已审');
    } catch (error) {
      ElMessage.error(getApiErrorMessage(error, '标记失败'));
      return;
    }
    await load();
  }

  return {
    items,
    total,
    loading,
    page,
    pageSize,
    query,
    sourceFilter,
    kindFilter,
    dateRange,
    settleFilter,
    selectedIds,
    summary,
    nowTick,
    load,
    applyQueryFromUrl,
    setSelection,
    clearSelection,
    exportUrl,
    renameSession,
    saveNotes,
    toggleSessionKind,
    removeSession,
    batchSetKind,
    batchRemove,
    resettle,
    markReviewed,
    sessionDurationLabel,
    settleStateOf,
  };
}
