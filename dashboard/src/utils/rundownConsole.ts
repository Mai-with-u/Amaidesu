/**
 * 直播控制台"当前环节"横幅的数据归一化：把流程单执行事实（走过的环节）
 * 与计划环节全量（含还没到的）合成一条有序列表。
 *
 * 两路输入：
 * - 执行事实（GET /live-sessions/{id}/rundown-execution）：走过哪几段、
 *   每段停留多久、谁切的、暂停了几次；尾段未封口 = 还在这段；
 * - 实时快照（GET /rundown/state 的 snapshot）：当前环节的实时已播时长与
 *   提纲要点（实时态的权威口径，续期后与执行事实的进入时刻可能不一致）。
 *
 * 纯函数：输入两份数据，输出与渲染无关的有序行列表。
 */
import type {
  RundownPlannedSegment,
  RundownSegmentRun,
  RundownSegmentView,
  RundownSnapshot,
  SessionRundownExecutionResponse,
} from '@/types';

/** 环节行在横幅里的状态：已过 / 正在这 / 还没到 */
export type RundownRowState = 'done' | 'current' | 'upcoming';

/** 横幅与悬浮时间轴共用的环节行 */
export interface RundownConsoleRow {
  /** 行唯一键（同一环节被回跳重入时靠它区分） */
  key: string;
  segId: string;
  title: string;
  /** 第几段（1 起） */
  order: number;
  state: RundownRowState;
  /** 进入时刻（未到的环节没有） */
  enteredAtMs: number | null;
  /** 离开时刻（还在这 = null） */
  leftAtMs: number | null;
  /** 这段还没封口 = 正在这（回看模式里最后一段已封口，是"停在这段"） */
  isOpen: boolean;
  /** 已停留时长（毫秒）；未到的环节为 0 */
  dwellMs: number;
  /** 预计时长（毫秒）；未知为 0 */
  expectedMs: number;
  /** 停留是否已超出预计时长 */
  overExpected: boolean;
  /** 进入本段的触发方（agent/human/system），空 = 未知 */
  by: string;
  /** 进入本段的变更动作 */
  action: string;
  /** 本段累计暂停时长（毫秒） */
  pauseMs: number;
  /** 本段暂停次数 */
  pauseCount: number;
  /** 环节目标（给 AI 的指引） */
  taskDescription: string;
  /** 环节备注 */
  notes: string | null;
  /** 本环节要聊的提纲要点 */
  beats: string[];
  /** 最少停留时长（毫秒） */
  minDurationMs: number | null;
  /** 说话模式：play 边玩边说 / talk 杂谈 */
  mode: string | null;
}

/** 触发方的展示文案（agent=Agent 自己 / human=你手动 / system=系统） */
export function rundownActorLabel(by: string): string {
  switch (by) {
    case 'agent':
      return 'Agent';
    case 'human':
      return '你';
    case 'system':
      return '系统';
    default:
      return '';
  }
}

/** 变更动作的展示文案（未收录的动作原样透出） */
export function rundownActionLabel(action: string): string {
  switch (action) {
    case 'begin':
    case 'load':
    case 'reload':
      return '开始';
    case 'next':
      return '推进';
    case 'goto':
      return '跳转';
    case 'append':
    case 'insert':
      return '插入';
    case 'restore':
      return '恢复';
    case 'renew':
      return '续期';
    case 'pause':
      return '暂停';
    case 'resume':
      return '恢复计时';
    case 'finish':
      return '结束';
    default:
      return action;
  }
}

/** 已过的段（执行事实）与计划环节（实例快照）按 seg_id 对齐 */
function plannedById(planned: RundownPlannedSegment[]): Map<string, RundownPlannedSegment> {
  const map = new Map<string, RundownPlannedSegment>();
  for (const seg of planned) map.set(seg.seg_id, seg);
  return map;
}

function rowFromRun(
  run: RundownSegmentRun,
  index: number,
  plan: RundownPlannedSegment | undefined,
): RundownConsoleRow {
  const expectedMs = run.expected_ms ?? plan?.expected_ms ?? 0;
  return {
    key: `run-${index}-${run.seg_id}`,
    segId: run.seg_id,
    title: run.seg_title || plan?.title || '',
    order: run.seg_index + 1,
    state: 'done',
    enteredAtMs: run.entered_at_ms,
    leftAtMs: run.left_at_ms,
    isOpen: run.left_at_ms === null,
    dwellMs: run.dwell_ms,
    expectedMs,
    overExpected: expectedMs > 0 && run.dwell_ms > expectedMs,
    by: run.by,
    action: run.action,
    pauseMs: run.pause_ms,
    pauseCount: run.pause_count,
    taskDescription: plan?.task_description ?? '',
    notes: plan?.notes ?? null,
    beats: plan?.beats ?? [],
    minDurationMs: plan?.min_duration_ms ?? null,
    mode: plan?.mode ?? null,
  };
}

function rowFromPlan(plan: RundownPlannedSegment): RundownConsoleRow {
  return {
    key: `plan-${plan.seq}-${plan.seg_id}`,
    segId: plan.seg_id,
    title: plan.title,
    order: plan.seq + 1,
    state: 'upcoming',
    enteredAtMs: null,
    leftAtMs: null,
    isOpen: false,
    dwellMs: 0,
    expectedMs: plan.expected_ms,
    overExpected: false,
    by: '',
    action: '',
    pauseMs: 0,
    pauseCount: 0,
    taskDescription: plan.task_description,
    notes: plan.notes,
    beats: plan.beats,
    minDurationMs: plan.min_duration_ms,
    mode: plan.mode,
  };
}

/**
 * 执行事实 + 计划环节 → 有序环节行。
 *
 * 当前段的判定：执行事实里尾段未封口（left_at_ms 为空）= 还在这段；
 * 没有实时快照时（回看模式）取最后一段作为"停在这段"。
 * 实时快照在场时，当前段的已播时长以快照为准（续期后执行事实的进入时刻
 * 会偏早）。
 *
 * 没有执行事实时（实时模式尚未开场次，变更事实行无处落库）退到模板环节
 * 清单 + 实时快照：当前段按快照给，其余段标为还没到。
 */
export function buildRundownRows(
  execution: SessionRundownExecutionResponse | null,
  snapshot: RundownSnapshot | null,
  templateSegments: RundownSegmentView[] = [],
): RundownConsoleRow[] {
  const planned = execution?.planned_segments?.length
    ? execution.planned_segments
    : templateSegments.map((seg, index) => ({
        seq: index,
        seg_id: seg.id,
        title: seg.title,
        task_description: seg.task_description,
        expected_ms: seg.expected_ms,
        min_duration_ms: seg.min_duration_ms,
        notes: seg.notes,
        mode: seg.mode ?? null,
        beats: seg.beats ?? [],
      }));
  const plan = plannedById(planned);
  const runs = execution?.segments ?? [];
  const rows: RundownConsoleRow[] = runs.map((run, index) =>
    rowFromRun(run, index, plan.get(run.seg_id)),
  );

  const openIndex = rows.findIndex(row => row.leftAtMs === null);
  const currentIndex = openIndex >= 0 ? openIndex : rows.length - 1;
  if (currentIndex >= 0) {
    const row = rows[currentIndex];
    row.state = 'current';
    const liveCurrent = snapshot?.current;
    if (liveCurrent && liveCurrent.id === row.segId) {
      // 实时态以快照的已播时长为准（已扣除暂停，且跟随续期重算）
      row.dwellMs = liveCurrent.elapsed_ms;
      row.expectedMs = liveCurrent.expected_ms || row.expectedMs;
      row.title = liveCurrent.title || row.title;
      row.beats = liveCurrent.beats?.length ? liveCurrent.beats : row.beats;
      row.mode = liveCurrent.mode ?? row.mode;
    }
    row.overExpected = row.expectedMs > 0 && row.dwellMs > row.expectedMs;
  }

  const visited = new Set(rows.map(row => row.segId));
  const upcoming = planned.filter(seg => !visited.has(seg.seg_id)).map(seg => rowFromPlan(seg));

  // 没有执行事实但有实时快照：当前段按快照补一行（未开场次时的实时态）
  if (rows.length === 0 && snapshot?.current) {
    const current = snapshot.current;
    const plannedSeg = plan.get(current.id);
    const row = rowFromPlan({
      seq: Math.max(0, snapshot.index),
      seg_id: current.id,
      title: current.title,
      task_description: plannedSeg?.task_description ?? '',
      expected_ms: current.expected_ms,
      min_duration_ms: plannedSeg?.min_duration_ms ?? null,
      notes: plannedSeg?.notes ?? null,
      mode: current.mode ?? plannedSeg?.mode ?? null,
      beats: current.beats?.length ? current.beats : (plannedSeg?.beats ?? []),
    });
    row.state = 'current';
    row.dwellMs = current.elapsed_ms;
    row.overExpected = row.expectedMs > 0 && row.dwellMs > row.expectedMs;
    return [row, ...upcoming.filter(seg => seg.segId !== current.id)];
  }

  return [...rows, ...upcoming];
}
