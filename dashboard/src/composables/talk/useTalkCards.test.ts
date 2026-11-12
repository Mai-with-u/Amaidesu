// useTalkCards.ts 提问箱抽屉的测试：列表加载（含业务失败）、塞卡校验与草稿复位、
// 启停替换、删除确认、复原已用后重拉。

import { describe, expect, it, vi, beforeEach } from 'vitest';

import { talkCardsApi } from '@/api';
import { ElMessage } from 'element-plus';
import { useTalkCards } from '@/composables/talk/useTalkCards';
import { confirmAction } from '@/utils/confirmAction';
import type { TalkCard } from '@/types';

vi.mock('@/api', async importOriginal => ({
  ...(await importOriginal<object>()),
  talkCardsApi: {
    list: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    remove: vi.fn(),
    resetUsed: vi.fn(),
  },
}));

vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

vi.mock('@/utils/confirmAction', () => ({
  confirmAction: vi.fn(),
}));

function card(id: number, partial: Partial<TalkCard> = {}): TalkCard {
  return {
    id,
    kind: 'marshmallow',
    title: `问题${id}`,
    body: '',
    asker: '',
    tags: [],
    enabled: true,
    used_in_session_id: null,
    used_at_ms: null,
    created_at_ms: 1,
    updated_at_ms: 1,
    ...partial,
  };
}

function ok<T>(data: T) {
  return Promise.resolve({ data }) as never;
}

describe('useTalkCards', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('加载卡池并统计可抽与已用张数', async () => {
    vi.mocked(talkCardsApi.list).mockReturnValue(
      ok({
        success: true,
        message: '',
        cards: [card(1), card(2, { used_at_ms: 5 }), card(3, { enabled: false })],
      }),
    );
    const tc = useTalkCards();
    await tc.fetchCards();
    expect(tc.cards.value).toHaveLength(3);
    expect(tc.availableCount.value).toBe(1);
    expect(tc.usedCount.value).toBe(1);
  });

  it('业务失败时清空列表并提示', async () => {
    vi.mocked(talkCardsApi.list).mockReturnValue(
      ok({ success: false, message: '存储未就绪', cards: [] }),
    );
    const tc = useTalkCards();
    await tc.fetchCards();
    expect(tc.cards.value).toEqual([]);
    expect(ElMessage.warning).toHaveBeenCalledWith('存储未就绪');
  });

  it('塞卡：空标题拦下；成功后追加并保留卡种清空草稿', async () => {
    const tc = useTalkCards();
    expect(await tc.addCard()).toBe(false);
    expect(talkCardsApi.create).not.toHaveBeenCalled();

    tc.draft.kind = 'opinion';
    tc.draft.title = '  甜还是咸  ';
    vi.mocked(talkCardsApi.create).mockReturnValue(
      ok({
        success: true,
        message: '已加入话题匣',
        cards: [card(7, { kind: 'opinion' })],
        affected: 1,
      }),
    );
    expect(await tc.addCard()).toBe(true);
    expect(talkCardsApi.create).toHaveBeenCalledWith({
      kind: 'opinion',
      title: '甜还是咸',
      body: '',
      asker: '',
    });
    expect(tc.cards.value.map(c => c.id)).toEqual([7]);
    expect(tc.draft.title).toBe('');
    expect(tc.draft.kind).toBe('opinion');
  });

  it('启停替换为后端返回的卡；删除需确认', async () => {
    vi.mocked(talkCardsApi.list).mockReturnValue(
      ok({ success: true, message: '', cards: [card(1)] }),
    );
    const tc = useTalkCards();
    await tc.fetchCards();

    vi.mocked(talkCardsApi.update).mockReturnValue(
      ok({ success: true, message: '', cards: [card(1, { enabled: false })], affected: 1 }),
    );
    await tc.toggleEnabled(tc.cards.value[0]);
    expect(talkCardsApi.update).toHaveBeenCalledWith(1, { enabled: false });
    expect(tc.cards.value[0].enabled).toBe(false);

    vi.mocked(confirmAction).mockResolvedValue(false);
    await tc.removeCard(tc.cards.value[0]);
    expect(talkCardsApi.remove).not.toHaveBeenCalled();

    vi.mocked(confirmAction).mockResolvedValue(true);
    vi.mocked(talkCardsApi.remove).mockReturnValue(
      ok({ success: true, message: '', cards: [], affected: 1 }),
    );
    await tc.removeCard(tc.cards.value[0]);
    expect(tc.cards.value).toEqual([]);
  });

  it('复原已用后重拉卡池', async () => {
    vi.mocked(confirmAction).mockResolvedValue(true);
    vi.mocked(talkCardsApi.resetUsed).mockReturnValue(
      ok({ success: true, message: '已复原 2 张', cards: [], affected: 2 }),
    );
    vi.mocked(talkCardsApi.list).mockReturnValue(
      ok({ success: true, message: '', cards: [card(1)] }),
    );
    const tc = useTalkCards();
    await tc.resetUsed();
    expect(ElMessage.success).toHaveBeenCalledWith('已复原 2 张');
    expect(talkCardsApi.list).toHaveBeenCalledTimes(1);
  });
});
