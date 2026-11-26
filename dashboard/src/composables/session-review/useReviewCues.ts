/**
 * 场次复盘页「本场记忆线索」的加载状态机
 *
 * 单场语义：切场先清空并丢弃过期响应（快速切换场次不会串卡片）；加载错误与空态
 * 分开（error 非空时页面渲染错误、不显示"这一场没有推过记忆线索"）。
 * 来源筛选只在本卡本地做，归 ReviewCuesCard 组件（条目更新时复位为"全部"）。
 *
 * 不含定时器 / 订阅 / WebSocket，无 keep-alive 生命周期语义。
 */
import { ref } from 'vue';
import type { ReviewCueItem } from '@/types';
import { reviewApi } from '@/api';
import { getApiErrorMessage } from '@/utils/apiError';

export function useReviewCues() {
  const items = ref<ReviewCueItem[]>([]);
  const loading = ref(false);
  /** 加载错误：非空时页面不渲染列表 */
  const error = ref('');
  let requestSeq = 0;

  async function load(liveSessionId: number | null): Promise<void> {
    const seq = ++requestSeq;
    error.value = '';
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
      error.value = getApiErrorMessage(err, '本场记忆线索加载失败');
    } finally {
      if (seq === requestSeq) loading.value = false;
    }
  }

  return { items, loading, error, load };
}
