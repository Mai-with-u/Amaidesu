/**
 * useClipReview 组合式测试：mock 依赖注入驱动，验证行草稿保全、单行乐观替换、
 * 确认/弃用自动前进、建段有序插入与调界夹取。核对逻辑从页面抽出后首次可单测
 * （此前只有源码结构断言）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ref, type Ref } from 'vue';

const mocks = vi.hoisted(() => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  confirmAction: vi.fn(),
}));

vi.mock('element-plus', () => ({ ElMessage: mocks.ElMessage }));
vi.mock('@/utils/confirmAction', () => ({ confirmAction: mocks.confirmAction }));

import { useClipReview, MIN_SPAN_MS } from '@/composables/useClipReview';
import type { ClipSegment } from '@/types/clips';

function segment(partial: Partial<ClipSegment> & { id: number }): ClipSegment {
  return {
    live_session_id: 7,
    origin: 'ai',
    start_ms: 0,
    end_ms: 0,
    title: `段${partial.id}`,
    reason: '',
    status: 'suggested',
    output_path: '',
    error_message: '',
    created_at_ms: 0,
    updated_at_ms: 0,
    ...partial,
  };
}

function harness(initial: ClipSegment[]) {
  const segments: Ref<ClipSegment[]> = ref(initial);
  const session = ref({ live_session_id: 7, started_at_ms: 1_000_000 });
  const sessionEndMs = ref(4_000_000);
  const updateBodies: Array<{ id: number; body: Record<string, unknown> }> = [];
  const previewedIds: number[] = [];
  let nextId = 900;
  const review = useClipReview({
    segments,
    session,
    sessionEndMs,
    createSegment: vi.fn(async (_sid: number, body: { start_ms: number; end_ms: number; title: string }) => {
      return segment({ id: nextId++, start_ms: body.start_ms, end_ms: body.end_ms, title: body.title });
    }),
    updateSegment: vi.fn(
      async (
        id: number,
        body: Partial<{ start_ms: number; end_ms: number; title: string; status: string }>,
      ) => {
        updateBodies.push({ id, body: { ...body } });
        const base = segments.value.find(s => s.id === id);
        return base ? ({ ...base, ...body } as ClipSegment) : null;
      },
    ),
    deleteSegment: vi.fn(async () => true),
    previewSegment: (s: ClipSegment) => previewedIds.push(s.id),
  });
  return { segments, session, sessionEndMs, review, updateBodies, previewedIds };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('useClipReview', () => {
  it('确认后单行替换为服务端值，并自动前进到下一个待核对段且预览', async () => {
    const { segments, review, previewedIds } = harness([
      segment({ id: 1, status: 'suggested' }),
      segment({ id: 2, status: 'suggested' }),
      segment({ id: 3, status: 'dismissed' }),
    ]);
    review.selectedSegmentId.value = 1;

    await review.onConfirmSegment(segments.value[0]);

    // 单行替换：id 1 状态翻 confirmed，数组长度不变（不整表回读）
    expect(segments.value).toHaveLength(3);
    expect(segments.value[0].status).toBe('confirmed');
    // 自动前进跳过 dismissed，落到 id 2 且联动预览
    expect(review.selectedSegmentId.value).toBe(2);
    expect(previewedIds).toEqual([2]);
    expect(mocks.ElMessage.info).not.toHaveBeenCalled();
  });

  it('弃用最后一个待核对段后给轻提示，不再前进', async () => {
    const { segments, review, previewedIds } = harness([segment({ id: 1, status: 'confirmed' })]);

    await review.onDismissSegment(segments.value[0]);

    expect(segments.value[0].status).toBe('dismissed');
    expect(mocks.ElMessage.info).toHaveBeenCalledWith('待核对的段都过完了');
    expect(previewedIds).toEqual([]);
  });

  it('单行替换不动其他行的未保存草稿', async () => {
    const { segments, review } = harness([segment({ id: 1 }), segment({ id: 2, start_ms: 2_000_000 })]);
    // id 2 正在编辑标题（未保存）
    review.drafts.value[2] = { title: '编辑中的名字', startText: '16:40', endText: '17:40' };

    const updated = { ...segments.value[0], status: 'confirmed' as const };
    review.replaceSegmentLocal(updated);

    expect(review.drafts.value[2]).toEqual({
      title: '编辑中的名字',
      startText: '16:40',
      endText: '17:40',
    });
    // 被替换行自身不再处于编辑态（草稿作废或重建为服务端值，两种实现都算干净）
    expect(review.isDirty(segments.value[0])).toBe(false);
    expect(review.isDirty(segments.value[1])).toBe(true);
  });

  it('saveRow 时间格式不认识/终点不晚于起点都拦截，不发起写请求', async () => {
    const { segments, review } = harness([segment({ id: 1 })]);
    review.drafts.value[1] = { title: 'x', startText: 'abc', endText: '1:00' };
    await review.saveRow(segments.value[0]);
    expect(mocks.ElMessage.warning).toHaveBeenCalledWith('时间格式不认识；用 m:ss 或 h:mm:ss，或裸秒数');

    review.drafts.value[1] = { title: 'x', startText: '1:00', endText: '0:30' };
    await review.saveRow(segments.value[0]);
    expect(mocks.ElMessage.warning).toHaveBeenCalledWith('终点要晚于起点');
  });

  it('saveRow 校验通过后按相对时刻换算墙钟提交，并清自身草稿', async () => {
    const { segments, review, updateBodies } = harness([segment({ id: 1, start_ms: 1_000_000 })]);
    review.drafts.value[1] = { title: '新标题', startText: '0:10', endText: '1:00' };

    await review.saveRow(segments.value[0]);

    expect(updateBodies).toEqual([
      { id: 1, body: { title: '新标题', start_ms: 1_010_000, end_ms: 1_060_000 } },
    ]);
    expect(review.drafts.value[1]).toBeUndefined();
  });

  it('onResize 少于最短时长的拖动拦截；成功提交夹取到场次范围内', async () => {
    const { review, updateBodies } = harness([segment({ id: 1, start_ms: 1_000_000, end_ms: 1_060_000 })]);

    await review.onResize(1, 1_000_000, 1_000_000 + 1000);
    expect(mocks.ElMessage.warning).toHaveBeenCalledWith('段太短了（至少 5 秒）');

    // 终点拖到场次终点之外：夹到 sessionEndMs
    await review.onResize(1, 1_000_000, 9_999_999);
    expect(updateBodies).toEqual([{ id: 1, body: { start_ms: 1_000_000, end_ms: 4_000_000 } }]);
    expect(MIN_SPAN_MS).toBe(5000);
  });

  it('onCreate 按起点有序插入并选中新段', async () => {
    const { segments, review } = harness([
      segment({ id: 1, start_ms: 1_200_000, end_ms: 1_260_000 }),
      segment({ id: 2, start_ms: 2_400_000, end_ms: 2_460_000 }),
    ]);

    await review.onCreate(1_500_000, 1_560_000);

    expect(segments.value.map(s => s.id)).toEqual([1, 900, 2]);
    expect(review.selectedSegmentId.value).toBe(900);
  });

  it('删除走二次确认；删除当前选中段后清空选中', async () => {
    const { segments, review } = harness([segment({ id: 1 }), segment({ id: 2 })]);
    review.selectedSegmentId.value = 1;
    mocks.confirmAction.mockResolvedValue(false);
    await review.onDeleteSegment(segments.value[0]);
    expect(segments.value).toHaveLength(2); // 拒绝则不动

    mocks.confirmAction.mockResolvedValue(true);
    await review.onDeleteSegment(segments.value[0]);
    expect(segments.value.map(s => s.id)).toEqual([2]);
    expect(review.selectedSegmentId.value).toBeNull();
  });

  it('markSegmentStart 以播放位置写段起点；越过终点拦截不盲改', async () => {
    const { review, updateBodies } = harness([
      segment({ id: 1, start_ms: 1_000_000, end_ms: 1_060_000 }),
    ]);
    review.selectedSegmentId.value = 1;

    await review.markSegmentStart(1_010_000);
    expect(updateBodies).toEqual([{ id: 1, body: { start_ms: 1_010_000 } }]);

    // 播放位置已在段终点之后：提示且不写
    await review.markSegmentStart(1_070_000);
    expect(mocks.ElMessage.warning).toHaveBeenCalled();
    expect(updateBodies).toHaveLength(1);

    // 未选中段：无动作
    review.selectedSegmentId.value = null;
    await review.markSegmentStart(1_020_000);
    expect(updateBodies).toHaveLength(1);
  });

  it('markSegmentEnd 以播放位置写段终点；未过起点拦截', async () => {
    const { review, updateBodies } = harness([
      segment({ id: 1, start_ms: 1_000_000, end_ms: 1_060_000 }),
    ]);
    review.selectedSegmentId.value = 1;

    await review.markSegmentEnd(1_050_000);
    expect(updateBodies).toEqual([{ id: 1, body: { end_ms: 1_050_000 } }]);

    await review.markSegmentEnd(990_000);
    expect(mocks.ElMessage.warning).toHaveBeenCalled();
    expect(updateBodies).toHaveLength(1);
  });

  it('selectSegment 默认联动预览；preview=false 只选中不预览', () => {
    const { review, previewedIds } = harness([segment({ id: 1 })]);

    review.selectSegment(1);
    expect(review.selectedSegmentId.value).toBe(1);
    expect(previewedIds).toEqual([1]);

    review.selectSegment(1, false);
    expect(previewedIds).toEqual([1]); // 未新增预览调用
  });

  it('resetForSession 清空选中与全部草稿', () => {
    const { review } = harness([segment({ id: 1 })]);
    review.selectedSegmentId.value = 1;
    review.drafts.value[1] = { title: 'a', startText: '0:01', endText: '0:02' };

    review.resetForSession();

    expect(review.selectedSegmentId.value).toBeNull();
    expect(review.drafts.value).toEqual({});
  });
});
