/**
 * 开播引导（直播控制台开播弹窗私有）：场次类型 + 组件意图清单 + 流程单选择 + 开场次提交。
 *
 * 弹窗打开时拉取组件清单与流程单库，把「本次直播要开哪些内容」物化为期望
 * 运行全集；提交经 liveSessionsApi.open({ kind, title, component_plan }) 单请求完成
 * 开场次 + 意图对齐——勾选的启动、在跑未勾的停止（审单即见调试残留），常驻
 * 主播不受清单影响。上次意图存 localStorage 作下次默认勾选（前端便利，不入
 * 配置契约面）。场次类型（正式 / 彩排）每次开播都要明确：默认跟随弹幕接入——
 * 接了 B 站弹幕算正式，没接算彩排；手动选过就以手动为准，且不存进上次意图，
 * 免得一次彩排让下次正式开播默认成彩排。不含定时器/订阅/WebSocket，无
 * keep-alive 生命周期语义。
 */

import { computed, ref } from 'vue';
import { componentApi, liveSessionsApi, rundownApi } from '@/api';
import { getApiErrorMessage } from '@/utils/apiError';
import { defaultSessionTitle } from '@/composables/live/useLiveSessions';
import type {
  ComponentListResponse,
  ComponentPlanReport,
  ComponentRef,
  ComponentSummary,
  RundownDefinition,
  SessionKind,
} from '@/types';

/** 意图清单条目的选择键（group/name，弹幕变体与勾选项共用） */
function keyOf(item: ComponentSummary): string {
  return `${item.group}/${item.name}`;
}

function keyToRef(key: string): ComponentRef | null {
  const slash = key.indexOf('/');
  if (slash <= 0) return null;
  const group = key.slice(0, slash);
  const name = key.slice(slash + 1);
  if (group !== 'collectors' && group !== 'agents') return null;
  return { group, name };
}

/** B 站弹幕三个接入变体：互斥单选而非复选（三种身份不互通，同时启用无意义） */
const DANMAKU_VARIANTS = ['bili_danmaku', 'bili_danmaku_official', 'bili_danmaku_laplace'] as const;

const DANMAKU_LABELS: Record<string, string> = {
  bili_danmaku: 'B站弹幕（legacy）',
  bili_danmaku_official: 'B站弹幕（官方接口）',
  bili_danmaku_laplace: 'B站弹幕（Laplace 直连）',
};

type PlanGroupId = 'input' | 'video' | 'game' | 'other';

/** 组件内容标签与分组策划表（未知组件落「其他」组兜底，新增组件无需改弹窗代码） */
const COMPONENT_META: Record<string, { label: string; group: PlanGroupId }> = {
  console_input: { label: '控制台输入', group: 'input' },
  stt: { label: '语音转写', group: 'input' },
  live_frame: { label: '画面取帧', group: 'video' },
  minecraft: { label: 'Minecraft', group: 'game' },
  text_adv: { label: '文字冒险', group: 'game' },
  game_2048: { label: '2048', group: 'game' },
};

/** 分组展示顺序与标题（内容视角命名，不用采集器/Agent 系统词） */
const GROUP_ORDER: Array<{ id: PlanGroupId; title: string }> = [
  { id: 'input', title: '直播输入' },
  { id: 'video', title: '画面' },
  { id: 'game', title: '游戏' },
  { id: 'other', title: '其他' },
];

const STORAGE_KEY = 'amaidesu.startStream.plan.v1';

/** localStorage 里的上次开播意图（恢复时逐项校验，失效条目静默丢弃） */
interface SavedPlan {
  keys: string[];
  danmaku: string;
  rundownId: string;
}

function readSavedPlan(): SavedPlan | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<SavedPlan>;
    if (!Array.isArray(parsed.keys)) return null;
    return {
      keys: parsed.keys.filter((k): k is string => typeof k === 'string'),
      danmaku: typeof parsed.danmaku === 'string' ? parsed.danmaku : '',
      rundownId: typeof parsed.rundownId === 'string' ? parsed.rundownId : '',
    };
  } catch {
    return null;
  }
}

export function useStartStream() {
  const loading = ref(false);
  const loadError = ref('');
  const submitting = ref(false);
  const submitError = ref('');
  /** 开场响应携带的意图对齐回执（null=尚未提交或请求本身失败） */
  const report = ref<ComponentPlanReport | null>(null);

  const title = ref(defaultSessionTitle());

  /** 可勾选的顶层采集器（附属随 Agent、弹幕变体走单选，均不在列） */
  const collectorItems = ref<ComponentSummary[]>([]);
  /** 可勾选的游戏 Agent（主播 Agent 常驻，不出现） */
  const agentItems = ref<ComponentSummary[]>([]);
  /** Agent 名 → 附属采集器标注（如「附属：身体事件 / 游戏聊天」） */
  const companionNotes = ref<Record<string, string>>({});

  const rundowns = ref<RundownDefinition[]>([]);
  const currentRundownId = ref('');
  const selectedRundownId = ref('');

  /** 勾选的组件键集合（不含弹幕变体） */
  const selectedKeys = ref<string[]>([]);
  /** 弹幕变体注册名，空串 = 不接入 */
  const danmaku = ref('');

  const hasActiveSessionHint = ref(false);

  /** 手动选过的场次类型；null = 未手动选，跟随弹幕接入推断 */
  const kindOverride = ref<SessionKind | null>(null);
  /** 本次场次类型：手动选过以手动为准，否则接了 B 站弹幕算正式、没接算彩排 */
  const kind = computed<SessionKind>({
    get: () => kindOverride.value ?? (danmaku.value ? 'live' : 'rehearsal'),
    set: value => {
      kindOverride.value = value;
    },
  });

  /** 分组后的勾选清单（供弹窗按组渲染） */
  const planGroups = computed(() => {
    const byGroup = new Map<PlanGroupId, ComponentSummary[]>();
    for (const item of [...collectorItems.value, ...agentItems.value]) {
      const meta = COMPONENT_META[item.name];
      const groupId: PlanGroupId = meta?.group ?? 'other';
      const bucket = byGroup.get(groupId) ?? [];
      bucket.push(item);
      byGroup.set(groupId, bucket);
    }
    return GROUP_ORDER.map(({ id, title: groupTitle }) => ({
      id,
      title: groupTitle,
      items: byGroup.get(id) ?? [],
    })).filter(group => group.items.length > 0 || group.id === 'input');
  });

  /** 最终期望运行全集 = 勾选项 + 弹幕单选 */
  const planRefs = computed<ComponentRef[]>(() => {
    const refs = selectedKeys.value.map(keyToRef).filter((r): r is ComponentRef => r !== null);
    if (danmaku.value) refs.push({ group: 'collectors', name: danmaku.value });
    return refs;
  });

  /** 提交是否干净成功（无失败项且名单写盘成功） */
  const hasFailures = computed(
    () =>
      !!report.value &&
      (report.value.items.some(i => i.result === 'failed') || report.value.config_error !== ''),
  );

  const failedItems = computed(() => report.value?.items.filter(i => i.result === 'failed') ?? []);

  function labelOf(item: ComponentSummary): string {
    return COMPONENT_META[item.name]?.label ?? DANMAKU_LABELS[item.name] ?? item.name;
  }

  function isChecked(item: ComponentSummary): boolean {
    return selectedKeys.value.includes(keyOf(item));
  }

  function toggle(item: ComponentSummary, checked: boolean): void {
    const key = keyOf(item);
    selectedKeys.value = checked
      ? [...selectedKeys.value, key]
      : selectedKeys.value.filter(k => k !== key);
  }

  /** 从组件全集按上次意图（或现状兜底）恢复默认勾选 */
  function restoreDefaults(components: ComponentListResponse): void {
    const selectable = [...components.collectors, ...components.agents].filter(
      c => !c.owner && c.name !== 'streamer' && !isDanmakuVariant(c.name),
    );
    const saved = readSavedPlan();
    if (saved) {
      selectedKeys.value = saved.keys.filter(k => selectable.some(c => keyOf(c) === k));
      danmaku.value = isDanmakuVariant(saved.danmaku) ? saved.danmaku : '';
      selectedRundownId.value = rundowns.value.some(r => r.rundown_id === saved.rundownId)
        ? saved.rundownId
        : currentRundownId.value;
      return;
    }
    // 首次使用：现状兜底 = 正在运行 ∪ 配置启用；弹幕取唯一在用的变体
    selectedKeys.value = selectable.filter(c => c.is_started || c.is_enabled).map(keyOf);
    const activeVariants = DANMAKU_VARIANTS.filter(v =>
      components.collectors.some(c => c.name === v && (c.is_started || c.is_enabled)),
    );
    danmaku.value = activeVariants.length === 1 ? activeVariants[0] : '';
    selectedRundownId.value = currentRundownId.value;
  }

  function isDanmakuVariant(name: string): boolean {
    return (DANMAKU_VARIANTS as readonly string[]).includes(name);
  }

  /** 弹窗打开时拉取数据（组件清单为主，流程单库失败不阻塞） */
  async function load(activeSessionExists: boolean): Promise<void> {
    loading.value = true;
    loadError.value = '';
    report.value = null;
    submitError.value = '';
    title.value = defaultSessionTitle();
    kindOverride.value = null;
    hasActiveSessionHint.value = activeSessionExists;
    try {
      const [componentsRes, rundownRes] = await Promise.all([
        componentApi.getAll(),
        rundownApi.listRundowns().catch(() => null),
      ]);
      const components = componentsRes.data;
      collectorItems.value = components.collectors.filter(
        c => !c.owner && !isDanmakuVariant(c.name),
      );
      agentItems.value = components.agents.filter(c => c.name !== 'streamer');
      companionNotes.value = Object.fromEntries(
        components.agents
          .filter(c => c.name !== 'streamer')
          .map(agent => [
            agent.name,
            components.collectors
              .filter(c => c.owner === agent.name)
              .map(c => COMPONENT_META[c.name]?.label ?? c.name)
              .join(' / '),
          ]),
      );
      rundowns.value = rundownRes?.data.rundowns ?? [];
      currentRundownId.value = rundownRes?.data.current_id ?? '';
      selectedRundownId.value = currentRundownId.value;
      restoreDefaults(components);
    } catch (error) {
      loadError.value = getApiErrorMessage(error, '组件清单加载失败');
    } finally {
      loading.value = false;
    }
  }

  /** 提交开播：必要时先切流程单，再单请求开场次 + 意图对齐。返回是否干净成功 */
  async function submit(): Promise<boolean> {
    submitting.value = true;
    submitError.value = '';
    report.value = null;
    try {
      if (selectedRundownId.value && selectedRundownId.value !== currentRundownId.value) {
        const activateRes = await rundownApi.activate(selectedRundownId.value);
        if (!activateRes.data.success) {
          submitError.value = activateRes.data.message || '流程单切换失败';
          return false;
        }
      }
      const { data } = await liveSessionsApi.open({
        kind: kind.value,
        title: title.value.trim() || undefined,
        component_plan: planRefs.value,
      });
      saveSelection();
      report.value = data.plan_report;
      return !hasFailures.value;
    } catch (error) {
      submitError.value = getApiErrorMessage(
        error,
        kind.value === 'live' ? '开播失败' : '开始彩排失败',
      );
      return false;
    } finally {
      submitting.value = false;
    }
  }

  function saveSelection(): void {
    try {
      const saved: SavedPlan = {
        keys: selectedKeys.value,
        danmaku: danmaku.value,
        rundownId: selectedRundownId.value,
      };
      localStorage.setItem(STORAGE_KEY, JSON.stringify(saved));
    } catch {
      /* 存不了就算了：下次回落现状兜底 */
    }
  }

  return {
    loading,
    loadError,
    submitting,
    submitError,
    report,
    hasFailures,
    failedItems,
    title,
    kind,
    planGroups,
    planRefs,
    companionNotes,
    rundowns,
    currentRundownId,
    selectedRundownId,
    selectedKeys,
    danmaku,
    hasActiveSessionHint,
    labelOf,
    DANMAKU_VARIANTS,
    DANMAKU_LABELS,
    isChecked,
    toggle,
    load,
    submit,
  };
}
