/**
 * 流程单编排页的展示派生：段状态判定、状态标签/标签样式映射、
 * 时长与时刻格式化、变更历史圆点类型。全部只读入参引用，无本地状态。
 */

import type { Ref } from 'vue';
import { formatTimeHMS } from '@/utils/format';
import type {
  RundownCurrentSegment,
  RundownSegmentView,
  RundownStateResponse,
  RundownTransitionEntry,
} from '@/types';

export interface UseRundownDisplayOptions {
  state: Ref<RundownStateResponse | null>;
  currentSegment: Ref<RundownCurrentSegment | null>;
}

export function useRundownDisplay(options: UseRundownDisplayOptions) {
  const { state, currentSegment } = options;

  function segmentStatusOf(seg: RundownSegmentView): 'done' | 'current' | 'pending' {
    const cur = currentSegment.value;
    if (cur && cur.id === seg.id) return 'current';
    // 简化：用 currentSegment.id 之前的视作 done，索引比较作为兜底
    const segments = state.value?.segments ?? [];
    const idx = segments.findIndex(s => s.id === seg.id);
    if (idx === -1) return 'pending';
    const curIdx = segments.findIndex(s => s.id === cur?.id);
    if (curIdx >= 0 && idx < curIdx) return 'done';
    return 'pending';
  }

  function segmentStatusLabel(seg: RundownSegmentView): string {
    const s = segmentStatusOf(seg);
    if (s === 'done') return '已完成';
    if (s === 'current') return '进行中';
    return '待开始';
  }

  function segmentStatusTagType(seg: RundownSegmentView): 'success' | 'warning' | 'info' {
    const s = segmentStatusOf(seg);
    if (s === 'done') return 'success';
    if (s === 'current') return 'warning';
    return 'info';
  }

  function segmentStatusTagEffect(seg: RundownSegmentView): 'plain' | 'dark' {
    return segmentStatusOf(seg) === 'current' ? 'dark' : 'plain';
  }

  function segmentTitleOf(id: string): string {
    const seg = (state.value?.segments ?? []).find(s => s.id === id);
    return seg?.title ?? id;
  }

  function rowClassName({ row }: { row: RundownSegmentView }): string {
    return segmentStatusOf(row) === 'current' ? 'is-current-row' : '';
  }

  function formatDuration(ms: number | null | undefined): string {
    if (ms == null || Number.isNaN(ms) || ms < 0) return '—';
    const totalSec = Math.floor(ms / 1000);
    const hh = Math.floor(totalSec / 3600);
    const mm = Math.floor((totalSec % 3600) / 60);
    const ss = totalSec % 60;
    const pad = (n: number) => String(n).padStart(2, '0');
    return hh > 0 ? `${pad(hh)}:${pad(mm)}:${pad(ss)}` : `${pad(mm)}:${pad(ss)}`;
  }

  function formatTime(tsMs: number): string {
    if (!tsMs) return '—';
    return formatTimeHMS(tsMs);
  }

  function historyDotType(
    entry: RundownTransitionEntry,
  ): 'primary' | 'success' | 'warning' | 'danger' | 'info' {
    const ev = entry.action.toLowerCase();
    if (ev.includes('fail') || ev.includes('error')) return 'danger';
    if (ev.includes('skip') || ev.includes('pause') || ev.includes('override')) return 'warning';
    if (ev.includes('done') || ev.includes('complete') || ev.includes('finish')) return 'success';
    if (ev.includes('start') || ev.includes('begin') || ev.includes('load')) return 'primary';
    return 'info';
  }

  return {
    segmentStatusOf,
    segmentStatusLabel,
    segmentStatusTagType,
    segmentStatusTagEffect,
    segmentTitleOf,
    rowClassName,
    formatDuration,
    formatTime,
    historyDotType,
  };
}
