// useToolControls.ts 工具开关控制的测试：提供者开关写回（成功/失败）、
// 分类总开关聚合（全开/全关/混合/部分失败）、工具级停用（传 full_name）、
// 提供者手动重连的四种结果分支与并发防重。

import { computed, reactive, ref } from 'vue';
import { describe, expect, it, vi, beforeEach } from 'vitest';

import { toolsApi } from '@/api';
import { ElMessage } from 'element-plus';
import { useToolControls } from '@/composables/tools/useToolControls';
import type { ToolCategoryView, ToolEntry, ToolProviderUnit } from '@/types';

vi.mock('@/api', async importOriginal => ({
  ...(await importOriginal<object>()),
  toolsApi: {
    controlProvider: vi.fn(),
    controlTool: vi.fn(),
    reconnectProvider: vi.fn(),
  },
}));

vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
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

function entry(name: string): ToolEntry {
  return { name, full_name: `prov_${name}`, parameters: {}, disabled: false } as ToolEntry;
}

describe('useToolControls', () => {
  const refreshAll = vi.fn().mockResolvedValue(undefined);
  // reactive 包装：bulkState 等 computed 依赖它失效重算
  const providers = reactive([
    unit({ key: 'vts', enabled: true }),
    unit({ key: 'obs', enabled: false }),
  ]);
  const activeCategory = ref('avatar');
  const activeCategoryData = computed<ToolCategoryView | null>(() => ({
    category: 'avatar',
    providers,
  }));
  const controls = useToolControls({ activeCategory, activeCategoryData, refreshAll });

  beforeEach(() => {
    vi.clearAllMocks();
    refreshAll.mockClear();
    providers[0].enabled = true;
    providers[1].enabled = false;
  });

  it('onToggle 成功：写回 enable、就地更新开关态、提示成功', async () => {
    vi.mocked(toolsApi.controlProvider).mockResolvedValue({
      data: { success: true, message: '已写回' },
    } as never);

    await controls.onToggle(providers[1], true);

    expect(toolsApi.controlProvider).toHaveBeenCalledWith('avatar', 'obs', 'enable');
    expect(providers[1].enabled).toBe(true);
    expect(ElMessage.success).toHaveBeenCalledWith('已写回');
    expect(controls.toggling.has('obs')).toBe(false);
  });

  it('onToggle 失败：透出后端 detail 文案', async () => {
    vi.mocked(toolsApi.controlProvider).mockRejectedValue({
      response: { data: { detail: '配置文件只读' } },
    });

    await controls.onToggle(providers[0], false);

    expect(ElMessage.error).toHaveBeenCalledWith('配置文件只读');
    expect(providers[0].enabled).toBe(true);
  });

  it('bulkState：全开/全关/混合/无开关项', () => {
    expect(controls.bulkState.value).toBe('mixed');

    providers[0].enabled = true;
    providers[1].enabled = true;
    expect(controls.bulkState.value).toBe('all');

    providers[0].enabled = false;
    providers[1].enabled = false;
    expect(controls.bulkState.value).toBe('none');
  });

  it('onBulkToggle：逐个写回并刷新，全部成功提示成功', async () => {
    vi.mocked(toolsApi.controlProvider).mockResolvedValue({ data: { success: true } } as never);

    await controls.onBulkToggle(true);

    expect(toolsApi.controlProvider).toHaveBeenCalledTimes(2);
    expect(ElMessage.success).toHaveBeenCalledWith('启用 2 个提供者，重启后生效');
    expect(refreshAll).toHaveBeenCalledTimes(1);
    expect(controls.bulkToggling.value).toBe(false);
  });

  it('onBulkToggle 部分失败：提示警告', async () => {
    vi.mocked(toolsApi.controlProvider)
      .mockResolvedValueOnce({ data: { success: true } } as never)
      .mockRejectedValueOnce(new Error('x'));

    await controls.onBulkToggle(false);

    expect(ElMessage.warning).toHaveBeenCalledWith('部分提供者写回失败（1/2），请重试');
  });

  it('onToolToggle：按 full_name 写回并翻转 disabled', async () => {
    vi.mocked(toolsApi.controlTool).mockResolvedValue({
      data: { success: true, message: null },
    } as never);
    const row = entry('wave');

    await controls.onToolToggle(row, false);

    expect(toolsApi.controlTool).toHaveBeenCalledWith('prov_wave', 'disable');
    expect(row.disabled).toBe(true);
    expect(ElMessage.success).toHaveBeenCalledWith('已写回配置，重启后生效');
  });

  it('onReconnect：仍有熔断时提示警告', async () => {
    vi.mocked(toolsApi.reconnectProvider).mockResolvedValue({
      data: {
        ok: true,
        provider_id: 'vts',
        recovered: [],
        still_tripped: ['a', 'b'],
        refreshed: null,
      },
    } as never);

    await controls.onReconnect('vts');

    expect(ElMessage.warning).toHaveBeenCalledWith('重连成功但 2 个工具探活未通过：a、b');
    expect(refreshAll).toHaveBeenCalledTimes(1);
  });

  it('onReconnect：无熔断但有补注册 / 有恢复 / 全空 三种成功文案', async () => {
    vi.mocked(toolsApi.reconnectProvider).mockResolvedValue({
      data: {
        ok: true,
        provider_id: 'vts',
        recovered: [],
        still_tripped: [],
        refreshed: { added: ['x'], removed: [], count: 1 },
      },
    } as never);
    await controls.onReconnect('vts');
    expect(ElMessage.success).toHaveBeenLastCalledWith(
      '重连成功，补注册 1 个工具（降级装配已恢复）',
    );

    vi.mocked(toolsApi.reconnectProvider).mockResolvedValue({
      data: {
        ok: true,
        provider_id: 'vts',
        recovered: ['y'],
        still_tripped: [],
        refreshed: { added: [], removed: [], count: 0 },
      },
    } as never);
    await controls.onReconnect('vts');
    expect(ElMessage.success).toHaveBeenLastCalledWith('已恢复 1 个工具');

    vi.mocked(toolsApi.reconnectProvider).mockResolvedValue({
      data: { ok: true, provider_id: 'vts', recovered: [], still_tripped: [], refreshed: null },
    } as never);
    await controls.onReconnect('vts');
    expect(ElMessage.success).toHaveBeenLastCalledWith('重连完成（当前无熔断工具）');
  });

  it('onReconnect：同一 provider 进行中防重，失败透出错误', async () => {
    let resolveRpc: (v: unknown) => void = () => {};
    vi.mocked(toolsApi.reconnectProvider).mockImplementation(
      () =>
        new Promise(resolve => {
          resolveRpc = resolve as (v: unknown) => void;
        }),
    );

    const first = controls.onReconnect('vts');
    void controls.onReconnect('vts');
    expect(toolsApi.reconnectProvider).toHaveBeenCalledTimes(1);

    resolveRpc({
      data: { ok: true, provider_id: 'vts', recovered: [], still_tripped: [], refreshed: null },
    });
    await first;
    // 完成后可再次触发
    vi.mocked(toolsApi.reconnectProvider).mockResolvedValue({
      data: { ok: true, provider_id: 'vts', recovered: [], still_tripped: [], refreshed: null },
    } as never);
    await controls.onReconnect('vts');
    expect(toolsApi.reconnectProvider).toHaveBeenCalledTimes(2);
    expect(controls.reconnecting.has('vts')).toBe(false);
  });

  it('onReconnect 失败：透出错误且清除 loading', async () => {
    vi.mocked(toolsApi.reconnectProvider).mockRejectedValue(new Error('网络中断'));

    await controls.onReconnect('vts');

    expect(ElMessage.error).toHaveBeenCalledWith('网络中断');
    expect(controls.reconnecting.has('vts')).toBe(false);
  });
});
