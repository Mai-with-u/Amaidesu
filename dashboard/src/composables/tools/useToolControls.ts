/**
 * 工具开关控制：提供者开关 / 分类批量开关 / 工具级停用 / 提供者手动重连。
 * 页面私有状态。选中分类与数据刷新由 useToolCatalog 持有、参数注入。
 */

import { computed, reactive, ref, type ComputedRef, type Ref } from 'vue';
import { ElMessage } from 'element-plus';
import { toolsApi } from '@/api';
import { getApiErrorMessage } from '@/utils/apiError';
import type { ToolCategoryView, ToolEntry, ToolProviderUnit } from '@/types';

export interface UseToolControlsOptions {
  activeCategory: Ref<string>;
  activeCategoryData: ComputedRef<ToolCategoryView | null>;
  refreshAll: () => Promise<void>;
}

export function useToolControls(options: UseToolControlsOptions) {
  const { activeCategory, activeCategoryData, refreshAll } = options;

  // 提供者开关

  const toggling = reactive(new Set<string>());

  async function onToggle(unit: ToolProviderUnit, next: boolean) {
    toggling.add(unit.key);
    try {
      const response = await toolsApi.controlProvider(
        activeCategory.value,
        unit.key,
        next ? 'enable' : 'disable',
      );
      unit.enabled = next;
      ElMessage.success(response.data.message ?? '已写回配置，重启后生效');
    } catch (e) {
      ElMessage.error(getApiErrorMessage(e, `开关写回失败（${unit.key}）`));
    } finally {
      toggling.delete(unit.key);
    }
  }

  // 分类总开关（聚合操作：一键开/关全部提供者）

  const bulkSwitchable = computed(() =>
    (activeCategoryData.value?.providers ?? []).some(p => p.switchable),
  );

  const bulkState = computed<'all' | 'none' | 'mixed'>(() => {
    const switchable = (activeCategoryData.value?.providers ?? []).filter(p => p.switchable);
    if (switchable.length === 0) return 'none';
    const on = switchable.filter(p => p.enabled).length;
    if (on === switchable.length) return 'all';
    if (on === 0) return 'none';
    return 'mixed';
  });

  const bulkToggling = ref(false);

  async function onBulkToggle(next: boolean) {
    const units = (activeCategoryData.value?.providers ?? []).filter(p => p.switchable);
    if (units.length === 0) return;
    bulkToggling.value = true;
    try {
      const results = await Promise.allSettled(
        units.map(u =>
          toolsApi.controlProvider(activeCategory.value, u.key, next ? 'enable' : 'disable'),
        ),
      );
      const failed = results.filter(r => r.status === 'rejected').length;
      if (failed > 0) {
        ElMessage.warning(`部分提供者写回失败（${failed}/${units.length}），请重试`);
      } else {
        ElMessage.success(`${next ? '启用' : '停用'} ${units.length} 个提供者，重启后生效`);
      }
    } finally {
      bulkToggling.value = false;
      await refreshAll();
    }
  }

  // 工具级停用

  const toolToggling = reactive(new Set<string>());

  async function onToolToggle(row: ToolEntry, next: boolean) {
    toolToggling.add(row.name);
    try {
      // 停用集合按注册表全名索引，必须传 full_name（裸名会被 apply_disabled 过滤丢弃）
      const response = await toolsApi.controlTool(row.full_name, next ? 'enable' : 'disable');
      row.disabled = !next;
      ElMessage.success(response.data.message ?? '已写回配置，重启后生效');
    } catch (e) {
      ElMessage.error(getApiErrorMessage(e, `开关写回失败（${row.name}）`));
    } finally {
      toolToggling.delete(row.name);
    }
  }

  // 工具提供者手动重连
  //
  // 按 provider 维度防重：同一 Provider 下多行触发同一调用，按行名防重会出现
  // loading 不同步；用 provider_id 做 Set 键，保证任意一行触发都共享 loading。
  const reconnecting = reactive(new Set<string>());

  async function onReconnect(providerId: string) {
    if (!providerId || reconnecting.has(providerId)) return;
    reconnecting.add(providerId);
    try {
      const resp = await toolsApi.reconnectProvider(providerId);
      const recoveredCount = resp.data.recovered.length;
      const stillTrippedCount = resp.data.still_tripped.length;
      const addedCount = resp.data.refreshed?.added.length ?? 0;
      if (stillTrippedCount > 0) {
        ElMessage.warning(
          `重连成功但 ${stillTrippedCount} 个工具探活未通过：${resp.data.still_tripped.join('、')}`,
        );
      } else if (addedCount > 0) {
        ElMessage.success(`重连成功，补注册 ${addedCount} 个工具（降级装配已恢复）`);
      } else if (recoveredCount > 0) {
        ElMessage.success(`已恢复 ${recoveredCount} 个工具`);
      } else {
        ElMessage.success('重连完成（当前无熔断工具）');
      }
      await refreshAll();
    } catch (e) {
      ElMessage.error(getApiErrorMessage(e, '重连失败'));
    } finally {
      reconnecting.delete(providerId);
    }
  }

  return {
    toggling,
    bulkSwitchable,
    bulkState,
    bulkToggling,
    onBulkToggle,
    toolToggling,
    onToolToggle,
    onToggle,
    reconnecting,
    onReconnect,
  };
}
