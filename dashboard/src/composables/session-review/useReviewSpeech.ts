/**
 * 场次复盘页「本场台词」的加载与删除状态机
 *
 * 单场语义：切场先清空并丢弃过期响应（快速切换场次不会串台词）；加载错误与
 * 空态分开（error 非空时页面渲染错误、不显示"这一场还没有台词"）。删除单句由
 * 页面先弹二次确认，成功后页面再刷新台词与档案两块；删除失败写 actionError
 * （列表保留，不因一次删除失败把整块内容藏掉）。
 *
 * 不含定时器 / 订阅 / WebSocket，无 keep-alive 生命周期语义。
 */
import { ref } from 'vue';
import type { ReviewSpeechDeleteResponse, ReviewSpeechLine } from '@/types';
import { reviewApi } from '@/api';
import { getApiErrorMessage } from '@/utils/apiError';

export interface ReviewSpeechLoadOptions {
  /** 刷新同一场次传 false 保留旧台词；切场默认先清空 */
  clearLines?: boolean;
}

export function useReviewSpeech() {
  const items = ref<ReviewSpeechLine[]>([]);
  const loading = ref(false);
  /** 加载错误：非空时页面不渲染列表（不与"这一场还没有台词"的空态混淆） */
  const error = ref('');
  /** 删除单句的失败原因：列表照常显示，错误就地提示 */
  const actionError = ref('');
  const busyChatId = ref<number | null>(null);
  let requestSeq = 0;

  async function load(
    liveSessionId: number | null,
    options: ReviewSpeechLoadOptions = {},
  ): Promise<void> {
    const seq = ++requestSeq;
    error.value = '';
    actionError.value = '';
    if (liveSessionId === null) {
      items.value = [];
      loading.value = false;
      return;
    }
    if (options.clearLines !== false) items.value = [];
    loading.value = true;
    try {
      const response = await reviewApi.listSpeech(liveSessionId);
      if (seq !== requestSeq) return;
      items.value = response.data.items;
    } catch (err) {
      if (seq !== requestSeq) return;
      items.value = [];
      error.value = getApiErrorMessage(err, '本场台词加载失败');
    } finally {
      if (seq === requestSeq) loading.value = false;
    }
  }

  /** 删除一句台词；成功返回后端回执（页面据它提示并刷新档案），失败返回 null 并写 actionError */
  async function remove(chatId: number): Promise<ReviewSpeechDeleteResponse | null> {
    busyChatId.value = chatId;
    actionError.value = '';
    try {
      const response = await reviewApi.deleteSpeech(chatId);
      return response.data;
    } catch (err) {
      actionError.value = getApiErrorMessage(err, '删除台词失败');
      return null;
    } finally {
      busyChatId.value = null;
    }
  }

  return { items, loading, error, actionError, busyChatId, load, remove };
}
