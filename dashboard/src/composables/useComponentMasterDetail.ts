/**
 * 组件主从视图共享组合式（Agents / Collectors 两页同构逻辑的单一实现）：
 * 选中保持、单组件控制、批量控制、状态文案、事件流缓冲与暂停/清空。
 * 页面各自的特有语义（确认框、归因映射、阶段标记等）留在页内。
 */
import { computed, reactive, ref, watch, type Ref } from 'vue';
import { ElMessage } from 'element-plus';
import { useComponentsStore } from '@/stores';
import type { ComponentControlAction, ComponentSummary, WebSocketMessage } from '@/types';

/** 事件流缓冲上限；超出从头丢弃 */
export const STREAM_CAP = 100;

/** 事件流条目公共字段；页面按需扩展自身字段 */
export interface ComponentStreamItem {
  id: string;
  eventType: string;
  summary: string;
  /** 事件时刻（Unix 毫秒，随事件流单位） */
  timestampMs: number;
}

/** 事件存储条目形状（WebSocketMessage + 去重 id） */
export type ComponentEvent = WebSocketMessage & { id: string };

interface Options<TItem extends ComponentStreamItem> {
  /** 组件清单（来自 components store 的 agentsList / collectorsList） */
  list: Ref<ComponentSummary[]>;
  /** 控制端点域（'agents' | 'collectors'） */
  domain: 'agents' | 'collectors';
  /** 面向用户的组件称谓（批量操作提示文案用） */
  noun: string;
  /** 事件源（events store 的 events） */
  events: Ref<ComponentEvent[]>;
  /** 单条事件 → 流条目；返回 null 表示不入选。ctx.selectedName 为当前选中项 */
  mapEvent: (event: ComponentEvent, ctx: { selectedName: string | null }) => TItem | null;
  /** 单组件控制完成后的回调（如 Agents 页刷新运行状态名册） */
  afterControl?: () => void;
}

export function useComponentMasterDetail<TItem extends ComponentStreamItem>(
  options: Options<TItem>,
) {
  const componentsStore = useComponentsStore();

  // 选中状态：默认首个 RUNNING，否则首个 enabled，否则首个；选中项仍存在时保持
  const selectedName = ref<string | null>(null);

  function pickDefault(): string | null {
    const list = options.list.value;
    if (list.length === 0) return null;
    return (list.find(c => c.is_started) ?? list.find(c => c.is_enabled) ?? list[0]).name;
  }

  watch(
    options.list,
    list => {
      if (list.length === 0) {
        selectedName.value = null;
        return;
      }
      if (selectedName.value && list.some(c => c.name === selectedName.value)) return;
      selectedName.value = pickDefault();
    },
    { immediate: true },
  );

  const selected = computed<ComponentSummary | null>(
    () => options.list.value.find(c => c.name === selectedName.value) ?? null,
  );

  function select(name: string): void {
    selectedName.value = name;
  }

  // 单组件控制：按 (name, action) 维度跟踪 loading
  const controlPending = reactive<Record<string, boolean>>({});
  const batchLoading = ref<'start' | 'stop' | null>(null);

  async function handleControl(action: ComponentControlAction): Promise<void> {
    const name = selectedName.value;
    if (!name) return;
    const key = `${name}-${action}`;
    controlPending[key] = true;
    try {
      const result = await componentsStore.controlComponent(options.domain, name, action);
      ElMessage.success(result.message);
    } catch (error) {
      ElMessage.error(error instanceof Error ? error.message : '操作失败');
    } finally {
      controlPending[key] = false;
      options.afterControl?.();
    }
  }

  async function runBatch(action: 'start' | 'stop'): Promise<void> {
    batchLoading.value = action;
    try {
      const { succeeded, failed, messages } = await componentsStore.batchControl(
        options.domain,
        action,
      );
      if (succeeded === 0 && failed === 0) {
        ElMessage.info(
          action === 'start' ? `所有${options.noun}已在运行中` : `所有${options.noun}已停止`,
        );
      } else if (failed === 0) {
        ElMessage.success(
          `已${action === 'start' ? '启动' : '停止'} ${succeeded} 个${options.noun}`,
        );
      } else {
        const failureHints = messages
          .filter(m => m.includes('失败') || m.includes('未启动') || m.includes('已停止'))
          .slice(0, 2)
          .join('；');
        ElMessage.warning(
          `${action === 'start' ? '启动' : '停止'}完成：${succeeded} 成功, ${failed} 失败${failureHints ? `（${failureHints}）` : ''}`,
        );
      }
    } catch (error) {
      ElMessage.error('批量操作失败');
      console.error('Batch control error:', error);
    } finally {
      batchLoading.value = null;
    }
  }

  function statusLabel(c: ComponentSummary): string {
    if (c.is_started) return '运行中';
    if (c.is_enabled) return '已停止';
    return '未启用';
  }

  // 事件流缓冲：暂停时停渲染（store 仍持续接收，不消费 = 不丢消息）
  const paused = ref(false);
  const streamItems = ref<TItem[]>([]);

  watch(
    [options.events, selectedName, paused],
    ([evts, sel, isPaused]) => {
      if (isPaused) return;
      const fresh: TItem[] = [];
      for (const e of evts.slice(-STREAM_CAP * 2)) {
        const item = options.mapEvent(e, { selectedName: sel });
        if (item) fresh.push(item);
      }
      streamItems.value = fresh.slice(-STREAM_CAP);
    },
    { immediate: true },
  );

  function togglePause(): void {
    paused.value = !paused.value;
  }

  function clearStream(): void {
    streamItems.value = [];
  }

  return {
    selectedName,
    selected,
    select,
    controlPending,
    batchLoading,
    handleControl,
    runBatch,
    statusLabel,
    paused,
    streamItems,
    togglePause,
    clearStream,
  };
}
