/**
 * 工具目录：分类/工具清单加载、分类导航与选中、工具过滤。
 * 页面私有状态（仅工具页消费）。
 */

import { computed, ref, watch } from 'vue';
import { toolsApi } from '@/api';
import type { ToolCategoryView, ToolEntry, ToolProviderUnit } from '@/types';

const CATEGORY_META: Record<string, { label: string; description: string }> = {
  avatar: {
    label: '虚拟形象',
    description: 'VTubeStudio / VRChat / Warudo 等虚拟形象后端提供的工具',
  },
  studio: { label: '演播室', description: 'OBS 等演播室控制后端提供的工具' },
  vision: { label: '视觉', description: '屏幕感知能力（look_at_screen）' },
  memory: {
    label: '记忆',
    description: '观众事实与画像查询（query_memory / query_viewer_profile）',
  },
  mcp: { label: 'MCP', description: '外部 MCP server 提供的工具' },
  game: { label: '游戏 Agent', description: '游戏 Agent 自声明的工具（text_adv 等）' },
  framework: { label: '框架', description: '框架内置工具（AgentControl 等）' },
};

function categoryMeta(category: string) {
  return CATEGORY_META[category] ?? { label: category, description: '' };
}

export function useToolCatalog() {
  const categories = ref<ToolCategoryView[]>([]);
  const tools = ref<ToolEntry[]>([]);
  const loading = ref(false);
  const error = ref<string | null>(null);

  async function refreshAll() {
    loading.value = true;
    error.value = null;
    try {
      const [catResp, toolResp] = await Promise.all([toolsApi.listCategories(), toolsApi.list()]);
      categories.value = catResp.data.categories ?? [];
      tools.value = toolResp.data.tools ?? [];
      ensureActiveCategory();
    } catch (e) {
      error.value = e instanceof Error ? e.message : '无法加载工具数据';
      categories.value = [];
      tools.value = [];
    } finally {
      loading.value = false;
    }
  }

  // 分类列表（左侧）

  const activeCategory = ref('');

  const categoryNav = computed(() =>
    categories.value.map(cat => {
      const switchable = cat.providers.filter(p => p.switchable);
      return {
        category: cat.category,
        label: categoryMeta(cat.category).label,
        toolCount: cat.providers.reduce((sum, p) => sum + p.tool_count, 0),
        enabledCount: switchable.filter(p => p.enabled).length,
      };
    }),
  );

  const totalToolCount = computed(() =>
    categoryNav.value.reduce((sum, cat) => sum + cat.toolCount, 0),
  );

  function ensureActiveCategory() {
    if (!categories.value.some(cat => cat.category === activeCategory.value)) {
      activeCategory.value = categories.value[0]?.category ?? '';
    }
  }

  function selectCategory(category: string): void {
    activeCategory.value = category;
  }

  const activeMeta = computed(() => categoryMeta(activeCategory.value));

  const activeCategoryData = computed(
    () => categories.value.find(cat => cat.category === activeCategory.value) ?? null,
  );

  const activeSwitchableCount = computed(
    () => (activeCategoryData.value?.providers ?? []).filter(p => p.switchable).length,
  );

  const activeEnabledCount = computed(
    () => (activeCategoryData.value?.providers ?? []).filter(p => p.switchable && p.enabled).length,
  );

  const pendingRestartCount = computed(
    () =>
      (activeCategoryData.value?.providers ?? []).filter(
        p => p.switchable && (p.enabled ? p.registered === false : p.tool_count > 0),
      ).length,
  );

  // 工具过滤

  const searchQuery = ref('');

  function toolsOf(unit: ToolProviderUnit): ToolEntry[] {
    const q = searchQuery.value.trim().toLowerCase();
    return tools.value.filter(t => {
      if (t.category !== activeCategory.value || t.provider !== unit.provider_name) return false;
      if (q && !`${t.name} ${t.description ?? ''}`.toLowerCase().includes(q)) return false;
      return true;
    });
  }

  const visibleProviders = computed<ToolProviderUnit[]>(() => {
    const units = activeCategoryData.value?.providers ?? [];
    if (!searchQuery.value.trim()) return units;
    return units.filter(unit => toolsOf(unit).length > 0);
  });

  watch(activeCategory, () => {
    searchQuery.value = '';
  });

  return {
    tools,
    loading,
    error,
    refreshAll,
    activeCategory,
    categoryNav,
    totalToolCount,
    selectCategory,
    activeMeta,
    activeCategoryData,
    activeSwitchableCount,
    activeEnabledCount,
    pendingRestartCount,
    searchQuery,
    toolsOf,
    visibleProviders,
  };
}
