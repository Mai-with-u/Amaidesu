/**
 * 长期记忆编辑（主播记忆页私有）：按场次类型读写 MEMORY.md 全文，查看改动记录、撤回某次改动。
 *
 * 正式与彩排各一份：切换类型时重新读取；编辑区与已保存内容分开存，
 * dirty 表示有未保存的改动（切换类型前由页面提示）。保存是整份覆盖写，
 * 下一次开播起生效——开播时读出、整场不变。每次写入（收播整理、保存、撤回）
 * 都有一条改动记录，读取、保存、撤回之后都刷新记录列表。
 * 不含定时器/订阅/WebSocket，无 keep-alive 生命周期语义。
 */

import { computed, ref } from 'vue';
import { memoryApi } from '@/api';
import { getApiErrorMessage } from '@/utils/apiError';
import type { LongMemoryHistoryItem, SessionKind } from '@/types';

export function useLongMemory() {
  const kind = ref<SessionKind>('live');
  const draft = ref('');
  const saved = ref('');
  const path = ref('');
  const updatedAtMs = ref(0);
  const loading = ref(false);
  const saving = ref(false);
  const error = ref('');
  const history = ref<LongMemoryHistoryItem[]>([]);
  const historyLoading = ref(false);

  const dirty = computed(() => draft.value.trim() !== saved.value.trim());

  function apply(data: { text: string; path: string; updated_at_ms: number }): void {
    draft.value = data.text;
    saved.value = data.text;
    path.value = data.path;
    updatedAtMs.value = data.updated_at_ms;
  }

  /** 刷新当前类型的改动记录（新的在前）；失败只写 error，不清空已有列表 */
  async function loadHistory(): Promise<void> {
    historyLoading.value = true;
    try {
      history.value = (await memoryApi.listLongMemoryHistory(kind.value)).data.items;
    } catch (err) {
      error.value = getApiErrorMessage(err, '改动记录加载失败');
    } finally {
      historyLoading.value = false;
    }
  }

  /** 读当前类型的全文（覆盖编辑区未保存的改动）与改动记录 */
  async function load(): Promise<void> {
    loading.value = true;
    error.value = '';
    try {
      apply((await memoryApi.getLongMemory(kind.value)).data);
    } catch (err) {
      error.value = getApiErrorMessage(err, '长期记忆加载失败');
    } finally {
      loading.value = false;
    }
    await loadHistory();
  }

  /** 某次改动之前的全文（只读查看用）；失败返回 null 并写 error */
  async function loadSnapshot(entryId: string): Promise<string | null> {
    try {
      return (await memoryApi.getLongMemorySnapshot(kind.value, entryId)).data.text;
    } catch (err) {
      error.value = getApiErrorMessage(err, '旧版本加载失败');
      return null;
    }
  }

  /** 撤回某次改动：恢复到它之前的全文（覆盖编辑区）；返回是否成功 */
  async function restore(entryId: string): Promise<boolean> {
    saving.value = true;
    error.value = '';
    try {
      apply((await memoryApi.restoreLongMemory(kind.value, entryId)).data);
      await loadHistory();
      return true;
    } catch (err) {
      error.value = getApiErrorMessage(err, '撤回失败');
      return false;
    } finally {
      saving.value = false;
    }
  }

  /** 切换正式 / 彩排并读取那一份 */
  async function switchKind(next: SessionKind): Promise<void> {
    kind.value = next;
    await load();
  }

  /** 整份保存编辑区内容；返回是否成功 */
  async function save(): Promise<boolean> {
    saving.value = true;
    error.value = '';
    try {
      apply((await memoryApi.saveLongMemory(kind.value, draft.value)).data);
      await loadHistory();
      return true;
    } catch (err) {
      error.value = getApiErrorMessage(err, '长期记忆保存失败');
      return false;
    } finally {
      saving.value = false;
    }
  }

  return {
    kind,
    draft,
    path,
    updatedAtMs,
    loading,
    saving,
    error,
    dirty,
    history,
    historyLoading,
    load,
    loadHistory,
    loadSnapshot,
    switchKind,
    save,
    restore,
  };
}
