// 直播流双轨：把时间线条目按「现场 / 游戏」两条轨道分配，并按环节分段。
//
// 为什么不再按生产方过滤：主播与游戏 Agent 是并发活动的两条线，观众想知道的是
// "同一时刻两边各在发生什么"，而不是把其中一条藏起来。分轨只决定左右位置，
// 不决定去留——所有条目都会出现。
//
// 为什么按环节分段而不是按分钟切：固定宽度（1 分钟一组）只是把连续流切成等宽块，
// 块边界不对应任何真实单位，会把一次派活、一轮决策从中间劈开，除了视觉分隔没有
// 别的用途。环节（流程单的 segment）才是这场直播真实的时间单位：边界由运行期事件
// 给出，段内可以整段折叠。
//
// 两条轨道都用同一套卡片渲染（LiveLanes），因此这里产出的是 ShowEntry[]。

import type { ShowEntry } from '@/utils/liveFeed';

/** 轨道：现场（观众 ⇄ 主播）与游戏（游戏 Agent 与它的世界事件） */
export type LaneId = 'stage' | 'game';

/** 一个环节段：标题来自流程单环节名，段内含两条轨道的条目 */
export interface LaneSection {
  key: string;
  /** 环节名；无环节信息可依时用「开场前」或「更早内容」 */
  title: string;
  /** 段头提示（如「环节 2/5」），无则空串 */
  note: string;
  startMs: number;
  endMs: number;
  stage: ShowEntry[];
  game: ShowEntry[];
}

/**
 * 环节边界：环节名 + 该环节的起止时刻。
 *
 * 回看时"首个流程单条目之前的桶"不能一律叫「开场前」——加载窗口可能落在
 * 流程单的中间甚至末尾，此时窗口起点所在的环节才是这段内容的真实归属。
 * 边界来自环节执行事实（见回看数据加载），实时模式不传（无此需求）。
 */
export interface SegmentBoundary {
  /** 环节名；空串表示未命名 */
  title: string;
  /** 进入该环节的时刻 */
  startMs: number;
  /** 离开该环节的时刻；空表示直到场次结束仍停留在该环节 */
  endMs: number | null;
}

/** 游戏侧工具：工具条目没有独立通道字段，只能看调用来源（游戏 Agent 命名下发） */
function isGameTool(entry: ShowEntry): boolean {
  if (entry.kind !== 'tool' && entry.kind !== 'thinking') return false;
  return entry.source === '游戏 Agent';
}

/** 主播派给游戏 Agent 的指令（跨轨因果的起点） */
const DELEGATE_TOOLS = new Set(['framework_delegate', 'framework_prompt']);

/** 条目归属的轨道（游戏侧 = 游戏 Agent 产生的条目 + 它的世界事件） */
export function laneOf(entry: ShowEntry): LaneId {
  if (
    entry.kind === 'game' ||
    entry.kind === 'game_body' ||
    entry.kind === 'game_chat' ||
    entry.kind === 'agent_reply'
  ) {
    return 'game';
  }
  return isGameTool(entry) ? 'game' : 'stage';
}

/**
 * 头桶（首个流程单条目之前的桶）的标题。
 *
 * 头桶是"窗口起点到首个流程单条目之前"的那段内容，它属于哪个环节，看窗口起点
 * 落在哪个环节里：
 *
 * - 窗口里有流程单条目：这段内容确实在窗口内第一个环节切换之前，沿用旧文案
 *   「开场前」；
 * - 窗口里没有流程单条目（回看锚点落在最后一次切换之后，整段内容都堆在头桶
 *   里），但查得到环节边界（回看按场次查到的环节执行事实）：落在某个环节内
 *   就用该环节名；
 * - 查不到环节边界（回看查过但本场没有环节事实）：用中性文案「更早内容」。
 *
 * `boundaries` 为 undefined 表示"没有环节边界这个信息渠道"（实时模式），沿用旧
 * 文案「开场前」。
 */
function headTitleOf(
  entries: ShowEntry[],
  boundaries: SegmentBoundary[] | undefined,
  windowStartMs: number,
): string {
  if (entries.some(entry => entry.kind === 'rundown')) return '开场前';
  if (boundaries === undefined) return '开场前';
  const hit = boundaries
    .filter(
      item => item.startMs <= windowStartMs && (item.endMs === null || windowStartMs <= item.endMs),
    )
    // 同一环节可能多次进入（回跳），取最近一次进入的那段
    .sort((a, b) => b.startMs - a.startMs)[0];
  if (!hit) return '更早内容';
  return hit.title || '未命名环节';
}

/**
 * 条目 → 环节段。切段点是流程单条目（`rundown.changed`）：它自带环节名与序号，
 * 是运行期给的真实边界。第一条流程单之前的内容归入头段（空段不产出）。
 *
 * 入参需按时间升序（调用方已排序）。
 *
 * `boundaries` 是回看时按场次查到的环节执行事实（环节名 + 起止时刻）。窗口里
 * 没有任何流程单条目时，整段内容只能靠它判断属于哪个环节。
 */
export function buildLaneSections(
  entries: ShowEntry[],
  boundaries?: SegmentBoundary[],
): LaneSection[] {
  const sections: LaneSection[] = [];
  const push = (section: LaneSection): void => {
    if (section.stage.length === 0 && section.game.length === 0) return;
    sections.push(section);
  };
  const windowStartMs = entries[0]?.tsMs ?? 0;
  let current: LaneSection = {
    key: 'head',
    title: headTitleOf(entries, boundaries, windowStartMs),
    note: '',
    startMs: windowStartMs,
    endMs: windowStartMs,
    stage: [],
    game: [],
  };

  for (const entry of entries) {
    if (entry.kind === 'rundown') {
      push(current);
      current = {
        key: entry.id,
        title: entry.text || '未命名环节',
        note: entry.note,
        startMs: entry.tsMs,
        endMs: entry.tsMs,
        stage: [],
        game: [],
      };
    }
    current[laneOf(entry)].push(entry);
    current.endMs = Math.max(current.endMs, entry.tsMs);
  }
  push(current);
  return sections;
}

/** 一次派活的生命期：从下达时刻到下一次派活（或最后一条回报）为止。
 *
 * 事件里没有跨 Agent 的任务号，所以"这次派活到哪一步结束"只能按时间推断：
 * 下一次派活下达即认为上一件结束；没有下一次时，最后一条回报结束就算收尾。
 * 推断只用于卡片上的标注，不改变任何条目归类。
 */
export interface DelegationWindow {
  delegate: ShowEntry;
  startMs: number;
  endMs: number;
  replies: number;
}

function isDelegateEntry(entry: ShowEntry): boolean {
  return entry.kind === 'tool' && DELEGATE_TOOLS.has(entry.actor);
}

export function delegationWindows(entries: ShowEntry[]): DelegationWindow[] {
  const delegates = entries.filter(isDelegateEntry);
  return delegates.map((delegate, index) => {
    const next = delegates[index + 1];
    const limit = next ? next.tsMs : Number.POSITIVE_INFINITY;
    const replies = entries.filter(
      entry => entry.kind === 'agent_reply' && entry.tsMs >= delegate.tsMs && entry.tsMs < limit,
    );
    const lastReply = replies.length > 0 ? replies[replies.length - 1]!.tsMs : delegate.tsMs;
    return {
      delegate,
      startMs: delegate.tsMs,
      endMs: next ? next.tsMs : lastReply + 1,
      replies: replies.length,
    };
  });
}

/** 一条回报属于哪次派活（不在任何派活生命期内返回 null） */
export function delegationOfReply(
  windows: DelegationWindow[],
  entry: ShowEntry,
): DelegationWindow | null {
  if (entry.kind !== 'agent_reply') return null;
  for (const window of windows) {
    if (entry.tsMs >= window.startMs && entry.tsMs < window.endMs) return window;
  }
  return null;
}

// ---------------------------------------------------------------------------
// 游戏侧任务区间：task.changed 的受理与终态配对成"这件工具调用从几点到几点"
// ---------------------------------------------------------------------------

/** 一条 task.changed 事实（受理与终态各算一条，同一 task_id 归并成一个区间） */
export interface TaskFact {
  taskId: string;
  status: string;
  tsMs: number;
  summary: string;
  /** 快照里的能力名（如 maicraft:travel），组头用它当短名 */
  ability: string;
}

/** 一次工具调用的完整区间 */
export interface TaskWindow {
  taskId: string;
  startMs: number;
  endMs: number;
  /** 终态的中文短名（完成 / 失败 / 取消 / 超时 / 未送达） */
  statusLabel: string;
  /** 组头短名：能力名，取不到时退回终态摘要 */
  label: string;
  /** 终态摘要（组头右侧提示用） */
  summary: string;
}

const TERMINAL_STATUS_LABEL: Record<string, string> = {
  succeeded: '完成',
  failed: '失败',
  cancelled: '取消',
  timeout: '超时',
  undelivered: '未送达',
};

/**
 * 把事实合成区间。
 *
 * 只有拿得到受理时刻的才成区间——旧数据里每个任务只剩一条终态回执，
 * 起点等于终点，画出来是零长度的假分组，这类直接丢弃。
 */
export function buildTaskWindows(facts: TaskFact[]): TaskWindow[] {
  const grouped = new Map<string, TaskFact[]>();
  for (const fact of facts) {
    const list = grouped.get(fact.taskId);
    if (list) list.push(fact);
    else grouped.set(fact.taskId, [fact]);
  }

  const windows: TaskWindow[] = [];
  for (const [taskId, list] of grouped) {
    const accepted = list.find(fact => fact.status === 'accepted');
    if (!accepted) continue;
    const terminal = list.find(fact => TERMINAL_STATUS_LABEL[fact.status]);
    if (!terminal) continue;
    windows.push({
      taskId,
      startMs: accepted.tsMs,
      endMs: Math.max(terminal.tsMs, accepted.tsMs),
      statusLabel: TERMINAL_STATUS_LABEL[terminal.status],
      label: terminal.ability || accepted.ability || '游戏侧任务',
      summary: terminal.summary || accepted.summary,
    });
  }
  return windows.sort((a, b) => a.startMs - b.startMs);
}

/** 条目落在哪个区间（取包含它的区间；没有则 null） */
export function taskOfEntry(entry: ShowEntry, windows: TaskWindow[]): TaskWindow | null {
  for (const window of windows) {
    if (entry.tsMs >= window.startMs && entry.tsMs <= window.endMs) return window;
  }
  return null;
}

/** 从 `task.changed` 载荷读一条事实（实时事件与归档条目同形状） */
export function taskFactOf(data: Record<string, unknown>, tsMs: number): TaskFact {
  const snapshot = (data.snapshot ?? {}) as Record<string, unknown>;
  const ability = typeof snapshot.ability === 'string' ? snapshot.ability : '';
  return {
    taskId: typeof data.task_id === 'string' ? data.task_id : '',
    status: typeof data.status === 'string' ? data.status : '',
    tsMs,
    summary: typeof data.summary === 'string' ? data.summary : '',
    ability,
  };
}
