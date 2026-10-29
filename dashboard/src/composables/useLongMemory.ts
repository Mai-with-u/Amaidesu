/**
 * 长期记忆编辑（主播记忆页私有）：按场次类型读写 MEMORY.md 全文。
 *
 * 正式与彩排各一份：切换类型时重新读取；编辑区与已保存内容分开存，
 * dirty 表示有未保存的改动（切换类型前由页面提示）。保存是整份覆盖写，
 * 下一次开播起生效——开播时整份读出、整场不变。
 * 不含定时器/订阅/WebSocket，无 keep-alive 生命周期语义。
 */

import { computed, ref } from 'vue';
import { memoryApi } from '@/api';
import { getApiErrorMessage } from '@/utils/apiError';
import type { SessionKind } from '@/types';

export function useLongMemory() {
  const kind = ref<SessionKind>('live');
  const draft = ref('');
  const saved = ref('');
  const path = ref('');
  const updatedAtMs = ref(0);
  const loading = ref(false);
  const saving = ref(false);
  const error = ref('');

  const dirty = computed(() => draft.value.trim() !== saved.value.trim());

  function apply(data: { text: string; path: string; updated_at_ms: number }): void {
    draft.value = data.text;
    saved.value = data.text;
    path.value = data.path;
    updatedAtMs.value = data.updated_at_ms;
  }

  /** 读当前类型的全文（覆盖编辑区未保存的改动） */
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
      return true;
    } catch (err) {
      error.value = getApiErrorMessage(err, '长期记忆保存失败');
      return false;
    } finally {
      saving.value = false;
    }
  }

  return { kind, draft, path, updatedAtMs, loading, saving, error, dirty, load, switchKind, save };
}
