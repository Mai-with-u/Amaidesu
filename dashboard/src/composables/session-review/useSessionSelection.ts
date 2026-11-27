/**
 * 场次复盘页场次下拉框的选区协调状态机
 *
 * 协调四方输入：场次列表装载、默认"最新已结束正式场"、`?session=` 深链、用户手动
 * 改选，外加 keep-alive 切回页面的静默刷新。规则：
 * - 首次挂载：优先应用 `?session=` 深链，否则默认选最新已结束正式场（ADR-050：
 *   统计复盘只认正式场；全无正式场回退最新已结束）；同时跳过紧随首次挂载触发的
 *   那一次 `onActivated`，避免双发请求；
 * - 切回页面（或手动刷新）时静默刷新列表：带入新深链则切到目标场次；用户仍停在
 *   旧的自动默认场次（或尚无选择且未手动改过）时自动跟进到最新已结束场次；
 *   手动选定与已应用深链一律保留不覆盖。
 *
 * keep-alive 生命周期语义：本组合式在 setup 期注册 onMounted / onActivated，
 * 依赖外层路由 `<keep-alive>`；不含定时器、事件订阅或 WebSocket。
 */
import { onActivated, onMounted, ref, type Ref } from 'vue';
import { useRoute } from 'vue-router';
import { liveSessionsApi } from '@/api';
import type { LiveSessionItem } from '@/types';
import { getApiErrorMessage } from '@/utils/apiError';
import { formatDateTime } from '@/utils/format';

export interface UseSessionSelectionOptions {
  /** 页面级错误横幅：列表加载失败写入、成功清空（与复盘聚合错误共用一格） */
  errorMessage: Ref<string>;
}

export type SessionListLoadMode = 'initial' | 'activated' | 'manual';

export function useSessionSelection(options: UseSessionSelectionOptions) {
  const { errorMessage } = options;
  const route = useRoute();

  const sessions = ref<LiveSessionItem[]>([]);
  const sessionsLoading = ref(false);
  const selectedIds = ref<number[]>([]);

  /** 场次列表请求序号：并发或快速切页时只认最后发起的响应 */
  let sessionsSeq = 0;
  /** 当前选中是否停留在页面自动选中的“最新已结束场次”；用户手动改动或深链命中后置 null */
  let autoSelectedDefaultId: number | null = null;
  /** 用户是否主动操作过下拉框（含手动清空），用于区分“从未选过”与“用户主动清空” */
  let userTouchedSelection = false;
  /** 最近一次已应用的 ?session= 深链原始字符串，用于识别切回页面时是否带入了新的深链 */
  let appliedDeepLinkQuery = '';
  /** Vue keep-alive 首次挂载后会紧接着触发一次 onActivated：置 true 跳过这次重复拉取 */
  let skipNextActivated = false;

  /** 解析当前路由 ?session= 里的有效正整数场次 id 列表 */
  function parseDeepLinkSessionIds(raw: unknown): number[] {
    return String(raw ?? '')
      .split(',')
      .map(Number)
      .filter(id => Number.isFinite(id) && id > 0);
  }

  /** 用户手动在下拉框修改或清空场次选择：标记为手动选择并清除已应用深链记录，后续静默刷新不再覆盖 */
  function onSelectChange(): void {
    userTouchedSelection = true;
    autoSelectedDefaultId = null;
    appliedDeepLinkQuery = '';
  }

  /**
   * 加载场次列表并按来源模式更新选中态。
   *
   * - initial（首次挂载）：优先应用 ?session= 深链，否则默认选最新已结束场次；
   * - activated（keep-alive 切回静默刷新）/ manual（点工具栏刷新）：
   *   若带入新的 ?session= 深链则切到目标场次；若仍停留在旧自动默认场次（或此前
   *   尚无已结束场次且用户未手动改过选择）且列表出现更新的已结束场次，自动跟进到
   *   最新已结束场次；用户手动选定的特定场次或已有深链保持不动。
   */
  async function loadSessions(mode: SessionListLoadMode = 'initial'): Promise<boolean> {
    const seq = ++sessionsSeq;
    // 切回静默刷新时若已有数据则不闪下拉 loading，首次与手动刷新正常展示加载态
    const showSpinner = mode !== 'activated' || sessions.value.length === 0;
    if (showSpinner) sessionsLoading.value = true;
    try {
      const resp = await liveSessionsApi.list({ limit: 200 });
      if (seq !== sessionsSeq) return false;
      errorMessage.value = '';
      const items = resp.data.items;
      sessions.value = items;

      const rawDeepLink = String(route.query.session ?? '').trim();
      const hasNewDeepLink =
        rawDeepLink.length > 0 && (mode === 'initial' || rawDeepLink !== appliedDeepLinkQuery);
      if (hasNewDeepLink) {
        const deepLinkIds = parseDeepLinkSessionIds(rawDeepLink);
        const linked = items.filter(item => deepLinkIds.includes(item.live_session_id));
        appliedDeepLinkQuery = rawDeepLink;
        if (linked.length) {
          autoSelectedDefaultId = null;
          selectedIds.value = linked.map(item => item.live_session_id);
          return true;
        }
        if (mode !== 'initial') return true;
      }

      // 默认选中只认正式场（ADR-050：统计复盘只认正式场，彩排场观众侧数据为模拟）；
      // 全无正式场时回退最新已结束，避免空列表下拉无可选项
      const ended = items.filter(item => item.ended_at_ms !== null);
      const latestEnded = ended.find(item => item.kind === 'live') ?? ended[0];
      if (mode === 'initial') {
        if (latestEnded) {
          autoSelectedDefaultId = latestEnded.live_session_id;
          selectedIds.value = [latestEnded.live_session_id];
        }
        return true;
      }

      // 切回页面或点刷新：仅当用户仍停在旧自动默认场次（或初始无可选场次且未手动改过）时，
      // 自动跟进到最新已结束场次；手动选择或深链场次一律尊重用户当前选择
      const stayingOnAutoDefault =
        autoSelectedDefaultId !== null &&
        selectedIds.value.length === 1 &&
        selectedIds.value[0] === autoSelectedDefaultId;
      const waitingForFirstEnded = !userTouchedSelection && selectedIds.value.length === 0;
      if ((stayingOnAutoDefault || waitingForFirstEnded) && latestEnded) {
        if (
          latestEnded.live_session_id !== autoSelectedDefaultId ||
          selectedIds.value.length === 0
        ) {
          autoSelectedDefaultId = latestEnded.live_session_id;
          selectedIds.value = [latestEnded.live_session_id];
        }
      }
      return true;
    } catch (error) {
      if (seq !== sessionsSeq) return false;
      errorMessage.value = getApiErrorMessage(error, '场次列表加载失败');
      return false;
    } finally {
      if (seq === sessionsSeq) sessionsLoading.value = false;
    }
  }

  onMounted(() => {
    skipNextActivated = true;
    void loadSessions('initial');
  });

  onActivated(() => {
    if (skipNextActivated) {
      skipNextActivated = false;
      return;
    }
    void loadSessions('activated');
  });

  /** 下拉框选项文案：彩排标注 + 标题 + 进行中标注 + 开播时间 */
  function sessionLabel(item: LiveSessionItem): string {
    const title = item.title || `场次 #${item.live_session_id}`;
    // 彩排场显式标注（默认选中不会落在它上面，选中即用户显式行为）
    const rehearsal = item.kind === 'rehearsal' ? '彩排 · ' : '';
    // 进行中场次在列表中显式标注，避免被误当历史场次深链回看
    const ongoing = item.ended_at_ms === null ? '（进行中）' : '';
    return `${rehearsal}${title}${ongoing} · ${formatDateTime(item.started_at_ms)}`;
  }

  return { sessions, sessionsLoading, selectedIds, loadSessions, onSelectChange, sessionLabel };
}
