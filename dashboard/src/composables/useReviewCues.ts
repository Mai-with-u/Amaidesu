/**
 * 场次复盘页「本场线索卡」的加载与来源筛选
 *
 * 单场语义：切场先清空并丢弃过期响应（快速切换场次不会串卡片）；加载错误与空态
 * 分开（error 非空时页面渲染错误、不显示"这一场没有推过线索卡"）。筛选只在本地做，
 * 切场时复位为"全部"。
 *
 * 不含定时器 / 订阅 / WebSocket，无 keep-alive 生命周期语义。
 */
import { computed, ref } from 'vue';
import type { ReviewCueItem } from '@/types';
import { reviewApi } from '@/api';
import { getApiErrorMessage } from '@/utils/apiError';
import { cueSourceOptions, filterCuesBySource } from '@/utils/memoryCues';

export function useReviewCues() {
  const items = ref<ReviewCueItem[]>([]);
  const loading = ref(false);
  /** 加载错误：非空时页面不渲染列表 */
  const error = ref('');
  /** 来源筛选：空串 = 全部 */
  const source = ref('');
  const sourceOptions = computed(() => cueSourceOptions(items.value));
  const visible = computed(() => filterCuesBySource(items.value, source.value));
  let requestSeq = 0;

  async function load(liveSessionId: number | null): Promise<void> {
    const seq = ++requestSeq;
    error.value = '';
    source.value = '';
    items.value = [];
    if (liveSessionId === null) {
      loading.value = false;
      return;
    }
    loading.value = true;
    try {
      const response = await reviewApi.listCues(liveSessionId);
      if (seq !== requestSeq) return;
      items.value = response.data.items;
    } catch (err) {
      if (seq !== requestSeq) return;
      error.value = getApiErrorMessage(err, '本场线索卡加载失败');
    } finally {
      if (seq === requestSeq) loading.value = false;
    }
  }

  return { items, visible, loading, error, source, sourceOptions, load };
}
