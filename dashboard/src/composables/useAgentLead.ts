/**
 * 动态主从（主播主控 / 游戏主控）状态观测与导演人工切权组合式。
 *
 * 数据面：
 * - 首屏与手动刷新走 `GET /api/v1/agents/lead`（LeadCoordinator 未注入时 503 静默降级为不可用）；
 * - 实时刷新订阅 WebSocket 的 `agent.lead.changed` 事件：先按事件载荷就地更新主控状态，
 *   再经短防抖静默重拉全量快照以同步候选就绪清单（candidates / ready_candidates）；
 * - 导演一键切换走 `POST /api/v1/agents/lead`，错误文案统一经 `getApiErrorMessage` 提取。
 *
 * keep-alive 生命周期语义：
 * 本组合式常挂在 keep-alive 缓存页（直播控制台 LiveObserver / Agent 页 Agents）内，
 * 页面隐藏不触发 unmount。WS 订阅、防抖定时器与静默轮询严格随激活态启停：
 * - `onMounted` 建立 WS 订阅并拉取首屏状态（轮询由 useKeepAlivePolling 在激活时启动）；
 * - `onActivated`（非首次）恢复 WS 订阅并静默重拉（补齐隐藏期间错过的切权事件），
 *   轮询恢复运行；
 * - `onDeactivated` 与 `onScopeDispose` 解除 WS 订阅、清理防抖定时器并停止轮询，
 *   避免后台驻留与泄漏。
 *
 * 轮询兜底的理由：候选就绪态变化（ready_candidates）不发事件，仅靠切权事件补拉
 * 会在"就绪变化但未引发切权"的窗口内陈旧（导演看到过期的就绪/未就绪标记），
 * 因此激活态每 20 秒静默重拉一次快照。
 */

import { computed, onActivated, onDeactivated, onMounted, onScopeDispose, ref } from 'vue';
import { ElMessage } from 'element-plus';
import { agentsApi } from '@/api';
import { useWebSocketStore } from '@/stores';
import { EVENT } from '@/types/events';
import type { LeadStateResponse, WebSocketMessage } from '@/types';
import { getApiErrorMessage } from '@/utils/apiError';
import { isRecord, str } from '@/utils/guards';
import { formatLeadReasonLabel, formatLeadRoleLabel } from '@/utils/liveFeed';
import { useKeepAlivePolling } from './useKeepAlivePolling';
import { confirmAction } from '@/utils/confirmAction';

/** 收到 agent.lead.changed 后补拉全量快照的防抖窗口（毫秒） */
const RELOAD_DEBOUNCE_MS = 200;

/** 激活态静默轮询周期（毫秒）：兜底同步不发事件的候选就绪态变化 */
const LEAD_POLL_INTERVAL_MS = 20_000;

/** 导演一键切权按钮项 */
export interface LeadSwitchOption {
  /** 提交给 POST /api/v1/agents/lead 的 target 值（auto / streamer / 候选游戏名） */
  target: string;
  /** 按钮展示文案 */
  label: string;
  /** 按钮语义类型 */
  kind: 'auto' | 'streamer' | 'game';
  /** 是否为当前生效的 override 策略 */
  active: boolean;
  /** 是否不可点击（禁用态或游戏候选未就绪） */
  disabled: boolean;
  /** 游戏候选是否处于可接管就绪态（非游戏项恒 true） */
  ready: boolean;
  /** 悬停解释文案 */
  hint: string;
}

/** 当前主控角色主徽章文案：「主播主控」或「游戏主控（<lead_agent>）」 */
export function leadBadgeLabelOf(state: LeadStateResponse | null): string {
  if (!state) return '';
  // 动态主从未启用或主控为 streamer 时固定呈现「主播主控」
  if (
    !state.enabled ||
    state.mode !== 'game' ||
    !state.lead_agent ||
    state.lead_agent === 'streamer'
  ) {
    return '主播主控';
  }
  return formatLeadRoleLabel(state.lead_agent);
}

/** 当前主控调度模式短标签（供胶囊内紧贴显示） */
export function leadModeLabelOf(state: LeadStateResponse | null): string {
  if (!state) return '';
  if (!state.enabled) return '固定主播主控';
  return state.override !== 'auto' ? '手动锁定' : '自动跟随';
}

/** 当前主控调度模式完整说明（供面板详情与 tooltip 展示） */
export function leadModeDetailOf(state: LeadStateResponse | null): string {
  if (!state) return '';
  if (!state.enabled) return '固定主播主控（streamer_only）';
  if (state.override !== 'auto') {
    const lockTarget = state.override === 'streamer' ? '主播' : state.override;
    return `手动锁定（${lockTarget}）`;
  }
  return '自动跟随';
}

/** 构造导演一键切权选项：自动跟随 / 切回主播主控 / 各游戏候选（就绪可点、未就绪置灰） */
export function buildLeadSwitchOptions(state: LeadStateResponse | null): LeadSwitchOption[] {
  if (!state) return [];
  const enabled = state.enabled;
  const readySet = new Set(state.ready_candidates);
  // 合并 candidates 与 ready_candidates（去重保序），防接口某侧漏列时丢选项
  const gameNames: string[] = [];
  for (const name of [...state.candidates, ...state.ready_candidates]) {
    if (name && !gameNames.includes(name)) gameNames.push(name);
  }

  const options: LeadSwitchOption[] = [
    {
      target: 'auto',
      label: '自动跟随',
      kind: 'auto',
      active: enabled && state.override === 'auto',
      disabled: !enabled,
      ready: true,
      hint: enabled ? '随流程单环节自动交权与收回' : '动态主从未启用（streamer_only）',
    },
    {
      target: 'streamer',
      label: '切回主播主控',
      kind: 'streamer',
      active: enabled && state.override === 'streamer',
      disabled: !enabled,
      ready: true,
      hint: enabled ? '手动锁定为主播主控，阻止自动向游戏交权' : '动态主从未启用（streamer_only）',
    },
  ];

  for (const name of gameNames) {
    const ready = readySet.has(name);
    options.push({
      target: name,
      label: `交给 ${name}`,
      kind: 'game',
      active: enabled && state.override === name,
      disabled: !enabled || !ready,
      ready,
      hint: !enabled
        ? '动态主从未启用（streamer_only）'
        : ready
          ? `立即交由 ${name} 自主控场（手动锁定）`
          : `${name} 当前未就绪，暂不可接管主控`,
    });
  }

  return options;
}

/** 用 WS 推送的 agent.lead.changed 事件载荷就地更新本地主从快照（保持 candidates 等既有字段） */
export function applyLeadChangedPayload(
  current: LeadStateResponse,
  data: Record<string, unknown>,
  fallbackTsMs: number,
): LeadStateResponse {
  const rawMode = str(data.mode);
  const mode = rawMode === 'game' || rawMode === 'streamer' ? rawMode : current.mode;
  const leadAgent = str(data.lead_agent) || (mode === 'streamer' ? 'streamer' : current.lead_agent);
  // manual_override 显式为字符串时采纳；显式为 null 表示无人工覆盖（自动跟随）
  let override = current.override;
  if (typeof data.manual_override === 'string' && data.manual_override) {
    override = data.manual_override;
  } else if (data.manual_override === null) {
    override = 'auto';
  }
  const reason = str(data.reason) || current.reason;
  const sinceMs =
    typeof data.timestamp_ms === 'number' &&
    Number.isFinite(data.timestamp_ms) &&
    data.timestamp_ms > 0
      ? data.timestamp_ms
      : fallbackTsMs || current.since_ms;
  return {
    ...current,
    mode,
    lead_agent: leadAgent,
    override,
    reason,
    since_ms: sinceMs,
  };
}

/** 切权成功后的简短中文反馈文案 */
function switchSuccessMessage(target: string, next: LeadStateResponse): string {
  if (target === 'auto') return `已切回自动跟随（当前：${leadBadgeLabelOf(next)}）`;
  if (target === 'streamer') return '已锁定为主播主控';
  return `已切换为${leadBadgeLabelOf(next)}`;
}

/**
 * 切权二次确认文案：按目标区分打断语义（null = 该操作不打断任何执行，免确认）。
 * - 切向游戏候选 N：N 将自主控场，打断主播当前节奏（原文即「它当前的自主执行」口径）；
 * - 切向主播且当前是游戏主控：收回游戏主控权，打断其当前执行；
 * - auto：恢复自动跟随。
 */
export function switchConfirmMessageOf(
  target: string,
  state: LeadStateResponse | null,
): string | null {
  if (target === 'auto') return '恢复自动跟随，确认？';
  if (target === 'streamer') {
    const current = state?.lead_agent ?? '';
    if (current && current !== 'streamer') {
      return `收回 ${current} 的主控权将打断其当前执行，确认？`;
    }
    return null;
  }
  return `切换到 ${target} 主控将打断它当前的自主执行，确认？`;
}

export function useAgentLead() {
  const wsStore = useWebSocketStore();
  const leadState = ref<LeadStateResponse | null>(null);
  const loading = ref(false);
  const switchingTarget = ref<string | null>(null);
  const errorMessage = ref<string | null>(null);

  const available = computed(() => leadState.value !== null);
  const enabled = computed(() => leadState.value?.enabled ?? false);
  const isGameLead = computed(() =>
    Boolean(leadState.value?.enabled && leadState.value.mode === 'game'),
  );
  const isManualLocked = computed(() =>
    Boolean(leadState.value?.enabled && leadState.value.override !== 'auto'),
  );
  const badgeLabel = computed(() => leadBadgeLabelOf(leadState.value));
  const modeLabel = computed(() => leadModeLabelOf(leadState.value));
  const modeDetail = computed(() => leadModeDetailOf(leadState.value));
  const reasonLabel = computed(() => formatLeadReasonLabel(leadState.value?.reason ?? ''));
  const switchOptions = computed(() => buildLeadSwitchOptions(leadState.value));

  /** 拉取动态主从当前快照；静默模式（首屏/WS 补拉）遇到 503 或网络故障时不弹窗干扰 */
  async function refresh(options: { silent?: boolean } = {}): Promise<LeadStateResponse | null> {
    const silent = options.silent ?? true;
    if (!silent) loading.value = true;
    try {
      const response = await agentsApi.getAgentLeadState();
      leadState.value = response.data;
      errorMessage.value = null;
      return response.data;
    } catch (error) {
      if (!silent) {
        const message = getApiErrorMessage(error, '获取主控状态失败');
        errorMessage.value = message;
        ElMessage.error(message);
      } else {
        leadState.value = null;
      }
      return null;
    } finally {
      if (!silent) loading.value = false;
    }
  }

  /** 导演一键切换主控权：二次确认（取消不算错误）→ 防连点 → 成功后即时更新状态并弹窗反馈 */
  async function switchLead(
    target: string,
    reason = 'manual_override',
  ): Promise<LeadStateResponse | null> {
    if (switchingTarget.value !== null) return null;
    if (leadState.value && !leadState.value.enabled) return null;
    const confirmMessage = switchConfirmMessageOf(target, leadState.value);
    if (confirmMessage !== null && !(await confirmAction(confirmMessage, '切换主控权'))) {
      return null;
    }
    switchingTarget.value = target;
    errorMessage.value = null;
    try {
      const response = await agentsApi.switchAgentLead({ target, reason });
      leadState.value = response.data;
      ElMessage.success(switchSuccessMessage(target, response.data));
      return response.data;
    } catch (error) {
      const message = getApiErrorMessage(error, '切换主控权失败');
      errorMessage.value = message;
      ElMessage.error(message);
      return null;
    } finally {
      switchingTarget.value = null;
    }
  }

  // WS 监听 agent.lead.changed：收到即就地刷新徽章，再防抖静默补拉完整快照（同步候选就绪态）
  let reloadTimer: ReturnType<typeof setTimeout> | null = null;
  let wsActive = false;

  function onWsMessage(message: WebSocketMessage): void {
    if (!wsActive) return;
    if (message.type !== EVENT.agentLeadChanged) return;
    const data = isRecord(message.data) ? message.data : {};
    if (leadState.value) {
      leadState.value = applyLeadChangedPayload(leadState.value, data, message.timestamp_ms);
    }
    if (reloadTimer !== null) clearTimeout(reloadTimer);
    reloadTimer = setTimeout(() => {
      reloadTimer = null;
      if (!wsActive) return;
      void refresh({ silent: true });
    }, RELOAD_DEBOUNCE_MS);
  }

  function startWs(): void {
    if (wsActive) return;
    wsActive = true;
    wsStore.subscribe(onWsMessage);
  }

  function stopWs(): void {
    wsActive = false;
    wsStore.unsubscribe(onWsMessage);
    if (reloadTimer !== null) {
      clearTimeout(reloadTimer);
      reloadTimer = null;
    }
  }

  onMounted(() => {
    startWs();
    void refresh({ silent: true });
  });

  // keep-alive 激活态启停：首次激活已由 onMounted 完成首拉，再次激活时恢复订阅并补拉状态
  let hasActivatedOnce = false;
  onActivated(() => {
    if (!hasActivatedOnce) {
      hasActivatedOnce = true;
      return;
    }
    startWs();
    void refresh({ silent: true });
  });

  onDeactivated(stopWs);
  onScopeDispose(stopWs);

  // 激活态静默轮询兜底（随激活态启停，见 useKeepAlivePolling）：候选就绪态变化
  // 不发事件，仅靠切权事件补拉会在"就绪变化但未引发切权"的窗口内陈旧
  useKeepAlivePolling(() => refresh({ silent: true }), LEAD_POLL_INTERVAL_MS);

  return {
    leadState,
    available,
    enabled,
    loading,
    switchingTarget,
    errorMessage,
    isGameLead,
    isManualLocked,
    badgeLabel,
    modeLabel,
    modeDetail,
    reasonLabel,
    switchOptions,
    refresh,
    switchLead,
  };
}
