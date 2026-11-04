/**
 * 场次档案行的加载状态机（记忆页档案面板与场次复盘页共用）
 *
 * 切场语义：换场次先清空当前行再发请求（新场次下不短暂显示上一场的行）；
 * 每次加载带请求序号，先发后到的响应直接丢弃（快速连点场次不会互相覆盖）。
 * 错误与空态分开：error 非空由调用方渲染错误，不把空列表当"这一场没有档案"。
 * 刷新同一场次可保留旧行（clearLines=false），只开 loading。
 *
 * 不含定时器 / 订阅 / WebSocket，无 keep-alive 生命周期语义。
 */
import { ref } from 'vue';
import type { ArchiveLineItem } from '@/types';
import { ledgerApi } from '@/api';
import { getApiErrorMessage } from '@/utils/apiError';

export interface ArchiveLinesLoadOptions {
  /** 默认（或不传）= 切场：请求发出前清空旧行；同一场刷新传 false 保留旧行 */
  clearLines?: boolean;
}

export function useArchiveLines() {
  const lines = ref<ArchiveLineItem[]>([]);
  const loading = ref(false);
  const error = ref('');
  let requestSeq = 0;

  async function load(
    liveSessionId: number | null,
    options: ArchiveLinesLoadOptions = {},
  ): Promise<void> {
    const seq = ++requestSeq;
    error.value = '';
    if (liveSessionId === null) {
      lines.value = [];
      loading.value = false;
      return;
    }
    if (options.clearLines !== false) lines.value = [];
    loading.value = true;
    try {
      const response = await ledgerApi.listArchive(liveSessionId);
      if (seq !== requestSeq) return;
      lines.value = response.data.items;
    } catch (err) {
      if (seq !== requestSeq) return;
      lines.value = [];
      error.value = getApiErrorMessage(err, '场次档案加载失败');
    } finally {
      if (seq === requestSeq) loading.value = false;
    }
  }

  return { lines, loading, error, load };
}
