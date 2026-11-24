/**
 * 切片核对工作流组合式：选中段、行内编辑草稿、单行乐观替换、确认/弃用后
 * 自动前进到下一个待核对段。数据读写经构造注入的 useClips 动作完成，预览
 * 联动经注入的回调完成——本组合式不持有播放器与 DOM。
 *
 * 草稿模型：segmentId → { title, startText, endText }。草稿统一在 segments
 * 数组被整体替换时重建（渲染期不写状态）：已有草稿保留（编辑中不被回读冲掉），
 * 新段按当前段值生成，消失的段连草稿一起清掉；单行写操作走 replaceSegmentLocal，
 * 不触发整表回读。
 */
import { computed, ref, watch, type Ref } from 'vue';
import { ElMessage } from 'element-plus';
import { confirmAction } from '@/utils/confirmAction';
import { clampMs, formatRelClock, parseRelClock } from '@/utils/clipsTime';
import type { ClipSegment } from '@/types/clips';

/** 段最短时长（毫秒）；调界/标记入出点都不得把段压到这以下 */
export const MIN_SPAN_MS = 5000;

export interface ClipReviewSession {
  live_session_id: number;
  started_at_ms: number;
}

export interface ClipReviewDeps {
  segments: Ref<ClipSegment[]>;
  /** 当前打开的场次；null = 没有可操作的上下文 */
  session: Ref<ClipReviewSession | null>;
  /** 场次终点（进行中场次用当前时刻兜底）；调界夹取用 */
  sessionEndMs: Ref<number>;
  createSegment: (
    sessionId: number,
    body: { start_ms: number; end_ms: number; title: string },
  ) => Promise<ClipSegment | null>;
  updateSegment: (
    segmentId: number,
    body: Partial<{ start_ms: number; end_ms: number; title: string; status: string }>,
  ) => Promise<ClipSegment | null>;
  deleteSegment: (segmentId: number) => Promise<boolean>;
  /** 自动前进跳到下一段时联动预览（页面接播放器） */
  previewSegment: (segment: ClipSegment) => void;
}

interface RowDraft {
  title: string;
  startText: string;
  endText: string;
}

export function useClipReview(deps: ClipReviewDeps) {
  const selectedSegmentId = ref<number | null>(null);
  const drafts = ref<Record<number, RowDraft>>({});

  const selectedSegment = computed(
    () => deps.segments.value.find(s => s.id === selectedSegmentId.value) ?? null,
  );

  /** 待核对 = AI 建议或已确认还没切的段（与"待切"口径一致） */
  function isReviewable(segment: ClipSegment): boolean {
    return segment.status === 'suggested' || segment.status === 'confirmed';
  }

  function makeDraft(segment: ClipSegment): RowDraft {
    const start = deps.session.value?.started_at_ms ?? 0;
    return {
      title: segment.title,
      startText: formatRelClock(segment.start_ms - start),
      endText: formatRelClock(segment.end_ms - start),
    };
  }

  watch(deps.segments, list => {
    const next: Record<number, RowDraft> = {};
    for (const segment of list) {
      next[segment.id] = drafts.value[segment.id] ?? makeDraft(segment);
    }
    drafts.value = next;
  });

  function draftOf(segment: ClipSegment): RowDraft {
    return drafts.value[segment.id] ?? makeDraft(segment);
  }

  function isDirty(segment: ClipSegment): boolean {
    const draft = drafts.value[segment.id];
    if (!draft) return false;
    const start = deps.session.value?.started_at_ms ?? 0;
    return (
      draft.title !== segment.title ||
      draft.startText !== formatRelClock(segment.start_ms - start) ||
      draft.endText !== formatRelClock(segment.end_ms - start)
    );
  }

  // ---- 段的乐观更新：写操作只替换单行，不整表回读 ----
  // 整表回读会清掉其他行未保存的编辑草稿，也让连续微调每段都付一次全量刷新；
  // 单行替换以服务端回传的段为准，权威状态仍由任务终态回读与手动刷新兜底。
  function replaceSegmentLocal(updated: ClipSegment): void {
    const index = deps.segments.value.findIndex(s => s.id === updated.id);
    if (index !== -1) deps.segments.value.splice(index, 1, updated);
    delete drafts.value[updated.id]; // 该行草稿作废，显示回服务端值
  }

  /** 按起点排序插到列表里（与后端列表排序一致），保持显示有序 */
  function insertSegmentLocal(created: ClipSegment): void {
    const index = deps.segments.value.findIndex(s => s.start_ms > created.start_ms);
    if (index === -1) deps.segments.value.push(created);
    else deps.segments.value.splice(index, 0, created);
  }

  function removeSegmentLocal(segmentId: number): void {
    const index = deps.segments.value.findIndex(s => s.id === segmentId);
    if (index !== -1) deps.segments.value.splice(index, 1);
    delete drafts.value[segmentId];
  }

  /** 保存行内编辑：相对时刻解析失败/终点不大于起点都拦下并提示 */
  async function saveRow(segment: ClipSegment): Promise<void> {
    const draft = drafts.value[segment.id];
    const session = deps.session.value;
    if (!draft || !session) return;
    const startRel = parseRelClock(draft.startText);
    const endRel = parseRelClock(draft.endText);
    if (startRel === null || endRel === null) {
      ElMessage.warning('时间格式不认识；用 m:ss 或 h:mm:ss，或裸秒数');
      return;
    }
    if (endRel <= startRel) {
      ElMessage.warning('终点要晚于起点');
      return;
    }
    const updated = await deps.updateSegment(segment.id, {
      title: draft.title.trim(),
      start_ms: session.started_at_ms + startRel,
      end_ms: session.started_at_ms + endRel,
    });
    if (updated) replaceSegmentLocal(updated);
  }

  /** 拖动时间轴边界提交 */
  async function onResize(segmentId: number, startMs: number, endMs: number): Promise<void> {
    const session = deps.session.value;
    if (!session) return;
    const clampedStart = clampMs(startMs, session.started_at_ms, deps.sessionEndMs.value);
    const clampedEnd = clampMs(endMs, session.started_at_ms, deps.sessionEndMs.value);
    if (clampedEnd - clampedStart < MIN_SPAN_MS) {
      ElMessage.warning('段太短了（至少 5 秒）');
      return;
    }
    const updated = await deps.updateSegment(segmentId, { start_ms: clampedStart, end_ms: clampedEnd });
    if (updated) replaceSegmentLocal(updated);
  }

  /** 时间轴框选松手建段 */
  async function onCreate(startMs: number, endMs: number): Promise<void> {
    const session = deps.session.value;
    if (!session) return;
    const created = await deps.createSegment(session.live_session_id, {
      start_ms: clampMs(startMs, session.started_at_ms, deps.sessionEndMs.value),
      end_ms: clampMs(endMs, session.started_at_ms, deps.sessionEndMs.value),
      title: '',
    });
    if (created) {
      insertSegmentLocal(created);
      selectedSegmentId.value = created.id;
    }
  }

  async function onConfirmSegment(segment: ClipSegment): Promise<void> {
    const updated = await deps.updateSegment(segment.id, { status: 'confirmed' });
    if (updated) {
      replaceSegmentLocal(updated);
      advanceToNextReviewable(segment.id);
    }
  }

  async function onDismissSegment(segment: ClipSegment): Promise<void> {
    const updated = await deps.updateSegment(segment.id, { status: 'dismissed' });
    if (updated) {
      replaceSegmentLocal(updated);
      advanceToNextReviewable(segment.id);
    }
  }

  /** 核对自动前进：当前段处理完后跳到列表中下一个待核对的段并自动预览 */
  function advanceToNextReviewable(currentId: number): void {
    const index = deps.segments.value.findIndex(s => s.id === currentId);
    const next = deps.segments.value.slice(index + 1).find(s => isReviewable(s));
    if (!next) {
      ElMessage.info('待核对的段都过完了');
      return;
    }
    selectedSegmentId.value = next.id;
    deps.previewSegment(next);
  }

  async function onDeleteSegment(segment: ClipSegment): Promise<void> {
    const confirmed = await confirmAction(`删除切片段「${segment.title || '未命名'}」？`, '删除');
    if (!confirmed) return;
    const ok = await deps.deleteSegment(segment.id);
    if (ok) {
      if (selectedSegmentId.value === segment.id) selectedSegmentId.value = null;
      removeSegmentLocal(segment.id);
    }
  }

  /** 切场/手动刷新前清空本地工作态 */
  function resetForSession(): void {
    selectedSegmentId.value = null;
    drafts.value = {};
  }

  return {
    selectedSegmentId,
    selectedSegment,
    drafts,
    isReviewable,
    draftOf,
    isDirty,
    saveRow,
    onResize,
    onCreate,
    onConfirmSegment,
    onDismissSegment,
    onDeleteSegment,
    advanceToNextReviewable,
    replaceSegmentLocal,
    insertSegmentLocal,
    removeSegmentLocal,
    resetForSession,
  };
}
