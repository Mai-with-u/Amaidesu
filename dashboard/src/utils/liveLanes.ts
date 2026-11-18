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

import {
  frameworkDelegateInstruction,
  frameworkDelegateTaskId,
  isMinecraftExecuteEntry,
  isMinecraftGoalEntry,
  makeEntry,
  minecraftExecuteGoalId,
  minecraftGoalIdOf,
  minecraftGoalOperationOf,
  type ShowEntry,
} from '@/utils/liveFeed';
import { truncateText } from '@/utils/format';

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

/** 游戏侧工具：工具条目没有独立通道字段，只能看调用来源（游戏 Agent 命名下发）。
 *  双轨归属与搜索分类共用同一判据，改动需同步两处消费方 */
export function isGameTool(entry: ShowEntry): boolean {
  if (entry.kind !== 'tool' && entry.kind !== 'thinking') return false;
  return entry.source === '游戏 Agent';
}

/** 条目归属的轨道（游戏侧 = 游戏 Agent 产生的条目 + 它的世界事件） */
export function laneOf(entry: ShowEntry): LaneId {
  if (
    entry.kind === 'game' ||
    entry.kind === 'game_body' ||
    entry.kind === 'others_things' ||
    entry.kind === 'game_chat' ||
    entry.kind === 'agent_reply' ||
    entry.kind === 'delegate_receipt'
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

/**
 * 收活卡：游戏 Agent 接活的权威事实是 task.changed 受理回执，按 task.changed 的
 * task_id 与主播列 framework_delegate 卡返回的 result.task_id 精确相等做身份配对
 * （任务号由后端按时间+序号生成，全局唯一），在受理时刻往游戏列插一张轻量小卡。
 *
 * 为什么不再按时间近邻猜归属：两次派活挨得近时，"最近一次派活"的推断会错配；
 * 任务号是身份，配不上就是配不上——没有对应派活卡的受理不渲染收活卡。
 * 终态回执不上这张卡：终态已由 execute 卡的任务终态徽标承载。
 */
export function buildDelegateReceipts(entries: ShowEntry[], facts: TaskFact[]): ShowEntry[] {
  // task_id → 派活卡条目（同任务号多张卡时取最早一张；正常不会发生，任务号唯一）
  const delegateByTaskId = new Map<string, ShowEntry>();
  for (const entry of entries) {
    const taskId = frameworkDelegateTaskId(entry);
    if (taskId && !delegateByTaskId.has(taskId)) delegateByTaskId.set(taskId, entry);
  }

  const seen = new Set<string>();
  const receipts: ShowEntry[] = [];
  for (const fact of facts) {
    // 只认受理回执；同一任务号的重复受理只出一张卡
    if (fact.status !== 'accepted' || seen.has(fact.taskId)) continue;
    const delegate = delegateByTaskId.get(fact.taskId);
    if (!delegate) continue;
    seen.add(fact.taskId);
    const args = (delegate.detail as { args?: unknown } | null)?.args;
    const instruction = frameworkDelegateInstruction(args);
    const receipt = makeEntry({
      id: `delegate_receipt:${fact.taskId}`,
      kind: 'delegate_receipt',
      tsMs: fact.tsMs,
      actor: '游戏 Agent',
      text: instruction ? truncateText(instruction, 20) : '（无指令摘要）',
      badge: '收到派活',
      source: '游戏 Agent',
    });
    receipt.delegateEntryId = delegate.id;
    receipt.delegateTsMs = delegate.tsMs;
    receipts.push(receipt);
  }

  // 受理时刻通常晚于派活时刻，两条流各自升序，按时间归并保持整列有序
  receipts.sort((a, b) => a.tsMs - b.tsMs);
  const merged: ShowEntry[] = [];
  let index = 0;
  for (const receipt of receipts) {
    while (index < entries.length && entries[index]!.tsMs <= receipt.tsMs) {
      merged.push(entries[index]!);
      index += 1;
    }
    merged.push(receipt);
  }
  merged.push(...entries.slice(index));
  return merged;
}

// ---------------------------------------------------------------------------
// 游戏侧任务：task.changed 的受理与终态配对成区间，按 goal_id 挂到发起它的
// maicraft_execute 卡上（终态徽标 + 进度查询收拢），不再有浮动的任务组头
// ---------------------------------------------------------------------------

/** 一条 task.changed 事实（受理与终态各算一条，同一 task_id 归并成一个区间） */
export interface TaskFact {
  taskId: string;
  status: string;
  tsMs: number;
}

/** 一次任务调用的完整区间（受理 → 终态） */
export interface TaskWindow {
  taskId: string;
  startMs: number;
  endMs: number;
  /** 终态的中文短名（完成 / 失败 / 取消 / 超时 / 未送达） */
  statusLabel: string;
}

const TERMINAL_STATUS_LABEL: Record<string, string> = {
  succeeded: '完成',
  closed_by_initiator: '认定完成',
  failed: '失败',
  cancelled: '取消',
  timeout: '超时',
  undelivered: '未送达',
};

/** 失败类终态：execute 卡徽标据此用红色 */
const FAILED_STATUS_LABEL = new Set(['失败', '超时', '未送达']);

/**
 * 把事实合成区间。
 *
 * 只有拿得到受理时刻的才成区间——旧数据里每个任务只剩一条终态回执，
 * 起点等于终点，没有耗时可说，这类直接丢弃。
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
      statusLabel: TERMINAL_STATUS_LABEL[terminal.status]!,
    });
  }
  return windows.sort((a, b) => a.startMs - b.startMs);
}

/**
 * 把任务区间按 goal_id 回填到发起它的 maicraft_execute 条目上。
 *
 * 配对键是身份不是时间：execute 的 result.data.goal_id ↔ 任务号的
 * `maicraft-goal-<goal_id>` 形态。配对成功且窗口已有终态时写 taskEnd
 * （终态短名 + 耗时），execute 卡据此把"执行中"徽标替换为"完成 · 9.8s"；
 * 配不上的条目原样返回，维持现状。
 */
export function annotateTaskEnds(entries: ShowEntry[], windows: TaskWindow[]): ShowEntry[] {
  if (windows.length === 0) return entries;
  const windowByTaskId = new Map(windows.map(window => [window.taskId, window]));
  return entries.map(entry => {
    if (!isMinecraftExecuteEntry(entry)) return entry;
    const goalId = minecraftExecuteGoalId(entry);
    if (goalId === null) return entry;
    const window = windowByTaskId.get(`maicraft-goal-${goalId}`);
    if (!window) return entry;
    return {
      ...entry,
      taskEnd: {
        statusLabel: window.statusLabel,
        durationMs: Math.max(0, window.endMs - window.startMs),
        failed: FAILED_STATUS_LABEL.has(window.statusLabel),
      },
    };
  });
}

/** 进度查询卡判定：maicraft_goal 的 operation=get 调用（answer/cancel 等是操作不是查询，不平铺收拢） */
function isGoalPollEntry(entry: ShowEntry): boolean {
  return isMinecraftGoalEntry(entry) && minecraftGoalOperationOf(entry) === 'get';
}

/**
 * 把进度查询卡收拢到对应的 maicraft_execute 卡上。
 *
 * 判据是 goal_id 精确相等，不是时间窗口：operation=get 且 goal_id 与列表内某张
 * execute 卡相等的查询卡从原位摘除，挂到那张 execute 条目的 polls 字段（卡体内部
 * 渲染"进度查询 ×N"，见 ToolCard）；goal_id 对不上的查询卡与 lookup/glance/observe
 * 等其他调用卡一律留在原位。
 *
 * 返回新数组：execute 条目是带 polls 的浅拷贝，其余条目原引用透传。
 */
export function collapseGoalPolls(entries: ShowEntry[]): ShowEntry[] {
  // goal_id → execute 条目在结果数组里的下标（同 goal_id 多张 execute 时取最早一张）
  const ownerOfGoal = new Map<number, number>();
  entries.forEach((entry, index) => {
    const goalId = minecraftExecuteGoalId(entry);
    if (goalId !== null && !ownerOfGoal.has(goalId)) ownerOfGoal.set(goalId, index);
  });

  const result: ShowEntry[] = entries.map(entry =>
    isMinecraftExecuteEntry(entry) ? { ...entry, polls: [] } : entry,
  );
  return result.filter(item => {
    if (!isGoalPollEntry(item)) return true;
    const goalId = minecraftGoalIdOf(item);
    const owner = goalId !== null ? ownerOfGoal.get(goalId) : undefined;
    if (owner === undefined) return true;
    const ownerEntry = result[owner]!;
    ownerEntry.polls = [...(ownerEntry.polls ?? []), item];
    return false;
  });
}

/** 从 `task.changed` 载荷读一条事实（实时事件与归档条目同形状） */
export function taskFactOf(data: Record<string, unknown>, tsMs: number): TaskFact {
  return {
    taskId: typeof data.task_id === 'string' ? data.task_id : '',
    status: typeof data.status === 'string' ? data.status : '',
    tsMs,
  };
}
