/**
 * 编排页提问箱 / 话题卡抽屉：卡池列表、快速塞卡、启停、删除、复原已用标记。
 *
 * 话题卡是主播杂谈冷场时的自备素材（提问箱问题 / 往场趣事 / 二选一态度）。导演在直播中
 * 随时塞一张新卡，后端落库后她下一个冷场窗就能看到，所以本组合式只管增删改与列表刷新，
 * 不订阅推送。生命周期：无定时器、无订阅、无 WebSocket，keep-alive 失活期间不持有资源，
 * 抽屉每次打开时调用 `fetchCards` 拉一次最新卡池。
 */

import { computed, reactive, ref } from 'vue';
import { ElMessage } from 'element-plus';
import { talkCardsApi } from '@/api';
import { getApiErrorMessage } from '@/utils/apiError';
import { confirmAction } from '@/utils/confirmAction';
import type { NewTalkCard, TalkCard, TalkCardKind } from '@/types';

/** 卡种中文名（抽屉标签与下拉选项共用）。 */
export const TALK_CARD_KIND_LABELS: Record<TalkCardKind, string> = {
  marshmallow: '提问箱',
  episode: '往场趣事',
  opinion: '二选一',
};

function emptyDraft(): Required<Pick<NewTalkCard, 'kind' | 'title' | 'body' | 'asker'>> {
  return { kind: 'marshmallow', title: '', body: '', asker: '' };
}

export function useTalkCards() {
  const cards = ref<TalkCard[]>([]);
  const loading = ref(false);
  const saving = ref(false);
  const draft = reactive(emptyDraft());

  /** 已用过的卡数（"复原已用"按钮的角标与可用性）。 */
  const usedCount = computed(() => cards.value.filter(c => c.used_at_ms != null).length);
  /** 还能被她抽到的卡数（启用且没用过）。 */
  const availableCount = computed(
    () => cards.value.filter(c => c.enabled && c.used_at_ms == null).length,
  );

  async function fetchCards(): Promise<void> {
    loading.value = true;
    try {
      const res = await talkCardsApi.list();
      if (!res.data.success) {
        ElMessage.warning(res.data.message || '话题卡读取失败');
        cards.value = [];
        return;
      }
      cards.value = res.data.cards;
    } catch (err) {
      ElMessage.error(getApiErrorMessage(err, '话题卡读取失败'));
    } finally {
      loading.value = false;
    }
  }

  /** 塞一张新卡：标题必填；成功后清空草稿（保留卡种，方便连续塞同类问题）。 */
  async function addCard(): Promise<boolean> {
    const title = draft.title.trim();
    if (!title) {
      ElMessage.warning('先写一句问题或话头');
      return false;
    }
    saving.value = true;
    try {
      const res = await talkCardsApi.create({
        kind: draft.kind,
        title,
        body: draft.body.trim(),
        asker: draft.asker.trim(),
      });
      if (!res.data.success) {
        ElMessage.error(res.data.message || '塞卡失败');
        return false;
      }
      cards.value = [...cards.value, ...res.data.cards];
      Object.assign(draft, { ...emptyDraft(), kind: draft.kind });
      ElMessage.success(res.data.message || '已加入话题匣');
      return true;
    } catch (err) {
      ElMessage.error(getApiErrorMessage(err, '塞卡失败'));
      return false;
    } finally {
      saving.value = false;
    }
  }

  function replaceCard(updated: TalkCard): void {
    cards.value = cards.value.map(c => (c.id === updated.id ? updated : c));
  }

  async function toggleEnabled(card: TalkCard): Promise<void> {
    try {
      const res = await talkCardsApi.update(card.id, { enabled: !card.enabled });
      if (!res.data.success || res.data.cards.length === 0) {
        ElMessage.error(res.data.message || '保存失败');
        return;
      }
      replaceCard(res.data.cards[0]);
    } catch (err) {
      ElMessage.error(getApiErrorMessage(err, '保存失败'));
    }
  }

  async function removeCard(card: TalkCard): Promise<void> {
    if (!(await confirmAction(`删除话题卡「${card.title}」？`, '删除话题卡'))) return;
    try {
      const res = await talkCardsApi.remove(card.id);
      if (!res.data.success) {
        ElMessage.error(res.data.message || '删除失败');
        return;
      }
      cards.value = cards.value.filter(c => c.id !== card.id);
    } catch (err) {
      ElMessage.error(getApiErrorMessage(err, '删除失败'));
    }
  }

  /** 复原全部已用标记：彩排抽过的卡，正式场还能再抽。 */
  async function resetUsed(): Promise<void> {
    if (!(await confirmAction('把所有已用过的卡复原成没用过？', '复原话题卡'))) return;
    try {
      const res = await talkCardsApi.resetUsed();
      if (!res.data.success) {
        ElMessage.error(res.data.message || '复原失败');
        return;
      }
      ElMessage.success(res.data.message || '已复原');
      await fetchCards();
    } catch (err) {
      ElMessage.error(getApiErrorMessage(err, '复原失败'));
    }
  }

  return {
    cards,
    loading,
    saving,
    draft,
    usedCount,
    availableCount,
    fetchCards,
    addCard,
    toggleEnabled,
    removeCard,
    resetUsed,
  };
}
