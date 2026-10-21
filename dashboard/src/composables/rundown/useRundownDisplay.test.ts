// useRundownDisplay.ts 流程单展示派生的测试：段状态判定（armed 全待开始 /
// 游标命中 / 索引兜底）、标签与 Tag 样式映射、时长与时刻格式化、变更历史
// 圆点类型的关键词分级。

import { ref } from 'vue';
import { describe, expect, it } from 'vitest';

import { useRundownDisplay } from '@/composables/rundown/useRundownDisplay';
import type {
  RundownCurrentSegment,
  RundownSegmentView,
  RundownStateResponse,
  RundownTransitionEntry,
} from '@/types';

function segment(id: string): RundownSegmentView {
  return {
    id,
    title: `环节${id}`,
    task_description: '',
    expected_ms: 60_000,
    min_duration_ms: null,
    notes: null,
  };
}

function stateResponse(partial: Partial<RundownStateResponse> = {}): RundownStateResponse {
  return {
    available: true,
    message: null,
    snapshot: null,
    transitions: [],
    segments: [segment('a'), segment('b'), segment('c')],
    config: { rundown_id: '' },
    ...partial,
  };
}

function current(id: string): RundownCurrentSegment {
  return { id, title: `环节${id}`, expected_ms: 60_000, elapsed_ms: 0, remaining_ms: 60_000 };
}

describe('useRundownDisplay 段状态判定', () => {
  const state = ref<RundownStateResponse | null>(null);
  const currentSegment = ref<RundownCurrentSegment | null>(null);
  const d = useRundownDisplay({ state, currentSegment });

  it('armed（待开播）：环节一概待开始', () => {
    state.value = stateResponse({
      snapshot: { status: 'armed' } as RundownStateResponse['snapshot'],
    });
    currentSegment.value = current('a');
    expect(d.segmentStatusOf(segment('a'))).toBe('pending');
    expect(d.segmentStatusLabel(segment('a'))).toBe('待开始');
    expect(d.segmentStatusTagType(segment('a'))).toBe('info');
    expect(d.segmentStatusTagEffect(segment('a'))).toBe('plain');
  });

  it('游标命中的环节为 current，其前按索引判 done', () => {
    state.value = stateResponse({
      snapshot: { status: 'running' } as RundownStateResponse['snapshot'],
    });
    currentSegment.value = current('b');
    expect(d.segmentStatusOf(segment('a'))).toBe('done');
    expect(d.segmentStatusOf(segment('b'))).toBe('current');
    expect(d.segmentStatusOf(segment('c'))).toBe('pending');
    expect(d.segmentStatusLabel(segment('b'))).toBe('进行中');
    expect(d.segmentStatusTagType(segment('b'))).toBe('warning');
    expect(d.segmentStatusTagEffect(segment('b'))).toBe('dark');
  });

  it('游标为空时全部 pending；rowClassName 只给 current 行加类', () => {
    state.value = stateResponse({
      snapshot: { status: 'running' } as RundownStateResponse['snapshot'],
    });
    currentSegment.value = null;
    expect(d.segmentStatusOf(segment('a'))).toBe('pending');
    expect(d.rowClassName({ row: segment('a') })).toBe('');
    currentSegment.value = current('c');
    expect(d.rowClassName({ row: segment('c') })).toBe('is-current-row');
  });

  it('segmentTitleOf：命中返回标题，未命中回落 id', () => {
    state.value = stateResponse();
    expect(d.segmentTitleOf('b')).toBe('环节b');
    expect(d.segmentTitleOf('missing')).toBe('missing');
  });
});

describe('useRundownDisplay 格式化', () => {
  const d = useRundownDisplay({
    state: ref(null),
    currentSegment: ref(null),
  });

  it('formatDuration：空/NaN/负值回落占位符', () => {
    expect(d.formatDuration(null)).toBe('—');
    expect(d.formatDuration(undefined)).toBe('—');
    expect(d.formatDuration(Number.NaN)).toBe('—');
    expect(d.formatDuration(-1)).toBe('—');
  });

  it('formatDuration：不足一小时不带小时位，超一小时带小时位', () => {
    expect(d.formatDuration(0)).toBe('00:00');
    expect(d.formatDuration(65_000)).toBe('01:05');
    expect(d.formatDuration(3_661_000)).toBe('01:01:01');
  });

  it('formatTime：0 回落占位符，其余走 formatTimeHMS', () => {
    expect(d.formatTime(0)).toBe('—');
    expect(d.formatTime(9_000_000)).toBe(
      new Date(9_000_000).toLocaleTimeString('zh-CN', {
        hour12: false,
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      }),
    );
  });
});

describe('useRundownDisplay 变更历史圆点', () => {
  const d = useRundownDisplay({ state: ref(null), currentSegment: ref(null) });

  function entry(action: string): RundownTransitionEntry {
    return { action, segment_id: 'a', by: 'human', at_ms: 0 };
  }

  it('失败类最高优先级，其次暂停/跳过，再次完成，最后启动', () => {
    expect(d.historyDotType(entry('segment_failed'))).toBe('danger');
    expect(d.historyDotType(entry('error'))).toBe('danger');
    expect(d.historyDotType(entry('skipped'))).toBe('warning');
    expect(d.historyDotType(entry('paused'))).toBe('warning');
    expect(d.historyDotType(entry('override'))).toBe('warning');
    expect(d.historyDotType(entry('segment_done'))).toBe('success');
    expect(d.historyDotType(entry('completed'))).toBe('success');
    expect(d.historyDotType(entry('started'))).toBe('primary');
    expect(d.historyDotType(entry('loaded'))).toBe('primary');
  });

  it('无法归类的动作为 info', () => {
    expect(d.historyDotType(entry('mystery'))).toBe('info');
  });
});
