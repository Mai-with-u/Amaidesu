// useToolCatalog.ts 工具目录的测试：清单加载与失败回落、分类导航派生
// （已知/未知分类的标签映射、计数聚合）、选中分类切换与过滤联动、
// 工具过滤、待重启计数。
//
// categories 未在返回值暴露（页面只消费派生量），清单经 API mock 注入、
// 断言走 categoryNav 等派生引用。

import { nextTick } from 'vue';
import { describe, expect, it, vi, beforeEach } from 'vitest';

import { toolsApi } from '@/api';
import { useToolCatalog } from '@/composables/tools/useToolCatalog';
import type { ToolCategoryView, ToolEntry, ToolProviderUnit } from '@/types';

vi.mock('@/api', async importOriginal => ({
  ...(await importOriginal<object>()),
  toolsApi: {
    listCategories: vi.fn(),
    list: vi.fn(),
  },
}));

function unit(partial: Partial<ToolProviderUnit>): ToolProviderUnit {
  return {
    key: 'vts',
    provider_name: 'vts',
    description: '',
    enabled: false,
    in_config: true,
    switchable: true,
    registered: true,
    tool_count: 0,
    health: null,
    ...partial,
  } as ToolProviderUnit;
}

function entry(partial: Partial<ToolEntry>): ToolEntry {
  return {
    name: 't',
    full_name: `p_${partial.name ?? 't'}`,
    parameters: {},
    disabled: false,
    ...partial,
  } as ToolEntry;
}

function category(partial: Partial<ToolCategoryView>): ToolCategoryView {
  return { category: 'avatar', providers: [], ...partial };
}

/** 两分类夹具：avatar（vts 已启用 + warudo 不可开关）+ unknown_cat（mcp_x） */
function mockTwoCategories(): void {
  vi.mocked(toolsApi.listCategories).mockResolvedValue({
    data: {
      categories: [
        category({
          category: 'avatar',
          providers: [
            unit({ key: 'vts', enabled: true, tool_count: 3 }),
            unit({
              key: 'warudo',
              provider_name: 'warudo',
              enabled: false,
              switchable: false,
              tool_count: 1,
              registered: false,
            }),
          ],
        }),
        category({ category: 'unknown_cat', providers: [unit({ key: 'mcp_x', tool_count: 2 })] }),
      ],
    },
  } as never);
  vi.mocked(toolsApi.list).mockResolvedValue({
    data: {
      tools: [
        entry({ name: 'move', provider: 'vts', category: 'avatar', description: '移动' }),
        entry({ name: 'wave', provider: 'mcp_x', category: 'unknown_cat' }),
      ],
    },
  } as never);
}

describe('useToolCatalog 加载', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('refreshAll 成功：清单落位，选中分类默认首个', async () => {
    mockTwoCategories();
    const c = useToolCatalog();
    await c.refreshAll();

    expect(c.loading.value).toBe(false);
    expect(c.error.value).toBeNull();
    expect(c.categoryNav.value).toHaveLength(2);
    expect(c.activeCategory.value).toBe('avatar');
  });

  it('refreshAll 失败：error 透出后端 detail 且清单清空', async () => {
    vi.mocked(toolsApi.listCategories).mockRejectedValue({
      response: { data: { detail: '后端不可用' } },
    });
    vi.mocked(toolsApi.list).mockRejectedValue(new Error('network'));

    const c = useToolCatalog();
    await c.refreshAll();

    expect(c.error.value).toBe('后端不可用');
    expect(c.categoryNav.value).toEqual([]);
    expect(c.tools.value).toEqual([]);
    expect(c.loading.value).toBe(false);
  });
});

describe('useToolCatalog 分类派生', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  async function setup(): Promise<ReturnType<typeof useToolCatalog>> {
    mockTwoCategories();
    const c = useToolCatalog();
    await c.refreshAll();
    return c;
  }

  it('categoryNav：已知分类取内置标签，未知分类透传原名；计数聚合正确', async () => {
    const c = await setup();
    const nav = c.categoryNav.value;
    expect(nav[0]).toMatchObject({
      category: 'avatar',
      label: '虚拟形象',
      toolCount: 4,
      enabledCount: 1,
    });
    expect(nav[1]).toMatchObject({
      category: 'unknown_cat',
      label: 'unknown_cat',
      toolCount: 2,
      enabledCount: 0,
    });
    expect(c.totalToolCount.value).toBe(6);
  });

  it('activeMeta：已知分类带描述，未知分类描述为空', async () => {
    const c = await setup();
    expect(c.activeMeta.value.label).toBe('虚拟形象');
    c.selectCategory('unknown_cat');
    expect(c.activeMeta.value).toEqual({ label: 'unknown_cat', description: '' });
  });

  it('切换分类时清空搜索词', async () => {
    const c = await setup();
    c.searchQuery.value = 'move';
    c.selectCategory('unknown_cat');
    await nextTick();
    expect(c.searchQuery.value).toBe('');
  });

  it('toolsOf：按分类+提供者过滤，搜索词匹配名称或描述', async () => {
    const c = await setup();
    const vtsUnit = unit({ key: 'vts' });
    expect(c.toolsOf(vtsUnit).map(t => t.name)).toEqual(['move']);
    c.searchQuery.value = 'wav';
    expect(c.toolsOf(vtsUnit)).toEqual([]);
    c.searchQuery.value = '移';
    expect(c.toolsOf(vtsUnit).map(t => t.name)).toEqual(['move']);
  });

  it('visibleProviders：搜索时隐藏无命中工具的提供者', async () => {
    const c = await setup();
    expect(c.visibleProviders.value).toHaveLength(2);
    c.searchQuery.value = 'move';
    expect(c.visibleProviders.value.map(u => u.key)).toEqual(['vts']);
  });

  it('开关计数：switchable 才计入 enabled/pendingRestart', async () => {
    const c = await setup();
    // avatar 分类：vts(switchable, enabled, registered) + warudo(非 switchable)
    expect(c.activeSwitchableCount.value).toBe(1);
    expect(c.activeEnabledCount.value).toBe(1);
    expect(c.pendingRestartCount.value).toBe(0);
  });

  it('pendingRestartCount：启用但未注册、停用但有工具的 switchable 提供者计入', async () => {
    vi.mocked(toolsApi.listCategories).mockResolvedValue({
      data: {
        categories: [
          category({
            providers: [
              unit({ key: 'a', enabled: true, registered: false }),
              unit({ key: 'b', enabled: false, tool_count: 2 }),
              unit({ key: 'c', enabled: true, registered: true }),
              unit({ key: 'd', enabled: false, tool_count: 0 }),
            ],
          }),
        ],
      },
    } as never);
    vi.mocked(toolsApi.list).mockResolvedValue({ data: { tools: [] } } as never);

    const c = useToolCatalog();
    await c.refreshAll();
    expect(c.pendingRestartCount.value).toBe(2);
  });
});
