import { defineStore } from 'pinia';
import { computed, ref } from 'vue';
import { componentApi } from '@/api';
import type {
  ComponentControlAction,
  ComponentControlResponse,
  ComponentListResponse,
  ComponentSummary,
} from '@/types';

export type ComponentGroup = 'collectors' | 'agents' | 'tools';

/**
 * 组件管理 store（v2.0）
 *
 * - 数据源：`GET /api/v1/components` 返回 `collectors / agents / tools` 三组。
 * - 控制端点：`POST /api/v1/components/{group}/{name}/control`。
 *
 * getters：
 * - `collectorsList` / `agentsList` / `toolsList`：各组原始 ComponentSummary 列表。
 * - `collectorsTotal` / `agentsTotal` / `toolsTotal`：各组配置总数（来源：tools.toml
 *   [tools.output.config] 段 / tools.toml [tools.perception.config] 段 / agents.toml [agents] 段）。
 *   ⚠️ **配置总数** ≠ 运行时 ToolRegistry 实例总数——前者是 TOML 声明的"提供方数"，
 *   后者是 provider 展开后注册的所有 spec（见 `GET /api/v1/system/status` 的
 *   `groups.tools.total`，由 `len(tool_registry)` 计算）。
 * - `batchControl(group, action)`：批量启停同一组下所有未在目标状态的组件。
 */
export const useComponentsStore = defineStore('components', () => {
  const components = ref<ComponentListResponse | null>(null);
  const loading = ref(false);
  const error = ref<string | null>(null);

  async function fetchComponents() {
    loading.value = true;
    error.value = null;
    try {
      const response = await componentApi.getAll();
      components.value = response.data;
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch components';
    } finally {
      loading.value = false;
    }
  }

  async function controlComponent(
    group: ComponentGroup,
    name: string,
    action: ComponentControlAction,
  ): Promise<ComponentControlResponse> {
    try {
      const response = await componentApi.control(group, name, { action });
      await fetchComponents();
      return response.data;
    } catch (e) {
      throw e instanceof Error ? e : new Error('Failed to control component');
    }
  }

  // ===== Getters（共享给 Dashboard / Collectors / Agents / Tools 页面） =====

  const collectorsList = computed<ComponentSummary[]>(() => components.value?.collectors ?? []);
  const agentsList = computed<ComponentSummary[]>(() => components.value?.agents ?? []);
  const toolsList = computed<ComponentSummary[]>(() => components.value?.tools ?? []);

  const collectorsStartedCount = computed(
    () => collectorsList.value.filter(c => c.is_started).length,
  );
  const agentsStartedCount = computed(() => agentsList.value.filter(c => c.is_started).length);

  const collectorsTotal = computed(() => collectorsList.value.length);
  const agentsTotal = computed(() => agentsList.value.length);
  /** 配置声明的"提供方"数（与运行时 ToolRegistry 实例总数不同；见 store 注释） */
  const toolsTotal = computed(() => toolsList.value.length);

  const collectorsEnabledCount = computed(
    () => collectorsList.value.filter(c => c.is_enabled).length,
  );
  const agentsEnabledCount = computed(() => agentsList.value.filter(c => c.is_enabled).length);

  /**
   * 批量启停一组内全部组件。返回 `{ succeeded, failed, messages }`。
   * tools 组后端无运行/启用语义，调用方应在 UI 层禁用此入口。
   */
  async function batchControl(
    group: Exclude<ComponentGroup, 'tools'>,
    action: 'start' | 'stop',
  ): Promise<{ succeeded: number; failed: number; messages: string[] }> {
    const list = group === 'collectors' ? collectorsList.value : agentsList.value;
    const targets =
      action === 'start' ? list.filter(c => !c.is_started) : list.filter(c => c.is_started);

    const messages: string[] = [];
    if (targets.length === 0) {
      return { succeeded: 0, failed: 0, messages: ['当前组内无符合状态的组件'] };
    }

    const results = await Promise.allSettled(
      targets.map(c => controlComponent(group, c.name, action)),
    );
    let succeeded = 0;
    let failed = 0;
    results.forEach(r => {
      if (r.status === 'fulfilled') {
        succeeded++;
        messages.push(r.value.message);
      } else {
        failed++;
        const reason = r.reason instanceof Error ? r.reason.message : String(r.reason);
        messages.push(reason);
      }
    });
    return { succeeded, failed, messages };
  }

  return {
    // state
    components,
    loading,
    error,
    // actions
    fetchComponents,
    controlComponent,
    batchControl,
    // getters — 组列表
    collectorsList,
    agentsList,
    toolsList,
    // getters — 计数（用于 Dashboard KPI / 页面 header）
    collectorsTotal,
    collectorsStartedCount,
    collectorsEnabledCount,
    agentsTotal,
    agentsStartedCount,
    agentsEnabledCount,
    toolsTotal,
  };
});
