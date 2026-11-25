// 工具条目的专用解析：按工具名分流的卡面取值与条目判定
//
// 直播时间线里有两类工具调用不走通用工具卡，各有专用卡面，这里装它们专属的解析：
//   - maicraft_execute：游戏 Agent 下达 Minecraft 目标的调用。卡面取能力名、意图句、
//     要点、分步计划与结果总结
//   - framework_delegate：委派（主播 Agent 交给游戏 Agent 去做的一件事）。卡面取
//     目标 Agent、指令原文、执行方复述与受理态
// 另有 maicraft_goal 的 goal_id / operation 读取：maicraft_execute 卡、进度查询卡与
// 任务终态三者按 goal_id 关联。
//
// 与具体工具无关的时间线条目模型（条目类型、事件到条目的映射、种类分类）在
// liveFeed.ts，所有页面共用。本文件对它只有类型依赖（ShowEntry），运行时不依赖。
//
// 两处消费方：工具卡（ToolCard）按条目的 actor（工具名）分流到专用卡面；双轨分组
// （liveLanes.ts）用这些解析把收活卡（游戏 Agent 接下委派后的回执卡）配回对应的
// framework_delegate 卡、把进度查询卡收拢到对应的 maicraft_execute 卡上、把任务终态
// 回填到 maicraft_execute 卡上。

import { bool, isRecord, num, str } from './guards';
import type { ShowEntry } from './liveFeed';

// maicraft_execute 专用卡面解析（纯函数，ToolCard 按 actor 分流时调用）

/** maicraft_execute 工具的 actor 取值：工具名历史上出现过 maicraft/minecraft 两种拼写 */
const MINECRAFT_EXECUTE_ACTORS: ReadonlySet<string> = new Set([
  'maicraft_execute',
  'minecraft_execute',
]);

/** 该工具条目是否走 maicraft_execute 专用卡面（actor = 工具名，来自 fromToolResult） */
export function isMinecraftExecuteEntry(entry: ShowEntry): boolean {
  return entry.kind === 'tool' && MINECRAFT_EXECUTE_ACTORS.has(entry.actor);
}

/** goal 的 ability 名去掉 maicraft: 前缀（前缀是包名，卡面上只留能力名） */
function stripAbilityPrefix(ability: string): string {
  return ability.replace(/^maicraft:/, '');
}

/** 单步行动（goal.steps 成员） */
export interface MinecraftExecuteStep {
  ability: string;
  purpose: string;
}

/** maicraft_execute 卡面的取值结果：字段缺失时为空串/空数组，渲染层按空值跳过 */
export interface MinecraftExecuteSummary {
  /** 目标能力名（已去 maicraft: 前缀） */
  ability: string;
  /** 人类写的意图句（goal.purpose，卡面主文案） */
  purpose: string;
  /** 从 target/parameters 提取的要点（坐标 / 物品·数量 / 操作名），多段用 · 连接 */
  keyPoints: string;
  /** 入参 dry_run=true 时为真（渲染灰色"预演"徽标） */
  dryRun: boolean;
  /** 异步受理状态：running=执行中 / finished=已完成；缺失或未知为空串 */
  state: string;
  /** 完成时的结果总结（result.data.result.summary），running 时为空 */
  summary: string;
  /** 分步计划（goal.steps），无则为空数组 */
  steps: MinecraftExecuteStep[];
}

/** target 字段 → 要点文本：position 给坐标，其余取 kind/name 等可读值 */
function targetKeyPoint(target: unknown): string {
  if (!isRecord(target)) return '';
  const x = num(target.x);
  const y = num(target.y);
  const z = num(target.z);
  if (x != null && y != null && z != null) return `坐标 ${x},${y},${z}`;
  return str(target.name) || str(target.kind) || str(target.item) || '';
}

/** parameters 字段 → 要点文本：识别"物品·数量"组合，其余原样跳过（完整内容在载荷区） */
function parametersKeyPoint(parameters: unknown): string {
  if (!isRecord(parameters)) return '';
  const item = str(parameters.item) || str(parameters.name);
  const count = num(parameters.count) ?? num(parameters.amount);
  if (item && count != null) return `${item} ×${count}`;
  return item || '';
}

/** maicraft_execute 的入参与结果 → 卡面取值。args/result 任一非对象时字段留空，
 *  调用方（ToolCard）以 ability/purpose 均空判定退回通用卡面 */
export function maicraftExecuteSummary(args: unknown, result: unknown): MinecraftExecuteSummary {
  const empty: MinecraftExecuteSummary = {
    ability: '',
    purpose: '',
    keyPoints: '',
    dryRun: false,
    state: '',
    summary: '',
    steps: [],
  };
  if (!isRecord(args)) return empty;
  const goal = isRecord(args.goal) ? args.goal : {};
  const dryRun = bool(args.dry_run);

  const keyPointParts = [targetKeyPoint(goal.target), parametersKeyPoint(goal.parameters)]
    .filter(Boolean)
    .map(part => part.trim())
    .filter(part => part.length > 0);

  // steps 每步 {ability, purpose}，ability 同样去前缀；非对象成员跳过
  const rawSteps = Array.isArray(goal.steps) ? goal.steps : [];
  const steps: MinecraftExecuteStep[] = rawSteps.flatMap(step => {
    if (!isRecord(step)) return [];
    const ability = stripAbilityPrefix(str(step.ability));
    const purpose = str(step.purpose);
    return ability || purpose ? [{ ability, purpose }] : [];
  });

  // 结果形状：{ok, data:{state, result:{summary, ...}}}；data 或内层 result 缺失时留空
  let state = '';
  let summary = '';
  if (isRecord(result)) {
    const data = isRecord(result.data) ? result.data : {};
    state = str(data.state);
    const inner = isRecord(data.result) ? data.result : {};
    summary = str(inner.summary);
  }

  return {
    ability: stripAbilityPrefix(str(goal.ability)),
    purpose: str(goal.purpose),
    keyPoints: keyPointParts.join(' · '),
    dryRun,
    state,
    summary,
    steps,
  };
}

// maicraft_goal（进度查询/回答/取消等目标操作）与 execute 卡的 goal_id 关联助手

/** maicraft_goal 工具的 actor 取值 */
export function isMinecraftGoalEntry(entry: ShowEntry): boolean {
  return entry.kind === 'tool' && entry.actor === 'maicraft_goal';
}

/** maicraft_execute 卡携带的 goal_id：结果载荷 result.data.goal_id（后端 ledger_task_id
 *  即 `maicraft-goal-<goal_id>` 的配对键）。缺失或非数字为 null */
export function minecraftExecuteGoalId(entry: ShowEntry): number | null {
  if (!isMinecraftExecuteEntry(entry)) return null;
  const result = entry.detail?.result;
  if (!isRecord(result)) return null;
  const data = isRecord(result.data) ? result.data : {};
  const goalId = num(data.goal_id);
  return goalId ?? null;
}

/** maicraft_goal 调用携带的 goal_id（入参 args.goal_id）；缺失或非数字为 null */
export function minecraftGoalIdOf(entry: ShowEntry): number | null {
  const args = entry.detail?.args;
  if (!isRecord(args)) return null;
  const goalId = num(args.goal_id);
  return goalId ?? null;
}

/** maicraft_goal 调用的 operation 入参（get/answer/cancel/…）；缺失为空串 */
export function minecraftGoalOperationOf(entry: ShowEntry): string {
  const args = entry.detail?.args;
  return isRecord(args) ? str(args.operation) : '';
}

// framework_delegate 专用卡面解析（主播 Agent 派活给游戏 Agent 的工具调用）

/** framework_delegate 工具的 actor 判定（actor = 工具名，来自 fromToolResult） */
export function isFrameworkDelegateEntry(entry: ShowEntry): boolean {
  return entry.kind === 'tool' && entry.actor === 'framework_delegate';
}

/** 是否走 framework_delegate 专用卡面：失败卡不进（走通用错误路径），与 maicraft_execute 同款处理 */
export function isFrameworkDelegateCard(entry: ShowEntry): boolean {
  return isFrameworkDelegateEntry(entry) && !entry.failed;
}

/** 派活入参里的指令原文（args.instruction）；framework_delegate 专用卡面与
 *  游戏列收活卡的指令摘要共用同一读取 */
export function frameworkDelegateInstruction(args: unknown): string {
  if (!isRecord(args)) return '';
  return str(args.instruction);
}

/** framework_delegate 卡面的取值结果：字段缺失时为空串/null，渲染层按空值跳过 */
export interface FrameworkDelegateSummary {
  /** 目标 Agent 名（args.agent，头行"→ 名字"） */
  targetAgent: string;
  /** 派活指令原文（args.instruction，卡面主文案） */
  instruction: string;
  /** 执行方复述（result.own_words）；与指令字面相同时留空（不渲染复述行） */
  ownWords: string;
  /** 目标受理标记（result.accepted） */
  accepted: boolean;
  /** 排队受理（result.queued）：目标手上有事，指令进了待办队列 */
  queued: boolean;
  /** 队列位次（result.queue_position），非数字为 null */
  queuePosition: number | null;
  /** 排队说明（result.note，卡面单行截断，全文在载荷区） */
  queueNote: string;
}

/** framework_delegate 的入参与结果 → 卡面取值。结果字段在线上样本位于 result 顶层，
 *  个别事件包一层 data 时穿透读取；入参非对象时各字段留空 */
export function frameworkDelegateSummary(args: unknown, result: unknown): FrameworkDelegateSummary {
  const a = isRecord(args) ? args : {};
  let r: Record<string, unknown> = isRecord(result) ? result : {};
  if (r.accepted === undefined && isRecord(r.data)) r = r.data;
  const instruction = frameworkDelegateInstruction(a);
  const ownWords = str(r.own_words);
  return {
    targetAgent: str(a.agent),
    instruction,
    ownWords: ownWords && ownWords !== instruction ? ownWords : '',
    accepted: bool(r.accepted),
    queued: bool(r.queued),
    queuePosition: num(r.queue_position),
    queueNote: str(r.note),
  };
}

/** framework_delegate 卡携带的任务号（结果载荷 result.task_id；个别事件包一层 data 时
 *  穿透读取）。缺失为空串。收活卡按它与 task.changed 受理回执的 task_id 做身份配对 */
export function frameworkDelegateTaskId(entry: ShowEntry): string {
  if (!isFrameworkDelegateEntry(entry)) return '';
  const result = entry.detail?.result;
  if (!isRecord(result)) return '';
  const source = result.task_id !== undefined ? result : isRecord(result.data) ? result.data : {};
  return str(source.task_id);
}
